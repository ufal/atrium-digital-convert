"""The llm-enrich #10 §9 measurement, as a test: every case the JSON route failed, end to end.

§9 of the #10 plan (atrium-llm-enrich, this repository's name before v1.0.0-beta; that plan was not
carried over to the new dev logs) ran the same inputs through markitdown, flexiconv, the
legacy direct converters and the JSON route (`digital_to_json` → `json_to_md`) and found the
JSON route behind on eight points (G1–G8). This module is that table's JSON-route column,
re-run on every commit against the generated fixtures: the Markdown the clients' auto-
convert now produces (`doc_to_visual_md.convert_to_visual_md`) must contain what the row
requires and nothing the row forbids.

Two-column ordering on a REAL complex page stays out of scope here (a Docling/MinerU
bake-off on the hub #22 harness, #10 §10 Phase 6); `two_column.pdf` pins the simple case.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from api_util import doc_to_visual_md

GENERATOR = Path(__file__).resolve().parent / "fixtures" / "digital" / "make_fixtures.py"


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("make_fixtures", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path_factory.mktemp("digital")
    for name, payload in module.build_all().items():
        (out / name).write_bytes(payload)
    return out


#: (§9 row, fixture, must appear in order, must not appear)
CASES = [
    ("page boundaries (PDF)", "minimal.pdf", ["## Page 1", "PAGE_BREAK: pg_2", "## Page 2"], []),
    (
        "page boundaries + heading + table (DOCX)",
        "minimal.docx",
        [
            "### Zpráva o sondě",
            "| Vrstva | Mocnost |",
            "| Ornice | 30 cm |",
            "## Page 2",
            "Třetí odstavec, už na druhé straně.",
        ],
        [],
    ),
    (
        "G1 text-less pages flagged, and told apart",
        "image_only.pdf",
        [
            "## Page 1",
            "## Page 2",
            "NEEDS_OCR: pg_2 (no extractable text layer: the page draws 1 image(s)",
            "## Page 3",
            "NEEDS_OCR: pg_3 (no extractable text layer: the page draws nothing",
        ],
        [],
    ),
    (
        "G6 garbled text layer: page flagged, no bad line leaks",
        "garbled.pdf",
        ["NEEDS_OCR: pg_1 (embedded text layer does not decode: 3 of 3 lines"],
        ["sondì", "høeby", "mìla"],
    ),
    (
        "G2 words separated, columns in order, furniture, roman labels",
        "two_column.pdf",
        [
            "## Page i",
            "HEADER_START",
            "### Hradiste u Horni Mezi: zachranny vyzkum",
            "Sonda II odkryla val.",
            "Konec leveho sloupce.",
            "Pravy sloupec zacina zde.",
            "FOOTER_START",
            "## Page ii",
        ],
        ["SondaIIodkryla"],
    ),
    (
        "PDF ruled table (legacy pdf_to_md parity)",
        "table.pdf",
        [
            "Tabulka vrstev je nize.",
            "| Vrstva | Mocnost | Nalezy |",
            "| Ornice | 30 cm | keramika |",
            "Text pod tabulkou.",
        ],
        [],
    ),
    (
        "#5 words set in two fonts stay words; a superscript stays apart",
        "font_switch.pdf",
        ["**Nalezova zprava**", "Jičín, Přílohy", "Nalezy viz slova 1 a dalsi."],
        ["Ji č", "P ř", "slova1", "**Jičín"],
    ),
    (
        "#5 unmapped glyphs: a page of them flagged, a page losing letters flagged too, never (cid:N)",
        "unmapped.pdf",
        [
            "NEEDS_OCR: pg_1 (garbled text layer: 1 of 1 lines are over 15% glyphs without a "
            "Unicode mapping",
            "## Page 2",
            "NEEDS_OCR: pg_2 (the text layer decodes, but 2 of its 10 words (20%) lose a letter",
            "Zprava o sond\ufffd cislo 3, vrstva m\ufffdla mocnost 30 cm.",
        ],
        ["(cid:"],
    ),
    (
        "#5 a page number over a full-page image is a scan; text and half-covered pages are not",
        "stamped_scan.pdf",
        [
            "NEEDS_OCR: pg_1 (no text layer under the page furniture: images cover 100% of the page",
            "## Page 2",
            "Zprava o vyzkumu, textova strana.",
            "## Page 3",
        ],
        ["NEEDS_OCR: pg_2", "NEEDS_OCR: pg_3"],
    ),
    (
        "#5 headings by size against the body text, not a table's or an OCR layer's",
        "headings.pdf",
        [
            "### Vyzkum hradiste",
            "Prvni odstavec popisuje sondy.",
            "| Sonda | Vrstva | Nalezy |",
            "## Page 2",
            "## Page 3",
            "Treti strana, odstavec o nalezech.",
        ],
        ["#### ", "### Prvni", "### Druhy", "### ,", "### Naskenovana", "### Treti"],
    ),
    (
        "#5 small capitals are whole words; real spaces and a raised capital stay apart",
        "small_caps.pdf",
        ["OBRÁZEK 12", "TABULKA 3", "SOUŘADNICOVÝ", "P VI a P IX", "V PRUBEHU", "KAPITOLA A"],
        ["O BRÁZEK", "T ABULKA", "S OU", "PVI", "VPRUBEHU", "KAPITOLAA"],
    ),
    (
        "#5 text on a 90° baseline is one line read in its direction",
        "rotated.pdf",
        ["| K_číslo | Datace |", "| 12 | Halstat |", "Obr. 18 Turnov"],
        ["K _", "e c a t a D", "nvonruT", "\n81\n"],
    ),
    (
        "#5 bold section titles at the body size are headings; other bold lines are not",
        "bold_titles.pdf",
        ["### Zprava o vyzkumu", "#### 1 Uvod", "#### 2 Metodika", "#### 3 Zaver"],
        ["#### Archeologicky", "#### Lokalita", "#### Tento", "#### - Keramika", "#### Poznamka"],
    ),
    (
        "G4 package without the main-part Override",
        "bare.docx",
        ["### Zpráva o sondě", "## Page 2"],
        [],
    ),
    (
        "G5 tracked changes, furniture, footnote, pages from three kinds of break",
        "rich.docx",
        [
            "HEADER_START",
            "### Hradiště u Horní Mezi",
            "#### Průběh výzkumu",
            "**Sonda II**",
            "Nalezena bronzová spona, viz katalog.",
            "[^1]: Katalog nálezů je uložen v archivu.",
            "## Page 2",
            "| Nálezy |  |",
            "FOOTER_START",
            "## Page 3",
            "## Page 4",
        ],
        ["železný nůž", "PAGE "],
    ),
]


@pytest.mark.parametrize("row, name, required, forbidden", CASES, ids=[c[0] for c in CASES])
def test_json_route_closes_the_section_9_row(fixtures, row, name, required, forbidden):
    pytest.importorskip("pdfplumber")
    pytest.importorskip("docx")
    pytest.importorskip("jsonschema")
    md = doc_to_visual_md.convert_to_visual_md(fixtures / name)
    positions = []
    for needle in required:
        assert needle in md, f"{row}: {needle!r} missing from\n{md}"
        positions.append(md.index(needle))
    assert positions == sorted(positions), f"{row}: out of order in\n{md}"
    for needle in forbidden:
        assert needle not in md, f"{row}: {needle!r} must not appear in\n{md}"


def test_ocr_layer_pdf_is_refused_on_the_json_route(fixtures):
    """G7: the auto-convert must not render an OCR layer as born-digital text either."""
    pytest.importorskip("pdfplumber")
    from api_util.digital_ir import DigitalInputError

    with pytest.raises(DigitalInputError, match="not born-digital"):
        doc_to_visual_md.convert_to_visual_md(fixtures / "ocr_layer.pdf")
