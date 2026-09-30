#!/usr/bin/env python3
"""
scripts/detail_budget.py — what each Markdown cue profile costs (atrium-project#70 item 1).

Renders each input at ``full``, ``standard`` and ``minimal`` (``api_util/layout_md.py``) and
prints a Markdown table of characters and approximate tokens (characters / 4) per profile,
with the saving against ``full``. A measurement, not a verdict: which lighter profile the
model can live with is #22's bake-off (gold set, quality) to decide; ``full`` stays the
default until then.

With no arguments it measures the committed fixtures: the digital-born ones (generated in
memory by ``tests/fixtures/digital/make_fixtures.py``; needs ``requirements_digital.txt``)
through the JSON route, and nlp-enrich's TEITOK writer sample through ``xml_to_md --format
layout``. Paths given on the command line are measured instead — ``.pdf``, ``.docx``,
``*.document.json``, ``*.teitok.xml`` or ALTO ``.xml``.

    python3 scripts/detail_budget.py
    python3 scripts/detail_budget.py records/*.document.json scans/*.alto.xml

Developer tooling: not shipped in the image (.dockerignore).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from api_util import layout_md as L  # noqa: E402
from api_util import xml_to_md  # noqa: E402

DIGITAL_FIXTURES = ("minimal.pdf", "table.pdf", "two_column.pdf", "enrichable.pdf", "rich.docx")
TEITOK_SAMPLE = REPO / "tests" / "fixtures" / "teitok" / "writer" / "CTX000000002.teitok.xml"


def _renderer(path: Path) -> Callable[[str], str]:
    """A ``detail -> Markdown`` function for one input, converting it only once."""
    name = path.name.lower()
    if name.endswith(".document.json"):
        from api_util import json_to_md

        return lambda detail: json_to_md.convert(path, detail=detail)
    if path.suffix.lower() in (".pdf", ".docx"):
        from api_util import digital_to_json, json_to_md

        record = digital_to_json.build_record(str(path))
        return lambda detail: json_to_md.render_record(record, title=path.stem, detail=detail)
    rows, pages = xml_to_md.read_document_layout(path)
    title = xml_to_md.doc_id_from_path(path)
    return lambda detail: xml_to_md.rows_to_layout_markdown(rows, pages, title, detail=detail)


def measure(label: str, render: Callable[[str], str]) -> Tuple[str, Dict[str, int]]:
    return label, {detail: len(render(detail)) for detail in L.DETAIL_LEVELS}


def _fixture_inputs(tmp: Path) -> Iterable[Tuple[str, Path]]:
    maker = REPO / "tests" / "fixtures" / "digital" / "make_fixtures.py"
    spec = importlib.util.spec_from_file_location("make_fixtures", maker)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in DIGITAL_FIXTURES:
        path = tmp / name
        path.write_bytes(module.BUILDERS[name]())
        yield f"{name} (JSON route)", path
    yield f"{TEITOK_SAMPLE.name} (xml_to_md layout)", TEITOK_SAMPLE


def table(rows: List[Tuple[str, Dict[str, int]]]) -> str:
    head = "| Input | " + " | ".join(f"{d} chars (≈tok)" for d in L.DETAIL_LEVELS) + " |"
    out = [head, "|" + " --- |" * (len(L.DETAIL_LEVELS) + 1)]
    for label, sizes in rows:
        full = sizes["full"] or 1
        cells = []
        for detail in L.DETAIL_LEVELS:
            n = sizes[detail]
            saving = "" if detail == "full" else f", −{100 * (full - n) / full:.0f} %"
            cells.append(f"{n} ({n // 4}{saving})")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("inputs", nargs="*", type=Path)
    args = parser.parse_args(argv)

    results = []
    with tempfile.TemporaryDirectory() as tmp:
        inputs = (
            [(p.name, p) for p in args.inputs] if args.inputs else list(_fixture_inputs(Path(tmp)))
        )
        for label, path in inputs:
            try:
                results.append(measure(label, _renderer(path)))
            except (ValueError, RuntimeError, ImportError) as exc:
                print(f"[skip] {label}: {exc}", file=sys.stderr)
    print(table(results))
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
