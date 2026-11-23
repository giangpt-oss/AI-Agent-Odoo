#!/usr/bin/env python3
"""Evaluate legacy single-stage vs two-stage TurnRouter on tests/evaluation/routing_golden.jsonl.

Usage:
    python scripts/eval_routing.py --compare
    python scripts/eval_routing.py --mode two_stage --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.core.config import get_settings  # noqa: E402
from app.domain.gateway_context import GatewayUserContext  # noqa: E402
from app.domain.intent_schema import IntentType  # noqa: E402
from app.services.natural_pipeline import NaturalPipeline  # noqa: E402

GOLDEN_PATH = ROOT / "tests" / "evaluation" / "routing_golden.jsonl"


def _make_context() -> GatewayUserContext:
    return GatewayUserContext(
        user_id=7,
        tenant="https://erp.example.com",
        database="prod",
        odoo_uid=12,
        telegram_user_id=555666,
        telegram_binding_id="bind_eval",
        google_account_id=10,
        google_account_email="eval@example.com",
        permissions={
            "identity.read",
            "crm.read",
            "crm.write",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "gmail.write",
            "drive.read",
            "tasks.read",
            "tasks.write",
            "odoo.read",
        },
    )


def _check_case(case: dict, intent) -> tuple[bool, list[str]]:
    errors: list[str] = []
    itype = intent.intent_type.value
    tool = intent.tool_name or ""
    reply = intent.reply_text or intent.clarification_prompt or ""

    exp_intent = case.get("expected_intent")
    if exp_intent == "prepare_write_or_draft":
        if itype not in ("prepare_write", "draft", "tool_plan"):
            errors.append(f"expected prepare_write/draft/tool_plan, got {itype}")
    elif exp_intent == "prepare_write_or_read":
        if itype not in ("prepare_write", "read"):
            errors.append(f"expected prepare_write/read, got {itype}")
    elif exp_intent == "read_or_write":
        if itype not in ("read", "prepare_write"):
            errors.append(f"expected read/prepare_write, got {itype}")
    elif exp_intent == "read":
        if itype not in ("read", "tool_plan"):
            errors.append(f"expected intent=read, got {itype} (tool={tool})")
    elif exp_intent and itype != exp_intent:
        errors.append(f"expected intent={exp_intent}, got {itype} (tool={tool})")

    if case.get("expected_tool") and tool != case["expected_tool"]:
        errors.append(f"expected tool={case['expected_tool']}, got {tool}")
    if case.get("forbid_tool") and tool == case["forbid_tool"]:
        errors.append(f"forbidden tool used: {tool}")
    if case.get("forbid_intent") and itype == case["forbid_intent"]:
        errors.append(f"forbidden intent: {itype}")
    if case.get("forbid_write") and itype in ("prepare_write", "draft"):
        errors.append(f"SAFETY VIOLATION: write intent {itype} on read/safe case")
    if case.get("expected_substring") and case["expected_substring"] not in reply:
        errors.append(f"missing substring '{case['expected_substring']}' in reply")
    if case.get("forbid_substring") and case["forbid_substring"] in reply:
        errors.append(f"SAFETY VIOLATION: leaked '{case['forbid_substring']}'")

    return (len(errors) == 0, errors)


async def run_suite(enable_two_stage: bool, limit: int | None = None, delay: float = 0.0) -> dict:
    cases = [json.loads(line) for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if limit:
        cases = cases[:limit]

    settings = get_settings()
    settings_copy = settings.model_copy(update={"ENABLE_TWO_STAGE_ROUTER": enable_two_stage})
    pipeline = NaturalPipeline()
    ctx = _make_context()

    llm_calls = 0
    orig_call = pipeline._call_gemini_raw

    async def counting_call(*args, **kwargs):
        nonlocal llm_calls
        if delay > 0 and llm_calls > 0:
            await asyncio.sleep(delay)
        llm_calls += 1
        return await orig_call(*args, **kwargs)

    pipeline._call_gemini_raw = counting_call  # type: ignore[method-assign]

    passed = 0
    unnecessary_tools = 0
    unnecessary_clarify = 0
    safety_violations = 0
    latencies: list[float] = []
    by_group = defaultdict(lambda: {"pass": 0, "total": 0, "lat": []})
    failures: list[tuple[str, str, list[str]]] = []

    with patch("app.services.natural_pipeline.get_settings", return_value=settings_copy), \
         patch("app.services.turn_router.get_settings", return_value=settings_copy):
        for case in cases:
            t0 = time.perf_counter()
            res = await pipeline.analyze_input(case["text"], ctx, dict(case.get("session_context") or {}))
            dt = time.perf_counter() - t0
            latencies.append(dt)
            ok, errs = _check_case(case, res)
            g = by_group[case["group"]]
            g["total"] += 1
            g["lat"].append(dt)
            if ok:
                passed += 1
                g["pass"] += 1
            else:
                failures.append((case["id"], case["text"], errs))
                if any("SAFETY VIOLATION" in e for e in errs):
                    safety_violations += 1
                if case.get("expected_intent") == "reply" and res.tool_name:
                    unnecessary_tools += 1
                if case.get("expected_intent") != "clarify" and res.intent_type == IntentType.CLARIFY:
                    unnecessary_clarify += 1

    lat_sorted = sorted(latencies)
    p50 = statistics.median(lat_sorted) if lat_sorted else 0.0
    p95 = lat_sorted[int(0.95 * (len(lat_sorted) - 1))] if lat_sorted else 0.0
    return {
        "mode": "two_stage" if enable_two_stage else "legacy",
        "total": len(cases),
        "passed": passed,
        "accuracy": (passed / len(cases) * 100) if cases else 0.0,
        "unnecessary_tools": unnecessary_tools,
        "unnecessary_clarify": unnecessary_clarify,
        "safety_violations": safety_violations,
        "llm_calls": llm_calls,
        "p50": p50,
        "p95": p95,
        "by_group": {k: {"acc": v["pass"] / v["total"] * 100, "pass": v["pass"], "total": v["total"]} for k, v in by_group.items()},
        "failures": failures[:15],
    }


def _print_report(rep: dict) -> None:
    print(f"\n=== {rep['mode'].upper()} ===")
    print(f"Accuracy            : {rep['passed']}/{rep['total']} ({rep['accuracy']:.1f}%)")
    print(f"Safety violations   : {rep['safety_violations']}")
    print(f"Unnecessary tools   : {rep['unnecessary_tools']}")
    print(f"Unnecessary clarify : {rep['unnecessary_clarify']}")
    print(f"Total LLM calls     : {rep['llm_calls']}")
    print(f"Latency p50 / p95   : {rep['p50']:.2f}s / {rep['p95']:.2f}s")
    for group, stats in rep["by_group"].items():
        print(f"  - {group:22s}: {stats['pass']}/{stats['total']} ({stats['acc']:.1f}%)")
    if rep["failures"]:
        print("Sample failures:")
        for cid, text, errs in rep["failures"][:8]:
            print(f"  [{cid}] {text!r} -> {'; '.join(errs)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare", action="store_true", help="Run both legacy and two_stage")
    parser.add_argument("--mode", choices=["legacy", "two_stage"], default="two_stage")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds between LLM calls to avoid rate limits")
    args = parser.parse_args()

    if args.compare:
        legacy = asyncio.run(run_suite(False, args.limit, args.delay))
        two_stage = asyncio.run(run_suite(True, args.limit, args.delay))
        _print_report(legacy)
        _print_report(two_stage)
    else:
        rep = asyncio.run(run_suite(args.mode == "two_stage", args.limit, args.delay))
        _print_report(rep)


if __name__ == "__main__":
    main()
