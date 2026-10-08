"""
tests/test_md_stress.py
=======================
The stress suite of atrium-digital-convert#3 (plan 3, section 6): the two renderers that
produce the LLM's one input representation, annotated Markdown, held to the properties that
make it a *representation of the source* and not just text that looks right.

  * the JSON route (``json_to_md``): a record that uses everything the renderer reads;
  * the TEITOK route (``xml_to_md``, layout format): the real format-2 fixture of nlp-enrich's
    writer, with a ``<pb/>`` inside a sentence and a box on every line.

Asserted for both, at every detail profile:

  * no unhandled exception;
  * every source page survives exactly once, in a stable order (non-numeric labels included);
  * every source text line survives exactly once, unless the quality policy drops it, and the
    text stream is the same in all three profiles (only cues are dropped);
  * the cues the profile promises survive, and the two routes speak one cue vocabulary;
  * grouped structure (paragraph groups, table cells, running header and footer, footnotes);
  * ``enrichment`` is never consulted;
  * repeated rendering is deterministic, in-process and across hash seeds.

What is *not* asserted: that keywords, entities or enrichment appear in the Markdown. They are
checked on the record (keyword-extract's tests), not here.
"""

import copy
import json
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from api_util import json_to_md, layout_md, xml_to_md

REPO_ROOT = Path(__file__).resolve().parent.parent
TEITOK_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "teitok" / "writer" / "CTX000000002.teitok.xml"

PROFILES = list(layout_md.DETAIL_LEVELS)

#: A string only ``enrichment`` carries. If it reaches the Markdown the renderer read a model's
#: own earlier answer back to it.
SENTINEL = "SENTINEL-ENRICHMENT-ONLY-7f3a"

_COMMENT = re.compile(r"<!--\s*([A-Z_]+)\b(.*?)-->", re.S)


def _canvas():
    return {"width": 595.28, "height": 841.89, "unit": "pt"}


def make_record():
    """A record with every field the JSON route reads, over four pages with real-world labels."""
    return {
        "schema_version": "1.0",
        "record_type": "atrium-document",
        "doc_id": "STRESS01",
        "pages": [
            {"page": "i", "page_index": 1, "canvas": _canvas()},
            {
                "page": "ii",
                "page_index": 2,
                "canvas": _canvas(),
                "needs_ocr": True,
                "needs_ocr_reason": "text layer does not decode",
            },
            {
                "page": "A-1",
                "page_index": 3,
                "canvas": _canvas(),
                "ocr": {"engine": "tesseract", "lang": "ces"},
            },
            {"page": "4", "page_index": 4, "canvas": _canvas()},
        ],
        "lines": [
            {
                "page": "i",
                "line": 1,
                "text": "Archeologická zpráva",
                "bbox": [72, 60, 300, 90],
                "group_id": "p1",
                "style": {"heading_level": 1},
            },
            {
                "page": "i",
                "line": 2,
                "text": "Úvod ke Ždánicím — žluťoučký kůň",
                "bbox": [72, 100, 400, 120],
                "group_id": "p2",
            },
            {
                "page": "i",
                "line": 3,
                "text": "druhý řádek odstavce",
                "bbox": [72, 122, 400, 142],
                "group_id": "p2",
            },
            {"page": "i", "line": 4, "text": "Strana 1", "style": {"region": "page_footer"}},
            {"page": "i", "line": 5, "text": "Zpráva č. 7", "style": {"region": "page_header"}},
            {
                "page": "A-1",
                "line": 1,
                "text": "Přepis z OCR",
                "bbox": [50, 50, 200, 70],
                "categ": "Clear",
                "quality_score": 0.91,
            },
            {
                "page": "A-1",
                "line": 2,
                "text": "~~ |||| 3",
                "categ": "Trash",
                "quality_score": 0.05,
            },
            {"page": "A-1", "line": 3, "text": "mojibake ÃƒÂ", "categ": "Garbage"},
            {"page": "A-1", "line": 4, "text": "převrácený text", "categ": "Inverted"},
            {
                "page": "A-1",
                "line": 5,
                "text": "Slabý řádek",
                "categ": "Noisy",
                "quality_score": 0.30,
            },
            {
                "page": "4",
                "line": 1,
                "text": "Tučný řádek",
                "style": {"bold": True},
                "group_id": "p9",
            },
            {
                "page": "4",
                "line": 2,
                "text": "Kurzíva",
                "style": {"italic": True},
                "group_id": "p10",
            },
            {
                "page": "4",
                "line": 3,
                "text": "Poznámka pod čarou",
                "style": {"region": "footnote"},
                "group_id": "fn1",
            },
            {"page": "4", "line": 4, "text": "Buňka A1", "group_id": "c1"},
            {"page": "4", "line": 5, "text": "Buňka B1", "group_id": "c2"},
            {"page": "4", "line": 6, "text": "Buňka A2", "group_id": "c3"},
            {"page": "4", "line": 7, "text": "Buňka B2", "group_id": "c4"},
            {
                "page": "4",
                "line": 8,
                "text": "hvězdička * v tučném",
                "style": {"bold": True},
                "group_id": "p12",
            },
            {
                "page": "4",
                "line": 9,
                "text": "emoji 😀 — 𝔘𝔫𝔦𝔠𝔬𝔡𝔢 — العربية — 日本語 — ǅ ß ﬁ",
                "group_id": "p11",
            },
        ],
        "tables": [
            {
                "table_id": "t1",
                "page": "4",
                "cells": [
                    {"row": 0, "col": 0, "group_id": "c1"},
                    {"row": 0, "col": 1, "group_id": "c2"},
                    {"row": 1, "col": 0, "group_id": "c3"},
                    {"row": 1, "col": 1, "group_id": "c4"},
                ],
            }
        ],
        "enrichment": {"items": [{"page": "i", "line": 2, "teater_category": SENTINEL}]},
    }


PAGE_LABELS = ["i", "ii", "A-1", "4"]

#: The lines that must survive, exactly once, whatever the profile: not dropped by category.
SURVIVORS = [
    "Archeologická zpráva",
    "Úvod ke Ždánicím — žluťoučký kůň",
    "druhý řádek odstavce",
    "Strana 1",
    "Zpráva č. 7",
    "Přepis z OCR",
    "Slabý řádek",
    "Tučný řádek",
    "Kurzíva",
    "Poznámka pod čarou",
    "Buňka A1",
    "Buňka B1",
    "Buňka A2",
    "Buňka B2",
    "hvězdička * v tučném",
    "emoji 😀 — 𝔘𝔫𝔦𝔠𝔬𝔡𝔢 — العربية — 日本語 — ǅ ß ﬁ",
]

#: Dropped by the quality policy (the hub's untrustworthy categories), on purpose.
DROPPED = ["~~ |||| 3", "mojibake ÃƒÂ", "převrácený text"]


def render(record=None, detail="full", **kwargs):
    return json_to_md.render_record(
        record or make_record(), title="STRESS01", detail=detail, **kwargs
    )


def cues(md):
    """``[(NAME, payload)]`` of every layout cue, in order."""
    return [(name, payload.strip(" :")) for name, payload in _COMMENT.findall(md)]


def cue_names(md):
    return {name for name, _ in cues(md)}


def headings(md):
    return re.findall(r"^## Page (.+)$", md, flags=re.M)


def text_stream(md):
    """The text a reader sees: no cues, no page or title headings, no emphasis marks, no table
    rules, whitespace collapsed. What must be the same in every profile."""
    body = _COMMENT.sub("", md)
    body = re.sub(r"^#{1,2} .*$", "", body, flags=re.M)  # title and page sections, not ### headings
    body = re.sub(r"^#{3,6} ", "", body, flags=re.M)
    body = re.sub(r"^\|[ :|-]*\|$", "", body, flags=re.M)  # the GFM header-rule rows
    body = body.replace("***", "").replace("**", "")
    body = re.sub(r"(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])", "", body)  # whole-line italics
    return re.sub(r"\s+", " ", body).strip()


# ── JSON route ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("detail", PROFILES)
def test_every_profile_renders_without_an_error(detail):
    assert render(detail=detail).strip()
    assert render(detail=detail, min_quality=0.5).strip()


@pytest.mark.parametrize("detail", PROFILES)
def test_every_page_survives_exactly_once_in_order(detail):
    md = render(detail=detail)
    assert headings(md) == PAGE_LABELS
    breaks = [payload for name, payload in cues(md) if name == "PAGE_BREAK"]
    # a break opens every page but the first, and names the page it opens
    assert breaks == [f"pg_{label}" for label in PAGE_LABELS[1:]]


@pytest.mark.parametrize("detail", PROFILES)
def test_every_surviving_line_appears_once_and_the_dropped_ones_never(detail):
    md = render(detail=detail)
    for text in SURVIVORS:
        assert md.count(text) == 1, f"{text!r} appears {md.count(text)} times"
    for text in DROPPED:
        assert text not in md


def test_the_quality_floor_drops_the_weak_line_and_nothing_else():
    md = render(min_quality=0.5)
    assert "Slabý řádek" not in md
    for text in SURVIVORS:
        if text != "Slabý řádek":
            assert md.count(text) == 1


def test_the_text_is_the_same_in_every_profile_and_only_cues_differ():
    streams = {detail: text_stream(render(detail=detail)) for detail in PROFILES}
    assert streams["full"] == streams["standard"] == streams["minimal"]


def test_the_cue_sets_nest():
    names = {detail: cue_names(render(detail=detail)) for detail in PROFILES}
    assert names["minimal"] <= names["standard"] <= names["full"]
    assert names["full"] - names["standard"] == {"LAYOUT_MARGIN"}
    assert names["standard"] - names["minimal"] == {"BBOX", "DOC_META", "OCR"}


def test_the_full_profile_keeps_every_cue_the_record_supports():
    md = render(detail="full")
    found = cues(md)
    assert sum(1 for name, _ in found if name == "DOC_META") == 4
    assert all("size=595.28x841.89pt" in payload for name, payload in found if name == "DOC_META")
    boxes = [payload for name, payload in found if name == "BBOX"]
    assert boxes == [
        "[72, 60, 300, 90]",
        "[72, 100, 400, 120]",
        "[72, 122, 400, 142]",
        "[50, 50, 200, 70]",
    ]
    assert ("NEEDS_OCR", "pg_ii (text layer does not decode)") in found
    assert ("OCR", "engine=tesseract, lang=ces") in found
    margins = [payload for name, payload in found if name == "LAYOUT_MARGIN"]
    assert len(margins) == 2 and all(
        re.fullmatch(r"top=\d+pt, bottom=\d+pt, left=\d+pt, right=\d+pt", m) for m in margins
    )


def test_the_standard_profile_keeps_one_box_per_block():
    boxes = [payload for name, payload in cues(render(detail="standard")) if name == "BBOX"]
    # the heading, and the two lines of paragraph p2 as one box; a line with no group gets none
    assert boxes == ["[72, 60, 300, 90]", "[72, 100, 400, 142]"]


def test_the_minimal_profile_keeps_the_structure_and_no_geometry():
    md = render(detail="minimal")
    assert cue_names(md) == {
        "PAGE_BREAK",
        "NEEDS_OCR",
        "HEADER_START",
        "HEADER_END",
        "FOOTER_START",
        "FOOTER_END",
    }
    assert (
        "### Archeologická zpráva" in md
        and "[^1]: Poznámka pod čarou" in md
        and "| Buňka A1 | Buňka B1 |" in md
    )
    assert "**" not in md and "\n*Kurzíva*" not in md


@pytest.mark.parametrize("detail", ["full", "standard"])
def test_emphasis_survives_and_a_line_with_its_own_stars_is_left_alone(detail):
    md = render(detail=detail)
    assert "**Tučný řádek**" in md and "\n*Kurzíva*\n" in md
    assert "hvězdička * v tučném" in md and "**hvězdička" not in md


@pytest.mark.parametrize("detail", PROFILES)
def test_grouped_structure_survives(detail):
    md = render(detail=detail)
    page_i = md.split("## Page ii")[0]
    # running header first, footer last, the body between them
    assert page_i.index("HEADER_START") < page_i.index("Zpráva č. 7") < page_i.index("HEADER_END")
    assert (
        page_i.index("HEADER_END")
        < page_i.index("### Archeologická zpráva")
        < page_i.index("FOOTER_START")
    )
    assert page_i.index("FOOTER_START") < page_i.index("Strana 1") < page_i.index("FOOTER_END")
    # the table is one GFM table with each cell once; the footnote is a definition
    assert md.count("| --- | --- |") == 1
    assert "| Buňka A1 | Buňka B1 |\n| Buňka A2 | Buňka B2 |" in md
    assert "[^1]: Poznámka pod čarou" in md
    # a heading sits below the page sections, never competes with them
    assert "### Archeologická zpráva" in md and "\n## Archeologická" not in md


def test_lines_of_one_paragraph_group_are_adjacent_and_other_groups_are_apart():
    md = render(detail="minimal")
    assert "Úvod ke Ždánicím — žluťoučký kůň\ndruhý řádek odstavce" in md
    assert (
        "### Archeologická zpráva\n\nÚvod ke Ždánicím" in md
    )  # a new group opens with a blank line


def test_a_page_flagged_for_ocr_is_still_a_section_with_its_cue():
    md = render(detail="minimal")
    section = md.split("## Page ii")[1].split("<!-- PAGE_BREAK")[0]
    assert section.strip() == "<!-- NEEDS_OCR: pg_ii (text layer does not decode) -->"


def test_the_order_does_not_depend_on_how_the_input_is_ordered():
    reference = render()
    record = make_record()
    record["lines"].reverse()
    assert render(record) == reference
    record = make_record()
    random.Random(7).shuffle(record["lines"])
    assert render(record) == reference
    record = make_record()
    record["pages"].reverse()  # page_index is the ordering key when labels are not numbers
    assert render(record) == reference


def test_unicode_survives_byte_for_byte():
    md = render()
    assert "😀 — 𝔘𝔫𝔦𝔠𝔬𝔡𝔢 — العربية — 日本語 — ǅ ß ﬁ" in md
    assert md.encode("utf-8").decode("utf-8") == md


# ── enrichment is never read ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("detail", PROFILES)
def test_enrichment_does_not_reach_the_markdown(detail):
    with_block = render(detail=detail)
    record = make_record()
    del record["enrichment"]
    assert SENTINEL not in with_block
    assert render(record, detail=detail) == with_block


def test_a_record_with_only_enrichment_is_refused_not_rendered():
    record = {
        "schema_version": "1.0",
        "doc_id": "ONLY-ENRICHMENT",
        "pages": [{"page": "1"}],
        "enrichment": {
            "items": [{"page": "1", "line": 1, "teater_category": SENTINEL, "citation": SENTINEL}]
        },
    }
    with pytest.raises(ValueError, match="nothing to render"):
        json_to_md.render_record(record, title="ONLY-ENRICHMENT")


def test_the_file_route_and_the_in_memory_route_agree(tmp_path):
    """The CLI (`convert`, from a file) and the service (`render_record`, from memory) are one conversion."""
    path = tmp_path / "STRESS01.document.json"
    path.write_text(json.dumps(make_record(), ensure_ascii=False), encoding="utf-8")
    assert json_to_md.convert(path) == render()


# ── determinism ────────────────────────────────────────────────────────────────────────────


def test_repeated_rendering_is_identical_and_does_not_mutate_the_record():
    record = make_record()
    before = copy.deepcopy(record)
    first = render(record)
    assert render(record) == first
    assert record == before


def test_rendering_is_identical_across_hash_seeds(tmp_path):
    """Iteration order of a set or dict must not leak into the output: a fresh interpreter per seed."""
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from api_util import json_to_md, xml_to_md\n"
        "record = json.load(sys.stdin)\n"
        "print(json_to_md.render_record(record, title='STRESS01', detail='full'))\n"
        "print('=====')\n"
        f"print(xml_to_md.convert({str(TEITOK_FIXTURE)!r}, fmt='layout'))\n"
    )
    outputs = set()
    for seed in ("0", "1", "4242"):
        done = subprocess.run(
            [sys.executable, "-c", code],
            input=json.dumps(make_record(), ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=120,
            cwd=REPO_ROOT,
            env={"PYTHONHASHSEED": seed, "PATH": ""},
        )
        assert done.returncode == 0, done.stderr[-1500:]
        outputs.add(done.stdout)
    assert len(outputs) == 1


# ── TEITOK route: the real format-2 fixture ────────────────────────────────────────────────


def _source_tokens(path):
    """The form of every ``<tok>`` in document order (``<dtok>`` parts are the token's own)."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(Path(path).read_text(encoding="utf-8"))
    forms = []

    def walk(element):
        for child in element:
            name = child.tag.split("}")[-1]
            if name == "dtok":
                continue
            if name == "tok":
                forms.append("".join(child.itertext()))
            else:
                walk(child)

    walk(root)
    return forms


def _source_pages(path):
    return re.findall(r'<pb n="([^"]+)"', Path(path).read_text(encoding="utf-8"))


def teitok(detail="full"):
    return xml_to_md.convert(TEITOK_FIXTURE, fmt="layout", detail=detail)


@pytest.mark.parametrize("detail", PROFILES)
def test_teitok_every_token_survives_once_in_order(detail):
    """52 tokens across four pages, one sentence running over a page break: the characters of
    the body are exactly the characters of the tokens, in the source order."""
    body = _COMMENT.sub("", teitok(detail))
    body = re.sub(r"^#{1,2} .*$", "", body, flags=re.M)
    assert re.sub(r"\s+", "", body) == "".join(_source_tokens(TEITOK_FIXTURE))


@pytest.mark.parametrize("detail", PROFILES)
def test_teitok_every_page_survives_once_in_order(detail):
    md = teitok(detail)
    assert len(_source_pages(TEITOK_FIXTURE)) == 4
    assert headings(md) == ["1", "2", "3", "4"]
    assert [p for n, p in cues(md) if n == "PAGE_BREAK"] == ["pg_2", "pg_3", "pg_4"]


def test_teitok_text_is_the_same_in_every_profile():
    assert (
        text_stream(teitok("full"))
        == text_stream(teitok("standard"))
        == text_stream(teitok("minimal"))
    )


def test_teitok_boxes_are_well_formed_and_inside_the_page():
    boxes = [payload for name, payload in cues(teitok("full")) if name == "BBOX"]
    assert boxes
    for box in boxes:
        x0, y0, x1, y1 = (int(n) for n in re.findall(r"-?\d+", box))
        assert 0 <= x0 < x1 <= 1654 and 0 <= y0 < y1 <= 2339, box
    assert (
        "size=1654x2339px"
        in {payload for name, payload in cues(teitok("full")) if name == "DOC_META"}.pop()
    )


def test_teitok_cue_sets_nest_like_the_record_routes():
    names = {detail: cue_names(teitok(detail)) for detail in PROFILES}
    assert names["minimal"] <= names["standard"] <= names["full"]
    assert "BBOX" in names["full"] and "BBOX" not in names["minimal"]
    # `standard` boxes a block, a run of rows sharing a group_id, and TEITOK rows carry none
    # (layout_md.BBOX_SCOPE): on this route it is `minimal` plus DOC_META. #22's profile
    # comparison on scanned documents compares `full` with that.
    assert names["standard"] - names["minimal"] == {"DOC_META"}


def test_teitok_rendering_is_deterministic():
    assert teitok() == teitok()


# ── the two routes speak one dialect ───────────────────────────────────────────────────────


def test_both_routes_use_only_catalogued_cues_in_the_same_forms():
    json_md, teitok_md = render(), teitok()
    catalogue = set(layout_md.CUE_SCHEMA)
    assert cue_names(json_md) <= catalogue and cue_names(teitok_md) <= catalogue
    for md in (json_md, teitok_md):
        for name, payload in cues(md):
            if name == "PAGE_BREAK":
                assert re.fullmatch(r"pg_\S+", payload)
            if name == "BBOX":
                assert re.fullmatch(r"\[-?\d+, -?\d+, -?\d+, -?\d+\]", payload)
            if name == "DOC_META":
                assert re.fullmatch(r"size=[\d.]+x[\d.]+(px|pt)", payload)
    # what both emit, they emit the same way: pages as `## Page <label>`, then DOC_META, then the lines
    for md in (json_md, teitok_md):
        assert re.search(r"^## Page \S+\n\n<!-- DOC_META: size=", md, flags=re.M)
