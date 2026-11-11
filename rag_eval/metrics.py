# -*- coding: utf-8 -*-
"""Evaluation metrics for RAG retrieval and generation.
Implements standard Information Retrieval (IR) and QA evaluation metrics.
"""

import math
from typing import List, Dict, Any

class RetrievalMetrics:
    @staticmethod
    def hit_rate_at_k(retrieved_chunk_ids: List[str], ground_truth_chunk_ids: List[str], k: int) -> float:
        """Hit Rate@K: 1.0 if at least one relevant chunk is in top-K, else 0.0."""
        if not ground_truth_chunk_ids:
            return 0.0
        top_k = retrieved_chunk_ids[:k]
        hits = set(top_k).intersection(set(ground_truth_chunk_ids))
        return 1.0 if hits else 0.0

    @staticmethod
    def recall_at_k(retrieved_chunk_ids: List[str], ground_truth_chunk_ids: List[str], k: int) -> float:
        """Recall@K: fraction of relevant chunks retrieved in top-K."""
        if not ground_truth_chunk_ids:
            return 0.0
        top_k = retrieved_chunk_ids[:k]
        hits = set(top_k).intersection(set(ground_truth_chunk_ids))
        return len(hits) / float(len(ground_truth_chunk_ids))

    @staticmethod
    def precision_at_k(retrieved_chunk_ids: List[str], ground_truth_chunk_ids: List[str], k: int) -> float:
        """Precision@K: fraction of retrieved top-K chunks that are relevant."""
        if k <= 0:
            return 0.0
        top_k = retrieved_chunk_ids[:k]
        if not top_k:
            return 0.0
        hits = set(top_k).intersection(set(ground_truth_chunk_ids))
        return len(hits) / float(k)

    @staticmethod
    def mrr_at_k(retrieved_chunk_ids: List[str], ground_truth_chunk_ids: List[str], k: int) -> float:
        """Mean Reciprocal Rank (MRR@K): reciprocal rank of the first relevant chunk."""
        if not ground_truth_chunk_ids:
            return 0.0
        top_k = retrieved_chunk_ids[:k]
        for rank, chunk_id in enumerate(top_k, 1):
            if chunk_id in ground_truth_chunk_ids:
                return 1.0 / rank
        return 0.0

    @staticmethod
    def ndcg_at_k(retrieved_chunk_ids: List[str], ground_truth_chunk_ids: List[str], k: int) -> float:
        """Normalized Discounted Cumulative Gain (nDCG@K) with binary relevance."""
        if not ground_truth_chunk_ids:
            return 0.0
        top_k = retrieved_chunk_ids[:k]
        dcg = 0.0
        for i, chunk_id in enumerate(top_k):
            rel = 1.0 if chunk_id in ground_truth_chunk_ids else 0.0
            dcg += (2**rel - 1.0) / math.log2(i + 2)
        
        # Ideal DCG
        ideal_rels = [1.0] * min(len(ground_truth_chunk_ids), k)
        idcg = sum((2**rel - 1.0) / math.log2(i + 2) for i, rel in enumerate(ideal_rels))
        return dcg / idcg if idcg > 0 else 0.0


class GenerationMetrics:
    @staticmethod
    def evaluate_citation_accuracy(answer: str, retrieved_sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Check whether citations conform to [Nguồn [x]: ...] and point to actual retrieved docs."""
        import re
        # Pattern matching [Nguồn [1]: ...] or [Nguồn: ...]
        citations = re.findall(r'\[Nguồn\s*(?:\[\d+\])?:?[^\]]*\]', answer, re.IGNORECASE)
        has_citation = len(citations) > 0
        valid_citations = 0
        for cite in citations:
            # Check if index number is within range
            idx_match = re.search(r'\[(\d+)\]', cite)
            if idx_match:
                idx = int(idx_match.group(1))
                if 1 <= idx <= len(retrieved_sources):
                    valid_citations += 1
            else:
                valid_citations += 1  # Generic source mention
        
        accuracy = (valid_citations / len(citations)) if citations else 0.0
        return {
            "has_citation": has_citation,
            "citation_count": len(citations),
            "valid_citations": valid_citations,
            "citation_accuracy": accuracy,
            "citations_found": citations
        }

    @staticmethod
    def evaluate_abstention(answer: str, is_answerable: bool) -> Dict[str, Any]:
        """Check whether system correctly refused when knowledge was insufficient."""
        is_refusal = "INSUFFICIENT_EVIDENCE" in answer or "không có thông tin" in answer.lower() or "chưa có thông tin" in answer.lower()
        if not is_answerable:
            # Correct rejection
            correct = is_refusal
            status = "CORRECT_ABSTENTION" if is_refusal else "FALSE_ALARM_OR_HALLUCINATION"
        else:
            # Should have answered
            correct = not is_refusal
            status = "CORRECT_ANSWER" if not is_refusal else "FALSE_ABSTENTION"
            
        return {
            "is_refusal": is_refusal,
            "expected_refusal": not is_answerable,
            "abstention_correct": correct,
            "status": status
        }

