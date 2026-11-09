"""tests/test_document_types.py — the document-type list for decision models (#8; atrium-project#83).

The list is a form list, asked from page images only (atrium-project#83: image tools decide form,
text tools content). The committed ``decisions/document_type.v3.json`` is checked on its own, and, when an
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
LIST = REPO_ROOT / "decisions" / "document_type.v3.json"
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


def test_each_view_matches_the_list_and_its_digest(doc_types):
    builder = _builder()
    for name, view in doc_types["views"].items():
        questions = view["questions"]
        assert list(questions) == ["group", view["type_question"]], name
        for question in questions.values():
            assert question["type"] == "choice" and 2 <= len(question["criteria"]) <= 255
        assert view["schema_digest"] == builder.schema_digest(questions), name
        assert list(questions["group"]["criteria"]) == list(doc_types["groups"])
    document = doc_types["views"]["pdf_document"]["questions"]
    assert list(document["document_type"]["criteria"]) == [t["id"] for t in doc_types["types"]]


def test_every_view_is_image_only(doc_types):
    views = doc_types["views"]
    assert set(views) == {"pdf_page", "pdf_document"}
    assert doc_types["version"] == 3 and doc_types["shared_with"] == []
    assert doc_types["scope"].startswith("form:") and "never asked from text" in doc_types["scope"]
    for name, view in views.items():
        assert view["inputs"]["text"] == "none", name
        assert view["inputs"]["image"].startswith("the rendered") and "image" in view["item"]


def test_a_page_is_asked_its_picture_type_and_a_document_its_full_type(doc_types):
    page = doc_types["views"]["pdf_page"]
    document = doc_types["views"]["pdf_document"]
    assert page["type_question"] == "image_type" and document["type_question"] == "document_type"
    pictures = [t["id"] for t in doc_types["types"] if t["group"] in doc_types["picture_groups"]]
    offered = list(page["questions"]["image_type"]["criteria"])
    assert offered == pictures + [doc_types["abstain"]] and len(offered) == 26
    written = {t["id"] for t in doc_types["types"] if t["group"] == "text"}
    assert len(written) == 6 and not written & set(offered)
    assert written <= set(document["questions"]["document_type"]["criteria"])
    # a type reads the same in both views
    page_criteria = page["questions"]["image_type"]["criteria"]
    document_criteria = document["questions"]["document_type"]["criteria"]
    assert all(page_criteria[k] == document_criteria[k] for k in offered)
    # the document view asks what v2's page view asked, so its digest is unchanged
    assert document["schema_digest"].startswith("6c5e8abb")


def test_the_questions_name_no_input(doc_types):
    for view in doc_types["views"].values():
        for question in view["questions"].values():
            assert (
                "page" not in question["instructions"]
                and "attached" not in question["instructions"]
            )
    groups = doc_types["views"]["pdf_page"]["questions"]["group"]["criteria"]
    assert all("page" not in text for text in groups.values())


def test_only_one_version_of_the_list_is_committed():
    assert sorted(p.name for p in LIST.parent.glob("document_type.v*.json")) == [LIST.name]


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
