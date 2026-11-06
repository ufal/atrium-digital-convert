"""tests/test_document_types.py — the document-type list v0 for decision models (#8; atrium-project#83).

The committed ``decisions/document_type.v0.json`` is checked on its own, and, when an
atrium-keyword-extract checkout sits next to this repository, against what
``tools/build_document_types.py`` generates from that checkout's vocabulary. That check is skipped
when the checkout is absent (CI), like the parity tests of the vendored files.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIST = REPO_ROOT / "decisions" / "document_type.v0.json"
SIBLING_VOCAB = (
    REPO_ROOT.parent / "atrium-keyword-extract" / "data_samples" / "vocab" / "amcr_flat.json"
)


def _builder():
    spec = importlib.util.spec_from_file_location(
        "build_document_types", REPO_ROOT / "tools" / "build_document_types.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def doc_types():
    return json.loads(LIST.read_text(encoding="utf-8"))


def test_the_list_has_32_types_in_6_groups(doc_types):
    assert len(doc_types["types"]) == 32
    assert list(doc_types["groups"]) == [
        "text",
        "photograph",
        "aerial_photograph",
        "plan_or_map",
        "drawing",
        "unspecified",
    ]
    assert len(doc_types["excluded"]) == 9
    assert doc_types["source"]["terms_in_scheme"] == 41
    assert len({t["id"] for t in doc_types["types"]}) == 32
    assert len({t["hes"] for t in doc_types["types"]}) == 32


def test_every_type_keeps_its_amcr_identity(doc_types):
    for t in doc_types["types"]:
        assert re.fullmatch(r"HES-\d{6}", t["hes"])
        assert t["uri"] == f"https://api.aiscr.cz/id/{t['hes']}"
        assert t["cs"] and t["en"]
        assert t["group"] in doc_types["groups"]
        assert re.fullmatch(r"[a-z][a-z0-9_]*", t["id"])


def test_the_abstain_option_is_the_unspecified_document(doc_types):
    (abstain,) = [t for t in doc_types["types"] if t["id"] == doc_types["abstain"]]
    assert abstain["hes"] == "HES-001112" and abstain["group"] == "unspecified"


def test_the_data_only_types_are_out(doc_types):
    kept = {t["hes"] for t in doc_types["types"]}
    assert all(e["hes"] not in kept for e in doc_types["excluded"])
    assert {e["hes"] for e in doc_types["excluded"]} >= {
        "HES-001113",
        "HES-001114",
        "HES-001115",
    }  # the 3D types


def test_the_questions_match_the_list_and_their_digest(doc_types):
    questions = doc_types["questions"]
    assert list(questions["group"]["criteria"]) == list(doc_types["groups"])
    assert list(questions["document_type"]["criteria"]) == [t["id"] for t in doc_types["types"]]
    for question in questions.values():
        assert question["type"] == "choice" and 2 <= len(question["criteria"]) <= 255
    assert doc_types["schema_digest"] == _builder().schema_digest(questions)


@pytest.mark.skipif(
    not SIBLING_VOCAB.is_file(), reason="no atrium-keyword-extract checkout beside this repository"
)
def test_the_committed_list_is_what_the_vocabulary_gives(doc_types):
    built = _builder().build(SIBLING_VOCAB, source_commit=doc_types["source"]["commit"])
    if built["source"]["sha256"] != doc_types["source"]["sha256"]:
        pytest.skip(
            "the sibling vocabulary is another version than the one the list was built from"
        )
    assert built == doc_types


def test_the_builder_refuses_an_unmapped_type(tmp_path):
    builder = _builder()
    data = (
        json.loads(SIBLING_VOCAB.read_text(encoding="utf-8")) if SIBLING_VOCAB.is_file() else None
    )
    if data is None:
        pytest.skip("no atrium-keyword-extract checkout beside this repository")
    terms = data["terms"]
    terms = list(terms.values()) if isinstance(terms, dict) else terms
    terms.append(
        {
            "scheme": "dokument_typ",
            "source_id": "HES-009999",
            "cs": "nový typ",
            "en": "new type",
            "uri": "https://api.aiscr.cz/id/HES-009999",
            "broader": ["HES-000230"],
            "sort": 99,
        }
    )
    data["terms"] = terms
    vocab = tmp_path / "amcr_flat.json"
    vocab.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(SystemExit, match="unmapped"):
        builder.build(vocab, source_commit="test")
