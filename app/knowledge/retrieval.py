from typing import List, Dict, Any, Optional
from app.providers.vectorstore.chroma import default_vector_store
from app.providers.embeddings.gemini import default_embedding_provider
from app.knowledge.models import SearchResult
from app.knowledge.store import metadata_store

class HybridSearchService:
    async def search(self, query: str, workspace_id: str, top_k: int = 5, filters: Dict[str, Any] = None) -> List[SearchResult]:
        # 1. Embed Query
        query_embedding = (await default_embedding_provider.embed_batch([query]))[0]
        
        # 2. Build filters
        chroma_filters = {}
        if filters:
            for k, v in filters.items():
                chroma_filters[k] = v
                
        # 3. Search Vector Store
        raw_results = default_vector_store.search(
            query_embedding=query_embedding,
            top_k=top_k * 2, # Get more for reranking
            filters=chroma_filters
        )
        
        if not raw_results:
            return []
            
        # 4. Filter by workspace & Build Results
        results = []
        for r in raw_results:
            meta = r["metadata"]
            source_id = meta.get("source_id")
            source = metadata_store.get_source(source_id)
            
            # Workspace Isolation Enforced Here
            if not source or source.workspace_id != workspace_id:
                continue
                
            sr = SearchResult(
                chunk_id=r["chunk_id"],
                source_id=source_id,
                title=source.title,
                content=r["document"],
                score=r["score"],
                source={
                    "file": source.path,
                    "page": meta.get("page"),
                    "section": meta.get("section"),
                    "sheet": meta.get("sheet"),
                    "slide": meta.get("slide")
                }
            )
            
            # Simple keyword bonus for exact match
            if query.lower() in sr.content.lower():
                sr.score += 0.2
                
            results.append(sr)
            
        # 5. Sort by final score
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]

search_service = HybridSearchService()
