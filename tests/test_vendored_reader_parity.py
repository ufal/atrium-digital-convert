"""tests/test_vendored_reader_parity.py -- the document reader vendored from atrium-ocr-postprocess.

atrium-project#72 settled "one document reader": atrium-ocr-postprocess owns the text-bearing
readers (`text_formats.py`, its #31 text-lines path), and the born-digital converter reads ODT,
ODS, XLSX and RTF with the SAME code (atrium-digital-convert#2, W2) instead of a second fork. The
copy sits at the repository root, as it does upstream, because its isolated PDF worker imports
`tool_limits` from the directory beside it.

The copy is pinned by the SHA-256 of the ocr-postprocess file it was taken from (line endings
normalised to ``\\n``). A mismatch means a local edit to a vendored file -- make the change in
atrium-ocr-postprocess instead -- or a re-vendor without updating the pin.

To re-vendor: the hub's ``scripts/revendor_shared.sh`` copies it from a sibling
``../atrium-ocr-postprocess`` checkout (its sibling-owned rows), then run
``python3 tests/test_vendored_reader_parity.py`` and paste the printed pin over ``VENDORED``.
Commit the copy and the pin together.

When an atrium-ocr-postprocess checkout sits next to this repo, the copy is also compared with it
byte for byte; that check is skipped when the checkout is absent (CI).
"""

import hashlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
OCR_POSTPROCESS = REPO_ROOT.parent / "atrium-ocr-postprocess"

#: Pinned 2026-10-04 to atrium-ocr-postprocess `test` 6a87397 (the file's last change: 6707320).
VENDORED = {
    "text_formats.py": "a3467488c4ca8ad4f93fe6ac3f7052856ae610b2f9430680c2d9f5b03e58c3d7",
}

#: The names the vendored module imports from this repository's `tool_limits.py`; they must keep
#: ocr-postprocess's variable names and defaults (see tool_limits.py).
TOOL_LIMITS_CONTRACT = {
    "ODF_REPEAT_CAP": ("ATRIUM_TEXT_INGEST_ODF_REPEAT_CAP", 100),
    "PDF_OBJECT_CAP": ("ATRIUM_TEXT_INGEST_PDF_OBJECT_CAP", 20000),
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


@pytest.mark.parametrize("rel", sorted(VENDORED))
def test_vendored_copy_matches_its_pin(rel):
    assert _digest(REPO_ROOT / rel) == VENDORED[rel], (
        f"{rel} differs from the atrium-ocr-postprocess copy it was vendored from -- change it in "
        "atrium-ocr-postprocess and re-vendor (see this module's docstring)"
    )


@pytest.mark.parametrize("rel", sorted(VENDORED))
def test_vendored_copy_matches_a_sibling_ocr_postprocess_checkout(rel):
    upstream = OCR_POSTPROCESS / rel
    if not upstream.is_file():
        pytest.skip("no ../atrium-ocr-postprocess checkout next to this repo")
    assert _digest(REPO_ROOT / rel) == _digest(upstream), (
        f"{rel} drifted from atrium-ocr-postprocess"
    )


def test_tool_limits_offers_what_the_reader_imports():
    import tool_limits

    for name, (env, default) in TOOL_LIMITS_CONTRACT.items():
        spec = getattr(tool_limits, name)
        assert (spec.env, spec.default) == (env, default), name


if __name__ == "__main__":
    for rel in sorted(VENDORED):
        print(f'    "{rel}": "{_digest(REPO_ROOT / rel)}",')
