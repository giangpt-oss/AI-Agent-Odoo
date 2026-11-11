"""Offline retrieval evaluation. Uses SQLite FTS5 and Chroma with a test embedding."""
import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = {
    "hours": "Giờ làm việc tại Hà Nội từ 08:30 đến 17:30.",
    "leave": "Đơn xin nghỉ phép cần phê duyệt trước 3 ngày.",
    "contract": "Hợp đồng HD-2026-A có thời hạn 24 tháng.",
    "contract-other": "Hợp đồng HD-2026-B có thời hạn 12 tháng.",
    "stock": "Sản phẩm PN22 có tồn kho tối thiểu 15 bộ.",
    "returns": "Trả hàng trong vòng 30 ngày nếu còn tem.",
}
CASES = [
    ("Giờ làm việc tại Hà Nội", ["hours"]),
    ("gio lam viec tai Ha Noi", ["hours"]),
    ("Đơn xin nghỉ phép", ["leave"]),
    ("HD-2026-A", ["contract"]),
    ("HD-2026-B", ["contract-other"]),
    ("PN22", ["stock"]),
    ("trả hàng còn tem", ["returns"]),
    ("Hành tinh sao Thổ có mấy vành đai?", []),
]


def vector(text):
    output = [0.0] * 768
    for token in re.findall(r"\w+", text.casefold()):
        index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:2], "big") % len(output)
        output[index] += 1
    norm = sum(value * value for value in output) ** 0.5 or 1
    return [value / norm for value in output]


def version(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


async def run(*, live_embeddings=False):
    from app.knowledge.models import IndexState, KnowledgeChunk, KnowledgeSource
    from app.knowledge.retrieval import search_service
    from app.knowledge.store import metadata_store
    from app.providers.embeddings.gemini import default_embedding_provider
    from app.providers.gemini_retry import GeminiRequestError
    from app.providers.vectorstore.chroma import default_vector_store
    from rag_eval.metrics import RetrievalMetrics

    inputs = list(CORPUS.values()) + [question for question, _ in CASES]
    started = time.perf_counter()
    try:
        embeddings = await default_embedding_provider.embed_batch(inputs) if live_embeddings else [vector(text) for text in inputs]
    except GeminiRequestError as exc:
        return {"status": "BLOCKED", "stage": "embedding", "category": exc.category,
                "http_status": exc.status_code, "dataset_version": version(CASES), "corpus_version": version(CORPUS)}
    embedding_ms = (time.perf_counter() - started) * 1000
    by_text = dict(zip(inputs, embeddings, strict=True))

    async def fixture_embed(texts):
        return [by_text[text] for text in texts]

    default_embedding_provider.embed_batch = fixture_embed
    scope = "eval-fixture"
    source = KnowledgeSource(source_id="eval-source", source_type="DOCUMENT", title="Synthetic policies",
                             workspace_id=scope, state=IndexState.INDEXED)
    chunks = [KnowledgeChunk(source_id=source.source_id, chunk_id=key, text=value)
              for key, value in CORPUS.items()]
    default_vector_store.upsert(chunks, [by_text[chunk.text] for chunk in chunks])
    metadata_store.upsert_source(source, chunks)
    report = {
        "dataset_status": "UNVERIFIED synthetic fixture, labels authored independently of retrieval output",
        "dataset_version": version(CASES), "corpus_version": version(CORPUS),
        "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                        "chroma_hnsw": default_vector_store.collection.configuration.get("hnsw"),
                        "embedding": default_embedding_provider.model_name if live_embeddings else "deterministic lexical test double, not Gemini",
                        "embedding_batch_ms": embedding_ms, "retrieval_timing_excludes_embedding": True},
        "generation": {"status": "NOT_EVALUATED", "reason": "No external calls in offline evaluation"},
        "modes": {},
    }
    for mode in ("legacy_reconstructed", "dense", "sparse", "hybrid"):
        rows = []
        for question, expected in CASES:
            started = time.perf_counter()
            trace = {}
            if mode == "legacy_reconstructed":
                candidates = default_vector_store.search(by_text[question], 20, {"source_id": {"$in": [source.source_id]}})
                for row in candidates:
                    if question.lower() in row["document"].lower():
                        row["score"] += 0.2
                candidates.sort(key=lambda row: row["score"], reverse=True)
                ids = [row["chunk_id"] for row in candidates[:10]]
                trace = {"candidates": [{key: row[key] for key in ("chunk_id", "distance", "score")} for row in candidates]}
            else:
                results = await search_service.search(question, scope, top_k=10, mode=mode, trace=trace)
                ids = [row.chunk_id for row in results]
            elapsed = (time.perf_counter() - started) * 1000
            rows.append({"question": question, "scope": scope, "expected_ids": expected, "retrieved_ids": ids,
                         "latency_ms": elapsed, "trace": trace})
        answerable = [row for row in rows if row["expected_ids"]]
        metrics = {}
        for k in (1, 3, 5, 10):
            for name in ("recall", "precision", "hit_rate", "mrr", "ndcg"):
                calculate = getattr(RetrievalMetrics, f"{name}_at_k")
                metrics[f"{name}@{k}"] = statistics.mean(
                    calculate(row["retrieved_ids"], row["expected_ids"], k) for row in answerable)
        timings = [row["latency_ms"] for row in rows]
        report["modes"][mode] = {"metrics": metrics, "cases": rows,
            "retrieval_ms_p50": statistics.median(timings),
            "retrieval_ms_p95": statistics.quantiles(timings, n=100, method="inclusive")[94],
            "failed_top_1": [row["question"] for row in answerable
                             if not set(row["retrieved_ids"][:1]) & set(row["expected_ids"])]}
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live-synthetic-embeddings", action="store_true", help="Send only the built-in synthetic fixture to Gemini")
    args = parser.parse_args()
    os.environ["AGENT_DATA_DIR"] = tempfile.mkdtemp(prefix="rag-offline-eval-")
    if not args.live_synthetic_embeddings:
        os.environ["GEMINI_API_KEY"] = ""
    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    result = asyncio.run(run(live_embeddings=args.live_synthetic_embeddings))
    output = args.output or ROOT / "rag_eval/results" / ("live_embedding_ablation.json" if args.live_synthetic_embeddings else "offline_ablation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
