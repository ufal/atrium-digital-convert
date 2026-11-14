#!/usr/bin/env python3
"""
api_util/digital_to_json.py — born-digital documents → docproc (`atr/docproc-json`).

Reads every born-digital type AMČR accepts (atrium-digital-convert#2): PDF and DOCX with this
repository's structural readers (geometry, groups, styles, tables), ODT, ODS, XLSX and RTF with
the shared reader atrium-ocr-postprocess owns (`text_formats.py`, vendored; see
`api_util/digital_text.py`), and legacy DOC and XLS after a headless LibreOffice conversion
(`api_util/digital_legacy.py`). An AMČR seed's `source.sha512` is checked against the bytes of
the original before anything is read (`source_digest_mismatch`, see `verify_seed_digest()`).

Since ARÚP's output contract of 12 November 2026 (atrium-project#84 §1) the output is docproc,
the layout format the ATR writes for scanned pages: pages, blocks and lines with the text-layer
verdict of each page and `needs_atr` where its text cannot be used (`api_util/docproc_writer.py`).
digital-convert writes no record and refuses no document: ocr-postprocess builds the record from
docproc. The docproc of a document with pages that need the ATR is an intermediate (`stored:
false` in the paradata, H14): the ATR's is the stored one.

Four layers, per §2 of llm-enrich's #18 plan (this repository's name before v1.0.0-beta; the plan was
not carried over to the new dev logs):

  A. **Extraction** — into the internal `DigitalDocument` (`api_util/digital_ir.py`).
     Two engines, chosen with `--engine`:

       * `light` (default): `api_util/digital_pdf.py` (pdfplumber + pypdfium2) for PDF,
         `api_util/digital_docx.py` (python-docx + lxml) for DOCX. MIT/BSD/Apache, no
         models, no network — `requirements_digital.txt`.
       * `docling` (opt-in, PDF only): `api_util/digital_docling.py` — Docling's layout and
         table models decide structure and reading order, the light engine's pdfplumber
         lines keep line-level geometry. For complex layouts, when accuracy is worth a
         torch stack — `requirements_digital_docling.txt`.

     Imported lazily, so the base install and the fast lane never pay for either.
  B. **Normalization** — paragraph grouping from geometry, and the decode-sanity gate
     that turns "born-digital" into "born-digital and actually readable".
  C. **Serialization** — docproc (`api_util/docproc_writer.py`), and a record-shaped
     development dict (`development_dict()`) that the Markdown and `/describe` views read.
     No run data and no licence in either.
  D. **Validation & export** — the docproc is validated against the proposal schema
     (`schemas/docproc/`); a docproc that fails is never written. The run's paradata name
     the original read and the docproc written by SHA-512 (`record_run()`).

## Born-digital ≠ trustworthy text

The reason layer B exists at all. Issue #10's research pass found PDFs whose text layer
extracts *successfully* and *plausibly*, and is wrong: non-embedded WinAnsi Helvetica with
no `/ToUnicode` map, so CP1250 (Central European) bytes are read as CP1252 (Western).

    intended : Zpráva o sondě číslo 3. Nalezeny hřeby, vrstva ornice měla
    extracted: Zpráva o sondì èíslo 3. Nalezeny høeby, vrstva ornice mìla

No exception, no encoding error, no length change — and `Zpráva` survives intact, because
`á` sits at the same codepoint in both encodings. *Partially* correct output is what makes
this dangerous: a downstream reader has no signal that anything went wrong.

`CP1250_MISREADS` is derived from the two codecs at import time rather than typed out by
hand, so it is exactly the set of byte values where the two disagree *and* the CP1250
reading is a Czech letter — provably complete, and self-documenting about why `á`/`é` are
absent from it.

Detection feeds two outputs, and deliberately not a third:

  * the line flag **`Garbage`** (docproc `lines[].flags`; `Inverted` for mirrored text): the
    decode verdict, never a line category (D18 rule 2: categories are the record builder's).
    `json_to_md` drops such a line from the Markdown view.
  * the page's `needsAtr` + `needsAtrReason` (in the development dict `needs_ocr` and its
    reason) — the page goes to the ATR, and `metadata.routing` says so.
  * **Not** a silent repair. `decode_sanity()` returns the recovered string because it is
    the single most useful thing a human triaging the document can see, but this converter
    REPORTS; routing policy lives outside it. Rewriting text under the reader's feet would
    make the original's digest describe a document whose text no longer matches it.

The same hand-off carries three more page verdicts (Issue #18, 2026-09-25 — the #10 §9 gap
register): a page with no text layer but an image or vector paths on it (a scanned plate,
text drawn as curves), a text layer of U+FFFD/control characters (a subset font without
/ToUnicode; pdfminer's `(cid:N)` placeholders count, #5), and a text layer that is a prior OCR
run's invisible text over a page image. A page whose only text is its running header or footer,
over images that cover it, has no text layer either (#5). No document is refused for its pages
(#84 §1): until #84 a PDF mostly of prior-OCR pages was refused whole (`ocr_text_layer`).

## Layout cues (the #18 mapping table), as docproc carries them

  * pages — PDF pages (with their `/PageLabels`), DOCX pages from explicit breaks, section
    breaks and Word's `lastRenderedPageBreak` (the rules alto-postprocess #31 uses);
  * blocks — PDF text blocks (per column), DOCX paragraphs, sheets (the IR's `group_id`);
  * block types — `heading` with `level` (PDF: by size; DOCX: outline level or style name),
    `header`, `footer`, `footnote` (the IR's `region`), `paragraph`;
  * `table` blocks with `cellGrid` — DOCX tables and ruled PDF tables (Docling: any table);
  * line and block boxes, page sizes — PDF only.

## Coordinates

`$defs/bbox` is normative: **origin top-left, y increasing downwards**, in the unit named by
that page's `canvas.unit`. For pdfplumber that means `top`/`bottom` — never `y0`/`y1`, which
are PDF user space (origin bottom-left) and are the specific mistake the schema description
calls out. DOCX has no page geometry without rendering, so a DOCX document carries no
box and no page size (the proposal schema allows it for flow documents, ask 13).

## Exit codes

    0  docproc written
    2  a dependency is missing (advice printed, no traceback) — LibreOffice for DOC/XLS too
    3  not an input this converter takes: unsupported format, a seed whose source.sha512 is
       not the file's digest (source_digest_mismatch), or a --document-json seed that cannot
       be opened (invalid_record)
    4  the file is broken: empty, corrupt, encrypted, over the ZIP limits, a failed
       conversion, or over a limit (MAX_PAGES, LIBREOFFICE_TIMEOUT_S, the ODF repeat cap)

Usage:
    python api_util/digital_to_json.py report.pdf --docproc-out report.docproc.json
    python api_util/digital_to_json.py report.docx --document-json seed.document.json --out-dir out/
    python api_util/digital_to_json.py report.pdf --engine docling --paradata-dir paradata/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

# The vendored hub canonical modules live at the repo root; this file is in api_util/.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from api_util.digital_ir import (  # noqa: E402  (re-exported: `d2j.DigitalLine` etc.)
    REGION_FOOTER,
    REGION_FOOTNOTE,
    REGION_HEADER,
    REGIONS,
    TEXT_LAYER_BLANK,
    TEXT_LAYER_DIGITAL,
    TEXT_LAYER_GARBLED,
    TEXT_LAYER_NONE,
    TEXT_LAYER_OCR,
    TEXT_LAYERS,
    DigitalDocument,
    DigitalInputError,
    DigitalLine,
    DigitalPage,
    DigitalTable,
    font_flags,
    is_inverted,
    missing_dependency,
    sha512_file,
    text_layer_of,
)
from atrium_document import (  # noqa: E402
    SCHEMA_VERSION,
    canonical_doc_id,
)
from atrium_limits import ContentLimitError, LimitExceeded  # noqa: E402

__all__ = [
    "REGION_FOOTER",
    "REGION_FOOTNOTE",
    "REGION_HEADER",
    "REGIONS",
    "TEXT_LAYER_BLANK",
    "TEXT_LAYER_DIGITAL",
    "TEXT_LAYER_GARBLED",
    "TEXT_LAYER_NONE",
    "TEXT_LAYER_OCR",
    "TEXT_LAYERS",
    "DigitalDocument",
    "DigitalInputError",
    "DigitalLine",
    "DigitalPage",
    "DigitalTable",
]

#: Kept for callers of the pre-split names.
_missing = missing_dependency
_font_flags = font_flags
_is_inverted = is_inverted

PROGRAM = "digital-convert"

#: `source.origin` values this converter writes. Both match an `ORIGIN_ORIGINATORS` prefix,
#: which is what routes the positional plane to this program. An origin the table has not
#: been taught makes `_assert_origin_consistent()` ABSTAIN rather than refuse — silently
#: switching §1a off for that document — so these strings are not cosmetic.
ORIGIN_PDF = "digital-born-pdf"
ORIGIN_DOCX = "digital-born-docx"

#: `--engine` values. `light` is the default and the only one the fast lane exercises.
ENGINES: Tuple[str, ...] = ("light", "docling")

#: `--docx-page-breaks` values — alto-postprocess `[TEXT_INGEST].PAGE_BREAKS`, same meaning.
DOCX_PAGE_BREAK_MODES: Tuple[str, ...] = ("auto", "explicit", "none")

# ---------------------------------------------------------------------------
# Line-category labels  (hub registry: atrium_vocab.LINE_CATEGORY_ORIGINATORS)
# ---------------------------------------------------------------------------
# The two load-bearing `categ` spellings this converter writes, and the only ones it
# ever writes. `lines[].categ`'s other authorised originator, alto-postprocess, emits a
# DISJOINT five-value set ({Clear, Empty, Noisy, Non-text, Trash}) — an OCR verdict over
# a rendered image, where these two are a decode-sanity verdict over an embedded text
# layer. The disjointness is deliberate and is not drift to reconcile; a consumer that
# filters this field must handle BOTH sets (see defect V-1 in the hub's
# docs/skos_strategy.md §6, and the note over `json_to_md.DROP_CATEGORIES`).
#
# The strings are TAKEN FROM the hub registry rather than typed here, so the declaration
# and the emission cannot drift apart unnoticed — but never at the cost of changing what
# this converter emits. `atrium_vocab.py` is a vendored copy and is legitimately absent
# in some execution contexts (a bare api_util/ next to a notebook, an image built before
# the vendor step), and a stale or re-ordered copy must not silently re-spell the
# contract. So the literals below are the floor: the registry is used only when it
# agrees with them, and a disagreement is REPORTED and then ignored. That follows the
# house idiom (atrium_document.py's origin check, alto-postprocess's text_util.py):
# abstain with a NOTE on stderr, never fatal. Silence means agreement.
_CATEG_FALLBACK: Tuple[str, ...] = ("Garbage", "Inverted")

try:
    from atrium_vocab import LINE_CATEGORY_ORIGINATORS as _VOCAB_LINE_CATEGORY_ORIGINATORS
except ImportError:  # registry not vendored here — abstain, do not guess
    _declared: Tuple[str, ...] = ()
else:
    _declared = tuple(sorted(_VOCAB_LINE_CATEGORY_ORIGINATORS.get(PROGRAM, ())))

if _declared and _declared != _CATEG_FALLBACK:
    print(
        f"[digital_to_json] NOTE - line-category drift: this module emits "
        f"{list(_CATEG_FALLBACK)} but atrium_vocab declares {list(_declared)} for "
        f"originator {PROGRAM!r}. Emission is unchanged; reconcile the registry or this "
        f"file (see defect V-1 in the hub's docs/skos_strategy.md).",
        file=sys.stderr,
    )

#: Sorted, so the comparison above and the unpacking here are order-independent — the
#: registry sorts its own union the same way (`atrium_vocab.LINE_CATEGORIES`).
CATEGORIES_EMITTED: Tuple[str, ...] = _declared if _declared == _CATEG_FALLBACK else _CATEG_FALLBACK

#: The two load-bearing `categ` spellings (`json_to_md.DROP_CATEGORIES`).
CATEG_GARBAGE, CATEG_INVERTED = CATEGORIES_EMITTED

#: Below this decode-sanity ratio a line is `Garbage` — the density signal, for a line
#: whose diacritics are mostly wrong.
QUALITY_GARBAGE_BELOW = 0.90

#: ...and this many confusable characters condemn a line regardless of its length.
#:
#: A ratio ALONE provably cannot catch the real corruption. The observed line
#: "Zpráva o sondì èíslo 3. Nalezeny høeby, vrstva ornice mìla" carries 4 misreads across
#: 46 letters, which scores 0.913 — comfortably above any ratio cut loose enough not to
#: condemn clean text. Czech simply does not contain `ì`, `è`, `ø` or `ù`, so their presence
#: is not a density question: ONE may be a foreign word in a quotation, but TWO in a single
#: line is a systematic decode fault, which is exactly the shape of this failure.
#:
#: The two rules are kept separate rather than folded into one score because they answer
#: different questions — "how corrupt is this line?" (which `quality_score` must keep
#: reporting as a 0–1 axis) and "is this line trustworthy at all?".
GARBAGE_MIN_HITS = 2

#: Share of a line's visible characters that are U+FFFD or control / format / private-use /
#: unassigned code points above which the text layer does not decode at all — a subset font
#: without /ToUnicode. The threshold legacy `pdf_to_md` and alto-postprocess #31 use; the
#: JSON route had no such check, so switching the auto-convert to it would have regressed.
#: A glyph pdfminer cannot map arrives here as U+FFFD: the light engine turns its `(cid:N)`
#: placeholder into one (`digital_ir.unmapped_to_replacement`, #5), so a line whose glyphs are
#: mostly unmapped is `Garbage`, and a line with a few (the letters outside WinAnsi of a font
#: without /ToUnicode) keeps them visible as U+FFFD without condemning its page.
GARBLE_CHAR_SHARE = 0.15

#: The mojibake verdict needs positive evidence the line is Czech read through the wrong
#: code page, not merely one of the four misread letters — which are ordinary French and
#: Italian letters (`è`, `ù`, `ì`, `ò`). Ported from atrium-alto-postprocess
#: `text_formats.mojibake_line` (#31, Phase 4) so both tools judge a line alike: a letter
#: CP1250 and CP1252 share that Czech uses (so it survives the misread), and no letter only
#: French uses. "très complète" was Garbage here before.
MOJIBAKE_SHARED_LETTERS = frozenset("áíúýšžÁÍÚÝŠŽ")
MOJIBAKE_FOREIGN_LETTERS = frozenset("àêûœÀÊÛŒ")

#: Page-level band cutoffs, matching the vocabulary alto-postprocess already uses so a
#: consumer does not have to know which originator wrote the page.
BAND_CLEAR_AT = 0.90
BAND_NOISY_AT = 0.50

#: Paragraph splitting. Measured on tests/fixtures/digital/minimal.pdf: 2.0 pt between
#: lines of one paragraph, 34.0 pt between paragraphs (digital_born/README.md). A ratio
#: is used rather than an absolute, because the gap scales with the font.
PARAGRAPH_GAP_RATIO = 1.8

#: Every input kind `sniff()` answers: every born-digital type AMČR accepts.
KINDS: Tuple[str, ...] = ("pdf", "docx", "odt", "ods", "xlsx", "rtf", "doc", "xls")

#: Media types per kind, for a caller (the service's 415 `accepted`, its /info).
MEDIA_TYPES: Dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "rtf": "application/rtf",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
}


def _czech_letters() -> frozenset:
    return frozenset("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ")


def _build_cp1250_misreads() -> Dict[str, str]:
    """Characters a CP1250 byte turns into when read as CP1252, where CP1250 meant Czech.

    Derived from the codecs, not hand-typed: for every high byte, decode it both ways and
    keep the pair only when the readings differ AND the CP1250 reading is a Czech letter.
    That makes the table provably complete for this failure mode, and explains its own
    omissions — `á` (0xE1) and `é` (0xE9) decode identically under both codecs, which is
    exactly why "Zpráva" survives a mis-decode while "sondě" does not.
    """
    czech = _czech_letters()
    table: Dict[str, str] = {}
    for byte in range(0x80, 0x100):
        try:
            western = bytes([byte]).decode("cp1252")
            eastern = bytes([byte]).decode("cp1250")
        except UnicodeDecodeError:  # undefined in one of the codecs
            continue
        if western != eastern and eastern in czech:
            table[western] = eastern
    return table


CP1250_MISREADS: Dict[str, str] = _build_cp1250_misreads()


# ──────────────────────────────────────────────────────────────────────────────
# Layer B — normalization and the decode-sanity gate  (pure; no optional deps)
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class DecodeReport:
    """What `decode_sanity()` concluded about one string."""

    score: float
    suspicious: int
    letters: int
    recovered: Optional[str] = None
    #: U+FFFD / control / format / private-use / unassigned characters among `chars`.
    bad: int = 0
    #: Visible (non-whitespace) characters.
    chars: int = 0
    #: The whole string is representable in CP1252 — a CP1250 byte string misread as CP1252
    #: always is; a string that also holds a correct `ě` or `ř` is not that failure.
    encodable: bool = True
    #: A letter only French uses (`à`, `ê`, `û`, `œ`) — evidence against the Czech reading.
    foreign: bool = False
    #: A letter Czech uses that survives the misread (`á`, `í`, `ú`, `ý`, `š`, `ž`).
    shared: bool = False

    @property
    def is_mojibake(self) -> bool:
        """CP1250 read as CP1252, on positive evidence (see `MOJIBAKE_SHARED_LETTERS`)."""
        systematic = self.suspicious >= GARBAGE_MIN_HITS or self.score < QUALITY_GARBAGE_BELOW
        return systematic and self.encodable and self.shared and not self.foreign

    @property
    def is_undecodable(self) -> bool:
        """Mostly U+FFFD or control characters: the text layer does not decode at all."""
        return bool(self.chars) and self.bad / self.chars > GARBLE_CHAR_SHARE

    @property
    def is_garbage(self) -> bool:
        """Either verdict condemns the line — see `GARBAGE_MIN_HITS` for why the mojibake
        rule needs a count as well as a ratio."""
        return self.is_mojibake or self.is_undecodable


def decode_sanity(text: str) -> DecodeReport:
    """Score 0..1 for "this text decoded correctly", plus the recovered reading.

    The score is the share of letters that are NOT a CP1250→CP1252 misread. It is
    deliberately a ratio, not a count: a caption of three words and a page of three hundred
    must be comparable, and `pages[].quality_score` is documented as a 0–1 axis either way.

    `recovered` is offered only when the whole string round-trips — `encode("cp1252")` then
    `decode("cp1250")`. A string containing a character CP1252 cannot represent was never a
    CP1250 byte sequence read this way, so a partial guess there would be fabrication.
    """
    visible = [c for c in text if not c.isspace()]
    bad = sum(1 for c in visible if c == "�" or unicodedata.category(c) in ("Cc", "Cf", "Co", "Cn"))
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return DecodeReport(score=1.0, suspicious=0, letters=0, bad=bad, chars=len(visible))

    suspicious = sum(1 for c in letters if c in CP1250_MISREADS)
    score = 1.0 - (suspicious / len(letters))

    try:
        encoded = text.encode("cp1252")
        encodable = True
    except UnicodeEncodeError:
        encoded, encodable = b"", False

    recovered: Optional[str] = None
    if suspicious and encodable:
        try:
            candidate = encoded.decode("cp1250")
        except UnicodeDecodeError:
            candidate = None
        if candidate and candidate != text:
            recovered = candidate

    present = set(letters)
    return DecodeReport(
        score=score,
        suspicious=suspicious,
        letters=len(letters),
        recovered=recovered,
        bad=bad,
        chars=len(visible),
        encodable=encodable,
        foreign=bool(present & MOJIBAKE_FOREIGN_LETTERS),
        shared=bool(present & MOJIBAKE_SHARED_LETTERS),
    )


def classify_line(line: DigitalLine, report: DecodeReport) -> Optional[str]:
    """The `categ` this line carries, or None to leave the field absent.

    Only the two spellings `json_to_md.DROP_CATEGORIES` knows are ever produced. `Inverted`
    wins over `Garbage`: mirrored text is a rendering-level fault, and its extracted string
    is not evidence of anything, so reporting a decode verdict on it would be noise.
    """
    if line.inverted:
        return CATEG_INVERTED
    if report.is_garbage:
        return CATEG_GARBAGE
    return None


def assign_group_ids(lines: Sequence[DigitalLine], prefix: str = "p") -> None:
    """Group consecutive lines into paragraphs, in place, from vertical gaps.

    `lines[].group_id` is what `json_to_md` turns back into a blank line, so this is the
    only thing preserving paragraph structure through the JSON. Only lines with geometry and
    no group yet are grouped: DOCX paragraphs and table cells arrive grouped from Layer A,
    and inventing groups from nothing would be worse than the absence the schema allows.

    The threshold is relative (`PARAGRAPH_GAP_RATIO` × a low percentile of the gaps) so it
    holds for a 10 pt body and a 24 pt heading alike; an absolute cut tuned on one fixture
    would not. A change of column, of region (header, body, footer) or of heading level,
    and any upward jump, also start a new group whatever the gap says.

    `prefix` makes the ids unique per document (`p{page_index}-{n}`): a consumer may group
    lines by `group_id` across pages, and "lines sharing a group_id are contiguous".
    """
    positioned = [ln for ln in lines if ln.bbox and ln.group_id is None]
    if not positioned:
        return
    if len(positioned) == 1:
        positioned[0].group_id = f"{prefix}0"
        return

    gaps: List[float] = []
    for previous, current in zip(positioned, positioned[1:], strict=False):
        gaps.append(max(0.0, current.bbox[1] - previous.bbox[3]))

    # The baseline is a LOW PERCENTILE of the positive gaps, not their median: within-
    # paragraph leading is the common small value, and paragraph breaks are the rare large
    # one, so the median is dragged upward by them exactly when there are few lines. On the
    # measured fixture (gaps 2.0 and 34.0) the median is 34.0 and nothing ever splits — the
    # threshold would sit above the very break it exists to find. The 25th percentile also
    # resists a single freak sub-point gap, which a bare `min()` would not.
    ordered = sorted(g for g in gaps if g > 0)
    baseline = ordered[max(0, len(ordered) // 4)] if ordered else 0.0
    threshold = baseline * PARAGRAPH_GAP_RATIO if baseline else None

    group_index = 0
    positioned[0].group_id = f"{prefix}{group_index}"
    for gap, previous, current in zip(gaps, positioned[:-1], positioned[1:], strict=True):
        boundary = (
            (threshold is not None and gap > threshold)
            or current.column != previous.column
            or current.region != previous.region
            or current.heading_level != previous.heading_level
            or current.bbox[1] < previous.bbox[1] - 1.0
        )
        if boundary:
            group_index += 1
        current.group_id = f"{prefix}{group_index}"


def _reason_no_text(page: DigitalPage) -> str:
    drawn = []
    if page.images:
        drawn.append(f"{page.images} image(s)")
    if page.vector_paths:
        drawn.append(f"{page.vector_paths} vector path(s)")
    if drawn:
        return (
            f"no extractable text layer: the page draws {' and '.join(drawn)} but carries no "
            f"text — a scanned page or text drawn as curves"
        )
    return (
        "no extractable text layer: the page draws nothing a parser can see — blank, or its "
        "content is out of the parser's reach; re-acquire it to be sure"
    )


def _reason_furniture_only(page: DigitalPage) -> str:
    furniture = " / ".join(line.text for line in page.lines)
    if len(furniture) > 60:
        furniture = furniture[:59] + "…"
    coverage = f"{page.image_coverage:.0%}" if page.image_coverage is not None else "most"
    return (
        f"no text layer under the page furniture: images cover {coverage} of the page and its "
        f"only text is its running header or footer ({furniture!r}) — a scanned page that the "
        f"PDF's producer numbered or stamped"
    )


def assess_page(page: DigitalPage) -> None:
    """Fill in `quality_score`, `quality_band`, `needs_ocr` and its reason, in place.

    `needs_ocr_reason` is written whenever `needs_ocr` is, and never alone. The flag means
    different things on the two originator paths — "no text layer" for a scan, "a text
    layer that lies" here — and `json_to_md` emits the reason as a cue the model reads.
    Without it every digital-born page rendered "no extractable text layer", which is false
    for a page that has one.

    A page with no lines has no score: there is nothing the score could be about, and
    writing Clear/1.0 for it is how an image-only page vanished (G1). A PDF page without text
    is flagged (Layer A's `text_layer == "none"`); an empty DOCX page (`blank`, two breaks in
    a row) is not — there is no page image for an OCR engine to read. A prior OCR layer is
    flagged whether or not any of its text became a line (a page whose words are all table
    cells has none), so the flag is exactly the `garbled`, `ocr` and `none` pages of
    `pages[].text_layer` (`digital_ir.text_layer_of`). That includes a `none` page that HAS lines:
    its only text is its running header or footer, over images that cover the page
    (`digital_pdf._mark_furniture_only`, #5); its lines are scored like any other.

    A page whose words lose letters to glyphs without a Unicode mapping (U+FFFD) but which decodes
    otherwise is not flagged: since atrium-digital-convert#9 (point 3, agreed 11-13) that is a
    question of text quality, answered by ocr-postprocess's scoring of its lines, not a reason to
    send the page to the ATR. (#5 flagged it from 10-22, at `ROUTE_DAMAGED_WORD_SHARE`.)
    """
    reasons: List[str] = []
    if not page.lines:
        page.quality_score = None
        page.quality_band = None
        if page.text_layer == TEXT_LAYER_NONE:
            reasons.append(_reason_no_text(page))
    else:
        scored = [ln.quality_score for ln in page.lines if ln.quality_score is not None]
        page.quality_score = round(sum(scored) / len(scored), 4) if scored else 1.0
        if page.quality_score >= BAND_CLEAR_AT:
            page.quality_band = "Clear"
        elif page.quality_score >= BAND_NOISY_AT:
            page.quality_band = "Noisy"
        else:
            page.quality_band = "Trash"

        garbled = [ln for ln in page.lines if ln.categ == CATEG_GARBAGE]
        undecodable = [ln for ln in garbled if decode_sanity(ln.text).is_undecodable]
        mojibake = [ln for ln in garbled if ln not in undecodable]
        if mojibake:
            reasons.append(
                f"embedded text layer does not decode: {len(mojibake)} of {len(page.lines)} "
                f"lines carry CP1250 bytes read as CP1252 (mojibake diacritics), page "
                f"decode-sanity {page.quality_score:.2f}. The page has a text layer; it is "
                f"not trustworthy."
            )
        if undecodable:
            reasons.append(
                f"garbled text layer: {len(undecodable)} of {len(page.lines)} lines are over "
                f"{GARBLE_CHAR_SHARE:.0%} glyphs without a Unicode mapping (U+FFFD) or control "
                f"characters (a font without /ToUnicode?). The page has a text layer; it does "
                f"not decode."
            )
        if page.text_layer == TEXT_LAYER_NONE:
            # The page's content has no text layer; its lines are a page number or a running
            # header. Scoring them would call a scan `Clear`, so the page carries no score.
            reasons.append(_reason_furniture_only(page))
            page.quality_score = None
            page.quality_band = None
    if page.text_layer == TEXT_LAYER_OCR:
        reasons.append(
            f"the text layer is a prior OCR run: {page.invisible_text_objects} of "
            f"{page.text_objects} text objects are invisible (render mode 3) over a page "
            f"image. Re-acquire the page through the OCR originator (ocr-postprocess, "
            f"source.origin ocr:pdf-text-layer)."
        )

    page.needs_ocr = bool(reasons)
    page.needs_ocr_reason = " ".join(reasons)


def normalize(doc: DigitalDocument) -> DigitalDocument:
    """Run Layer B over an extracted document, in place, and return it.

    Two passes per page. The first judges every line on its own evidence. The second
    applies the page's verdict (G6): once a page is condemned for mojibake, a line with a
    single misread letter is not "one foreign word" any more — it is the same corruption
    at a lower density, and letting it through leaked 2 of `garbled.pdf`'s 3 bad lines.
    Only the shared-letter requirement is relaxed there; a French letter or a character
    CP1252 cannot hold still clears the line.
    """
    for page in doc.pages:
        reports: Dict[int, DecodeReport] = {}
        for line in page.lines:
            report = decode_sanity(line.text)
            reports[id(line)] = report
            quality = report.score
            if report.chars:
                quality = min(quality, 1.0 - report.bad / report.chars)
            line.quality_score = round(quality, 4)
            categ = classify_line(line, report)
            if categ:
                line.categ = categ
        condemned = any(
            line.categ == CATEG_GARBAGE and reports[id(line)].is_mojibake for line in page.lines
        )
        if condemned:
            for line in page.lines:
                report = reports[id(line)]
                if (
                    line.categ is None
                    and report.suspicious >= 1
                    and report.encodable
                    and not report.foreign
                ):
                    line.categ = CATEG_GARBAGE
        assign_group_ids(page.lines, prefix=f"p{page.page_index}-")
        assess_page(page)
    return doc


# ──────────────────────────────────────────────────────────────────────────────
# Layer A — extraction (lazy optional imports)
# ──────────────────────────────────────────────────────────────────────────────

_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_OLE2_ENCRYPTED_STREAM = "EncryptedPackage".encode("utf-16-le")
#: OLE2 directory entry names (UTF-16LE) that tell a Word 97–2003 document from an Excel one.
_OLE2_WORD_STREAM = "WordDocument".encode("utf-16-le")
_OLE2_EXCEL_STREAMS = ("Workbook".encode("utf-16-le"), "Book".encode("utf-16-le"))
#: The `mimetype` member of an OpenDocument package, for the two types AMČR accepts.
_ODF_KINDS = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.text-template": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.spreadsheet-template": "ods",
}
_ACCEPTED = "PDF, DOCX, ODT, ODS, XLSX, RTF, DOC or XLS"


def sniff(path: str) -> str:
    """The input's kind (one of `KINDS`), decided by content — the extension only names the file.

    A PDF header may sit anywhere in the first KB; an RTF file starts with `{\\rtf`. A ZIP is a
    Word package when its package relationship or members name WordprocessingML (so `.docm`,
    `.dotx`, `.dotm` and a mis-named `.zip` all qualify), an Excel one when it has
    `xl/workbook.xml`, an OpenDocument text or spreadsheet when its `mimetype` member says so.
    OLE2 is an encrypted OOXML package, a legacy Word document (`WordDocument` stream), a
    legacy Excel workbook (`Workbook`/`Book`), or another legacy Office file this converter
    does not take (PowerPoint). Everything else, presentations and e-books included, is not a
    born-digital type AMČR accepts.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(1024)
    except OSError as exc:
        raise DigitalInputError("unsupported", f"unsupported input {path!r}: {exc}") from exc
    if not head:
        # An empty file is a broken one, as the service answers it (422 `corrupt`), not a
        # format this converter does not take.
        raise DigitalInputError("corrupt", f"the file {os.path.basename(path)!r} is empty")
    if b"%PDF-" in head:
        return "pdf"
    if head.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                mimetype = ""
                if "mimetype" in names:
                    mimetype = zf.read("mimetype")[:200].decode("ascii", "replace").strip()
        except (zipfile.BadZipFile, OSError) as exc:
            raise DigitalInputError("corrupt", f"not a readable ZIP package ({exc})") from exc
        if "word/document.xml" in names or (
            "_rels/.rels" in names and any(n.startswith("word/") for n in names)
        ):
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        if mimetype in _ODF_KINDS:
            return _ODF_KINDS[mimetype]
        raise DigitalInputError(
            "unsupported",
            f"unsupported input {path!r}: a ZIP package but not a born-digital document type "
            f"this converter reads (expected {_ACCEPTED})",
        )
    if head.startswith(_OLE2_MAGIC):
        with open(path, "rb") as handle:
            blob = handle.read(64 * 1024 * 1024)
        if _OLE2_ENCRYPTED_STREAM in blob:
            raise DigitalInputError("encrypted", "password-protected Office document")
        if _OLE2_WORD_STREAM in blob:
            return "doc"
        if any(stream in blob for stream in _OLE2_EXCEL_STREAMS):
            return "xls"
        raise DigitalInputError(
            "legacy_office_unsupported",
            f"unsupported input {path!r}: a legacy binary Office file that is neither a Word "
            f"document nor an Excel workbook (expected {_ACCEPTED})",
        )
    raise DigitalInputError("unsupported", f"unsupported input {path!r}: expected {_ACCEPTED}")


def open_record(baseline: Optional[str]) -> Optional[Dict[str, Any]]:
    """The record `--document-json` names, opened as the service opens its `document_json` part.

    None when there is none: no path, a missing file (rule 1: a first stage has no record) or an
    empty one. A record that cannot be opened at all is refused, `invalid_record` (exit 3; the
    service's 422): not UTF-8 JSON, not a JSON object, or a `schema_version` major newer than
    this tool reads. It used to end in a traceback. A record that opens but does not validate
    is returned: it is accreted onto, and reported (`inherited_schema_problem`).
    """
    if not baseline or not os.path.exists(baseline):
        return None
    with open(baseline, "rb") as handle:
        raw = handle.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DigitalInputError("invalid_record", f"{baseline!r} is not UTF-8 text") from exc
    if not text.strip():
        return None
    try:
        record = json.loads(text)
    except ValueError as exc:
        raise DigitalInputError("invalid_record", f"{baseline!r} is not valid JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise DigitalInputError(
            "invalid_record", f"{baseline!r} is a JSON {type(record).__name__}, not an object"
        )
    version = record.get("schema_version")
    if version is not None:
        supported = int(str(SCHEMA_VERSION).split(".")[0])
        try:
            major = int(str(version).split(".")[0])
        except ValueError:
            raise DigitalInputError(
                "invalid_record",
                f"{baseline!r} has schema_version {version!r}, which is not a version",
            ) from None
        if major > supported:
            raise DigitalInputError(
                "invalid_record",
                f"{baseline!r} has schema_version {version}; this tool reads up to {supported}.x",
            )
    return record


def verify_seed_digest(input_path: str, baseline: Optional[str]) -> Optional[str]:
    """Refuse a seed whose `source.sha512` is not the digest of the file received.

    atrium-digital-convert#2 (accepted by AMČR 2026-09-30): the service holds the original's
    bytes, so it can check what the archive says they are. Done BEFORE anything is parsed, on
    the ORIGINAL bytes — for a DOC/XLS that is the file before LibreOffice converts it. A seed
    without `sha512` passes (the field is optional in the schema), as does a missing baseline.
    Returns the verified digest, or None when there was nothing to verify.
    """
    seed = open_record(baseline)
    if seed is None:
        return None
    source = seed.get("source") or {}
    expected = source.get("sha512") if isinstance(source, dict) else None
    if not isinstance(expected, str) or not expected.strip():
        return None
    actual = sha512_file(input_path)
    if actual != expected.strip().lower():
        raise DigitalInputError(
            "source_digest_mismatch",
            f"the record's source.sha512 ({expected.strip()[:16]}…) is not the SHA-512 of the file "
            f"received ({actual[:16]}…): the record would describe one file under another file's "
            f"identity. Send the original the seed was made for, or a seed made for this file.",
        )
    return actual


def _pdf_page_count(path: str) -> Optional[int]:
    """The PDF's page count from its page tree (pypdfium2), or None when it cannot be read."""
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415  (optional dependency, imported on use)
    except ImportError:
        return None
    try:
        pdf = pdfium.PdfDocument(path)
    except Exception:
        return None
    try:
        return len(pdf)
    finally:
        pdf.close()


def check_page_count(pages: Optional[int], what: str = "The document") -> None:
    """Refuse a document over `MAX_PAGES` (422 `limit_exceeded`; CLI exit 4)."""
    if pages is None:
        return
    import tool_limits  # noqa: PLC0415  (repo root)

    tool_limits.MAX_PAGES.check(
        pages,
        detail=(
            f"{what} has {pages} pages; the limit is {tool_limits.MAX_PAGES.get()} (MAX_PAGES). "
            "Split it, or raise the limit."
        ),
    )


def extract_pdf(path: str, doc_id: Optional[str] = None) -> DigitalDocument:
    """Layer A for PDF, light engine — `api_util/digital_pdf.py`."""
    from api_util.digital_pdf import extract_pdf as _extract  # noqa: PLC0415

    return _extract(path, doc_id or canonical_doc_id(path), ORIGIN_PDF)


def extract_docx(
    path: str, doc_id: Optional[str] = None, page_breaks: str = "auto"
) -> DigitalDocument:
    """Layer A for DOCX — `api_util/digital_docx.py`."""
    from api_util.digital_docx import extract_docx as _extract  # noqa: PLC0415

    return _extract(path, doc_id or canonical_doc_id(path), ORIGIN_DOCX, page_breaks=page_breaks)


def extract(
    path: str,
    doc_id: Optional[str] = None,
    engine: str = "light",
    docx_page_breaks: str = "auto",
) -> DigitalDocument:
    """Dispatch on the content. Unknown formats fail loudly rather than guessing."""
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, got {engine!r}")
    kind = sniff(path)
    doc_id = doc_id or canonical_doc_id(path)
    if engine == "docling" and kind != "pdf":
        print(
            "[digital-convert] NOTE - --engine docling applies to PDF only: Docling's DOCX "
            "backend reads the same OOXML with no page model. Using the light readers.",
            file=sys.stderr,
        )
    if kind == "pdf":
        check_page_count(_pdf_page_count(path), "The PDF")
        document = extract_pdf(path, doc_id=doc_id)
        if engine == "docling":
            try:
                from api_util.digital_docling import refine_with_docling  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - the module itself has no hard deps
                raise missing_dependency(
                    "docling", "docling", "requirements_digital_docling.txt"
                ) from exc
            document = refine_with_docling(path, document)
        # No refusal (atrium-project#84 §1): a page whose text layer is a prior OCR run needs
        # the ATR like any page whose text cannot be used (`assess_page`, `needs_atr`). Until
        # #84 a PDF mostly of such pages was refused whole (422 `ocr_text_layer`).
    elif kind in ("doc", "xls"):
        from api_util.digital_legacy import extract_legacy  # noqa: PLC0415

        document = extract_legacy(
            path,
            doc_id,
            kind,
            read_converted=lambda converted, converted_kind: _read_native(
                converted, doc_id, converted_kind, docx_page_breaks
            ),
        )
    else:
        document = _read_native(path, doc_id, kind, docx_page_breaks)
    document.kind = document.kind or kind
    check_page_count(len(document.pages))
    return document


def _read_native(path: str, doc_id: str, kind: str, docx_page_breaks: str) -> DigitalDocument:
    """DOCX through this repository's reader; ODT/ODS/XLSX/RTF through the shared reader."""
    if kind == "docx":
        document = extract_docx(path, doc_id=doc_id, page_breaks=docx_page_breaks)
        document.kind = "docx"
        return document
    from api_util.digital_text import extract_text_document  # noqa: PLC0415

    try:
        import tool_limits  # noqa: PLC0415  (repo root)

        max_file_mb, max_pages = tool_limits.MAX_UPLOAD.get(), tool_limits.MAX_PAGES.get()
    except ImportError:  # pragma: no cover
        max_file_mb, max_pages = None, None
    return extract_text_document(path, doc_id, kind, max_file_mb=max_file_mb, max_pages=max_pages)


# ──────────────────────────────────────────────────────────────────────────────
# Layer C — the development dict (the Markdown and /describe views) and docproc
# ──────────────────────────────────────────────────────────────────────────────


def _page_rows(doc: DigitalDocument) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in doc.pages:
        row: Dict[str, Any] = {"page": page.page, "page_index": page.page_index}
        # Absent, never null: a line-less page has nothing to score.
        if page.quality_score is not None:
            row["quality_score"] = page.quality_score
        if page.quality_band is not None:
            row["quality_band"] = page.quality_band
        if page.width and page.height:
            row["canvas"] = {"width": page.width, "height": page.height, "unit": page.unit}
        if page.needs_ocr:
            row["needs_ocr"] = True
            row["needs_ocr_reason"] = page.needs_ocr_reason
        row["text_layer"] = text_layer_of(page)
        rows.append(row)
    return rows


def _line_rows(doc: DigitalDocument) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in doc.all_lines():
        row: Dict[str, Any] = {"page": line.page, "line": line.line, "text": line.text}
        if line.bbox:
            row["bbox"] = line.bbox
        if line.group_id is not None:
            row["group_id"] = line.group_id
        # The decode verdict is a flag, as on docproc lines (D18 rule 2): `json_to_md` drops
        # the line, and no line category is written (that is the record builder's).
        if line.categ:
            row["flags"] = [line.categ]
        if line.quality_score is not None:
            row["quality_score"] = line.quality_score
        if line.lang:
            row["lang"] = line.lang
        # Semantic style only — bold/italic/heading_level, plus `region` for page furniture
        # and footnotes. Typeface and point size are deliberately dropped: a reader can act on
        # "this was a heading" and cannot act on "this was Helvetica 12pt".
        style: Dict[str, Any] = {}
        if line.bold:
            style["bold"] = True
        if line.italic:
            style["italic"] = True
        if line.heading_level:
            style["heading_level"] = line.heading_level
        if line.region:
            style["region"] = line.region
        if style:
            row["style"] = style
        rows.append(row)
    return rows


def _table_rows(doc: DigitalDocument) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for page in doc.pages:
        for table in page.tables:
            rows.append(
                {
                    "table_id": table.table_id,
                    "page": table.page,
                    "caption": table.caption,
                    "n_rows": table.n_rows,
                    "n_cols": table.n_cols,
                    "group_id": table.group_id,
                    "cells": table.cells,
                }
            )
    return rows


def _content_text(doc: DigitalDocument) -> str:
    """Reading-order text for search: no `Garbage`, no running headers or footers.

    Furniture repeats on every page and says nothing about the document's content; a
    footnote does, so it stays.
    """
    return "\n".join(
        line.text
        for line in doc.all_lines()
        if line.categ != CATEG_GARBAGE and line.region not in (REGION_HEADER, REGION_FOOTER)
    )


#: `record_type` of the development dict: record-shaped, never a record (atrium-project#84).
DEVELOPMENT_RECORD_TYPE = "digital-convert-development"


def development_dict(doc: DigitalDocument) -> Dict[str, Any]:
    """The document as a record-shaped development dict: what `json_to_md` renders as Markdown
    and `/describe` assesses. It is not the ATRIUM record and is never stored: since record
    schema 2.0 (atrium-project#84) digital-convert writes docproc, and the record is built from
    it by ocr-postprocess. No run data and no licence; the layout cues (`group_id`, `style`,
    `tables`) and the page verdicts docproc carries are kept for the views."""
    return {
        "record_type": DEVELOPMENT_RECORD_TYPE,
        "doc_id": doc.doc_id,
        "source": {
            k: v
            for k, v in {
                "sha512": doc.sha512,
                "filename": doc.filename,
                "media_type": doc.media_type,
                "origin": doc.origin,
                "page_count": len(doc.pages),
            }.items()
            if v not in ("", None)
        },
        "pages": _page_rows(doc),
        "lines": _line_rows(doc),
        "content": {"text": _content_text(doc) or None, "reading_order": doc.reading_order},
        "tables": _table_rows(doc),
    }


def seed_doc_id(seed: Optional[Dict[str, Any]]) -> Optional[str]:
    """The seed's `doc_id` (AMČR's file id), which wins over one derived from a file name."""
    if not isinstance(seed, dict):
        return None
    value = seed.get("doc_id")
    return str(value) if isinstance(value, str) and value.strip() else None


def prepare(
    input_path: str,
    baseline: Optional[str] = None,
    doc_id: Optional[str] = None,
    engine: str = "light",
    docx_page_breaks: str = "auto",
) -> Tuple[DigitalDocument, Dict[str, Any]]:
    """Layers A–C for one file: the normalised document and its development dict.

    A seed (``baseline``) is read, never written: its `source.sha512` is verified against the
    original first (`verify_seed_digest`), so a mismatched seed never reaches a reader, and its
    `doc_id` names the document."""
    verify_seed_digest(input_path, baseline)
    doc_id = seed_doc_id(open_record(baseline)) or doc_id
    document = normalize(
        extract(input_path, doc_id=doc_id, engine=engine, docx_page_breaks=docx_page_breaks)
    )
    if not document.sha512:
        document.sha512 = sha512_file(input_path)
    document.use("jsonschema")  # the docproc gate runs on every output
    return document, development_dict(document)


def build_development(
    input_path: str,
    baseline: Optional[str] = None,
    doc_id: Optional[str] = None,
    engine: str = "light",
    docx_page_breaks: str = "auto",
) -> Dict[str, Any]:
    """The development dict, written nowhere — for in-memory consumers.

    `api_util/doc_to_visual_md.py` renders PDF/DOCX to Markdown through this, so the LLM's
    diet and the docproc come from one reading (G8). A seed (`baseline`) is checked as for a
    conversion."""
    _, dev = prepare(
        input_path,
        baseline=baseline,
        doc_id=doc_id,
        engine=engine,
        docx_page_breaks=docx_page_breaks,
    )
    return dev


# ──────────────────────────────────────────────────────────────────────────────
# Layer D — docproc, validated, and the run's paradata
# ──────────────────────────────────────────────────────────────────────────────

#: AMČR distribution names (atrium-project#84). The original's name is open (#84, Q11).
ORIGINAL_DISTRIBUTION = "original"
RECORD_DISTRIBUTION = "atrium/document-json"


def _paradata_logger(paradata_dir: str, config: Dict[str, Any]) -> Optional[Any]:
    try:
        from atrium_paradata import ParadataLogger  # noqa: PLC0415
    except ImportError:
        return None
    return ParadataLogger(PROGRAM, config, paradata_dir=paradata_dir, config_dir=REPO_ROOT)


def run_config(
    input_name: str,
    engine: str,
    docx_page_breaks: str,
    markdown: bool = False,
    describe: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The run's configuration as its paradata records it, for the command line and the service
    alike (atrium-project#72); the service adds the `endpoint` it was called on."""
    config: Dict[str, Any] = {
        "input": os.path.basename(input_name),
        "engine": engine,
        "docx_page_breaks": docx_page_breaks,
    }
    if markdown:
        config["markdown"] = True
    if describe is not None:
        config["describe"] = dict(describe)
    return config


def record_run(
    logger: Any,
    document: DigitalDocument,
    docproc: Dict[str, Any],
    original: Dict[str, Any],
    docproc_data: bytes,
    seed: Optional[Dict[str, Any]] = None,
) -> None:
    """Paradata 3.0 of one conversion (atrium-project#84, H14): the original read (`original`,
    and the seed if one was sent, by SHA-512), the docproc written (`atr/docproc-json`, marked
    `stored: false` when a page needs the ATR: the ATR's docproc is then the stored one), the
    parameters that shaped it, and the components used, as facts."""
    from api_util import docproc_writer  # noqa: PLC0415

    logger.add_input(ORIGINAL_DISTRIBUTION, **original)
    if seed is not None:
        logger.add_input(RECORD_DISTRIBUTION, **seed)
    logger.add_output(
        docproc_writer.DISTRIBUTION,
        docproc_data,
        media_type="application/json",
        stored=not docproc_writer.needs_atr(docproc),
    )
    logger.set_parameter("engine", document.engine)
    for name in document.components:
        logger.log_component(name)


def docproc_bytes(docproc: Dict[str, Any]) -> bytes:
    """The bytes a docproc is written and named (SHA-512) by."""
    return (json.dumps(docproc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def convert(
    input_path: str,
    out_path: Optional[str] = None,
    baseline: Optional[str] = None,
    doc_id: Optional[str] = None,
    out_dir: str = ".",
    engine: str = "light",
    docx_page_breaks: str = "auto",
    paradata_dir: Optional[str] = None,
    markdown_out: Optional[str] = None,
    describe_out: Optional[str] = None,
    describe_stages: Optional[str] = None,
    classify_pages: str = "needs_ocr",
    include_text: bool = True,
) -> str:
    """A → D for one file. Returns the written docproc's path (`<doc_id>.docproc.json` in
    `out_dir` unless `out_path` names one).

    With `paradata_dir`, the run also writes its paradata record there (`record_run()`). A
    failed conversion writes no docproc, and its paradata say `failed` (D13).

    The two views `POST /reformat` and `POST /describe` add, run by the same code
    (`service/describe.py`, atrium-project#72): `markdown_out` writes the development dict as
    annotated Markdown (`markdown=true`), `describe_out` the per-page assessment.
    """
    from api_util import docproc_writer  # noqa: PLC0415

    selected: List[str] = []
    if describe_out is not None:
        from service import stages as describe_stages_module  # noqa: PLC0415

        selected = describe_stages_module.select_stages(describe_stages)
    logger = None
    if paradata_dir:
        config = run_config(
            input_path,
            engine,
            docx_page_breaks,
            markdown=markdown_out is not None,
            describe=(
                {"stages": selected, "classify_pages": classify_pages, "include_text": include_text}
                if describe_out is not None
                else None
            ),
        )
        logger = _paradata_logger(paradata_dir, config)
    try:
        # What the service checks before it converts, in its order: an empty file, then a seed
        # that cannot be opened (atrium-project#72).
        if os.path.isfile(input_path) and os.path.getsize(input_path) == 0:
            raise DigitalInputError(
                "corrupt", f"the file {os.path.basename(input_path)!r} is empty"
            )
        seed = open_record(baseline)
        if seed is None:
            baseline = None  # an empty file counts as none, as an empty part does
        document, dev = prepare(
            input_path,
            baseline=baseline,
            doc_id=doc_id,
            engine=engine,
            docx_page_breaks=docx_page_breaks,
        )
        docproc = docproc_writer.build_docproc(document)
        data = docproc_bytes(docproc)
        written = out_path or os.path.join(out_dir, f"{document.doc_id}.docproc.json")
        os.makedirs(os.path.dirname(written) or ".", exist_ok=True)
        tmp_path = f"{written}.tmp"
        with open(tmp_path, "wb") as fh:
            fh.write(data)
        os.replace(tmp_path, written)
        if markdown_out is not None or describe_out is not None:
            _write_views(
                document,
                dev,
                input_path,
                written,
                markdown_out,
                describe_out,
                selected,
                classify_pages,
                include_text,
                logger,
            )
    except Exception as exc:
        if logger is not None:
            logger.fail(f"{getattr(exc, 'reason', type(exc).__name__)}: {exc}")
            logger.finalize(input_total=1, processed_total=0)
        raise
    if logger is not None:
        record_run(
            logger,
            document,
            docproc,
            {"path": input_path, "media_type": document.media_type},
            data,
            {"path": baseline, "media_type": "application/json"} if baseline else None,
        )
        logger.log_success("docproc_json")
        logger.log_document_success()
        logger.finalize(input_total=1)
    return written


def _write_json(path: str, data: Dict[str, Any]) -> None:
    """Write-then-rename."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def view_path(output_path: str, doc_id: str, requested: str, suffix: str) -> str:
    """The path of a view: the one asked for, or `<doc_id><suffix>` beside the docproc."""
    if requested:
        return requested
    return os.path.join(os.path.dirname(output_path) or ".", f"{doc_id}{suffix}")


def _write_views(
    document: DigitalDocument,
    data: Dict[str, Any],
    input_path: str,
    output_path: str,
    markdown_out: Optional[str],
    describe_out: Optional[str],
    selected: List[str],
    classify_pages: str,
    include_text: bool,
    logger: Optional[Any],
) -> None:
    """`--describe` and `--markdown`: the service's two views, from the service's own code."""
    from service import describe as views  # noqa: PLC0415  (the stage client, only when asked)

    doc_id = str(data["doc_id"])
    if describe_out is not None:
        with open(input_path, "rb") as fh:
            raw = fh.read()
        assessed = views.describe_document(
            document,
            data,
            raw,
            os.path.basename(input_path),
            selected,
            classify_pages=classify_pages,
            include_text=include_text,
        )
        data = assessed["document"]  # with a stage's guarded write, when one was adopted
        report = {
            "doc_id": doc_id,
            "reader": views.reader_info(document, data),
            "pages": assessed["pages"],
            "summary": assessed["summary"],
            "stages": assessed["stages"],
        }
        target = view_path(output_path, doc_id, describe_out, ".describe.json")
        _write_json(target, report)
        print(f"[digital-convert] wrote {target}")
        if logger is not None:
            logger.log_success("describe_json")
    if markdown_out is not None:
        target = view_path(output_path, doc_id, markdown_out, ".md")
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(views.render_markdown(data))
        print(f"[digital-convert] wrote {target}")
        if logger is not None:
            logger.log_success("markdown")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Exposed separately so `--help` is testable in-process (repo convention I1)."""
    parser = argparse.ArgumentParser(
        prog="digital_to_json.py",
        description="Convert a born-digital PDF, DOCX, ODT, ODS, XLSX, RTF, DOC or XLS into docproc "
        "(atr/docproc-json): its pages, blocks and lines with the text-layer verdict of each page "
        "(atrium-project#84). The ATRIUM record is built from it by ocr-postprocess.",
        epilog="Exit codes: 0 written; 2 dependency missing (LibreOffice for DOC/XLS too); 3 not a "
        "born-digital input this converter takes, a seed whose source.sha512 does not match "
        "(source_digest_mismatch), or a --document-json seed that cannot be opened "
        "(invalid_record); 4 empty, corrupt, encrypted, over the ZIP limits, a failed "
        "conversion, or over a limit (MAX_PAGES, LIBREOFFICE_TIMEOUT_S, ODF repeats).",
    )
    parser.add_argument(
        "input", help="path to the born-digital file (.pdf .docx .odt .ods .xlsx .rtf .doc .xls)"
    )
    parser.add_argument(
        "--docproc-out",
        "--out",
        dest="out",
        default=None,
        help="output path for the docproc (default <out-dir>/<doc_id>.docproc.json). `--out` is an "
        "alias. Until atrium-project#84 this wrote the record (--document-json-out).",
    )
    parser.add_argument(
        "--document-json",
        default=None,
        help="the AMČR seed (or a record) of the document: its doc_id names the output, and its "
        "source.sha512 is checked against the file. Read only; missing is not an error",
    )
    parser.add_argument("--doc-id", default=None, help="override the derived doc_id")
    parser.add_argument("--out-dir", default=".", help="directory for the default output path")
    parser.add_argument(
        "--paradata-dir",
        default=None,
        help="also write this run's paradata record into DIR (paradata 3.0: the original read "
        "and the docproc written, by distribution name and SHA-512)",
    )
    parser.add_argument(
        "--engine",
        choices=ENGINES,
        default="light",
        help="light (default): pdfplumber / python-docx. docling: Docling layout + table "
        "models for PDF structure — pip install -r requirements_digital_docling.txt",
    )
    parser.add_argument(
        "--docx-page-breaks",
        choices=DOCX_PAGE_BREAK_MODES,
        default="auto",
        help="DOCX pages: auto = explicit breaks + Word's last rendered breaks (default); "
        "explicit = explicit breaks only; none = one page",
    )
    views = parser.add_argument_group(
        "views (what POST /reformat's markdown=true and POST /describe return)"
    )
    views.add_argument(
        "--markdown",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="also write the document as annotated Markdown (default <doc_id>.md beside the docproc)",
    )
    views.add_argument(
        "--describe",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="also write the per-page assessment of POST /describe (default <doc_id>.describe.json "
        "beside the docproc); the stages are called when PAGE_CLASSIFICATION_URL / "
        "OCR_POSTPROCESS_URL (or the two flags below) are set",
    )
    views.add_argument(
        "--stages",
        default=None,
        help="with --describe: a comma-separated subset of page-classification, ocr-postprocess; "
        "unset for every configured one; `none` for none",
    )
    views.add_argument(
        "--classify-pages",
        choices=("needs_ocr", "all"),
        default="needs_ocr",
        help="with --describe: the pages page-classification is asked about",
    )
    views.add_argument(
        "--no-text",
        dest="include_text",
        action="store_false",
        help="with --describe: leave each page's text out of pages[].text",
    )
    views.add_argument(
        "--page-classification-url",
        default=None,
        help="with --describe: page-classification's base URL (sets PAGE_CLASSIFICATION_URL)",
    )
    views.add_argument(
        "--ocr-postprocess-url",
        default=None,
        help="with --describe: ocr-postprocess's base URL (sets OCR_POSTPROCESS_URL)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.page_classification_url:
        os.environ["PAGE_CLASSIFICATION_URL"] = args.page_classification_url
    if args.ocr_postprocess_url:
        os.environ["OCR_POSTPROCESS_URL"] = args.ocr_postprocess_url
    if args.describe is not None:
        from service import stages as describe_stages_module  # noqa: PLC0415

        try:
            describe_stages_module.select_stages(args.stages)
        except ValueError as exc:
            parser.error(str(exc))
    try:
        written = convert(
            args.input,
            out_path=args.out,
            baseline=args.document_json,
            doc_id=args.doc_id,
            out_dir=args.out_dir,
            engine=args.engine,
            docx_page_breaks=args.docx_page_breaks,
            paradata_dir=args.paradata_dir,
            markdown_out=args.markdown,
            describe_out=args.describe,
            describe_stages=args.stages,
            classify_pages=args.classify_pages,
            include_text=args.include_text,
        )
    except DigitalInputError as exc:
        print(f"[digital-convert] {exc.reason}: {exc}", file=sys.stderr)
        return exc.exit_code
    except LimitExceeded as exc:
        print(f"[digital-convert] limit_exceeded: {exc.detail}", file=sys.stderr)
        return 4
    except ContentLimitError as exc:
        # D13 (atrium-project#84): a limit that would cut content fails the run; nothing written.
        print(f"[digital-convert] limit_exceeded: {exc}", file=sys.stderr)
        return 4
    except RuntimeError as exc:  # a missing optional dependency, reported as advice
        print(f"[digital-convert] {exc}", file=sys.stderr)
        return 2
    print(f"[digital-convert] wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
