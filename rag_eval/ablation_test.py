# -*- coding: utf-8 -*-
"""Phase 3: Hybrid Search Audit and Ablation Study.
Compares:
- Strategy A: Pure Semantic Search (Dense ChromaDB)
- Strategy B: Pure Keyword Search (BM25 token-based)
- Strategy C: Current Production "Hybrid" (ChromaDB + exact query substring bonus 0.2)
- Strategy D: Real Hybrid Search (BM25 + Dense fused via Reciprocal Rank Fusion - RRF)

Calculates retrieval metrics (HitRate@1, HitRate@3, MRR@3) across test queries.
"""

import asyncio
import os
import sys
import json
import math
import re
from typing import List, Dict, Any
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.providers.embeddings.gemini import default_embedding_provider
from app.providers.vectorstore.chroma import default_vector_store
from app.knowledge.store import metadata_store
from app.knowledge.models import IndexState, SearchResult
from app.knowledge.retrieval import search_service

# Simple in-memory BM25 implementation for Vietnamese/General text
class SimpleBM25:
    def __init__(self, corpus: List[Dict[str, Any]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        self.doc_len = [len(self._tokenize(doc["content"])) for doc in corpus]
        self.avg_doc_len = sum(self.doc_len) / float(len(self.doc_len)) if self.doc_len else 1.0
        self.doc_freqs = []
        self.df = {}
        for doc in corpus:
            tokens = self._tokenize(doc["content"])
            freq = {}
            for t in tokens:
                freq[t] = freq.get(t, 0) + 1
            self.doc_freqs.append(freq)
            for t in freq:
                self.df[t] = self.df.get(t, 0) + 1
                
        self.idf = {}
        total_docs = len(corpus)
        for t, freq in self.df.items():
            self.idf[t] = math.log((total_docs - freq + 0.5) / (freq + 0.5) + 1.0)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        # Simple tokenization: lowercase, remove special characters
        cleaned = re.sub(r'[^\w\s]', ' ', text.lower())
        return [w for w in cleaned.split() if w]

    def score(self, query: str) -> List[float]:
        q_tokens = self._tokenize(query)
        scores = []
        for i, doc_freq in enumerate(self.doc_freqs):
            score = 0.0
            doc_len = self.doc_len[i]
            for t in q_tokens:
                if t in doc_freq:
                    tf = doc_freq[t]
                    idf = self.idf.get(t, 0.0)
                    numerator = idf * tf * (self.k1 + 1)
                    denominator = tf + self.k1 * (1 - self.b + self.b * (doc_len / self.avg_doc_len))
                    score += numerator / denominator
            scores.append(score)
        return scores


async def run_ablation():
    # Load golden dataset
    dataset_path = Path(__file__).resolve().parent / "golden_dataset.json"
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    test_cases = data["test_cases"]
    
    # 1. Fetch all indexed corpus chunks
    sources = metadata_store.list_sources("default")
    sources_map = {s.source_id: s for s in sources if s.state == IndexState.INDEXED}
    
    # Query Chroma for all docs in knowledge_base
    col_items = default_vector_store.collection.get(include=["documents", "metadatas"])
    all_chunks = []
    for i in range(len(col_items["ids"])):
        cid = col_items["ids"][i]
        meta = col_items["metadatas"][i]
        sid = meta.get("source_id")
        src = sources_map.get(sid)
        if src:
            all_chunks.append({
                "chunk_id": cid,
                "source_id": sid,
                "title": src.title,
                "content": col_items["documents"][i],
                "metadata": meta
            })
            
    bm25 = SimpleBM25(all_chunks)
    
    strategies = ["A_semantic_only", "B_bm25_only", "C_current_hybrid", "D_rrf_true_hybrid"]
    metrics = {s: {"hit@1": 0, "hit@3": 0, "mrr@3": 0.0, "total_eval": 0} for s in strategies}
    
    trace_details = []
    
    for tc in test_cases:
        if not tc["answerable"]:
            continue  # Retrieval metrics defined only on queries with target relevant docs
            
        q = tc["question"]
        gt_chunks = tc["relevant_chunk_ids"]
        metrics["A_semantic_only"]["total_eval"] += 1
        metrics["B_bm25_only"]["total_eval"] += 1
        metrics["C_current_hybrid"]["total_eval"] += 1
        metrics["D_rrf_true_hybrid"]["total_eval"] += 1
        
        # Strategy A: Semantic Search only
        q_emb = (await default_embedding_provider.embed_batch([q]))[0]
        raw_dense = default_vector_store.search(q_emb, top_k=5)
        res_a = [r["chunk_id"] for r in raw_dense]
        
        # Strategy B: BM25 only
        bm25_scores = bm25.score(q)
        sorted_bm25_indices = sorted(range(len(bm25_scores)), key=lambda i: bm25_scores[i], reverse=True)
        res_b = [all_chunks[i]["chunk_id"] for i in sorted_bm25_indices[:5]]
        
        # Strategy C: Current "Hybrid" in codebase (search_service)
        curr_res = await search_service.search(q, workspace_id="default", top_k=5)
        res_c = [r.chunk_id for r in curr_res]
        
        # Strategy D: Real Reciprocal Rank Fusion (RRF)
        # RRF formula: score = 1 / (60 + rank_dense) + 1 / (60 + rank_bm25)
        rrf_scores = {}
        for rank, r in enumerate(raw_dense, 1):
            cid = r["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (60.0 + rank))
        for rank, idx in enumerate(sorted_bm25_indices[:10], 1):
            cid = all_chunks[idx]["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (60.0 + rank))
            
        sorted_rrf = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        res_d = sorted_rrf[:5]
        
        # Calculate metrics for each
        for s_key, res_list in [
            ("A_semantic_only", res_a),
            ("B_bm25_only", res_b),
            ("C_current_hybrid", res_c),
            ("D_rrf_true_hybrid", res_d),
        ]:
            h1 = 1.0 if res_list and res_list[0] in gt_chunks else 0.0
            h3 = 1.0 if any(c in gt_chunks for c in res_list[:3]) else 0.0
            mrr = 0.0
            for rank, c in enumerate(res_list[:3], 1):
                if c in gt_chunks:
                    mrr = 1.0 / rank
                    break
            metrics[s_key]["hit@1"] += h1
            metrics[s_key]["hit@3"] += h3
            metrics[s_key]["mrr@3"] += mrr
            
        # Check if current "hybrid" substring bonus was actually triggered!
        bonus_triggered = any(q.lower() in chunk["content"].lower() for chunk in all_chunks)
        
        trace_details.append({
            "test_case_id": tc["id"],
            "question": q,
            "bonus_triggered_in_c": bonus_triggered,
            "res_a": res_a[:3],
            "res_b": res_b[:3],
            "res_c": res_c[:3],
            "res_d": res_d[:3]
        })
        
    # Aggregate summary
    summary = {}
    for s_key, m in metrics.items():
        total = max(1, m["total_eval"])
        summary[s_key] = {
            "hit_rate_at_1": round(m["hit@1"] / total, 4),
            "hit_rate_at_3": round(m["hit@3"] / total, 4),
            "mrr_at_3": round(m["mrr@3"] / total, 4),
            "total_queries_evaluated": total
        }
        
    results_payload = {
        "summary": summary,
        "bonus_triggered_count": sum(1 for t in trace_details if t["bonus_triggered_in_c"]),
        "total_test_cases": len(trace_details),
        "traces": trace_details
    }
    
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "phase3_ablation_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results_payload, f, ensure_ascii=False, indent=2)
        
    print(f"Phase 3 Ablation completed! Results saved to {out_path}")
    print("\nAblation Summary:")
    for k, v in summary.items():
        print(f" - {k}: Hit@1={v['hit_rate_at_1']}, Hit@3={v['hit_rate_at_3']}, MRR@3={v['mrr_at_3']}")
    print(f"\nExact substring bonus triggered in Current Hybrid: {results_payload['bonus_triggered_count']}/{results_payload['total_test_cases']} queries!")

if __name__ == "__main__":
    asyncio.run(run_ablation())

