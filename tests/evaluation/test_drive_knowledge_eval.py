"""Validate the Vietnamese corpus and grade observations from a deployed test run.

Run each case through the Agent against the supplied synthetic corpus. Record
Drive calls at Gateway, top chunks before synthesis, and human citation review.
Map uploaded Drive IDs and section locators back to the fixture labels.

Run this file with the observations JSON path to print separate quality metrics.
Each observation has case_id and the fields for its category: top_chunks and
citations_supported for evidence, reported_insufficient for missing, and
drive_calls for greetings. The review checks support for every Drive claim.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


FIXTURE = Path(__file__).with_name("drive_knowledge_60q.json")
CORPUS = json.loads(FIXTURE.read_text(encoding="utf-8"))
CASES = CORPUS["cases"]


def test_vietnamese_fixture_has_complete_source_labels():
    assert Counter(case["category"] for case in CASES) == {
        "evidence": 30, "missing": 20, "greeting": 10,
    }
    assert len({case["id"] for case in CASES}) == 60
    sections = {
        (document["file_id"], section["label"])
        for document in CORPUS["documents"] for section in document["sections"]
    }
    for case in CASES:
        if case["category"] == "evidence":
            source = case["expected_source"]
            assert (source["file_id"], source["page_or_section"]) in sections


def test_drive_follow_up_uses_user_reference_from_fixture_only():
    from app.knowledge.drive import resolve_drive_follow_up

    case = next(case for case in CASES if case["id"] == "e02")
    history = case["history"] + [{"role": "assistant", "content": "Mã ZX99."}]
    assert resolve_drive_follow_up(case["question"], history) == f"PN22 — {case['question']}"
    ambiguous = [{"role": "user", "content": "Tìm PN22 và ZX99."}]
    assert resolve_drive_follow_up(case["question"], ambiguous) == case["question"]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [case for case in CASES if case["category"] == "greeting"],
                         ids=lambda case: case["id"])
async def test_greetings_do_not_read_or_queue_drive(case, monkeypatch):
    from app.core.config import get_settings
    from app.domain.gateway_context import GatewayUserContext
    from app.services.gateway_client import gateway_client
    from app.services.natural_pipeline import NaturalPipeline
    from app.services.turn_trace import request_id
    from app.services.unified_executor import UnifiedExecutor

    monkeypatch.setattr(get_settings(), "WORKSPACE_BACKGROUND_READS", True)
    monkeypatch.setattr(get_settings(), "DRIVE_INDEX_ENABLED", True)
    reads = AsyncMock(side_effect=AssertionError("Greeting accessed Drive"))
    for method in ("drive_search", "drive_knowledge", "create_workspace_job"):
        monkeypatch.setattr(gateway_client, method, reads)
    context = GatewayUserContext(
        user_id=1, tenant="evaluation", database="test", odoo_uid=1,
        telegram_user_id=7, chat_id=7, telegram_binding_id="evaluation",
        permissions={"workspace.drive.read"},
    )
    token = request_id.set(case["id"])
    try:
        intent = await NaturalPipeline().analyze_input(case["question"], context, case.get("history"))
        reply = await UnifiedExecutor().execute(intent, context, 7, 7, {}, 9999999999, {})
    finally:
        request_id.reset(token)
    assert "Xin chào" in reply[0]
    reads.assert_not_awaited()


def score_observations(observations):
    by_id = {row["case_id"]: row for row in observations}
    expected_ids = {case["id"] for case in CASES}
    if len(observations) != len(by_id) or set(by_id) != expected_ids:
        raise ValueError("Expected exactly one observation per fixture case")
    totals = Counter()
    for case in CASES:
        observation = by_id[case["id"]]
        if case["category"] == "evidence":
            source = case["expected_source"]
            chunks = observation["top_chunks"][:8]
            totals["document_hits_at_8"] += any(chunk["file_id"] == source["file_id"] for chunk in chunks)
            totals["section_hits_at_8"] += any(
                chunk["file_id"] == source["file_id"] and chunk.get("page_or_section") == source["page_or_section"]
                for chunk in chunks
            )
            totals["answers_with_supported_citations"] += observation["citations_supported"] is True
        elif case["category"] == "missing":
            totals["answers_reporting_insufficient_evidence"] += observation["reported_insufficient"] is True
        else:
            calls = observation["drive_calls"]
            if type(calls) is not int or calls < 0:
                raise ValueError("drive_calls must be a nonnegative integer")
            totals["greetings_with_zero_drive_calls"] += calls == 0
    return {
        "document_recall_at_8": totals["document_hits_at_8"] / 30,
        "section_recall_at_8": totals["section_hits_at_8"] / 30,
        "supported_citation_rate": totals["answers_with_supported_citations"] / 30,
        "insufficient_evidence_rate": totals["answers_reporting_insufficient_evidence"] / 20,
        "greeting_zero_read_rate": totals["greetings_with_zero_drive_calls"] / 10,
        "minimum_document_recall_met": totals["document_hits_at_8"] >= 27,
        "zero_greeting_reads_met": totals["greetings_with_zero_drive_calls"] == 10,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("observations", type=Path)
    args = parser.parse_args()
    report = score_observations(json.loads(args.observations.read_text(encoding="utf-8")))
    print(json.dumps(report, indent=2))
