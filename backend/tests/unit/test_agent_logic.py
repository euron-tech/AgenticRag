"""Agent routing, loop caps, and citation mapping. The LLM is never called."""

from __future__ import annotations

from app.agent.graph import _after_grade, _after_route
from app.agent.nodes import _citations_for
from app.core.config import settings
from app.retrieval.hybrid import RetrievedChunk


def chunk(chunk_id: str, filename: str = "policy.pdf") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id="doc-1",
        filename=filename,
        chunk_index=0,
        content="Employees may carry over five days of leave.",
        location="Page 4",
        score=0.9,
        metadata={"location": "Page 4"},
    )


# ------------------------------------------------------------------ routing
def test_document_questions_go_through_retrieval():
    assert _after_route({"route": "document_qa"}) == "rewrite"
    assert _after_route({"route": "summarize"}) == "rewrite"


def test_conversational_routes_answer_without_searching():
    assert _after_route({"route": "chitchat"}) == "chitchat"
    assert _after_route({"route": "catalog"}) == "catalog"
    assert _after_route({"route": "out_of_scope"}) == "out_of_scope"


def test_unknown_route_defaults_to_searching():
    """Searching and finding nothing is cheap; refusing a real question is not."""
    assert _after_route({}) == "rewrite"


# -------------------------------------------------------------- grade loop
def test_sufficient_sources_go_straight_to_generation():
    assert _after_grade({"sufficient": True, "attempts": 0}) == "generate"


def test_weak_sources_trigger_a_broader_retry():
    assert _after_grade({"sufficient": False, "attempts": 1}) == "rewrite"


def test_the_refine_loop_is_capped():
    """An uncapped loop in an agent is an outage waiting for an odd question."""
    over_cap = settings.max_refine_loops + 1
    assert _after_grade({"sufficient": False, "attempts": over_cap}) == "no_answer"


# --------------------------------------------------------------- citations
def test_only_markers_the_model_actually_used_become_citations():
    chunks = [chunk("c1"), chunk("c2"), chunk("c3")]
    citations = _citations_for("Carry-over is five days [2].", chunks)
    assert [c["marker"] for c in citations] == [2]
    assert citations[0]["chunk_id"] == "c2"


def test_citations_map_to_real_retrieved_chunks_only():
    """A hallucinated [9] must not produce a citation out of thin air."""
    citations = _citations_for("Some claim [9].", [chunk("c1")])
    assert citations == []


def test_multiple_markers_are_deduplicated_and_ordered():
    chunks = [chunk("c1"), chunk("c2")]
    citations = _citations_for("A [2][1]. B [1].", chunks)
    assert [c["marker"] for c in citations] == [1, 2]


def test_an_answer_with_no_markers_yields_no_citations():
    assert _citations_for("I could not find this in the documents.", [chunk("c1")]) == []


def test_citation_carries_the_location_a_reader_can_verify():
    citation = _citations_for("Claim [1].", [chunk("c1")])[0]
    assert citation["location"] == "Page 4"
    assert citation["filename"] == "policy.pdf"
    assert citation["snippet"]
