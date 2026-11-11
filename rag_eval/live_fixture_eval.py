"""Run real Gemini only on the fixed, synthetic corpus from offline_ablation."""
import argparse
import asyncio
import json
import os
import statistics
import tempfile
import time

from offline_ablation import CASES, CORPUS, ROOT, version

EXPECTED_FACTS = [
    ("08:30", "17:30"), ("08:30", "17:30"), ("3 ngày",),
    ("24 tháng",), ("12 tháng",), ("15 bộ",), ("30 ngày", "tem"), (),
]
CONFLICT_IDS = ["conflict-a", "conflict-b"]
STAGING_CORPUS = {**CORPUS,
    "conflict-a": "Quy định QX-774. Hạn nộp phiếu là 5 ngày. Không ghi phiên bản.",
    "conflict-b": "Quy định QX-774. Hạn nộp phiếu là 10 ngày. Không ghi phiên bản.",
}
STAGING_CASES = [*CASES, ("Hạn nộp phiếu theo QX-774 là bao nhiêu ngày?", CONFLICT_IDS)]


async def run(case_selection="all"):
    from app.core.config import get_settings
    from app.knowledge.models import IndexState, KnowledgeChunk, KnowledgeSource
    from app.knowledge.rag import rag_service
    from app.knowledge.store import metadata_store
    from app.providers.embeddings.gemini import default_embedding_provider
    from app.providers.gemini_retry import GeminiRequestError
    from app.providers.vectorstore.chroma import default_vector_store
    import re

    settings = get_settings()
    selections = {"all": tuple(range(len(STAGING_CASES))), "answerable": (0,),
                  "missing": (len(CASES) - 1,), "conflict": (len(STAGING_CASES) - 1,)}
    indices = selections[case_selection]
    report = {"dataset_version": version(STAGING_CASES), "corpus_version": version(STAGING_CORPUS),
              "case_selection": case_selection, "planned_cases": len(indices),
              "dataset_status": "UNVERIFIED synthetic fixture", "cases": [],
              "embedding": default_embedding_provider.model_name,
              "generation_model": settings.INTENT_LLM_MODEL or settings.DEFAULT_LLM_MODEL}
    source = KnowledgeSource(source_id="live-synthetic", source_type="DOCUMENT", title="Synthetic fixture",
                             workspace_id="eval-synthetic", state=IndexState.INDEXED)
    chunks = [KnowledgeChunk(chunk_id=key, source_id=source.source_id, text=value)
              for key, value in STAGING_CORPUS.items()]
    try:
        vectors = await default_embedding_provider.embed_batch([chunk.text for chunk in chunks])
    except GeminiRequestError as exc:
        return {**report, "status": "BLOCKED", "stage": "embedding_ingestion", "error_category": exc.category}
    default_vector_store.upsert(chunks, vectors)
    metadata_store.upsert_source(source, chunks)

    staging_facts = [*EXPECTED_FACTS, ()]
    for index in indices:
        question, expected_ids = STAGING_CASES[index]
        facts = staging_facts[index]
        trace = {}
        expected_refusal = not expected_ids or expected_ids == CONFLICT_IDS
        row = {"question": question, "expected_ids": expected_ids, "expected_facts": facts,
               "expected_refusal": expected_refusal, "trace": trace}
        started = time.perf_counter()
        try:
            answer, results = await rag_service.get_answer(question, source.workspace_id, trace=trace)
            citations = [int(index) for index in re.findall(r"\[Nguồn\s*\[(\d+)\]", answer, re.I)]
            cited_ids = [results[index - 1].chunk_id for index in citations]
            abstained = answer == "INSUFFICIENT_EVIDENCE"
            coverage = sum(fact in answer.lower() for fact in facts) / len(facts) if facts else None
            row.update(answer=answer, retrieved_ids=[result.chunk_id for result in results], cited_ids=cited_ids,
                       abstention_correct=abstained == expected_refusal, expected_fact_coverage=coverage,
                       citation_expected_evidence_precision=(
                           sum(chunk_id in expected_ids for chunk_id in cited_ids) / len(cited_ids) if cited_ids else None))
        except GeminiRequestError as exc:
            row["error_category"] = exc.category
            row["http_status"] = exc.status_code
        row["end_to_end_ms"] = (time.perf_counter() - started) * 1000
        report["cases"].append(row)
        if row.get("error_category") in {"rate_limit", "authentication", "configuration"} or row.get("http_status") in {400, 404}:
            break

    rows = report["cases"]
    successful = [row for row in rows if "answer" in row]
    metrics = {}
    for field in ("abstention_correct", "expected_fact_coverage", "citation_expected_evidence_precision"):
        values = [row[field] for row in successful if row[field] is not None]
        metrics[field] = {"mean": statistics.mean(values) if values else None, "n": len(values)}
    for field in ("retrieval_ms", "generation_ms", "end_to_end_ms"):
        values = [row.get(field, row["trace"].get(field)) for row in successful]
        values = [value for value in values if value is not None]
        metrics[field] = {"p50": statistics.median(values) if values else None,
                          "p95": statistics.quantiles(values, n=100, method="inclusive")[94] if len(values) > 1 else None}
    report.update(status="MEASURED" if len(successful) == len(indices) else "PARTIAL" if successful else "BLOCKED",
                  attempted=len(rows), successful=len(successful), error_rate=(len(rows)-len(successful))/len(rows),
                  metrics=metrics, semantic_correctness="NOT_EVALUATED", faithfulness="NOT_EVALUATED")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-synthetic-fixture", action="store_true", required=True)
    parser.add_argument("--case", choices=("all", "answerable", "missing", "conflict"), default="all")
    args = parser.parse_args()
    os.environ["AGENT_DATA_DIR"] = tempfile.mkdtemp(prefix="rag-live-fixture-")
    result = asyncio.run(run(args.case))
    suffix = "" if args.case == "all" else "_" + args.case
    output = ROOT / "rag_eval/results" / f"live_synthetic{suffix}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["status"], result.get("error_category", ""), output)
