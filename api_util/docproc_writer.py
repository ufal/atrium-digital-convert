"""api_util/docproc_writer.py — a born-digital document as docproc (`atr/docproc-json`, atrium-project#84).

Since ARÚP's output contract of 12 November 2026 digital-convert writes no record: it writes the
document's layout as docproc, the format the ATR writes for scanned pages, and ocr-postprocess
builds the record from it (#84 §1). ``build_docproc()`` maps the converter's internal
``DigitalDocument`` (``api_util/digital_ir.py``) to it:

* **pages** -- one per page that has text or needs the ATR, numbered by ``pageNumber`` (the page
  index, the record's ``page_index``). A page with nothing on it (a blank page, an empty DOCX
  page) has no entry: it is routed ``empty`` (H15). A page carries its size in points when the
  reader knows one (a PDF; never a flow document), the text-layer verdict (``textLayer``) and,
  when its text cannot be used, ``needsAtr`` with ``needsAtrReason``.
* **blocks** -- one per paragraph group (``lines[].group_id`` of the IR: a paragraph from the
  vertical gaps, a DOCX paragraph, a sheet), ids ``p{page}-b{n}`` (``n`` from 0), ``readingOrder``
  dense from 0. The type follows the line style: ``heading`` with ``level``, ``header``,
  ``footer``, ``footnote``, else ``paragraph``. A table is one ``table`` block with its
  ``cellGrid`` and its plain text (rows by line, cells by tab). A block's ``content`` is its
  lines joined by line breaks, its ``bbox`` the union of its line boxes.
* **lines** -- every text block carries its ``lines[]``: the text, the measured box, its
  ``bboxPrecision`` (``measured`` or ``absent``), and the decode verdict as ``flags``
  (``Garbage``, ``Inverted``: ``atrium_vocab.DECODE_FLAGS``; D18 rule 2).
* **routing** -- ``metadata.routing.pages[]`` lists every page: ``page``, ``route``
  (``text-layer``, ``atr``, ``empty``) and ``text_layer`` (whether the page's text layer is
  used).

No run data: no ``processedAt``, ``pipeline``, ``sourceFile`` or ``sourceHash`` (the run, the
original and its SHA-512 are the paradata's; ask 3). The output validates against
``schemas/docproc/document.atrium-proposal.schema.json``: the ATR's schema with asks 3 and 13
applied (``tools/docproc_proposal.py``).

The coordinate work of ask 10 / D28 (UserUnit, CropBox, /Rotate) is not done here yet: a box is
in the reader's points, origin top-left.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from api_util.digital_ir import (
    REGION_FOOTER,
    REGION_FOOTNOTE,
    REGION_HEADER,
    TABLE_GROUP_PREFIX,
    DigitalDocument,
    DigitalLine,
    DigitalPage,
    DigitalTable,
    text_layer_of,
)

#: The docproc version this writer emits: v1 with asks 3 and 13 (the ATRIUM proposal).
DOCPROC_VERSION = "1.1"

#: The AMČR distribution name of docproc (#84 §4).
DISTRIBUTION = "atr/docproc-json"

#: Routes of `metadata.routing.pages[].route` (#84 §7, H15).
ROUTE_TEXT_LAYER = "text-layer"
ROUTE_ATR = "atr"
ROUTE_EMPTY = "empty"

#: digital-convert's decode verdicts, as line flags (`atrium_vocab.DECODE_FLAGS`).
DECODE_FLAGS: Tuple[str, ...] = ("Garbage", "Inverted")

#: `sourceFormat` per input kind (`digital_to_json.sniff()`).
SOURCE_FORMATS: Dict[str, str] = {
    "docx": "docx",
    "odt": "odt",
    "ods": "ods",
    "xlsx": "xlsx",
    "rtf": "rtf",
    "doc": "doc",
    "xls": "xls",
}

_SCHEMAS = Path(__file__).resolve().parent.parent / "schemas" / "docproc"
PROPOSAL_SCHEMA = _SCHEMAS / "document.atrium-proposal.schema.json"
ACTUAL_SCHEMA = _SCHEMAS / "document.actual.schema.json"


class DocprocError(ValueError):
    """The docproc this writer built does not validate: a defect here, never written."""


def source_format(document: DigitalDocument) -> str:
    """`pdf-scanned` when no page's text layer is used, else `pdf-digital` (a mixed PDF too)."""
    if document.kind == "pdf" or not document.kind:
        used = any(not page.needs_ocr and page.lines for page in document.pages)
        return "pdf-digital" if used else "pdf-scanned"
    return SOURCE_FORMATS.get(document.kind, document.kind)


def route(page: DigitalPage) -> str:
    """Where the page goes: the ATR when its text cannot be used, nowhere when it is empty."""
    if page.needs_ocr:
        return ROUTE_ATR
    if not page.lines:
        return ROUTE_EMPTY
    return ROUTE_TEXT_LAYER


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


def _line(line: DigitalLine) -> Dict[str, Any]:
    out: Dict[str, Any] = {"text": line.text}
    if line.bbox:
        out["bbox"] = [round(float(v), 3) for v in line.bbox]
        out["bboxPrecision"] = "measured"
    else:
        out["bboxPrecision"] = "absent"
    if line.lang and len(line.lang) == 2 and line.lang.isalpha():
        out["language"] = line.lang.lower()
    if line.categ in DECODE_FLAGS:
        out["flags"] = [line.categ]
    return out


def _block_type(lines: Sequence[DigitalLine]) -> Tuple[str, Optional[int]]:
    first = lines[0]
    if first.region == REGION_HEADER:
        return "header", None
    if first.region == REGION_FOOTER:
        return "footer", None
    if first.region == REGION_FOOTNOTE:
        return "footnote", None
    if first.heading_level:
        return "heading", max(1, min(6, int(first.heading_level)))
    return "paragraph", None


def _table_key(group_id: Optional[str]) -> Optional[str]:
    """`tbl3` for a cell line `tbl3-r0c1` (`digital_ir.TABLE_GROUP_PREFIX`), else None."""
    if not group_id or not str(group_id).startswith(TABLE_GROUP_PREFIX):
        return None
    return str(group_id).split("-", 1)[0]


def _table_block(table: Optional[DigitalTable], lines: Sequence[DigitalLine]) -> Dict[str, Any]:
    """A table: its `cellGrid` from the IR's grid, the cells' text from their lines."""
    by_cell: Dict[str, List[str]] = {}
    for line in lines:
        by_cell.setdefault(str(line.group_id), []).append(line.text)
    rows: List[Dict[str, Any]] = []
    if table is not None and table.cells:
        grid: Dict[int, Dict[int, Dict[str, Any]]] = {}
        for cell in table.cells:
            text = " ".join(by_cell.get(str(cell.get("group_id")), []))
            entry: Dict[str, Any] = {"text": text}
            if cell.get("is_header"):
                entry["isHeader"] = True
            grid.setdefault(int(cell.get("row", 0)), {})[int(cell.get("col", 0))] = entry
        for r in sorted(grid):
            rows.append({"cells": [grid[r][c] for c in sorted(grid[r])]})
    else:  # no grid known: one row per line, as read
        rows = [{"cells": [{"text": line.text}]} for line in lines]
    content = "\n".join("\t".join(cell["text"] for cell in row["cells"]) for row in rows)
    block: Dict[str, Any] = {"type": "table", "content": content, "cellGrid": {"rows": rows}}
    if table is not None and table.caption:
        block["caption"] = table.caption
    return block


def _groups(page: DigitalPage) -> List[Tuple[str, List[DigitalLine]]]:
    """The page's lines as blocks in reading order: `(kind, lines)`, kind `table` or `text`.

    Consecutive lines of one `group_id` are one block; every cell line of one table is that
    table's block, placed where its first cell line is."""
    groups: List[Tuple[str, List[DigitalLine]]] = []
    tables: Dict[str, List[DigitalLine]] = {}
    previous: Optional[str] = None
    for line in page.lines:
        key = _table_key(line.group_id)
        if key is not None:
            if key not in tables:
                tables[key] = []
                groups.append(("table:" + key, tables[key]))
            tables[key].append(line)
            previous = None
            continue
        group = str(line.group_id) if line.group_id is not None else None
        if (
            groups
            and not groups[-1][0].startswith("table:")
            and group is not None
            and group == previous
        ):
            groups[-1][1].append(line)
        else:
            groups.append(("text", [line]))
        previous = group
    return groups


def _page(page: DigitalPage) -> Dict[str, Any]:
    tables = {str(t.group_id or t.table_id): t for t in page.tables}
    blocks: List[Dict[str, Any]] = []
    for kind, lines in _groups(page):
        n = len(blocks)
        if kind.startswith("table:"):
            block = _table_block(tables.get(kind.split(":", 1)[1]), lines)
        else:
            block_type, level = _block_type(lines)
            block = {"type": block_type, "content": "\n".join(line.text for line in lines)}
            if level is not None:
                block["level"] = level
            block["lines"] = [_line(line) for line in lines]
        bbox = _union([line.bbox for line in lines if line.bbox])
        ordered: Dict[str, Any] = {"id": f"p{page.page_index}-b{n}", "type": block["type"]}
        if bbox is not None:
            ordered["bbox"] = bbox
        ordered["readingOrder"] = n
        ordered["confidence"] = 1.0  # native text extraction (the schema's own convention)
        ordered.update({k: v for k, v in block.items() if k != "type"})
        blocks.append(ordered)

    out: Dict[str, Any] = {"pageNumber": page.page_index}
    if page.width and page.height:
        out["width"] = round(float(page.width), 3)
        out["height"] = round(float(page.height), 3)
        out["coordinateUnit"] = "pt" if page.unit in ("pt", "", None) else "px"
    out["blocks"] = blocks
    out["textLayer"] = text_layer_of(page)
    if page.needs_ocr:
        out["needsAtr"] = True
        out["needsAtrReason"] = page.needs_ocr_reason or "the page's text layer cannot be used"
    return out


def _language(document: DigitalDocument) -> Optional[str]:
    counts: Dict[str, int] = {}
    for line in document.all_lines():
        if line.lang and len(line.lang) == 2 and line.lang.isalpha():
            counts[line.lang.lower()] = counts.get(line.lang.lower(), 0) + 1
    return max(counts, key=counts.get) if counts else None


def build_docproc(document: DigitalDocument) -> Dict[str, Any]:
    """The document as docproc (see the module docstring); validated, else `DocprocError`."""
    pages = [_page(page) for page in document.pages if route(page) != ROUTE_EMPTY]
    routing = [
        {
            "page": page.page_index,
            "route": route(page),
            # whether the page's own text layer is what its text comes from
            "text_layer": route(page) == ROUTE_TEXT_LAYER,
        }
        for page in document.pages
    ]
    by_route: Dict[str, int] = {}
    for entry in routing:
        by_route[entry["route"]] = by_route.get(entry["route"], 0) + 1
    out: Dict[str, Any] = {"version": DOCPROC_VERSION, "sourceFormat": source_format(document)}
    language = _language(document)
    if language:
        out["documentLanguage"] = language
    out["pages"] = pages
    out["metadata"] = {"routing": {"pages": routing, "counts": {"by_route": by_route}}}
    validate(out)
    return out


def needs_atr(docproc: Dict[str, Any]) -> bool:
    """Whether any page goes to the ATR: this docproc is then not the stored one (H14)."""
    return any(entry.get("route") == ROUTE_ATR for entry in docproc["metadata"]["routing"]["pages"])


@lru_cache(maxsize=2)
def _validator(path: str):
    import jsonschema  # noqa: PLC0415  (requirements.txt; the gate never passes without it)

    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def problems(docproc: Dict[str, Any], schema: Path = PROPOSAL_SCHEMA) -> List[str]:
    """Every schema problem of `docproc` against `schema` (the proposal by default)."""
    return [
        f"{error.json_path}: {error.message}"
        for error in _validator(str(schema)).iter_errors(docproc)
    ]


def validate(docproc: Dict[str, Any]) -> None:
    """Raise `DocprocError` when `docproc` does not validate against the proposal schema."""
    found = problems(docproc)
    if found:
        raise DocprocError("the docproc does not validate: " + "; ".join(found[:5]))


__all__ = [
    "ACTUAL_SCHEMA",
    "DISTRIBUTION",
    "DOCPROC_VERSION",
    "PROPOSAL_SCHEMA",
    "ROUTE_ATR",
    "ROUTE_EMPTY",
    "ROUTE_TEXT_LAYER",
    "DocprocError",
    "build_docproc",
    "needs_atr",
    "problems",
    "route",
    "source_format",
    "validate",
]
