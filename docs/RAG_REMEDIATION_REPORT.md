# Báo cáo khắc phục RAG

**NOT READY**. Bản sửa deadline đã khôi phục generation trên fixture tổng hợp. Quota Gemini và việc xác nhận corpus doanh nghiệp còn chặn nghiệm thu staging đầy đủ.

## Kết quả xác minh deadline và staging

- Gemini xác nhận HTTP 400 do deadline 8 giây thấp hơn mức tối thiểu 10 giây của endpoint generation. Đã tăng deadline generation lên 15 giây, giữ model hiện tại và thêm ca hồi quy kiểm tra cấu hình client.
- `live_synthetic.json` ghi nhận hai câu có dấu và không dấu đều trả đúng 08:30–17:30 và trích dẫn đúng đoạn. Câu thứ ba gặp HTTP 429 sau retry có giới hạn. Ca thiếu bằng chứng chạy riêng cũng gặp 429, ghi tại `live_synthetic_missing.json`. Ca nguồn mâu thuẫn đã có fixture, còn chờ lượt chạy thành công.
- `live_embedding_ablation.json` dùng embedding Gemini thật trên cùng fixture retrieval cũ. Xếp hạng cũ tái lập, dense, sparse và RRF đều đạt 7/7 HitRate@1 và MRR@3 bằng 1,0. Latency truy xuất trong tệp loại thời gian gọi embedding vì các nhánh dùng chung vector. Tệp ghi riêng thời gian của lô embedding.
- `local_corpus_preflight.json` ghi nhận một tệp TXT đọc được thành một đoạn. Phép kiểm tra chỉ chạy parser cục bộ. Tệp còn chờ xác nhận tenant/database và bộ câu hỏi có nhãn độc lập để đánh giá nghiệp vụ.
- Hồi quy sau sửa deadline qua 223 ca trong 16,02 giây. Ruff trên các tệp sửa ở lượt này đã qua.

## Trước và sau

Baseline trước sửa qua 216 kiểm thử offline. Baseline chất lượng doanh nghiệp bị chặn vì runner lịch sử gửi trích đoạn từ corpus mặc định, dùng đường sinh câu trả lời riêng và dùng khớp token để suy ra độ đúng. Các điểm 100% HitRate@1, 1,0 MRR@3, 83% Answer Correctness và 100% Abstention Accuracy lịch sử chưa được xác minh độc lập. Xem `rag_eval/results/baseline/status.json`.

Sau sửa, `python -m pytest tests/unit -q` qua 222 ca trong 14,10 giây. Ruff trên các module sửa và `compileall -q app rag_eval` đều qua.

Bảng dưới dùng sáu đoạn tổng hợp, bảy câu có bằng chứng và một câu thiếu bằng chứng. Nhãn được viết riêng với kết quả truy xuất và mang trạng thái UNVERIFIED. Chroma và FTS5 chạy thật. Embedding dùng test double từ vựng cố định. Thuật toán cũ được tái lập trên cùng corpus với Chroma, công thức khoảng cách cũ và bonus substring. Các chỉ số relevance lấy trung bình trên bảy câu có bằng chứng.

| Nhánh | HitRate@1 | MRR@3 | P50 retrieval | P95 retrieval |
| --- | ---: | ---: | ---: | ---: |
| Xếp hạng cũ tái lập | 85,71% | 0,9048 | 1,48 ms | 3,40 ms |
| Dense | 85,71% | 0,9048 | 3,66 ms | 10,56 ms |
| Sparse FTS5 | 100% | 1,0000 | 2,49 ms | 2,88 ms |
| Hybrid RRF | 100% | 1,0000 | 3,45 ms | 4,24 ms |

Hybrid tăng 14,29 điểm phần trăm HitRate@1 và 0,0952 MRR@3 so với xếp hạng cũ tái lập. Sparse đạt cùng điểm trên fixture này. Mẫu nhỏ, embedding kiểm thử và khác biệt về kiểm tra quyền làm cho các số latency chỉ có giá trị mô tả lần chạy cục bộ. Các chỉ số Recall, Precision, HitRate, MRR và nDCG ở K bằng 1, 3, 5 và 10 cùng kết quả từng câu nằm trong `rag_eval/results/offline_ablation.json`.

Lần đo trước sửa deadline gặp HTTP 400 ở generation. Lần đo sau sửa có hai mẫu thành công với trace, token usage, latency và kiểm tra dữ kiện/trích dẫn. Chất lượng ngữ nghĩa, faithfulness và khả năng từ chối trên ca thiếu nguồn hoặc mâu thuẫn vẫn chưa xác minh bằng Gemini thật. Bộ kiểm thử dùng generation stub để xác minh giới hạn context và hành vi abstain khi thiếu kết quả.

## Thay đổi và bằng chứng

| Phạm vi | Thay đổi |
| --- | --- |
| Quyền | `gateway_context.py` tạo scope từ tenant/database và danh tính đã liên kết. `unified_executor.py` kiểm tra quyền đọc/ghi riêng và giữ phạm vi độc lập với tham số client. `store.py` chỉ liệt kê workspace chính xác. Registry cũ chuyển sang đường Gateway có xác thực. Worker giới hạn đường dẫn theo scope. |
| Cache | `internal.py` ngăn lịch sử RAG mới đưa câu trả lời vào prompt định tuyến. Cache phản hồi Telegram có scope tenant/database. Replay cache cũ yêu cầu truy vấn lại. |
| Retrieval | `retrieval.py` chạy Chroma và FTS5 độc lập, kiểm tra lại nguồn trước khi trả context, hợp nhất bằng RRF và khử trùng ID. `store.py` chuẩn hóa Unicode, hỗ trợ không dấu và giữ token mã ghép. |
| Ingestion | `indexing.py` dùng overlap cấu hình được, đọc paragraph và bảng DOCX theo thứ tự, giữ header và giới hạn dòng theo sheet. PDF ghi nhận trang thiếu text. Tệp hoàn toàn thiếu text hoặc dòng bảng quá dài thất bại rõ ràng. |
| Nhà cung cấp | `gemini_retry.py` phân loại lỗi, retry tối đa ba lần với backoff, jitter và Retry-After trong ngân sách. `rag.py` bỏ ngưỡng 0,25 và kiểm tra chỉ số trích dẫn. Embedding model giữ nguyên `gemini-embedding-001`. |
| Schema | Thêm bảng `knowledge_fts` và cột nullable `turn_records.cache_scope`. Xuất bản metadata INDEXED và các hàng FTS trong cùng transaction sau khi ghi Chroma. Nguồn đang cập nhật hoặc thất bại nằm ngoài retrieval. |

Đọc SQLite Chroma ở chế độ read-only cho thấy schema của collection `knowledge_base` dùng `l2`. Công thức `1/(1+d)` có thể dùng để sắp xếp đơn điệu trong nhánh dense, nhưng chưa có calibration để dùng làm ngưỡng relevance. Trace giữ raw distance riêng với điểm RRF. Một câu thiếu bằng chứng chưa đủ để hiệu chuẩn ngưỡng.

Ngưỡng cũ 0,25 cho qua cả 7/7 câu có bằng chứng và câu thiếu bằng chứng. Câu thiếu bằng chứng có điểm cũ 0,3602, gần điểm 0,3666 của câu có bằng chứng viết không dấu. Đây là phép đo trên test double. Chưa chọn ngưỡng mới.

## Kiểm tra bảo mật và trường hợp lỗi

- Truy vấn từ workspace A qua dense, sparse và hybrid trả đúng đoạn A. Nguồn B, `default` và `global` nằm ngoài context LLM. Workspace thiếu, wildcard và workspace không tồn tại trả rỗng.
- Hai database cùng tenant có scope riêng. Worker từ chối tệp của database khác trước khi gọi embedding. Xóa nguồn khỏi SQLite loại bỏ hàng FTS và ngăn truy xuất vector cũ qua metadata.
- Kiểm thử Telegram xác minh danh tính và binding trước khi phát lại kết quả. Cache có scope xác thực. Kết quả RAG mới chỉ lưu thông báo chung để chống chạy lặp.
- Câu `gio lam viec tai Ha Noi` rớt hạng đầu ở dense và xếp hạng cũ. Sparse chuẩn hóa tiếng Việt có dấu và khôi phục đúng đoạn ở hạng đầu.
- Các lỗi HTTP 400 của hai model trong lần đo cũ được tái hiện với deadline 8 giây. Chẩn đoán và kết quả sau sửa nằm ở phần đầu báo cáo.

## Điều kiện trước khi chạy pilot

Ánh xạ nguồn cũ vào kho tenant/database đã xác minh. Thư mục nhập có dạng `knowledge_docs/kb_<sha256>` với hash được tính trên JSON `[tenant, database]` theo `GatewayUserContext.knowledge_workspace`. Các nguồn tenant cũ, `default` và `global` cần quyết định quyền và nhập lại ở staging. Cấu hình `RAG_TOP_K_DENSE`, `RAG_TOP_K_SPARSE`, `RAG_TOP_K_FINAL`, `RAG_CHUNK_CHARS` và `RAG_CHUNK_OVERLAP` tại Agent.

Kiểm tra lịch sử hội thoại cũ trước khi chuyển kho. Phiên mang cache `knowledge.query` cũ được loại lịch sử khỏi prompt khi xử lý, còn các bản ghi lịch sử không có nhãn nguồn cần kế hoạch cách ly ở staging. Mô hình workspace hiện theo tenant/database. Phân quyền phòng ban trong cùng database và chính sách chia sẻ tài liệu cần nguồn membership đáng tin cậy riêng. Các PDF scan cần đánh giá OCR trên mẫu thật. Độ tin cậy khi nhiều tiến trình cùng ghi chỉ mục và chất lượng trên corpus doanh nghiệp còn cần kiểm chứng.

Tái chạy theo `rag_eval/README.md`. Các lần đo sử dụng kho tạm. Không có lần reindex hoặc triển khai production trong đợt này.
