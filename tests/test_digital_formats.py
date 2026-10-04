"""tests/test_digital_formats.py — every born-digital type AMČR accepts, and the seed handshake.

atrium-digital-convert#2 (W2): besides PDF and DOCX the converter reads ODT, ODS, XLSX and RTF
through the shared reader (the vendored `text_formats.py`), and legacy DOC and XLS through a
headless LibreOffice conversion. The documents are built here, in a few hundred bytes each, so the
tests need neither binary fixtures nor LibreOffice: the conversion is exercised against a fake
`soffice` that copies a prepared DOCX/XLSX into place, which is all the adapter relies on.

Also pinned here: the AMČR seed's `source.sha512` is compared with the bytes received before
anything is read (`source_digest_mismatch`), and the page limit (`MAX_PAGES`).
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

from api_util import digital_to_json as d2j
from atrium_limits import LimitExceeded

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "digital"
sys.path.insert(0, str(FIXTURES))
import make_fixtures  # noqa: E402

pytest.importorskip("lxml")
pytest.importorskip("jsonschema")


# ── tiny documents ──────────────────────────────────────────────────────────────────────────

_ODF_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
)


def _odf(path: Path, mimetype: str, body: str) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zipfile.ZipInfo("mimetype"), mimetype, compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/manifest.xml",
            '<?xml version="1.0" encoding="UTF-8"?><manifest:manifest '
            'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
            f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mimetype}"/>'
            '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
            "</manifest:manifest>",
        )
        zf.writestr(
            "content.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><office:document-content {_ODF_NS}>'
            f"<office:body>{body}</office:body></office:document-content>",
        )
    return path


def make_odt(path: Path) -> Path:
    return _odf(
        path,
        "application/vnd.oasis.opendocument.text",
        "<office:text><text:h>Zpráva o výzkumu</text:h>"
        "<text:p>Sonda 3 odkryla vrstvu ornice.</text:p>"
        "<text:p>Nalezeny střepy a hřeby.</text:p></office:text>",
    )


def make_ods(path: Path) -> Path:
    return _odf(
        path,
        "application/vnd.oasis.opendocument.spreadsheet",
        '<office:spreadsheet><table:table table:name="Nálezy">'
        "<table:table-row><table:table-cell><text:p>Sonda</text:p></table:table-cell>"
        "<table:table-cell><text:p>Nález</text:p></table:table-cell></table:table-row>"
        "<table:table-row><table:table-cell><text:p>S3</text:p></table:table-cell>"
        "<table:table-cell><text:p>střep</text:p></table:table-cell></table:table-row>"
        "</table:table></office:spreadsheet>",
    )


def make_xlsx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?><Types '
            'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            "</Types>",
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?><workbook '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Nálezy" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?><worksheet '
            'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="inlineStr"><is><t>Sonda</t></is></c>'
            '<c r="B1" t="inlineStr"><is><t>Nález</t></is></c></row>'
            '<row r="2"><c r="A2" t="inlineStr"><is><t>S3</t></is></c>'
            '<c r="B2" t="inlineStr"><is><t>střep</t></is></c><c r="C2"><v>12</v></c></row>'
            "</sheetData></worksheet>",
        )
    return path


def make_rtf(path: Path) -> Path:
    path.write_bytes(
        rb"{\rtf1\ansi\ansicpg1250\deff0{\fonttbl{\f0 Arial;}}"
        rb"\f0 Zpr\'e1va o sond\'ec 3.\par Nalezeny h\'f8eby.\par}"
    )
    return path


def make_doc_stub(path: Path) -> Path:
    """An OLE2 container naming a WordDocument stream — what `sniff()` keys on."""
    path.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 56 + "WordDocument".encode("utf-16-le")
    )
    return path


def make_xls_stub(path: Path) -> Path:
    path.write_bytes(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 56 + "Workbook".encode("utf-16-le")
    )
    return path


def _lines(record):
    return [row["text"] for row in record.get("lines", [])]


# ── sniffing ────────────────────────────────────────────────────────────────────────────────


def test_every_accepted_type_is_recognised_by_content(tmp_path):
    made = {
        "odt": make_odt(tmp_path / "a.bin"),
        "ods": make_ods(tmp_path / "b.bin"),
        "xlsx": make_xlsx(tmp_path / "c.bin"),
        "rtf": make_rtf(tmp_path / "d.bin"),
        "doc": make_doc_stub(tmp_path / "e.bin"),
        "xls": make_xls_stub(tmp_path / "f.bin"),
    }
    for kind, path in made.items():
        assert d2j.sniff(str(path)) == kind, kind
    assert set(made) | {"pdf", "docx"} == set(d2j.KINDS)


def test_a_presentation_is_not_an_accepted_type(tmp_path):
    pptx = tmp_path / "slides.pptx"
    with zipfile.ZipFile(pptx, "w") as zf:
        zf.writestr("ppt/presentation.xml", "<p/>")
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.sniff(str(pptx))
    assert info.value.reason == "unsupported"


# ── the shared reader ───────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "maker, origin, expected",
    [
        (
            make_odt,
            "digital-born-odt",
            ["Zpráva o výzkumu", "Sonda 3 odkryla vrstvu ornice.", "Nalezeny střepy a hřeby."],
        ),
        (make_rtf, "digital-born-rtf", ["Zpráva o sondě 3.", "Nalezeny hřeby."]),
    ],
)
def test_text_documents_become_records_with_lines(tmp_path, maker, origin, expected):
    path = maker(tmp_path / f"doc.{origin.rsplit('-', 1)[-1]}")
    record = d2j.build_record(str(path))
    assert record["source"]["origin"] == origin
    assert _lines(record) == expected
    assert all("bbox" not in row for row in record["lines"]), "no geometry is fabricated"
    assert all("canvas" not in page for page in record["pages"])
    assert record["assembled"]["blocks"]["lines"]["program"] == "digital-convert"


@pytest.mark.parametrize(
    "maker, origin", [(make_ods, "digital-born-ods"), (make_xlsx, "digital-born-xlsx")]
)
def test_a_sheet_is_a_page_and_a_row_a_line(tmp_path, maker, origin):
    path = maker(tmp_path / f"sheet.{origin.rsplit('-', 1)[-1]}")
    record = d2j.build_record(str(path))
    assert record["source"]["origin"] == origin
    assert [page["page"] for page in record["pages"]] == ["Nálezy"]
    assert _lines(record) == ["Sonda\tNález", "S3\tstřep"], (
        "numbers are not text; rows keep their cells"
    )
    assert {row["group_id"] for row in record["lines"]} == {"s1"}


# ── legacy DOC/XLS through LibreOffice ──────────────────────────────────────────────────────


def _fake_soffice(tmp_path: Path, payload: Path, monkeypatch) -> Path:
    """A `soffice` that 'converts' by copying `payload` to <outdir>/input.<target>."""
    script = tmp_path / "soffice"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import shutil, sys\n"
        "args = sys.argv[1:]\n"
        "target = args[args.index('--convert-to') + 1]\n"
        "outdir = args[args.index('--outdir') + 1]\n"
        f"shutil.copyfile({str(payload)!r}, outdir + '/input.' + target)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LIBREOFFICE_BIN", str(script))
    return script


def test_a_legacy_doc_keeps_the_originals_identity(tmp_path, monkeypatch):
    docx = tmp_path / "converted.docx"
    docx.write_bytes(make_fixtures.minimal_docx())
    _fake_soffice(tmp_path, docx, monkeypatch)
    original = make_doc_stub(tmp_path / "stara-zprava.doc")
    record = d2j.build_record(str(original))
    source = record["source"]
    assert source["origin"] == "digital-born-doc"
    assert source["media_type"] == "application/msword"
    assert source["filename"] == "stara-zprava.doc"
    assert source["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest(), (
        "the ORIGINAL's digest"
    )
    assert _lines(record), "the converted DOCX was read"
    detail = json.dumps(record["provenance"]["license_detail"])
    assert "libreoffice" not in detail, (
        "a converter of the container adds nothing to the record's licence"
    )
    assert record["provenance"]["license"] in ("MIT", "Apache-2.0")


def test_a_legacy_xls_is_read_as_a_spreadsheet(tmp_path, monkeypatch):
    xlsx = make_xlsx(tmp_path / "converted.xlsx")
    _fake_soffice(tmp_path, xlsx, monkeypatch)
    record = d2j.build_record(str(make_xls_stub(tmp_path / "nalezy.xls")))
    assert record["source"]["origin"] == "digital-born-xls"
    assert record["source"]["media_type"] == "application/vnd.ms-excel"
    assert _lines(record) == ["Sonda\tNález", "S3\tstřep"]


def test_without_libreoffice_a_legacy_file_is_dependency_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBREOFFICE_BIN", str(tmp_path / "no-such-soffice"))
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.extract(str(make_doc_stub(tmp_path / "x.doc")))
    assert info.value.reason == "dependency_missing" and info.value.exit_code == 2


def test_a_failed_conversion_is_named(tmp_path, monkeypatch):
    script = tmp_path / "soffice"
    script.write_text(
        "#!/bin/sh\necho 'source file could not be loaded' >&2\nexit 1\n", encoding="utf-8"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LIBREOFFICE_BIN", str(script))
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.extract(str(make_xls_stub(tmp_path / "x.xls")))
    assert info.value.reason == "conversion_failed" and info.value.exit_code == 4
    assert "could not be loaded" in str(info.value)


# ── the seed handshake ──────────────────────────────────────────────────────────────────────


def _seed(path: Path, sha512: str) -> Path:
    seed = path.parent / "seed.document.json"
    seed.write_text(
        json.dumps(
            {
                "doc_id": "AMCR-F-1",
                "source": {
                    "sha512": sha512,
                    "filename": "zprava.pdf",
                    "media_type": "application/pdf",
                },
            }
        ),
        encoding="utf-8",
    )
    return seed


def test_a_seed_with_the_files_digest_is_accepted(tmp_path):
    pdf = tmp_path / "minimal.pdf"
    pdf.write_bytes(make_fixtures.minimal_pdf())
    seed = _seed(pdf, hashlib.sha512(pdf.read_bytes()).hexdigest())
    record = d2j.build_record(str(pdf), baseline=str(seed))
    assert record["doc_id"] == "AMCR-F-1"
    assert "sha256" not in record["source"], "the archive's sha512 stays the only digest"


def test_a_seed_for_another_file_is_source_digest_mismatch(tmp_path, monkeypatch):
    pdf = tmp_path / "minimal.pdf"
    pdf.write_bytes(make_fixtures.minimal_pdf())
    seed = _seed(pdf, "0" * 128)
    read = []
    monkeypatch.setattr(d2j, "extract", lambda *a, **k: read.append(a))
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.build_record(str(pdf), baseline=str(seed))
    assert info.value.reason == "source_digest_mismatch" and info.value.exit_code == 3
    assert read == [], "refused before any reader ran"
    out = tmp_path / "out.json"
    assert d2j.main([str(pdf), "--document-json", str(seed), "--document-json-out", str(out)]) == 3
    assert not out.exists(), "no record is written for the wrong file"


def test_a_seed_without_sha512_is_not_checked(tmp_path):
    pdf = tmp_path / "minimal.pdf"
    pdf.write_bytes(make_fixtures.minimal_pdf())
    seed = tmp_path / "seed.document.json"
    seed.write_text(
        json.dumps({"doc_id": "AMCR-F-2", "source": {"filename": "x.pdf"}}), encoding="utf-8"
    )
    assert d2j.verify_seed_digest(str(pdf), str(seed)) is None


def test_the_legacy_digest_is_checked_before_the_conversion(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBREOFFICE_BIN", str(tmp_path / "never-called"))
    original = make_doc_stub(tmp_path / "x.doc")
    seed = _seed(original, "f" * 128)
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.build_record(str(original), baseline=str(seed))
    assert info.value.reason == "source_digest_mismatch", (
        "not dependency_missing: no conversion was tried"
    )


# ── limits and settings ─────────────────────────────────────────────────────────────────────


def test_a_document_over_max_pages_is_refused(tmp_path, monkeypatch):
    pdf = tmp_path / "minimal.pdf"
    pdf.write_bytes(make_fixtures.minimal_pdf())  # two pages
    monkeypatch.setenv("MAX_PAGES", "1")
    with pytest.raises(LimitExceeded) as info:
        d2j.extract(str(pdf))
    assert info.value.key == "max_pages" and info.value.http_status == 422
    assert d2j.main([str(pdf), "--document-json-out", str(tmp_path / "o.json")]) == 4


def _mixed_pdf() -> bytes:
    """One born-digital page and one page that is a prior OCR run's invisible text."""
    digital = make_fixtures._text_at(72, 720, [b"Zprava o vyzkumu, strana jedna."])
    ocr = (
        make_fixtures.FULL_PAGE_IMAGE
        + b"\n"
        + make_fixtures._text_at(72, 720, [b"Naskenovana strana."], render=3)
    )
    return make_fixtures._build_pdf_ex([digital, ocr])


def test_the_ocr_layer_share_is_a_setting(tmp_path, monkeypatch):
    """1 of 2 text-bearing pages is a prior OCR layer: refused at the default 0.5 (1 >= 0.5 x 2),
    converted with that page flagged at 0.75 (1 < 1.5) — motyc's two readings on #2, one setting."""
    pdf = tmp_path / "mixed.pdf"
    pdf.write_bytes(_mixed_pdf())
    with pytest.raises(d2j.DigitalInputError) as info:
        d2j.extract(str(pdf))
    assert info.value.reason == "ocr_text_layer"
    assert "OCR_LAYER_DOCUMENT_SHARE 0.5" in str(info.value)
    monkeypatch.setenv("OCR_LAYER_DOCUMENT_SHARE", "0.75")
    record = d2j.build_record(str(pdf))
    flagged = [page for page in record["pages"] if page.get("needs_ocr")]
    assert [page["page_index"] for page in flagged] == [2]
    assert "prior OCR run" in flagged[0]["needs_ocr_reason"]


def test_the_reader_limits_follow_the_service_limits(monkeypatch):
    from api_util.digital_text import reader_limits

    monkeypatch.setenv("MAX_UPLOAD_MB", "7")
    limits = reader_limits(7, 3)
    assert limits.max_file_mb == 7.0 and limits.max_pages == 3


def test_kinds_and_media_types_agree():
    assert set(d2j.MEDIA_TYPES) == set(d2j.KINDS)
    assert os.path.basename(d2j.__file__) == "digital_to_json.py"
