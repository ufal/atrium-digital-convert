"""tests/test_office_render.py — office documents rendered for their pages (atrium-digital-convert#9).

The working copy LibreOffice renders (tracked changes accepted, the package repaired), the pinned
command, the normalised PDF and a layout that reruns keep, and the failures (no LibreOffice, a timeout). The tests that need a
real LibreOffice are marked `needs_libreoffice`; the rest run against a fake `soffice`.
"""

from __future__ import annotations

import hashlib
import json
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures" / "digital"))
import make_fixtures  # noqa: E402

from api_util import digital_to_json as d2j  # noqa: E402
from api_util import office_render  # noqa: E402
from api_util.digital_ir import DigitalInputError  # noqa: E402
from atrium_limits import LimitExceeded  # noqa: E402
from tests.office_support import needs_libreoffice  # noqa: E402

pytest.importorskip("lxml")


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures")
    for name, data in make_fixtures.build_all().items():
        (out / name).write_bytes(data)
    return out


def _part(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as zf:
        return zf.read(name).decode("utf-8")


def test_the_docx_copy_reads_as_the_native_text(fixtures, tmp_path):
    """Tracked changes accepted (the native reader's G5 rule): the deletion is gone, the
    insertion kept, and nothing else changes."""
    copy = Path(office_render.prepared_copy(str(fixtures / "rich.docx"), "docx", str(tmp_path)))
    body = _part(copy, "word/document.xml")
    assert "železný nůž" not in body and "w:del " not in body and "<w:del>" not in body
    assert "bronzová spona" in body and "<w:ins" not in body
    assert "Poznámka v rámečku." in body, "the rest of the document is untouched"
    with zipfile.ZipFile(fixtures / "rich.docx") as original, zipfile.ZipFile(copy) as made:
        assert original.namelist() == made.namelist()


def test_the_docx_copy_carries_the_main_content_type(fixtures, tmp_path):
    """G4: LibreOffice will not load a package whose main part has no WordprocessingML type."""
    copy = Path(office_render.prepared_copy(str(fixtures / "bare.docx"), "docx", str(tmp_path)))
    types = _part(copy, "[Content_Types].xml")
    assert 'PartName="/word/document.xml"' in types and "document.main+xml" in types


def test_the_odt_copy_drops_the_tracked_changes(tmp_path):
    text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        f'xmlns:text="{text_ns}" xmlns:dc="http://purl.org/dc/elements/1.1/"><office:body><office:text>'
        '<text:tracked-changes><text:changed-region text:id="c1"><text:deletion>'
        "<office:change-info><dc:creator>A</dc:creator></office:change-info>"
        "<text:p>železný nůž</text:p></text:deletion></text:changed-region></text:tracked-changes>"
        '<text:p>Nalezena <text:change text:change-id="c1"/><text:change-start text:change-id="c2"/>'
        'bronzová spona<text:change-end text:change-id="c2"/>, viz katalog.</text:p>'
        "</office:text></office:body></office:document-content>"
    )
    odt = tmp_path / "zprava.odt"
    with zipfile.ZipFile(odt, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml", content)
    workdir = tmp_path / "work"
    workdir.mkdir()
    copy = Path(office_render.prepared_copy(str(odt), "odt", str(workdir)))
    body = _part(copy, "content.xml")
    assert "železný nůž" not in body and "tracked-changes" not in body and "text:change" not in body
    assert "Nalezena bronzová spona, viz katalog." in body, "the text around the marks is kept"
    with zipfile.ZipFile(copy) as zf:
        first = zf.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED


def test_the_pdf_is_made_reproducible_length_for_length():
    pdf = (
        b"%PDF-1.6\n1 0 obj\n<</Producer(LibreOffice 24.2)/CreationDate(D:20261114214239Z')>>\nendobj\n"
        b"trailer\n<</Size 2/Root 1 0 R\n/ID [ <208DEF0F81B011B853149808A623D167>\n"
        b"<208DEF0F81B011B853149808A623D167> ]\n/DocChecksum /6E3C1E6FD118114A8A626EE5B60CFD93\n>>\n%%EOF\n"
    )
    stable = office_render.stable_pdf(pdf, b"the copy")
    assert len(stable) == len(pdf)
    assert (
        b"D:19800101000000Z'" in stable and b"208DEF0F" not in stable and b"6E3C1E6F" not in stable
    )
    assert office_render.stable_pdf(pdf.replace(b"214239", b"235959"), b"the copy") == stable


def _recording_soffice(tmp_path: Path, monkeypatch, pdf: bytes) -> Path:
    log = tmp_path / "argv.json"
    script = tmp_path / "soffice"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']:\n"
        "    print('LibreOffice 9.9.9.9 test'); sys.exit(0)\n"
        f"json.dump(args, open({str(log)!r}, 'w'))\n"
        "outdir = args[args.index('--outdir') + 1]\n"
        f"open(outdir + '/input.pdf', 'wb').write({pdf!r})\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LIBREOFFICE_BIN", str(script))
    office_render._version_of.cache_clear()
    return log


def test_the_rendering_runs_with_the_pinned_options(fixtures, tmp_path, monkeypatch):
    log = _recording_soffice(tmp_path, monkeypatch, make_fixtures.minimal_pdf())
    pdf, facts = office_render.render_pdf(
        str(fixtures / "minimal.docx"), "docx", str(tmp_path / "w")
    )
    args = json.loads(log.read_text(encoding="utf-8"))
    assert args[args.index("--convert-to") + 1] == office_render.CONVERT_TO
    assert args[0].startswith("-env:UserInstallation=") and "--headless" in args
    options = json.loads(office_render.CONVERT_TO.split(":", 2)[2])
    assert (
        options["ExportNotes"]["value"] == "false"
        and options["ReduceImageResolution"]["value"] == "false"
    )
    assert Path(pdf).read_bytes().startswith(b"%PDF")
    assert facts["tool"] == "libreoffice" and facts["to"] == "pdf" and len(facts["sha512"]) == 128
    assert facts["version"] == "9.9.9.9"
    assert "seconds" not in facts, "the facts describe the rendering, not the run"
    office_render._version_of.cache_clear()


def test_without_libreoffice_an_office_document_is_dependency_missing(fixtures, monkeypatch):
    monkeypatch.setenv("LIBREOFFICE_BIN", "/no/such/soffice")
    with pytest.raises(DigitalInputError) as info:
        d2j.extract(str(fixtures / "minimal.docx"))
    assert info.value.reason == "dependency_missing" and info.value.exit_code == 2


def test_a_rendering_over_the_time_limit_fails_the_run(fixtures, tmp_path, monkeypatch):
    script = tmp_path / "soffice"
    script.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LIBREOFFICE_BIN", str(script))
    monkeypatch.setenv("LIBREOFFICE_TIMEOUT_S", "1")
    with pytest.raises(LimitExceeded):
        office_render.render_pdf(str(fixtures / "minimal.docx"), "docx", str(tmp_path / "w"))


def _layout(pdf: str):
    """A rendering as the docproc sees it: each page's size and its words with their positions."""
    import pdfplumber

    with pdfplumber.open(pdf) as doc:
        return [
            (
                round(float(page.width), 2),
                round(float(page.height), 2),
                [
                    (w["text"], round(w["x0"], 1), round(w["top"], 1), round(w["x1"], 1), round(w["bottom"], 1))
                    for w in page.extract_words()
                ],
            )
            for page in doc.pages
        ]


@needs_libreoffice
def test_a_rerun_lays_the_document_out_the_same_and_shows_no_deletion(fixtures, tmp_path):
    """What a rerun keeps is the layout the docproc is built on, not the PDF's bytes: on the
    Ubuntu CI runner two renderings differed in bytes (2026-11-15) and gave one docproc."""
    layouts, digests = [], []
    for run in range(2):
        pdf, facts = office_render.render_pdf(
            str(fixtures / "rich.docx"), "docx", str(tmp_path / str(run))
        )
        layouts.append(_layout(pdf))
        digests.append(facts["sha512"])
        assert facts["sha512"] == hashlib.sha512(Path(pdf).read_bytes()).hexdigest(), (
            "the digest names the PDF handed over"
        )
    assert layouts[0] == layouts[1], "a rerun lays the pages out the same"
    words = " ".join(w[0] for _, _, page in layouts[0] for w in page)
    assert "železný" not in words and "bronzová spona" in words
