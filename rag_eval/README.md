# RAG Evaluation Framework (`rag_eval`)

## Runner của bản khắc phục

Chạy từ thư mục Agent bằng môi trường ảo của dự án.

```powershell
.venv\Scripts\python.exe rag_eval/offline_ablation.py
.venv\Scripts\python.exe -m pytest tests/unit -q
```

Runner offline dùng SQLite và Chroma trong thư mục tạm. Embedding là test double cố định. Tệp `results/offline_ablation.json` chứa dữ liệu từng câu, thứ hạng, khoảng cách L2, BM25, RRF và thời gian. Baseline trong tệp là thuật toán xếp hạng cũ được tái lập trên cùng fixture. Nó được báo cáo riêng với kết quả doanh nghiệp cũ.

Để gọi Gemini thật với fixture tổng hợp cố định, chạy lệnh dưới đây. Runner tạo kho tạm, dùng model từ cấu hình Agent và dừng sớm khi cấu hình hoặc quota chặn. Tệp kết quả là `results/live_synthetic.json`. Chấm khớp dữ kiện và chỉ số nguồn được tách khỏi đánh giá ngữ nghĩa và faithfulness.

```powershell
.venv\Scripts\python.exe rag_eval/live_fixture_eval.py --send-synthetic-fixture
.venv\Scripts\python.exe rag_eval/live_fixture_eval.py --send-synthetic-fixture --case missing
.venv\Scripts\python.exe rag_eval/live_fixture_eval.py --send-synthetic-fixture --case conflict
.venv\Scripts\python.exe rag_eval/offline_ablation.py --live-synthetic-embeddings
```

Các mục dưới đây mô tả runner lịch sử. Các tệp golden, trace và điểm lịch sử chưa được xác nhận độc lập. Runner lịch sử có thể đọc kho mặc định và gửi trích đoạn ra ngoài. Chỉ chạy chúng trong môi trường staging với corpus được phép gửi.

Bộ công cụ kiểm chuẩn, đánh giá và audit chất lượng hệ thống RAG độc lập và cô lập (Isolated Testing Harness) cho dự án Enterprise AI Agent.

---

## 1. Cấu trúc thư mục

```
rag_eval/
├── README.md                           # Hướng dẫn chi tiết cách chạy và cấu trúc eval
├── dataset_schema.json                 # JSON Schema chuẩn cho Golden Evaluation Dataset
├── golden_dataset.json                 # Tập dữ liệu kiểm chuẩn vàng (20+ test cases đa dạng)
├── metrics.py                          # Thư viện tính toán các chỉ số Retrieval & Generation IR
├── verify_runtime.py                   # Suite kiểm chứng thực thi Phase 2 (Test A, B, C, D, E)
├── ablation_test.py                    # Suite kiểm thử cắt bỏ Phase 3 (Semantic vs BM25 vs Current Hybrid vs RRF)
├── evaluate.py                         # Runner tự động chạy End-to-End Evaluation (Phase 5 & 6)
├── security_test.py                    # Suite kiểm tra Prompt Injection & Workspace Isolation (Phase 8)
├── debug_traces/                       # Chứa log chi tiết (Full Trace) của các test case thất bại hoặc cảnh báo
└── results/                            # Chứa kết quả kiểm chuẩn dạng JSON
    ├── phase2_verify_runtime_results.json
    ├── phase3_ablation_results.json
    ├── phase8_security_results.json
    ├── benchmark_summary.json
    └── evaluation_records.json
```

---

## 2. Hướng dẫn chạy các bài kiểm thử

### Kích hoạt môi trường ảo:
```powershell
Set-Location "d:\ODOO\Module AI - Agent"
.\.venv\Scripts\Activate.ps1
```

### Bước 1: Chạy kiểm chứng thực thi độc lập (Phase 2)
```powershell
python rag_eval/verify_runtime.py
```
*Kiểm tra 5 phép thử:*
- **Test A:** Context Removal (Ablation có vs không có context)
- **Test B:** Controlled Knowledge (Dữ liệu giả định bí mật để chứng minh LLM dùng dữ liệu truy xuất)
- **Test C:** Irrelevant Context (Kiểm tra chống ảo giác khi context lạc đề)
- **Test D:** Conflicting Documents (Xung đột tài liệu cũ v1.0 và mới v2.0)
- **Test E:** Missing Knowledge (Từ chối `INSUFFICIENT_EVIDENCE` khi không có dữ liệu)

### Bước 2: Chạy Ablation Study về Hybrid Search (Phase 3)
```powershell
python rag_eval/ablation_test.py
```
*So sánh 4 chiến lược:*
- Strategy A: Dense Semantic Search (ChromaDB)
- Strategy B: Tokenized BM25 Sparse Search
- Strategy C: Current "Hybrid" trong codebase (ChromaDB + chuỗi con khớp tuyệt đối)
- Strategy D: Reciprocal Rank Fusion (RRF) kết hợp BM25 và Vector Search

### Bước 3: Chạy End-to-End Golden Dataset Evaluation (Phase 5 & 6)
```powershell
python rag_eval/evaluate.py
```
*Tính toán:*
- Retrieval: HitRate@1, HitRate@3, MRR@3, Latency P50/P95
- Generation: Correctness, Abstention Accuracy, Citation Compliance, Latency P50/P95
- Xuất kết quả vào `results/benchmark_summary.json` và lưu trace tại `debug_traces/`.

### Bước 4: Chạy kiểm tra bảo mật & Prompt Injection (Phase 8)
```powershell
python rag_eval/security_test.py
```
*Kiểm tra Direct Prompt Injection, Indirect Prompt Injection (tài liệu chứa mã độc) và rò rỉ dữ liệu giữa các workspace.*

---

## 3. Các chỉ số đo lường (Metrics Defined)

1. **Hit Rate@K:** Tỷ lệ truy xuất có ít nhất 1 chunk mục tiêu nằm trong top-K.
2. **Mean Reciprocal Rank (MRR@K):** Nghịch đảo vị trí xuất hiện đầu tiên của chunk mục tiêu:
   $$\text{MRR@K} = \frac{1}{\text{rank}}$$
3. **Abstention Accuracy:** Độ chính xác của việc từ chối trả lời (`INSUFFICIENT_EVIDENCE`) khi câu hỏi không thuộc kho tri thức.
4. **Citation Compliance Rate:** Tỷ lệ câu trả lời trích dẫn nguồn đúng theo định dạng chuẩn `[Nguồn [x]: ...]`.
5. **Answer Correctness:** Mức độ trùng khớp thông tin cốt lõi so với Ground Truth.

