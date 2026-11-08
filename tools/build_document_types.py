#!/usr/bin/env python3
"""tools/build_document_types.py — the shared document-type list for decision models (#8; ufal/atrium-project#83).

A decision model gives each PDF page, and each document, a type chosen from a fixed list. That list
is AMČR's heslář ``dokument_typ`` (41 terms). This script generates it, so it is never written by
hand. The list is shared with atrium-keyword-extract (its #3), which pins a copy of the output
(``tests/test_vendored_doctype_parity.py`` there). The source is atrium-keyword-extract's vocabulary
build, ``data_samples/vocab/amcr_flat.json``, harvested from AMČR's OAI-PMH endpoint, CC0.

* **The 9 data-only types** are left out: those whose ``broader`` is ``HES-000217`` ("digitální
  soubor"), i.e. the ecofact-analysis, geodetic, geophysical, GNSS, fieldwork and vector-plan data and
  the three 3D types. A PDF page is none of them. The script fails unless exactly these 9 drop out.
* **The other 32** keep their ``api.aiscr.cz`` URI and Czech and English labels. Each gets an English
  slug and one of 6 coarse groups, from the table below (the #8 plan). A term the table does not map
  is an error: when AMČR adds a type, it is placed here on purpose.
* ``neurčený dokument`` (``unspecified``) is the abstain option.

The output, ``decisions/document_type.v2.json``, records where it came from (the source repository's
commit and the file's SHA-256). Its ``views`` hold the questions in the ``/v1/systemone`` wire format,
one set per input, each with its digest, computed exactly as ``decision_client.schema_digest``
computes it (``VIEWS``):

* ``pdf_page`` (digital-convert, ``tools/decide_documents.py``): the rendered page image, always; its
  text layer only on request. ``group`` (6) and ``document_type`` (32 types and the abstain option).
* ``document_text`` (keyword-extract, ``dm_run.py --doc-type``): the document's OCR text, never an
  image. Photographs, aerial photographs, plans, maps and drawings cannot be told from OCR text, so it
  asks ``document_type`` over the ``text`` group only (6 types and the abstain option), with no
  ``group`` question.

The questions name no input ("this document"); the view's ``item`` says what it is, in the request's
state. v2 (2026-11-08) adds the views; v1 had one question set for both tools, and v0 was never run.
The list is a draft until AMČR agrees to it (ufal/atrium-project#83); nothing reaches the record
before then.

    python3 tools/build_document_types.py --vocab ../atrium-keyword-extract/data_samples/vocab/amcr_flat.json
    python3 tools/build_document_types.py --vocab <file> --check     # exit 1 if the committed list differs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "decisions" / "document_type.v2.json"
VERSION = 2
SHARED_WITH = ("ufal/atrium-keyword-extract",)
DEFAULT_VOCAB = (
    REPO_ROOT.parent / "atrium-keyword-extract" / "data_samples" / "vocab" / "amcr_flat.json"
)

SCHEME = "dokument_typ"
DATA_ONLY_BROADER = "HES-000217"  # digitální soubor
EXPECTED_DATA_ONLY = 9
ABSTAIN = "unspecified"

GROUPS: Dict[str, Tuple[str, str]] = {
    "text": (
        "text",
        "a written document: a report, its annex, an expert or client report, a brief report or other text",
    ),
    "photograph": (
        "photograph",
        "a photograph taken on the ground: landscape, site, trench, feature, object, people",
    ),
    "aerial_photograph": (
        "aerial photograph",
        "a photograph taken from the air, of a landscape, site or crop marks",
    ),
    "plan_or_map": (
        "plan or map",
        "a plan of a region, site, excavation, trench or feature, or a map",
    ),
    "drawing": ("drawing", "a drawing of an object"),
    "unspecified": ("unspecified", "none of the above can be told"),
}

#: HES number -> (group, option id): the 32 types a PDF page can be (#8 plan, Phase A).
TYPE_MAP: Dict[str, Tuple[str, str]] = {
    "HES-001075": ("text", "fieldwork_report"),
    "HES-001076": ("text", "report_annex"),
    "HES-001077": ("text", "expert_report"),
    "HES-001079": ("text", "client_report"),
    "HES-001080": ("text", "brief_report"),
    "HES-001081": ("text", "other_text"),
    "HES-001088": ("photograph", "photo_landscape"),
    "HES-001089": ("photograph", "photo_site"),
    "HES-001090": ("photograph", "photo_excavation"),
    "HES-001091": ("photograph", "photo_person"),
    "HES-001092": ("photograph", "photo_event"),
    "HES-001093": ("photograph", "photo_fieldwork"),
    "HES-001094": ("photograph", "photo_feature"),
    "HES-001095": ("photograph", "photo_object"),
    "HES-001096": ("photograph", "microphotograph"),
    "HES-001097": ("photograph", "photo_conservation"),
    "HES-001098": ("aerial_photograph", "aerial_landscape"),
    "HES-001099": ("aerial_photograph", "aerial_site"),
    "HES-001100": ("aerial_photograph", "aerial_fieldwork"),
    "HES-001102": ("aerial_photograph", "aerial_vegetation_marks"),
    "HES-001103": ("aerial_photograph", "aerial_soil_marks"),
    "HES-001104": ("aerial_photograph", "aerial_snow_marks"),
    "HES-001105": ("aerial_photograph", "aerial_shadow_marks"),
    "HES-001116": ("aerial_photograph", "aerial_method"),
    "HES-001101": ("plan_or_map", "plan_region"),
    "HES-001106": ("plan_or_map", "plan_site"),
    "HES-001107": ("plan_or_map", "plan_fieldwork"),
    "HES-001108": ("plan_or_map", "plan_trench"),
    "HES-001109": ("plan_or_map", "plan_feature"),
    "HES-001111": ("plan_or_map", "map"),
    "HES-001110": ("drawing", "object_drawing"),
    "HES-001112": ("unspecified", ABSTAIN),
}

GROUP_INSTRUCTIONS = "What kind of document is this, broadly?"
TYPE_INSTRUCTIONS = "Which AMČR document type is this?"

#: The question sets, one per input. ``groups``: the type groups a view offers (``None``: all), the
#: abstain option always included; ``ask_group``: whether the coarse ``group`` question is asked too.
VIEWS: Dict[str, Dict[str, Any]] = {
    "pdf_page": {
        "used_by": "ufal/atrium-digital-convert (#8, tools/decide_documents.py)",
        "inputs": {
            "image": "the rendered page, always",
            "text": "the page's text layer, only on request (--with-text); off by default",
        },
        "item": "the attached image of one page of a PDF from an archaeological archive",
        "groups": None,
        "ask_group": True,
    },
    "document_text": {
        "used_by": "ufal/atrium-keyword-extract (#3, dm_run.py --doc-type)",
        "inputs": {
            "image": "none",
            "text": "the document's OCR text (its first lines, in order), always",
        },
        "item": "the OCR text of one archaeological archive document; `text` holds its first lines, in order",
        "groups": ("text",),
        "ask_group": False,
    },
}


def schema_digest(questions: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of a question set (the algorithm of ``decision_client.schema_digest``)."""
    canonical = json.dumps(questions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_commit(path: Path) -> str:
    """The commit of the repository holding *path*, with ``+dirty`` when the file differs from it."""
    try:
        head = subprocess.run(
            ["git", "-C", str(path.parent), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(path.parent), "status", "--porcelain", "--", path.name],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return head + ("+dirty" if dirty else "")


def load_scheme(vocab: Path) -> List[Dict[str, Any]]:
    data = json.loads(vocab.read_text(encoding="utf-8"))
    terms = data["terms"]
    terms = list(terms.values()) if isinstance(terms, dict) else terms
    scheme = [t for t in terms if t.get("scheme") == SCHEME]
    return sorted(scheme, key=lambda t: (t.get("sort") or 0, t["source_id"]))


def view_options(
    types: Sequence[Mapping[str, Any]], groups: Optional[Sequence[str]] = None
) -> List[str]:
    """The type ids a view offers, in list order: those of *groups* (all when ``None``) and the abstain option."""
    return [t["id"] for t in types if groups is None or t["group"] in groups or t["id"] == ABSTAIN]


def view_questions(types: Sequence[Mapping[str, Any]], view: Mapping[str, Any]) -> Dict[str, Any]:
    """The questions of one view. ``document_type``'s criteria are the same text in every view."""
    questions: Dict[str, Any] = {}
    if view["ask_group"]:
        questions["group"] = {
            "type": "choice",
            "instructions": GROUP_INSTRUCTIONS,
            "criteria": {gid: f"{label}: {text}" for gid, (label, text) in GROUPS.items()},
        }
    by_id = {t["id"]: t for t in types}
    questions["document_type"] = {
        "type": "choice",
        "instructions": TYPE_INSTRUCTIONS,
        "criteria": {
            tid: f"{by_id[tid]['en']} ({by_id[tid]['cs']})"
            for tid in view_options(types, view["groups"])
        },
    }
    return questions


def build(vocab: Path, source_commit: Optional[str] = None) -> Dict[str, Any]:
    """The list as a JSON-ready dict; raises ``SystemExit`` when the vocabulary and the map disagree."""
    scheme = load_scheme(vocab)
    if not scheme:
        raise SystemExit(f"{vocab}: no terms of scheme {SCHEME!r}")
    excluded = [t for t in scheme if (t.get("broader") or [None])[0] == DATA_ONLY_BROADER]
    if len(excluded) != EXPECTED_DATA_ONLY:
        raise SystemExit(
            f"{len(excluded)} data-only terms (broader {DATA_ONLY_BROADER}), expected {EXPECTED_DATA_ONLY}"
        )
    kept = [t for t in scheme if t not in excluded]
    unmapped = sorted(t["source_id"] for t in kept if t["source_id"] not in TYPE_MAP)
    stale = sorted(set(TYPE_MAP) - {t["source_id"] for t in kept})
    if unmapped or stale:
        raise SystemExit(
            f"the type map and the vocabulary disagree: unmapped {unmapped}, not in the vocabulary {stale}"
        )

    types = []
    for t in kept:
        group, option = TYPE_MAP[t["source_id"]]
        types.append(
            {
                "id": option,
                "group": group,
                "hes": t["source_id"],
                "uri": t["uri"],
                "cs": t["cs"],
                "en": t["en"],
            }
        )
    views = {}
    for name, view in VIEWS.items():
        questions = view_questions(types, view)
        views[name] = {
            "used_by": view["used_by"],
            "inputs": view["inputs"],
            "item": view["item"],
            "questions": questions,
            "schema_digest": schema_digest(questions),
        }
    return {
        "name": "document_type",
        "version": VERSION,
        "status": "draft: AMČR's agreement to the list is pending (ufal/atrium-project#83)",
        "generated_by": "tools/build_document_types.py",
        "shared_with": list(SHARED_WITH),
        "source": {
            "repository": "ufal/atrium-keyword-extract",
            "path": "data_samples/vocab/amcr_flat.json",
            "commit": source_commit or _source_commit(vocab),
            "sha256": hashlib.sha256(vocab.read_bytes()).hexdigest(),
            "scheme": SCHEME,
            "terms_in_scheme": len(scheme),
            "license": "CC0 (AMČR heslář, OAI-PMH)",
        },
        "excluded": [
            {
                "hes": t["source_id"],
                "cs": t["cs"],
                "en": t["en"],
                "reason": f"data only (broader {DATA_ONLY_BROADER})",
            }
            for t in excluded
        ],
        "groups": {
            gid: {"label": label, "description": text} for gid, (label, text) in GROUPS.items()
        },
        "types": types,
        "abstain": ABSTAIN,
        "views": views,
    }


def render(content: Mapping[str, Any]) -> str:
    return json.dumps(content, indent=2, ensure_ascii=False) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate decisions/document_type.v2.json (#8).")
    parser.add_argument(
        "--vocab", type=Path, default=DEFAULT_VOCAB, help="atrium-keyword-extract's amcr_flat.json"
    )
    parser.add_argument(
        "--source-commit", help="the vocabulary's commit, when --vocab is not in a git checkout"
    )
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument(
        "--check", action="store_true", help="compare with --out instead of writing it"
    )
    args = parser.parse_args(argv)
    if not args.vocab.is_file():
        parser.error(f"{args.vocab} not found; pass --vocab")
    content = build(args.vocab, args.source_commit)
    if args.check:
        committed = json.loads(args.out.read_text(encoding="utf-8"))
        same = {k: v for k, v in committed.items() if k != "source"} == {
            k: v for k, v in content.items() if k != "source"
        }
        same = same and committed["source"]["sha256"] == content["source"]["sha256"]
        print(
            "up to date"
            if same
            else f"{args.out} differs from what {args.vocab} gives; regenerate it"
        )
        return 0 if same else 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(content), encoding="utf-8")
    print(
        f"wrote {args.out}: {len(content['types'])} types in {len(content['groups'])} groups, "
        f"{len(content['excluded'])} left out; "
        + ", ".join(
            f"{name} {view['schema_digest'][:12]}" for name, view in content["views"].items()
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
