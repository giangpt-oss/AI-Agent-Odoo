# Kế hoạch khắc phục RAG

Gateway xác minh danh tính Telegram và cung cấp tenant, database, UID cùng quyền. UnifiedExecutor gọi SQLite để chọn nguồn, Chroma để tìm đoạn và Gemini để tạo câu trả lời. Worker nhập tài liệu theo lô. SQLite lưu nguồn và tác vụ. Chroma lưu văn bản, metadata và embedding.

Các phát hiện đã xác nhận gồm chia sẻ ngầm `default/global`, fallback khi thiếu tenant, thiếu phân tách database cùng tenant, thư mục nhập chung, và registry cũ chỉ yêu cầu quyền tệp. Nhánh tìm kiếm hiện tại cộng điểm substring sau dense. DOCX bỏ bảng. CSV/XLSX có lặp header mỗi 25 dòng nhưng có thể mất header sau lần cắt tiếp. PDF giữ số trang và tệp hoàn toàn rỗng thất bại, song các trang rỗng trong tệp hỗn hợp bị bỏ qua. Ngưỡng 0,25 chưa có calibration. Chưa xác minh độc lập nhãn golden, kết quả Gemini cũ hoặc nhu cầu OCR.

Thứ tự thay đổi là khóa quyền ở executor, registry cũ, store và worker, rồi thêm FTS5 và RRF. Giữ model embedding và collection hiện tại. Thay đổi parser và đồng bộ sparse trong bước xuất bản chỉ mục. Kiểm thử trên SQLite và Chroma tạm, so sánh các nhánh trên fixture cố định, kiểm tra lỗi nhà cung cấp bằng fixture. Kết quả offline tách khỏi chất lượng Gemini và dữ liệu doanh nghiệp.

Migration chỉ thêm bảng FTS5. Nguồn cũ cần được quản trị viên xác định tenant/database và nhập lại vào kho staging riêng. Sao lưu SQLite và Chroma khi dịch vụ đã dừng, đối chiếu số nguồn/đoạn, kiểm tra phân quyền rồi mới xem xét chuyển kho. Khi rollback, giữ bản sửa phân quyền và bản sao dữ liệu, tắt nhánh sparse bằng cách quay về retrieval dense có cùng giới hạn quyền. Không quay lại SQL chia sẻ ngầm.
