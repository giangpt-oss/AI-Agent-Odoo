# -*- coding: utf-8 -*-
"""Phase 2: Runtime Verification and Controlled Experiments.
Tests:
- Test A: Context Removal (ablation: with context vs without context)
- Test B: Controlled Knowledge (fictitious non-public facts to verify retrieval usage)
- Test C: Irrelevant Context (verify hallucination resistance)
- Test D: Conflicting Documents (v1 vs v2 precedence)
- Test E: Missing Knowledge (verify INSUFFICIENT_EVIDENCE on unanswerable query)

Safety: All synthetic documents use an isolated temporary Chroma collection and isolated SQLite rows.
"""

import asyncio
import os
import sys
import json
import uuid
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import get_settings
from app.providers.embeddings.gemini import default_embedding_provider
from app.providers.vectorstore.chroma import ChromaVectorStore
from app.knowledge.models import KnowledgeChunk, SearchResult
from app.knowledge.rag import rag_service
from google import genai
from google.genai import types

TEMP_COLLECTION_NAME = f"audit_verify_temp_{uuid.uuid4().hex[:6]}"

async def run_test_a_context_removal(ai_client, model_name: str) -> dict:
    """Test A: Context Removal.
    Compares LLM response when context is provided vs when context is removed.
    """
    question = "Thời gian nghỉ trưa và số ngày làm việc từ xa tối đa tại Công ty Hopita là bao nhiêu?"
    context = (
        "Nội quy công ty Hopita:\n"
        "- Giờ làm việc: 08:30 đến 17:30. Nghỉ trưa từ 12:00 đến 13:30.\n"
        "- Làm việc từ xa (WFH): Tối đa 02 ngày/tuần sau thời gian thử việc."
    )
    
    system_instruction = (
        "Bạn là trợ lý AI trả lời câu hỏi dựa trên tài liệu đính kèm.\n"
        "NGUYÊN TẮC BẮT BUỘC:\n"
        "1. Chỉ trả lời dựa trên thông tin trong tài liệu (Context) bên dưới.\n"
        "2. Nếu tài liệu không chứa đủ thông tin để trả lời, HÃY TRẢ LỜI CHÍNH XAC LÀ: 'INSUFFICIENT_EVIDENCE'. Không bịa đặt.\n"
    )
    
    # Run with context
    resp_with_context = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=model_name,
        contents=f"Câu hỏi: {question}\n\nContext:\n{context}",
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
        )
    )
    
    # Run without context (empty context)
    resp_no_context = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=model_name,
        contents=f"Câu hỏi: {question}\n\nContext:\n(Không có tài liệu nào)",
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
        )
    )
    
    text_with = resp_with_context.text.strip()
    text_without = resp_no_context.text.strip()
    
    passed = ("12:00" in text_with or "13:30" in text_with) and ("INSUFFICIENT_EVIDENCE" in text_without or "không có" in text_without.lower())
    
    return {
        "test": "Test A: Context Removal",
        "question": question,
        "answer_with_context": text_with,
        "answer_without_context": text_without,
        "passed": passed,
        "finding": "LLM tuân thủ chỉ dẫn: trả lời chính xác khi có context và từ chối (INSUFFICIENT_EVIDENCE) khi thiếu context." if passed else "LLM hallucinated hoặc không tuân thủ quy tắc từ chối."
    }


async def run_test_b_controlled_knowledge(temp_store: ChromaVectorStore, ai_client, model_name: str) -> dict:
    """Test B: Controlled Knowledge.
    Uses completely fictitious, non-public data (Project Xylon-99, Budget 47.85 tỷ, Lead Dr. Elena Hoang).
    Proves whether LLM strictly uses retrieved information.
    """
    fictitious_text = (
        "Dự án bí mật 'Xylon-99' của Tập đoàn Hopita được phê duyệt vào ngày 15/09/2026. "
        "Tổng ngân sách phê duyệt là 47.85 tỷ VNĐ. "
        "Chủ nhiệm dự án là Tiến sĩ Elena Hoàng, mã nhân viên HP-9901."
    )
    
    chunk = KnowledgeChunk(
        chunk_id="chk_xylon_99",
        source_id="src_xylon_99",
        text=fictitious_text,
        position=0,
        metadata={"title": "Du_an_Xylon_99.txt"}
    )
    
    # Embed and insert into isolated collection
    embeddings = await default_embedding_provider.embed_batch([fictitious_text])
    temp_store.upsert([chunk], embeddings)
    
    # Query Chroma
    q = "Ngân sách và chủ nhiệm của dự án bí mật Xylon-99 là ai và bao nhiêu tiền?"
    q_emb = (await default_embedding_provider.embed_batch([q]))[0]
    search_res = temp_store.search(q_emb, top_k=1)
    
    if not search_res:
        return {
            "test": "Test B: Controlled Knowledge",
            "passed": False,
            "error": "Retrieval failed on synthetic document."
        }
        
    retrieved_chunk = search_res[0]
    
    # Generate answer
    prompt = f"Câu hỏi: {q}\n\nContext:\nNguồn [1]: Du_an_Xylon_99.txt\n{retrieved_chunk['document']}"
    system_instruction = (
        "Bạn là trợ lý AI trả lời câu hỏi dựa trên tài liệu đính kèm.\n"
        "NGUYÊN TẮC BẮT BUỘC:\n"
        "1. Chỉ trả lời dựa trên thông tin trong tài liệu (Context) bên dưới.\n"
        "2. Nếu tài liệu không chứa đủ thông tin để trả lời, HÃY TRẢ LỜI CHÍNH XÁC LÀ: 'INSUFFICIENT_EVIDENCE'. Không bịa đặt.\n"
        "3. LUÔN LUÔN trích dẫn nguồn ở cuối mỗi ý hoặc cuối câu trả lời (VD: [Nguồn [1]: ...]).\n"
    )
    
    resp = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
        )
    )
    
    ans = resp.text.strip()
    passed = ("47.85" in ans or "47,85" in ans) and ("Elena Hoàng" in ans or "Elena Hoang" in ans)
    
    return {
        "test": "Test B: Controlled Knowledge",
        "question": q,
        "retrieved_score": retrieved_chunk["score"],
        "generated_answer": ans,
        "passed": passed,
        "finding": "RAG pipeline trích xuất chính xác thông tin giả định kiểm soát, chứng minh câu trả lời sinh ra từ retrieval chứ không phải từ tri thức nền của LLM." if passed else "Agent không trích xuất đúng thông tin giả định."
    }


async def run_test_c_irrelevant_context(ai_client, model_name: str) -> dict:
    """Test C: Irrelevant Context.
    Feeds irrelevant context (culinary recipe) and asks about corporate financial reimbursement.
    Checks if model hallucinates or correctly refuses.
    """
    irrelevant_context = (
        "Nguồn [1]: Cong_thuc_nau_pho.txt\n"
        "Để nấu nước dùng phở bò thơm ngon, cần ninh xương ống bò trong 8 tiếng cùng với quế, hồi, thảo quả nướng.\n"
        "Thêm gừng nướng đập dập và hành tây để nước dùng trong và ngọt tự nhiên."
    )
    question = "Hạn chót nộp hóa đơn hoàn ứng công tác phí là ngày mấy hàng tháng?"
    
    prompt = f"Câu hỏi: {question}\n\nContext:\n{irrelevant_context}"
    system_instruction = (
        "Bạn là trợ lý AI trả lời câu hỏi dựa trên tài liệu đính kèm.\n"
        "NGUYÊN TẮC BẮT BUỘC:\n"
        "1. Chỉ trả lời dựa trên thông tin trong tài liệu (Context) bên dưới.\n"
        "2. Nếu tài liệu không chứa đủ thông tin để trả lời, HÃY TRẢ LỜI CHÍNH XÁC LÀ: 'INSUFFICIENT_EVIDENCE'. Không bịa đặt.\n"
    )
    
    resp = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
        )
    )
    
    ans = resp.text.strip()
    passed = "INSUFFICIENT_EVIDENCE" in ans or "không có thông tin" in ans.lower()
    
    return {
        "test": "Test C: Irrelevant Context",
        "question": question,
        "context_supplied": irrelevant_context,
        "generated_answer": ans,
        "passed": passed,
        "finding": "LLM kháng ảo giác (anti-hallucination) thành công khi context hoàn toàn không liên quan, trả về INSUFFICIENT_EVIDENCE." if passed else "LLM bị ảo giác khi gặp context không liên quan."
    }


async def run_test_d_conflicting_documents(temp_store: ChromaVectorStore, ai_client, model_name: str) -> dict:
    """Test D: Conflicting Documents.
    Version 1.0 (Old): WFH 1 day/week, dated 2024.
    Version 2.0 (New): WFH 3 days/week, dated 2026.
    Tests how retrieval and LLM handle conflicting documents without explicit freshness reranking.
    """
    doc_v1 = "Chính sách WFH 2024 (v1.0): Mỗi nhân viên chỉ được làm việc từ xa tối đa 01 ngày/tuần."
    doc_v2 = "Chính sách WFH 2026 (v2.0 - Mới nhất): Mỗi nhân viên được làm việc từ xa tối đa 03 ngày/tuần."
    
    chunks = [
        KnowledgeChunk(chunk_id="chk_wfh_v1", source_id="src_wfh_v1", text=doc_v1, position=0, metadata={"version": "1.0", "year": "2024"}),
        KnowledgeChunk(chunk_id="chk_wfh_v2", source_id="src_wfh_v2", text=doc_v2, position=0, metadata={"version": "2.0", "year": "2026"}),
    ]
    embs = await default_embedding_provider.embed_batch([doc_v1, doc_v2])
    temp_store.upsert(chunks, embs)
    
    q = "Chính sách mới nhất hiện nay cho phép nhân viên WFH tối đa mấy ngày một tuần?"
    q_emb = (await default_embedding_provider.embed_batch([q]))[0]
    results = temp_store.search(q_emb, top_k=2)
    
    context_text = "\n---\n".join([f"Nguồn [{i+1}] (v{r['metadata'].get('version')}): {r['document']}" for i, r in enumerate(results)])
    
    prompt = f"Câu hỏi: {q}\n\nContext:\n{context_text}"
    system_instruction = (
        "Bạn là trợ lý AI trả lời câu hỏi dựa trên tài liệu đính kèm.\n"
        "NGUYÊN TẮC BẮT BUỘC:\n"
        "1. Chỉ trả lời dựa trên thông tin trong tài liệu (Context) bên dưới.\n"
        "2. Nếu có sự mâu thuẫn giữa các phiên bản tài liệu, ưu tiên phiên bản mới nhất (v2.0 / năm 2026).\n"
        "3. LUÔN LUÔN trích dẫn nguồn rõ ràng."
    )
    
    resp = await asyncio.to_thread(
        ai_client.models.generate_content,
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
        )
    )
    
    ans = resp.text.strip()
    prioritized_v2 = "03 ngày" in ans or "3 ngày" in ans
    
    return {
        "test": "Test D: Conflicting Documents",
        "question": q,
        "retrieved_items": [{"id": r["chunk_id"], "score": r["score"], "doc": r["document"]} for r in results],
        "generated_answer": ans,
        "passed": prioritized_v2,
        "finding": "Khi context có cả 2 bản, LLM nhận diện đúng bản mới nhất 2026 (03 ngày) nhờ system prompt. Tuy nhiên, hệ thống retrieval hiện tại KHÔNG có cơ chế lọc hay boost freshness ở tầng vector store." if prioritized_v2 else "Agent không ưu tiên được bản cập nhật mới nhất."
    }


async def run_test_e_missing_knowledge() -> dict:
    """Test E: Missing Knowledge in production KB.
    Query something completely absent from production KB (Hopita Maternity policy).
    Verify if RAG service returns INSUFFICIENT_EVIDENCE.
    """
    query = "Chính sách nghỉ thai sản của công ty Hopita chi trả bao nhiêu tháng lương và chế độ quà tặng thế nào?"
    answer, results = await rag_service.get_answer(query, workspace_id="default")
    
    passed = answer == "INSUFFICIENT_EVIDENCE" or "INSUFFICIENT_EVIDENCE" in answer
    
    return {
        "test": "Test E: Missing Knowledge (Abstention)",
        "question": query,
        "retrieved_results_count": len(results),
        "best_score": max([r.score for r in results]) if results else 0.0,
        "generated_answer": answer,
        "passed": passed,
        "finding": "RAG pipeline từ chối trả lời chính xác (INSUFFICIENT_EVIDENCE) khi kho tri thức không có thông tin." if passed else f"LỖI: RAG pipeline không từ chối khi thông tin thiếu, đã trả về: {answer[:100]}"
    }


async def main():
    settings = get_settings()
    key = settings.GEMINI_API_KEY
    if not key:
        print("ERROR: GEMINI_API_KEY is not configured.")
        return
        
    ai_client = genai.Client(api_key=key)
    # Use reliable gemini-flash-lite-latest to avoid free-tier 20 req/day quota exhaustion on gemini-3-flash-preview
    model_name = 'gemini-flash-lite-latest'
    
    temp_store = ChromaVectorStore(collection_name=TEMP_COLLECTION_NAME)
    
    results = {}
    try:
        print("[1/5] Running Test A (Context Removal)...")
        results["test_a"] = await run_test_a_context_removal(ai_client, model_name)
        
        print("[2/5] Running Test B (Controlled Knowledge)...")
        results["test_b"] = await run_test_b_controlled_knowledge(temp_store, ai_client, model_name)
        
        print("[3/5] Running Test C (Irrelevant Context)...")
        results["test_c"] = await run_test_c_irrelevant_context(ai_client, model_name)
        
        print("[4/5] Running Test D (Conflicting Documents)...")
        results["test_d"] = await run_test_d_conflicting_documents(temp_store, ai_client, model_name)
        
        print("[5/5] Running Test E (Missing Knowledge)...")
        results["test_e"] = await run_test_e_missing_knowledge()
        
    finally:
        # Cleanup temporary collection
        try:
            temp_store.client.delete_collection(TEMP_COLLECTION_NAME)
            print(f"Cleaned up temporary collection {TEMP_COLLECTION_NAME}")
        except Exception as e:
            print(f"Warning cleaning temp collection: {e}")
            
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "phase2_verify_runtime_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
        
    print(f"\nVerification completed! Results written to {out_path}")
    print("\nSummary of results:")
    for k, v in results.items():
        print(f" - {v['test']}: {'PASS' if v.get('passed') else 'FAIL'}")

if __name__ == "__main__":
    asyncio.run(main())

