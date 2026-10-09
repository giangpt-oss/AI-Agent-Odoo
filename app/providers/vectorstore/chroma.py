import os
import json
from pathlib import Path
from typing import List, Dict, Any
import chromadb
from chromadb.config import Settings
from app.providers.vectorstore.base import VectorStore
from app.knowledge.models import KnowledgeChunk
from app.services.file_service import file_service

class ChromaVectorStore(VectorStore):
    def __init__(self, collection_name: str = "knowledge_base"):
        self.db_dir = file_service.data_dir / "chroma_db"
        self.db_dir.mkdir(exist_ok=True)
        
        self.client = chromadb.PersistentClient(
            path=str(self.db_dir),
            settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self.client.get_or_create_collection(name=collection_name)

    def upsert(self, chunks: List[KnowledgeChunk], embeddings: List[List[float]]) -> None:
        if not chunks:
            return
            
        ids = [chunk.chunk_id for chunk in chunks]
        documents = [chunk.text for chunk in chunks]
        metadatas = []
        for chunk in chunks:
            # Chroma requires values to be str, int, float or bool
            meta = {
                "source_id": chunk.source_id,
                "position": chunk.position
            }
            if chunk.page: meta["page"] = chunk.page
            if chunk.section: meta["section"] = chunk.section
            if chunk.sheet: meta["sheet"] = chunk.sheet
            if chunk.slide: meta["slide"] = chunk.slide
            
            # Pack nested dicts as JSON string
            if chunk.metadata:
                meta["extra"] = json.dumps(chunk.metadata)
                
            metadatas.append(meta)
            
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )

    def search(self, query_embedding: List[float], top_k: int = 5, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        where = filters if filters else None
        
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        
        if not results["ids"] or not results["ids"][0]:
            return []
            
        out = []
        for i in range(len(results["ids"][0])):
            chunk_id = results["ids"][0][i]
            document = results["documents"][0][i]
            metadata = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            
            # Chroma uses cosine distance if configured, defaults to L2.
            # Convert L2 distance to score (smaller is better).
            score = 1.0 / (1.0 + distance)
            
            out.append({
                "chunk_id": chunk_id,
                "document": document,
                "metadata": metadata,
                "score": score
            })
        return out

    def delete_source(self, source_id: str) -> None:
        self.collection.delete(where={"source_id": source_id})

    def delete_chunks(self, chunk_ids: List[str]) -> None:
        if chunk_ids:
            self.collection.delete(ids=chunk_ids)

    def count(self) -> int:
        return self.collection.count()

    def health_check(self) -> bool:
        try:
            self.client.heartbeat()
            return True
        except Exception:
            return False

default_vector_store = ChromaVectorStore()
