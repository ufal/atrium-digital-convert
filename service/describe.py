"""
service/describe.py — the per-page assessment and the Markdown view, shared by the service and the CLI.

`POST /describe` and `api_util/digital_to_json.py --describe` run the same code: the report of
`api_util/digital_report.py` over what the converter read. `POST /reformat`'s `markdown=true` and
the CLI's `--markdown` render the development dict through the same function. The module imports
no FastAPI, so the CLI image runs it as it is (atrium-project#72).

Since atrium-project#84 digital-convert writes docproc, not a record, and `/describe` is parked
(not part of the pilot, H11): it works on the converter's development dict
(`digital_to_json.development_dict()`), record-shaped but no record. Since v2.0.0-beta it calls no
other tool: the assessment rests on what the converter itself read from the file. A page's type
and the quality of its text come from page-classification and ocr-postprocess, which the
pipeline runs as its own steps.
"""

from __future__ import annotations

from typing import Any, Dict

from api_util.digital_ir import DigitalDocument
from api_util.digital_report import build_report


def render_markdown(record: Dict[str, Any]) -> str:
    """The development dict as annotated Markdown, the `full` profile of `api_util/json_to_md.py`.

    A document without text still gets its title line, so the answer is never empty.
    """
    from api_util import json_to_md  # noqa: PLC0415

    try:
        return json_to_md.render_record(record, title=str(record["doc_id"]))
    except ValueError:
        return f"# {record['doc_id']}\n"  # nothing to render: a document without text


def reader_info(document: DigitalDocument, record: Dict[str, Any]) -> Dict[str, Any]:
    """Which reader read the file: kind, workflow, media type, origin, engine, pages, any conversion
    or rendering (atrium-digital-convert#9)."""
    source = record.get("source") or {}
    return {
        "kind": document.kind,
        "media_type": source.get("media_type") or document.media_type,
        "origin": source.get("origin") or document.origin,
        "engine": document.engine,
        "pages": len(document.pages),
        "workflow": document.workflow,
        "conversion": document.conversion,
        "rendering": document.rendering,
        "notes": list(document.notes),
    }


def describe_document(
    document: DigitalDocument, record: Dict[str, Any], include_text: bool = True
) -> Dict[str, Any]:
    """The assessment of `/describe`: the development dict (`document`), `pages`, `summary`."""
    report = build_report(document, include_text=include_text)
    return {"document": record, "pages": report["pages"], "summary": report["summary"]}
