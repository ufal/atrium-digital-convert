"""
service/describe.py — the per-page assessment and the Markdown view, shared by the service and the CLI.

`POST /describe` and `api_util/digital_to_json.py --describe` run the same code: the stage loop
that asks page-classification and ocr-postprocess (through `service/stages.py`), the guard that
adopts their record writes, and the report of `api_util/digital_report.py`. `POST /reformat`'s
`markdown=true` and the CLI's `--markdown` render the record through the same function. The
module imports no FastAPI, so the CLI image runs it as it is (atrium-project#72, the service–CLI
alignment).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from api_util.digital_ir import DigitalDocument
from api_util.digital_report import build_report, furniture_only
from atrium_document import validate_document
from tool_limits import ROUTE_DAMAGED_WORD_SHARE, ROUTE_TRASH_SHARE, STAGE_TIMEOUT_S

from . import stages


def render_markdown(record: Dict[str, Any]) -> str:
    """The record as annotated Markdown, the `full` profile of `api_util/json_to_md.py`.

    A document without text still gets its title line, so the answer is never empty.
    """
    from api_util import json_to_md  # noqa: PLC0415

    try:
        return json_to_md.render_record(record, title=str(record["doc_id"]))
    except ValueError:
        return f"# {record['doc_id']}\n"  # nothing to render: a document without text


def reader_info(document: DigitalDocument, record: Dict[str, Any]) -> Dict[str, Any]:
    """Which reader read the file: kind, media type, origin, engine, pages, any conversion."""
    source = record.get("source") or {}
    return {
        "kind": document.kind,
        "media_type": source.get("media_type") or document.media_type,
        "origin": source.get("origin") or document.origin,
        "engine": document.engine,
        "pages": len(document.pages),
        "conversion": document.conversion,
        "notes": list(document.notes),
    }


def validates(record: Dict[str, Any]) -> bool:
    try:
        validate_document(record)
    except Exception:
        return False
    return True


def run_stages(
    document: DigitalDocument,
    record: Dict[str, Any],
    data: bytes,
    name: str,
    selected: List[str],
    classify_pages: str,
    timeout: Optional[float] = None,
) -> Tuple[
    Dict[str, Any], List[stages.StageOutcome], Dict[int, Dict[str, Any]], Dict[int, Dict[str, Any]]
]:
    """Call the selected, configured stages in order; adopt each guarded record write."""
    timeout = float(STAGE_TIMEOUT_S.get()) if timeout is None else timeout
    current = record
    outcomes: List[stages.StageOutcome] = []
    categories: Dict[int, Dict[str, Any]] = {}
    quality: Dict[int, Dict[str, Any]] = {}
    for stage in stages.STAGES:
        base = stages.stage_url(stage)
        if stage not in selected:
            outcomes.append(
                stages.StageOutcome(stage=stage, status=stages.STATUS_NOT_REQUESTED, url=base)
            )
            continue
        if base is None:
            outcomes.append(
                stages.StageOutcome(
                    stage=stage,
                    status=stages.STATUS_NOT_CONFIGURED,
                    detail=f"{stages.URL_ENV[stage]} is not set",
                )
            )
            continue
        if stage == stages.PAGE_CLASSIFICATION:
            if document.kind != "pdf":
                outcomes.append(
                    stages.StageOutcome(
                        stage=stage,
                        status=stages.STATUS_SKIPPED,
                        url=base,
                        detail=f"page-classification reads the page images of a PDF; a {document.kind.upper()} has none",
                    )
                )
                continue
            # A page whose only text is its running header or footer is asked about too: its page
            # type says whether its image holds text the text layer lacks (#5, plans with legends).
            wanted = [
                page.page_index
                for page in document.pages
                if classify_pages == "all" or page.needs_ocr or furniture_only(page)
            ]
            if not wanted:
                outcomes.append(
                    stages.StageOutcome(
                        stage=stage,
                        status=stages.STATUS_SKIPPED,
                        url=base,
                        detail="every page has usable body text (send classify_pages=all to classify them anyway)",
                    )
                )
                continue
            result = stages.run_page_classification(base, data, name, current, wanted, timeout)
            categories = result.per_page
        else:
            lines = {
                page.page_index: (page.page, [line.text for line in page.lines])
                for page in document.pages
                if page.lines
            }
            if not lines:
                outcomes.append(
                    stages.StageOutcome(
                        stage=stage,
                        status=stages.STATUS_SKIPPED,
                        url=base,
                        detail="the document has no lines to score",
                    )
                )
                continue
            result = stages.run_ocr_postprocess(base, current, lines, timeout)
            quality = result.per_page
        adopted = stages.adopt(result, current)
        if adopted is not current and not validates(adopted):
            result.outcome.status = stages.STATUS_REJECTED
            result.outcome.record_adopted = False
            result.outcome.reason = "schema"
            result.outcome.detail = "the returned record does not validate against the schema; the record was kept as it was"
            adopted = current
        current = adopted
        outcomes.append(result.outcome)
    return current, outcomes, categories, quality


def describe_document(
    document: DigitalDocument,
    record: Dict[str, Any],
    data: bytes,
    name: str,
    selected: List[str],
    classify_pages: str = "needs_ocr",
    include_text: bool = True,
) -> Dict[str, Any]:
    """The assessment of `/describe`: the record as the stages left it, `pages`, `summary`, `stages`."""
    current, outcomes, categories, quality = run_stages(
        document, record, data, name, selected, classify_pages
    )
    report = build_report(
        document,
        categories=categories,
        quality=quality,
        trash_share_limit=ROUTE_TRASH_SHARE.get(),
        include_text=include_text,
        damaged_word_limit=ROUTE_DAMAGED_WORD_SHARE.get(),
    )
    return {
        "document_json": current,
        "pages": report["pages"],
        "summary": report["summary"],
        "stages": [outcome.to_dict() for outcome in outcomes],
    }
