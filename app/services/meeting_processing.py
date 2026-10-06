import json
from typing import List, Dict, Any
from pydantic import BaseModel, Field

class MeetingProcessingService:
    async def process_large_transcript(self, transcript: str, ai_client: Any) -> Dict[str, Any]:
        """
        Xử lý transcript dài bằng cách chunking.
        Sử dụng LLM extraction với Schema Validation và Evidence Validation.
        """
        if not ai_client:
            return {"error": "AI Client not configured"}
            
        chunks = [transcript[i:i+4000] for i in range(0, len(transcript), 4000)]
        all_decisions = []
        all_action_items = []
        all_topics = []
        
        import asyncio
        from google.genai import types
        
        # Schema matching Phase P1D requirements
        schema = {
            "type": "object",
            "properties": {
                "topics": {"type": "array", "items": {"type": "string"}},
                "decisions": {
                    "type": "array", 
                    "items": {
                        "type": "object",
                        "properties": {
                            "decision": {"type": "string"},
                            "source_excerpt": {"type": "string", "description": "Trích dẫn nguyên văn bằng chứng từ transcript. Quan trọng!"}
                        },
                        "required": ["decision", "source_excerpt"]
                    }
                },
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string"},
                            "owner": {"type": "string", "nullable": True},
                            "deadline": {"type": "string", "nullable": True},
                            "source_excerpt": {"type": "string", "description": "Trích dẫn nguyên văn bằng chứng. Quan trọng!"}
                        },
                        "required": ["task", "source_excerpt"]
                    }
                }
            },
            "required": ["topics", "decisions", "action_items"]
        }
        
        prompt = (
            "Trích xuất các quyết định và action items từ đoạn ghi âm cuộc họp sau. "
            "CHÚ Ý: Bạn PHẢI cung cấp 'source_excerpt' chứa đoạn text gốc làm bằng chứng. "
            "KHÔNG ĐƯỢC bịa đặt. Nếu không có deadline/owner thì để null.\n\n"
            "Transcript chunk:\n"
        )
        
        async def process_chunk(chunk, index):
            try:
                response = await asyncio.to_thread(
                    ai_client.models.generate_content,
                    model='gemini-2.5-flash',
                    contents=prompt + chunk,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=schema,
                        temperature=0.1
                    ),
                )
                if response.text:
                    return json.loads(response.text), index
                return None, index
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Error extracting chunk: {e}")
                return None, index

        tasks = [process_chunk(chunk, i) for i, chunk in enumerate(chunks)]
        results = await asyncio.gather(*tasks)
        
        for res_data, idx in results:
            if not res_data:
                continue
            all_topics.extend(res_data.get("topics", []))
            
            for d in res_data.get("decisions", []):
                d["chunk_id"] = str(idx)
                # Evidence validation
                if d.get("source_excerpt") and d["source_excerpt"] in chunks[idx]:
                    d["confidence"] = "SUPPORTED"
                else:
                    d["confidence"] = "UNVERIFIED"
                all_decisions.append(d)
                
            for a in res_data.get("action_items", []):
                a["chunk_id"] = str(idx)
                # Evidence validation
                if a.get("source_excerpt") and a["source_excerpt"] in chunks[idx]:
                    a["confidence"] = "SUPPORTED"
                else:
                    a["confidence"] = "UNVERIFIED"
                all_action_items.append(a)
                
        # Deduplicate
        unique_decisions = {d["decision"]: d for d in all_decisions}.values()
        unique_tasks = {a["task"]: a for a in all_action_items}.values()
        unique_topics = list(set(all_topics))
        
        # Merge step to get overall summary
        summary = "Bản tóm tắt cuộc họp tự động."
        
        return {
            "summary": summary,
            "topics": unique_topics,
            "decisions": list(unique_decisions),
            "action_items": list(unique_tasks),
            "open_questions": [],
            "follow_ups": []
        }

meeting_processor = MeetingProcessingService()
