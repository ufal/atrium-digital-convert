"""
api_util/render_place.py — the native text of an office document placed on its rendered pages
(atrium-digital-convert#9).

An office document (DOCX, DOC, ODT, RTF) is read twice: its text by the native readers, which see
the document as it was written (paragraphs, headings, notes, tables, accepted tracked changes), and
its pages by LibreOffice's rendering read with the light PDF engine (`api_util/office_render.py`),
which gives the physical pages, their sizes in pt, the words' boxes and the page census.
`place(native, rendered)` joins the two: the result has the rendered pages and the native text only.

1. **Words.** Both sides become word tokens with a comparison key: NFKC (ligatures, superscript
   digits), casefolded, soft hyphens and zero-width characters dropped, quotes and dashes unified,
   punctuation and symbols stripped from both ends. A token whose key is empty (a bullet, a dash,
   a leader) takes no part in matching. A rendered word broken by a hyphen at a line end joins the
   next line's first word when the joined key is a native word.
2. **Alignment, in reading order.** Each native line is matched (`difflib.SequenceMatcher`) against
   a bounded window of the rendered words from a cursor, which then moves past its last match.
   Matches far from the line's best block are dropped, and a line that would jump far on a weak
   match is left for later, so a common word cannot pull the cursor away. Rendered words matched by
   nothing are words the native text does not hold here: a running header repeated on every page,
   a list number, a recomputed field. A second pass looks for each native line still unmatched (a
   text box, a note placed elsewhere) among the unmatched rendered words: between its neighbours,
   one page either side, or anywhere when it occurs exactly once and has at least three words.
3. **Visual lines.** A native line is cut where its matched words change rendered line, so a line
   of the result is a visual line (D18 rule 5) whose text is exactly a stretch of the native text.
   A word without a match goes with the word before it. The box is the union of the matched words'
   boxes, measured on the rendering. A native line with no match at all keeps its whole text, on
   the page of the line before it, without a box (`absent`): it is never dropped.
4. **Blocks and tables.** Lines keep their native group (a paragraph; an ungrouped native line is
   a group of its own), region and heading level, so a paragraph that crosses a page break becomes
   one block on each page. A table row goes, whole, to the page most of its cells landed on; a
   table spread over pages becomes one table per page, each with its rows.
5. **Coverage.** Each rendered page counts its body words (not its running header or footer, and
   only words with a letter) and how many of them the native text matched
   (`DigitalPage.render_words`, `render_matched`). A page whose rendering shows text the native
   text does not place there needs the ATR (`digital_to_json.assess_page`, H12).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Sequence, Tuple

from api_util.digital_ir import (
    REGION_FOOTER,
    REGION_HEADER,
    TABLE_GROUP_PREFIX,
    DigitalDocument,
    DigitalLine,
    DigitalPage,
    DigitalTable,
    renumber_lines,
)

#: The rendered words one native line is matched against: at least this many from the cursor...
WINDOW_MIN = 300
#: ...or this many per word of the line, plus a margin, whichever is more.
WINDOW_PER_WORD = 4
WINDOW_MARGIN = 80
#: A retry over a window this many times longer resynchronises after a long stretch of rendered
#: text the native text does not hold; it needs a strong match (`RESYNC_ANCHOR`, `RESYNC_SHARE`).
RESYNC_FACTOR = 4
RESYNC_ANCHOR = 4
RESYNC_SHARE = 0.8
#: A line may skip this many unmatched rendered words before its first match on a weak match.
FREE_GAP = 40
#: A matching block is kept when its distance from the anchor block, in rendered words, is at most
#: twice its distance in native words plus this slack.
NEAR_SLACK = 25
#: The second pass places a line anywhere in the document only with this many words, and only
#: when they occur exactly once among the unmatched rendered words.
UNIQUE_MIN_WORDS = 3

_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)
_UNIFY = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "«": '"',
        "»": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "−": "-",
    }
)
_HYPHENS = ("-", "­", "‐")
_TOKEN = re.compile(r"\S+")


def _edge(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PSZC"


def comparison_key(word: str) -> str:
    """The key two spellings of one word share (see the module docstring, step 1)."""
    text = unicodedata.normalize("NFKC", word).translate(_INVISIBLE).translate(_UNIFY).casefold()
    start, end = 0, len(text)
    while start < end and _edge(text[start]):
        start += 1
    while end > start and _edge(text[end - 1]):
        end -= 1
    return text[start:end]


@dataclass
class _Word:
    """One rendered word."""

    page: int  # index into rendered.pages
    line: int  # index into that page's lines
    raw: str
    bbox: Optional[List[float]]
    key: str
    #: counts toward the page's coverage: body text, with a letter
    counts: bool
    matched: bool = False
    skip: bool = False
    #: the word a line-end hyphen joined to this one
    joined: Optional["_Word"] = None


@dataclass
class _Native:
    """One native line and its words."""

    order: int
    line: DigitalLine
    spans: List[Tuple[int, int]]
    keys: List[str]
    hits: List[Optional[int]] = field(default_factory=list)

    @property
    def keyed(self) -> List[int]:
        return [i for i, key in enumerate(self.keys) if key]

    @property
    def placed(self) -> bool:
        return any(hit is not None for hit in self.hits)


def _rendered_words(rendered: DigitalDocument) -> List[_Word]:
    words: List[_Word] = []
    for pi, page in enumerate(rendered.pages):
        for li, line in enumerate(page.lines):
            body = line.region not in (REGION_HEADER, REGION_FOOTER)
            pairs = line.words or [(token, line.bbox) for token in line.text.split()]
            for raw, bbox in pairs:
                key = comparison_key(raw)
                if not key:
                    continue
                counts = body and any(ch.isalpha() for ch in key)
                words.append(_Word(pi, li, raw, list(bbox) if bbox else None, key, counts))
    return words


def _join_hyphens(words: List[_Word], native_keys: set) -> None:
    """A word cut by a hyphen at a line end, joined to the next line's first word (step 1)."""
    for word, following in zip(words, words[1:], strict=False):
        if word.skip or not word.raw.endswith(_HYPHENS):
            continue
        if (word.page, word.line) == (following.page, following.line):
            continue
        if word.key in native_keys:
            continue
        joined = comparison_key(word.raw.rstrip("".join(_HYPHENS)) + following.raw)
        if joined in native_keys:
            word.key = joined
            word.joined = following
            following.skip = True


def _natives(native: DigitalDocument) -> List[_Native]:
    out: List[_Native] = []
    for order, line in enumerate(native.all_lines()):
        spans = [(m.start(), m.end()) for m in _TOKEN.finditer(line.text)]
        keys = [comparison_key(line.text[a:b]) for a, b in spans]
        out.append(_Native(order, line, spans, keys, [None] * len(spans)))
    return out


def _align(keys: Sequence[str], rkeys: Sequence[str], cursor: int, resync: bool):
    """The `(native word, rendered word)` pairs of one line, or None (step 2)."""
    span = max(WINDOW_MIN, WINDOW_PER_WORD * len(keys) + WINDOW_MARGIN)
    if resync:
        span *= RESYNC_FACTOR
    window = rkeys[cursor : cursor + span]
    if not window:
        return None
    blocks = [
        b
        for b in SequenceMatcher(None, keys, window, autojunk=False).get_matching_blocks()
        if b.size
    ]
    if not blocks:
        return None
    at = max(range(len(blocks)), key=lambda i: (blocks[i].size, -i))
    anchor = blocks[at]
    kept = [anchor]
    reference = anchor
    for block in reversed(blocks[:at]):
        if (
            reference.b - (block.b + block.size)
            <= 2 * (reference.a - (block.a + block.size)) + NEAR_SLACK
        ):
            kept.insert(0, block)
            reference = block
    reference = anchor
    for block in blocks[at + 1 :]:
        if (
            block.b - (reference.b + reference.size)
            <= 2 * (block.a - (reference.a + reference.size)) + NEAR_SLACK
        ):
            kept.append(block)
            reference = block
    matched = sum(block.size for block in kept)
    share = matched / len(keys)
    if resync:
        accepted = anchor.size >= RESYNC_ANCHOR and share >= RESYNC_SHARE
    else:
        accepted = share >= 0.5 and (len(keys) < 3 or anchor.size >= 2 or share >= 0.6)
        if kept[0].b > FREE_GAP and anchor.size < 3:
            accepted = False
    if not accepted:
        return None
    return [(block.a + k, cursor + block.b + k) for block in kept for k in range(block.size)]


def _first_pass(natives: List[_Native], words: List[_Word]) -> None:
    rkeys = [word.key for word in words]
    cursor = 0
    for nat in natives:
        keyed = nat.keyed
        if not keyed:
            continue
        keys = [nat.keys[i] for i in keyed]
        pairs = _align(keys, rkeys, cursor, resync=False) or _align(
            keys, rkeys, cursor, resync=True
        )
        if not pairs:
            continue
        for a, r in pairs:
            nat.hits[keyed[a]] = r
            words[r].matched = True
        cursor = max(cursor, pairs[-1][1] + 1)


def _occurrences(
    keys: Sequence[str], words: List[_Word], by_key: Dict[str, List[int]]
) -> List[int]:
    found: List[int] = []
    n = len(keys)
    for start in by_key.get(keys[0], ()):
        if start + n > len(words):
            continue
        if all(not words[start + k].matched and words[start + k].key == keys[k] for k in range(n)):
            found.append(start)
    return found


def _second_pass(natives: List[_Native], words: List[_Word]) -> None:
    by_key: Dict[str, List[int]] = defaultdict(list)
    for index, word in enumerate(words):
        by_key[word.key].append(index)
    # The rendered words around each line, from the first pass: the last match before it and the
    # first after it.
    before: List[Optional[int]] = []
    last: Optional[int] = None
    for nat in natives:
        before.append(last)
        if nat.placed:
            last = max(h for h in nat.hits if h is not None)
    after: List[Optional[int]] = [None] * len(natives)
    following: Optional[int] = None
    for position in range(len(natives) - 1, -1, -1):
        after[position] = following
        if natives[position].placed:
            following = min(h for h in natives[position].hits if h is not None)
    last_page = words[-1].page if words else 0
    for position, nat in enumerate(natives):
        keyed = nat.keyed
        if not keyed or nat.placed:
            continue
        keys = [nat.keys[i] for i in keyed]
        found = _occurrences(keys, words, by_key)
        if not found:
            continue
        prev, nxt = before[position], after[position]
        low = words[prev].page - 1 if prev is not None else 0
        high = words[nxt].page + 1 if nxt is not None else last_page
        near = [start for start in found if low <= words[start].page <= high]
        if near:
            anchor = prev if prev is not None else 0
            start = min(near, key=lambda s: (abs(s - anchor), s))
        elif len(keys) >= UNIQUE_MIN_WORDS and len(found) == 1:
            start = found[0]
        else:
            continue
        for k, i in enumerate(keyed):
            nat.hits[i] = start + k
            words[start + k].matched = True


def _union(boxes: Sequence[Sequence[float]]) -> Optional[List[float]]:
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [
        round(min(b[0] for b in boxes), 3),
        round(min(b[1] for b in boxes), 3),
        round(max(b[2] for b in boxes), 3),
        round(max(b[3] for b in boxes), 3),
    ]


@dataclass
class _Segment:
    order: Tuple[int, int]
    page: int
    line: DigitalLine


def _segments(natives: List[_Native], words: List[_Word]) -> List[_Segment]:
    """Step 3: every native line as visual-line segments on rendered pages."""
    out: List[_Segment] = []
    pending: List[_Native] = []  # unplaced lines waiting for the page of a placed one
    last_page: Optional[int] = None

    def group_of(nat: _Native) -> Optional[str]:
        return nat.line.group_id if nat.line.group_id is not None else f"n{nat.order}"

    def unplaced(nat: _Native, page: int) -> _Segment:
        return _Segment(
            (nat.order, 0),
            page,
            replace(nat.line, bbox=None, words=[], group_id=group_of(nat)),
        )

    for nat in natives:
        if not nat.placed:
            if last_page is None:
                pending.append(nat)
            else:
                out.append(unplaced(nat, last_page))
            continue
        where: List[Optional[Tuple[int, int]]] = [
            (words[h].page, words[h].line) if h is not None else None for h in nat.hits
        ]
        first = next(w for w in where if w is not None)
        current = first
        for i, w in enumerate(where):
            if w is None:
                where[i] = current
            else:
                current = w
        runs: List[Tuple[int, int]] = []  # (first token, last token) per run
        for i in range(len(where)):
            if runs and where[runs[-1][0]] == where[i]:
                runs[-1] = (runs[-1][0], i)
            else:
                runs.append((i, i))
        for n, (t0, t1) in enumerate(runs):
            text = nat.line.text[nat.spans[t0][0] : nat.spans[t1][1]]
            boxes: List[List[float]] = []
            for t in range(t0, t1 + 1):
                hit = nat.hits[t]
                if hit is None:
                    continue
                boxes.append(words[hit].bbox or [])
                if words[hit].joined is not None:
                    boxes.append(words[hit].joined.bbox or [])
            page = where[t0][0]
            out.append(
                _Segment(
                    (nat.order, n),
                    page,
                    replace(
                        nat.line, text=text, bbox=_union(boxes), words=[], group_id=group_of(nat)
                    ),
                )
            )
        if pending:
            out.extend(unplaced(p, first[0]) for p in pending)
            pending = []
        last_page = where[-1][0]
    out.extend(unplaced(p, 0) for p in pending)
    return out


def _table_key(group_id: Optional[str]) -> Optional[str]:
    if not group_id or not str(group_id).startswith(TABLE_GROUP_PREFIX):
        return None
    return str(group_id).split("-", 1)[0]


_ROW = re.compile(r"-r(\d+)c\d+")


def _place_tables(
    native: DigitalDocument, segments: List[_Segment], pages: List[DigitalPage]
) -> None:
    """Step 4 for tables: each row on one page, each page its rows' table."""
    tables: Dict[str, DigitalTable] = {}
    for page in native.pages:
        for table in page.tables:
            tables[str(table.group_id or table.table_id)] = table
    rows: Dict[Tuple[str, int], List[_Segment]] = defaultdict(list)
    for seg in segments:
        key = _table_key(seg.line.group_id)
        if key is None or key not in tables:
            continue
        match = _ROW.search(str(seg.line.group_id))
        rows[(key, int(match.group(1)) if match else 0)].append(seg)
    on_page: Dict[Tuple[str, int], set] = defaultdict(set)
    for (key, row), segs in rows.items():
        votes = Counter(seg.page for seg in segs if seg.line.bbox) or Counter(
            seg.page for seg in segs
        )
        top = max(votes.values())
        page = min(p for p, v in votes.items() if v == top)
        for seg in segs:
            if seg.page != page:
                seg.page = page
                seg.line.bbox = None  # measured on another page: not a box of this one
        on_page[(key, page)].add(row)
    for (key, page_index), wanted in sorted(on_page.items()):
        table = tables[key]
        pages[page_index].tables.append(
            replace(
                table,
                page=pages[page_index].page,
                cells=[dict(cell) for cell in table.cells if int(cell.get("row", 0)) in wanted],
            )
        )


def place(native: DigitalDocument, rendered: DigitalDocument) -> DigitalDocument:
    """The native document's text on the rendered document's pages (the module docstring)."""
    natives = _natives(native)
    words = _rendered_words(rendered)
    _join_hyphens(words, {key for nat in natives for key in nat.keys if key})
    words = [w for w in words if not w.skip]
    _first_pass(natives, words)
    _second_pass(natives, words)

    document = DigitalDocument(
        doc_id=native.doc_id,
        origin=native.origin,
        media_type=native.media_type,
        sha512=native.sha512,
        filename=native.filename,
        reading_order=native.reading_order,
        engine=rendered.engine,
        components=list(native.components),
        kind=native.kind,
        notes=list(native.notes),
        conversion=native.conversion,
    )
    document.use(*rendered.components)
    pages: List[DigitalPage] = []
    for rp in rendered.pages:
        pages.append(
            DigitalPage(
                page=rp.page,
                page_index=rp.page_index,
                width=rp.width,
                height=rp.height,
                unit=rp.unit,
                text_layer=rp.text_layer,
                images=rp.images,
                vector_paths=rp.vector_paths,
                text_objects=rp.text_objects,
                invisible_text_objects=rp.invisible_text_objects,
                image_coverage=rp.image_coverage,
                render_words=0,
                render_matched=0,
                frame=rp.frame,
                user_unit=rp.user_unit,
            )
        )
    for word in words:
        if word.counts:
            pages[word.page].render_words += 1
            pages[word.page].render_matched += int(word.matched)
            if word.joined is not None and word.joined.counts:
                pages[word.joined.page].render_words += 1
                pages[word.joined.page].render_matched += int(word.matched)

    segments = _segments(natives, words) if pages else []
    _place_tables(native, segments, pages)
    for seg in sorted(segments, key=lambda s: s.order):
        page = pages[seg.page]
        seg.line.page = page.page
        page.lines.append(seg.line)
    document.pages = pages
    renumber_lines(document)
    return document


__all__ = ["comparison_key", "place"]
