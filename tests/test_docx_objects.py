"""tests/test_docx_objects.py — a DOCX's text outside WordprocessingML runs, read from its XML
(v2.0.0-beta): Office Math equations, SmartArt diagrams and charts.

A DOCX is XML, so its text can be read in full without rendering it: an equation joins its
paragraph as linear text, a SmartArt diagram's node text and a chart's titles, series names and
text categories follow their anchor paragraph. The rendering still gives the pages; with
LibreOffice present, `objects.docx` checks that the rendered page is covered by this text and so
does not go to the ATR, as it did when the reader skipped it.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("docx")
pytest.importorskip("lxml")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "digital"))
import make_fixtures as mf  # noqa: E402

from api_util import digital_docx as dd  # noqa: E402
from api_util import digital_to_json as d2j  # noqa: E402
from tests.office_support import needs_libreoffice  # noqa: E402

M = f'xmlns:m="{mf.M_NS}" xmlns:w="{mf.W_NS}"'


def _omml(inner: str) -> str:
    return dd._omml_text(dd._parse_xml(f"<m:oMath {M}>{inner}</m:oMath>".encode()))


def _m(text: str) -> str:
    return f"<m:r><m:t>{text}</m:t></m:r>"


def _read(tmp_path: Path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return dd.extract_docx(str(path), path.stem, "digital-born-docx")


def _lines(document):
    return [(line.group_id, line.text) for page in document.pages for line in page.lines]


# ── the fixture, read ─────────────────────────────────────────────────────────


def test_equations_join_their_paragraph_as_linear_text(tmp_path):
    lines = [text for _, text in _lines(_read(tmp_path, "o.docx", mf.objects_docx()))]
    assert "Plocha sondy je S=πr^2 podle poloměru." in lines, "inline: in its paragraph's line"
    assert "x=(-b±√(b^2-4ac))/(2a)" in lines, "display: its paragraph's only line"


def test_smartart_nodes_follow_their_anchor_one_group_each(tmp_path):
    lines = _lines(_read(tmp_path, "o.docx", mf.objects_docx()))
    texts = [text for _, text in lines]
    start = texts.index("Postup výzkumu:") + 1
    nodes = lines[start : start + 3]
    assert [text for _, text in nodes] == list(mf._SMARTART_NODES)
    assert len({group for group, _ in nodes}) == 3, "a group per node"


def test_chart_text_follows_its_anchor_as_one_group_without_numbers(tmp_path):
    lines = _lines(_read(tmp_path, "o.docx", mf.objects_docx()))
    texts = [text for _, text in lines]
    start = texts.index("Přehled nálezů:") + 1
    end = texts.index("Konec přehledu.")
    chart = lines[start:end]
    assert [text for _, text in chart] == [
        "Nálezy podle sond",
        "Druh nálezu",
        "Počet kusů",
        "Sonda I",
        "Sonda II",
        "Keramika",
        "Kosti",
        "Mazanice",
    ]
    assert len({group for group, _ in chart}) == 1
    assert not any(text.isdigit() for text in texts), "values are not read"


# ── equations ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "inner, expected",
    [
        (f"<m:sSub><m:e>{_m('x')}</m:e><m:sub>{_m('i')}</m:sub></m:sSub>", "x_i"),
        (
            f"<m:sSubSup><m:e>{_m('x')}</m:e><m:sub>{_m('i')}</m:sub><m:sup>{_m('2')}</m:sup></m:sSubSup>",
            "x_i^2",
        ),
        (f"<m:f><m:num>{_m('a')}</m:num><m:den>{_m('b+c')}</m:den></m:f>", "a/(b+c)"),
        (
            f"<m:rad><m:deg>{_m('3')}</m:deg><m:e>{_m('x')}</m:e></m:rad>",
            "√(3&x)",
        ),
        (
            '<m:d><m:dPr><m:begChr m:val="["/><m:endChr m:val="]"/><m:sepChr m:val=","/></m:dPr>'
            f"<m:e>{_m('a')}</m:e><m:e>{_m('b')}</m:e></m:d>",
            "[a,b]",
        ),
        (f"<m:d><m:e>{_m('a+b')}</m:e></m:d>", "(a+b)"),
        (
            '<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr>'
            f"<m:sub>{_m('i=1')}</m:sub><m:sup>{_m('n')}</m:sup><m:e>{_m('x')}</m:e></m:nary>",
            "∑_(i=1)^n x",
        ),
        (f"<m:func><m:fName>{_m('sin')}</m:fName><m:e>{_m('α')}</m:e></m:func>", "sin α"),
        (
            f"<m:limLow><m:e>{_m('lim')}</m:e><m:lim>{_m('n→∞')}</m:lim></m:limLow>",
            "lim_(n→∞)",
        ),
        (
            f"<m:m><m:mr><m:e>{_m('1')}</m:e><m:e>{_m('0')}</m:e></m:mr>"
            f"<m:mr><m:e>{_m('0')}</m:e><m:e>{_m('1')}</m:e></m:mr></m:m>",
            "[1 0; 0 1]",
        ),
        (f"<m:eqArr><m:e>{_m('x=1')}</m:e><m:e>{_m('y=2')}</m:e></m:eqArr>", "x=1; y=2"),
    ],
)
def test_omml_is_written_linearly(inner, expected):
    assert _omml(inner) == expected


def test_a_tracked_deletion_inside_an_equation_is_left_out():
    inner = _m("a+") + "<w:del><m:r><m:t>b</m:t></m:r></w:del>" + "<w:ins>" + _m("c") + "</w:ins>"
    assert _omml(inner) == "a+c"


def test_properties_carry_no_text():
    inner = "<m:ctrlPr><w:rPr><w:i/></w:rPr></m:ctrlPr>" + _m("x")
    assert _omml(inner) == "x"


# ── SmartArt and charts, part by part ─────────────────────────────────────────


def test_smartart_points_without_text_are_left_out():
    nodes = dd.smartart_lines(mf.OBJECTS_SMARTART_DATA.encode())
    assert nodes == [[text] for text in mf._SMARTART_NODES]


def test_a_chart_without_text_categories_gives_its_titles_and_series_only():
    xml = mf.OBJECTS_CHART.replace(
        "<c:strRef><c:f>List1!$A$2:$A$4</c:f>", "<c:numRef><c:f>List1!$A$2:$A$4</c:f>"
    )
    xml = xml.replace(
        mf._str_cache(mf._CHART_CATEGORIES) + "</c:strRef></c:cat>",
        mf._num_cache((2019, 2020, 2021)) + "</c:numRef></c:cat>",
    )
    lines = dd.chart_lines(xml.encode())
    assert lines == ["Nálezy podle sond", "Druh nálezu", "Počet kusů", "Sonda I", "Sonda II"]


def test_a_chartex_chart_gives_its_title_series_and_categories():
    cx = "http://schemas.microsoft.com/office/drawing/2014/chartex"
    xml = (
        f'<cx:chartSpace xmlns:cx="{cx}" xmlns:a="{mf.A_NS}"><cx:chartData><cx:data id="0">'
        '<cx:strDim type="cat"><cx:f>A2:A3</cx:f><cx:lvl ptCount="2">'
        '<cx:pt idx="0">Hroby</cx:pt><cx:pt idx="1">Jámy</cx:pt></cx:lvl></cx:strDim>'
        '<cx:numDim type="val"><cx:lvl ptCount="2"><cx:pt idx="0">4</cx:pt><cx:pt idx="1">9</cx:pt>'
        "</cx:lvl></cx:numDim></cx:data></cx:chartData><cx:chart><cx:title><cx:tx><cx:txData>"
        "<cx:v>Objekty</cx:v></cx:txData></cx:tx></cx:title><cx:plotArea><cx:plotAreaRegion>"
        '<cx:series layoutId="waterfall"><cx:tx><cx:txData><cx:f>B1</cx:f><cx:v>Počet</cx:v>'
        '</cx:txData></cx:tx><cx:dataId val="0"/></cx:series></cx:plotAreaRegion></cx:plotArea>'
        "</cx:chart></cx:chartSpace>"
    )
    assert dd.chart_lines(xml.encode()) == ["Objekty", "Počet", "Hroby", "Jámy"]


def test_a_chart_in_a_header_is_resolved_through_the_headers_own_part(tmp_path):
    """A story's drawing names its chart by the story part's own relationship id: a header's
    `rId1` is not the document's `rId1`."""
    rel = mf._REL
    chart = mf._inline_graphic(mf.C_NS, f'<c:chart xmlns:c="{mf.C_NS}" r:id="rId1"/>', 9, 2.0, 1.0)
    header = mf.XML_DECL + (
        f'<w:hdr xmlns:w="{mf.W_NS}" xmlns:r="{mf.R_NS}" xmlns:wp="{mf.WP_NS}" xmlns:a="{mf.A_NS}">'
        f"<w:p><w:r><w:t>Hlavička</w:t></w:r></w:p><w:p>{chart}</w:p></w:hdr>"
    )
    document = mf.XML_DECL + (
        f'<w:document xmlns:w="{mf.W_NS}" xmlns:r="{mf.R_NS}"><w:body>'
        "<w:p><w:r><w:t>Text.</w:t></w:r></w:p>"
        '<w:sectPr><w:headerReference w:type="default" r:id="rId7"/></w:sectPr></w:body></w:document>'
    )
    content_types = mf.OBJECTS_CONTENT_TYPES.replace(
        "</Types>",
        '<Override PartName="/word/header1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>',
    )
    doc_rels = mf.DOC_RELS.replace(
        "</Relationships>",
        f'<Relationship Id="rId7" Type="{rel}header" Target="header1.xml"/></Relationships>',
    )
    header_rels = mf.XML_DECL + (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}chart" Target="charts/chart1.xml"/></Relationships>'
    )
    data = mf._zip_parts(
        [
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", mf.ROOT_RELS),
            ("docProps/core.xml", mf.CORE_PROPS),
            ("word/_rels/document.xml.rels", doc_rels),
            ("word/document.xml", document),
            ("word/styles.xml", mf.STYLES),
            ("word/header1.xml", header),
            ("word/_rels/header1.xml.rels", header_rels),
            ("word/charts/chart1.xml", mf.OBJECTS_CHART),
        ]
    )
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "word/_rels/header1.xml.rels" in zf.namelist()
    page = _read(tmp_path, "header_chart.docx", data).pages[0]
    header_lines = [line.text for line in page.lines if line.region == "page_header"]
    assert header_lines[:3] == ["Hlavička", "Nálezy podle sond", "Druh nálezu"]


def test_a_relationship_that_names_nothing_reads_as_no_text(tmp_path):
    data = mf.objects_docx()
    broken = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(broken, "w") as dst:
        for info in src.infolist():
            content = src.read(info)
            if info.filename == "word/_rels/document.xml.rels":
                content = content.replace(b'Id="rId20"', b'Id="rId99"')
            dst.writestr(info, content)
    texts = [text for _, text in _lines(_read(tmp_path, "o.docx", broken.getvalue()))]
    assert "Nálezy podle sond" not in texts and "Výkop sondy" in texts


# ── the rendered page ─────────────────────────────────────────────────────────


@needs_libreoffice
def test_the_rendered_page_is_covered_and_shows_its_chart_and_diagram(tmp_path):
    """Before v2.0.0-beta the reader skipped the SmartArt and chart text, which the rendering
    shows: 19 of the page's 33 words were not in the document's text (coverage 42 %), and the
    page went to the ATR. Now the text is read, the page keeps it (it is the page's own text), and
    the chart and the diagram are the page's pictures, figure blocks for page-classification."""
    from api_util.digital_ir import page_graphics

    path = tmp_path / "objects.docx"
    path.write_bytes(mf.objects_docx())
    document, _dev = d2j.prepare(str(path))
    [page] = document.pages
    assert page.render_words and page.render_matched / page.render_words >= 0.8
    assert not page.needs_ocr, page.needs_ocr_reason
    assert page_graphics(document) == [2], "the chart and the SmartArt diagram"
    texts = [line.text for line in page.lines]
    assert "x=(-b±√(b^2-4ac))/(2a)" in texts, (
        "an equation LibreOffice draws as curves keeps its text"
    )


# ── a drawing's size, for its box on the rendering (atrium-project#84 decision 6) ─────────────


def test_a_drawing_carries_its_size_from_the_docx(tmp_path):
    """The SmartArt diagram (6 x 1.2 in) and the chart (5.5 x 3 in) of objects.docx: their sizes
    go with the line they count on, in pt, so the rendering can box them."""
    document = _read(tmp_path, "o.docx", mf.objects_docx())
    sizes = [size for page in document.pages for line in page.lines for size in line.graphic_sizes]
    assert sizes == [(432.0, 86.4), (396.0, 216.0)]
    counted = [line.graphics for page in document.pages for line in page.lines if line.graphics]
    assert counted == [1, 1]


def test_vml_and_ole_sizes_are_read_in_pt():
    V = 'xmlns:v="urn:schemas-microsoft-com:vml"'
    pict = dd._parse_xml(
        f'<w:pict xmlns:w="{mf.W_NS}" {V}><v:rect style="width:2in;height:36pt"/></w:pict>'.encode()
    )
    assert dd._vml_size(pict) == (144.0, 36.0)
    ole = dd._parse_xml(
        f'<w:object xmlns:w="{mf.W_NS}" w:dxaOrig="2880" w:dyaOrig="1440"/>'.encode()
    )
    assert dd.object_size(ole) == (144.0, 72.0)
    bare = dd._parse_xml(
        f'<w:object xmlns:w="{mf.W_NS}" {V}><v:shape style="width:96;height:48"/></w:object>'.encode()
    )
    assert dd.object_size(bare) == (72.0, 36.0), "a bare number is a pixel at 96 dpi"
    assert dd._vml_size(dd._parse_xml(f'<w:pict xmlns:w="{mf.W_NS}"/>'.encode())) == (0.0, 0.0)


@needs_libreoffice
def test_the_chart_and_the_diagram_are_figure_blocks_boxed_on_the_rendering(tmp_path):
    """Each drawing is a `figure` block with the box of its vector paths on the rendering: the
    SmartArt's three shapes grown into one box the diagram's width, the chart its own frame."""
    from api_util import docproc_writer

    path = tmp_path / "objects.docx"
    path.write_bytes(mf.objects_docx())
    document, _dev = d2j.prepare(str(path))
    docproc = docproc_writer.build_docproc(document)
    figures = [b for b in docproc["pages"][0]["blocks"] if b["type"] == "figure"]
    assert len(figures) == 2 and docproc_writer.figures_without_box(docproc) == 0
    (smartart, chart) = sorted(figures, key=lambda b: b["bbox"][1])
    width = smartart["bbox"][2] - smartart["bbox"][0]
    assert abs(width - 432.0) <= 30, "the three node shapes, as one diagram"
    assert abs((chart["bbox"][2] - chart["bbox"][0]) - 396.0) <= 30
    assert abs((chart["bbox"][3] - chart["bbox"][1]) - 216.0) <= 30
