#!/usr/bin/env python3
"""tools/docproc_proposal.py — the docproc schema digital-convert writes against (atrium-project#84).

``schemas/docproc/document.actual.schema.json`` is the docproc schema as the ATR emits it (Ronald,
10 November 2026; #84 comment 1), kept verbatim. ``document.atrium-proposal.schema.json`` is the
same schema with two of the asks on #84 applied, and nothing else:

* **ask 3**: ``processedAt`` and ``pipeline`` are run data (they belong to the paradata), so they
  are no longer required;
* **ask 13**: other producers than the ATR: the ``pipeline`` pattern is opened, ``sourceFormat``
  gains the formats digital-convert reads (ODT, ODS, XLSX, RTF, DOC, XLS), a page carries the
  text-layer verdict and the routing decision (``textLayer``, ``needsAtr``, ``needsAtrReason``)
  and may carry ``userUnit``, a block may carry its ``lines[]`` (``text``, ``bbox``,
  ``bboxPrecision``, ``flags``), and a flow document (DOCX, ODT, a spreadsheet) has pages
  without a size and blocks without a box.

The proposal is a file, not a branch of code: it is what ARÚ/B and the ATR are asked to adopt
(#84 §7). ``apply_asks()`` builds it from the actual schema; ``tests/test_docproc_writer.py``
re-derives it and pins that the committed file is exactly that, so the two schemas never drift
apart silently. Regenerate after a change to either input::

    python tools/docproc_proposal.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

SCHEMAS = Path(__file__).resolve().parent.parent / "schemas" / "docproc"
ACTUAL = SCHEMAS / "document.actual.schema.json"
PROPOSAL = SCHEMAS / "document.atrium-proposal.schema.json"

PROPOSAL_ID = "https://github.com/ufal/atrium-digital-convert/schemas/docproc/document.atrium-proposal.schema.json"

#: ask 13: the formats digital-convert reads, beyond the ATR's.
FLOW_FORMATS = ("odt", "ods", "xlsx", "rtf", "doc", "xls")

#: ask 13: a producer id, `<producer>` or `<producer>/<variant>` (the ATR's `pipeline-e2e` fits).
PIPELINE_PATTERN = "^[a-z0-9][a-z0-9-]*(/[a-z0-9-]+)?$"


def apply_asks(actual: Dict[str, Any]) -> Dict[str, Any]:
    """The proposal: ``actual`` with asks 3 and 13 applied (see the module docstring)."""
    schema = copy.deepcopy(actual)
    schema["$id"] = PROPOSAL_ID
    schema["title"] = "docproc document v1.1 — ATRIUM proposal (atrium-project#84, asks 3 and 13)"
    schema["description"] = (
        actual["description"]
        + " ATRIUM proposal: the actual schema of 2026-11-10 with asks 3 (no run data required) and 13"
        " (other producers, formats, the page verdict, block lines, flow documents) applied."
    )
    props = schema["properties"]

    # ask 3: run data is the paradata's; neither is required any more.
    schema["required"] = [
        name for name in schema["required"] if name not in ("processedAt", "pipeline")
    ]

    # ask 13: other producers and formats.
    props["pipeline"]["pattern"] = PIPELINE_PATTERN
    props["pipeline"]["description"] += " ATRIUM proposal: any producer id, e.g. `digital-convert`."
    props["sourceFormat"]["enum"] = list(props["sourceFormat"]["enum"]) + list(FLOW_FORMATS)

    defs = schema["$defs"]
    page = defs["Page"]
    # ask 13: a flow document (DOCX, ODT, a spreadsheet) has no page geometry.
    page["required"] = [name for name in page["required"] if name not in ("width", "height")]
    page["properties"]["textLayer"] = {
        "description": (
            "ATRIUM proposal (ask 13): the page's text-layer verdict: `digital` (decodes), `garbled` (does not "
            "decode), `ocr` (a prior OCR run's invisible text), `none` (no text layer), `blank` (an empty page)."
        ),
        "type": "string",
        "enum": ["digital", "garbled", "ocr", "none", "blank"],
    }
    page["properties"]["needsAtr"] = {
        "description": "ATRIUM proposal (ask 13): the page's text cannot be used; it goes to the ATR.",
        "type": "boolean",
    }
    page["properties"]["needsAtrReason"] = {
        "description": "ATRIUM proposal (ask 13): why the page needs the ATR; present exactly when needsAtr is true.",
        "type": "string",
        "minLength": 1,
    }
    page["properties"]["userUnit"] = {
        "description": "ATRIUM proposal (asks 10, 13): the PDF page's UserUnit; coordinates are in 1/72 inch x userUnit.",
        "type": "number",
        "exclusiveMinimum": 0,
        "default": 1,
    }

    block = defs["Block"]
    # ask 13: a block of a flow document has no box.
    block["required"] = [name for name in block["required"] if name != "bbox"]
    block["properties"]["lines"] = {
        "description": (
            "ATRIUM proposal (asks 7, 13): the block's lines in reading order, so a consumer keeps the line "
            "breaks and the box of each line, and knows how the box was obtained."
        ),
        "type": "array",
        "items": {"$ref": "#/$defs/Line"},
    }
    defs["Line"] = {
        "type": "object",
        "description": "ATRIUM proposal (ask 13): one visual line of a block.",
        "required": ["text"],
        "additionalProperties": False,
        "properties": {
            "text": {"type": "string"},
            "bbox": {"$ref": "#/$defs/BBox"},
            "bboxPrecision": {
                "description": "`measured` for the line by its producer, `derived` from its block, `absent`.",
                "type": "string",
                "enum": ["measured", "derived", "absent"],
            },
            "language": {"$ref": "#/$defs/Language"},
            "flags": {
                "description": (
                    "Open vocabulary of advisory tags, as on blocks. digital-convert's decode verdicts: `Garbage` "
                    "(the text layer decodes to wrong characters), `Inverted` (mirrored or rotated text)."
                ),
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }
    return schema


def proposal() -> Dict[str, Any]:
    """The proposal derived from the committed actual schema."""
    return apply_asks(json.loads(ACTUAL.read_text(encoding="utf-8")))


def main() -> int:
    PROPOSAL.write_text(
        json.dumps(proposal(), ensure_ascii=False, indent=4) + "\n", encoding="utf-8"
    )
    print(f"wrote {PROPOSAL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
