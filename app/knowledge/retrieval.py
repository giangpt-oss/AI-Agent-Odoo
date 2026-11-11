import asyncio
import logging
import time
import uuid

from app.knowledge.models import IndexState, SearchResult
from app.knowledge.store import metadata_store
from app.providers.embeddings.gemini import default_embedding_provider
from app.providers.vectorstore.chroma import default_vector_store

logger = logging.getLogger(__name__)


class HybridSearchService:
    async def search(self, query, workspace_id=None, top_k=None, filters=None, *, mode="hybrid", trace=None):
        from app.core.config import get_settings

        if mode not in {"dense", "sparse", "hybrid"}:
            raise ValueError("Unknown retrieval mode")
        if filters and mode != "dense":
            raise ValueError("Additional metadata filters require dense mode")
        settings = get_settings()
        top_k = settings.RAG_TOP_K_FINAL if top_k is None else top_k
        if not 1 <= top_k <= 100:
            raise ValueError("Invalid retrieval limit")
        started = time.perf_counter()
        sources = await asyncio.to_thread(metadata_store.list_sources, workspace_id)
        sources_map = {source.source_id: source for source in sources if source.state == IndexState.INDEXED}
        if not sources_map or not query.strip():
            return []

        async def dense():
            source_filter = {"source_id": {"$in": list(sources_map)}}
            where = {"$and": [source_filter, filters]} if filters else source_filter
            vector = (await default_embedding_provider.embed_batch([query]))[0]
            return await asyncio.to_thread(default_vector_store.search, vector, settings.RAG_TOP_K_DENSE, where)

        async def sparse():
            return await asyncio.to_thread(metadata_store.search_sparse, workspace_id, query, settings.RAG_TOP_K_SPARSE)

        branches = {"dense": dense, "sparse": sparse}
        selected = list(branches) if mode == "hybrid" else [mode]

        async def timed(branch):
            start = time.perf_counter()
            rows = await branches[branch]()
            return rows, round((time.perf_counter() - start) * 1000, 3)

        outputs = await asyncio.gather(*(timed(branch) for branch in selected))
        current = await asyncio.to_thread(metadata_store.list_sources, workspace_id)
        authorized = {s.source_id for s in current if s.state == IndexState.INDEXED
                      and s.source_id in sources_map and s.version == sources_map[s.source_id].version}
        scores, candidates, branch_trace = {}, {}, {}
        for branch, (rows, elapsed) in zip(selected, outputs, strict=True):
            seen = set()
            ranked = []
            for row in rows:
                chunk_id = row["chunk_id"]
                if chunk_id in seen or row["metadata"].get("source_id") not in authorized:
                    continue
                seen.add(chunk_id)
                ranked.append({key: row[key] for key in ("chunk_id", "distance", "bm25") if key in row})
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (60 + len(ranked))
                candidates.setdefault(chunk_id, row)
            branch_trace[branch] = {"results": ranked, "ms": elapsed}
        ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))[:top_k]
        results = []
        for chunk_id in ids:
            row = candidates[chunk_id]
            meta = row["metadata"]
            source = sources_map[meta["source_id"]]
            results.append(SearchResult(
                chunk_id=chunk_id, source_id=source.source_id, title=source.title,
                content=row["document"], score=scores[chunk_id],
                source={"file": source.path, **{key: meta.get(key) for key in ("page", "section", "sheet", "slide")}},
            ))
        duration = round((time.perf_counter() - started) * 1000, 3)
        correlation_id = uuid.uuid4().hex
        if trace is not None:
            trace.update(correlation_id=correlation_id, branches=branch_trace, final_ids=ids,
                         fusion_scores=scores, retrieval_ms=duration)
        logger.info("rag_retrieval id=%s mode=%s results=%d duration_ms=%s", correlation_id, mode, len(results), duration)
        return results


search_service = HybridSearchService()
