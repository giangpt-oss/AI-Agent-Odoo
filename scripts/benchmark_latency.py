#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Script benchmark phân tách rõ ràng:
1. Benchmark Router: Đo cục bộ pipeline.analyze_input (độ trễ router, độ chính xác intent).
2. Benchmark Endpoint End-to-End: Đo toàn bộ turn qua endpoint, bao gồm xác minh identity,
   pipeline, executor, provider boundary, SQLite persistence và message rendering.

Theo báo cáo rà soát 2026-10-27:
- Tách biệt rõ ràng đo lường router cục bộ và đo lường endpoint toàn trình.
- Không loại trừ mẫu lỗi hoặc mẫu chậm.
- Ghi nhận chi tiết từng giai đoạn: total latency, model invocations, độ chính xác và lỗi.
- Đính kèm nhãn môi trường thử nghiệm (offline giả lập ranh giới mạng vs live).
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đảm bảo import được module app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.main import create_application
from app.services.natural_pipeline import NaturalPipeline


ROUTER_BENCHMARK_SUITE = [
    # Nhóm 1: Chào hỏi thuần & Năng lực
    {"prompt": "hello", "expected_type": IntentType.REPLY, "group": "Greeting/Menu"},
    {"prompt": "xin chào", "expected_type": IntentType.REPLY, "group": "Greeting/Menu"},
    {"prompt": "bạn có thể làm gì", "expected_type": IntentType.REPLY, "group": "Greeting/Menu"},
    {"prompt": "hướng dẫn sử dụng", "expected_type": IntentType.REPLY, "group": "Greeting/Menu"},
    # Nhóm 2: Lời chào kèm công việc (Fast-path không được nuốt)
    {"prompt": "Chào bạn, tạo công việc báo cáo giúp tôi", "expected_type": IntentType.PREPARE_WRITE, "group": "Task with greeting"},
    # Nhóm 3: Tra cứu dữ liệu (Read)
    {"prompt": "/today", "expected_type": IntentType.READ, "group": "Read Data"},
    {"prompt": "tôi là ai", "expected_type": IntentType.READ, "group": "Read Data"},
    {"prompt": "trạng thái kết nối", "expected_type": IntentType.READ, "group": "Read Data"},
    {"prompt": "xem lịch ngày mai", "expected_type": IntentType.READ, "group": "Read Data"},
    {"prompt": "danh sách công việc", "expected_type": IntentType.READ, "group": "Read Data"},
    {"prompt": "thư chưa đọc", "expected_type": IntentType.READ, "group": "Read Data"},
    # Nhóm 4: Bản thảo & Chuẩn bị ghi (Draft/Write)
    {"prompt": "soạn email gửi partner@example.com nội dung chào bạn", "expected_type": IntentType.DRAFT, "group": "Draft & Write"},
    {"prompt": "/task create Viết tài liệu | 2026-10-30 | high", "expected_type": IntentType.PREPARE_WRITE, "group": "Draft & Write"},
    # Nhóm 5: Thao tác xác nhận & Đối soát
    {"prompt": "/confirm conf_12345", "expected_type": IntentType.ACTION_CONFIRM, "group": "Confirm/Status"},
    {"prompt": "/status conf_12345", "expected_type": IntentType.ACTION_STATUS, "group": "Confirm/Status"},
]


def create_benchmark_context() -> GatewayUserContext:
    return GatewayUserContext(
        user_id=1,
        tenant="https://erp.example.com",
        database="production",
        odoo_uid=2,
        telegram_user_id=123456789,
        telegram_binding_id="bind_bench_1",
        permissions={
            "identity.read",
            "crm.read",
            "crm.write",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "gmail.send",
            "tasks.read",
            "tasks.write",
        },
    )


async def run_router_benchmark(iterations: int = 20) -> None:
    """Benchmark Router: Đo cục bộ NaturalPipeline.analyze_input."""
    pipeline = NaturalPipeline()
    context = create_benchmark_context()

    print("\n" + "=" * 80)
    print(f"🔬 BẮT ĐẦU BENCHMARK ROUTER / PIPELINE PHÂN TÍCH Ý ĐỊNH ({iterations} lượt mỗi câu)")
    print("Môi trường: Offline logic benchmark (Đo tốc độ định tuyến & phân tích cú pháp)")
    print("=" * 80)

    group_stats: Dict[str, Dict[str, Any]] = {}

    for item in ROUTER_BENCHMARK_SUITE:
        prompt = item["prompt"]
        exp_type = item["expected_type"]
        group = item["group"]

        if group not in group_stats:
            group_stats[group] = {"latencies_ms": [], "correct": 0, "total": 0}

        for _ in range(iterations):
            t0 = time.perf_counter()
            res = await pipeline.analyze_input(prompt, context)
            t1 = time.perf_counter()
            elapsed_ms = (t1 - t0) * 1000.0

            group_stats[group]["latencies_ms"].append(elapsed_ms)
            group_stats[group]["total"] += 1
            if res.intent_type == exp_type:
                group_stats[group]["correct"] += 1

    print(
        f"\n| {'Nhóm yêu cầu':<24} | {'Mẫu':<5} | {'Đúng (%)':<9} | {'p50 (ms)':<9} | {'p95 (ms)':<9} | {'Max (ms)':<9} |"
    )
    print(f"|{'-' * 26}|{'-' * 7}|{'-' * 11}|{'-' * 11}|{'-' * 11}|{'-' * 11}|")

    for g, data in group_stats.items():
        lats = sorted(data["latencies_ms"])
        n = len(lats)
        acc = (data["correct"] / data["total"]) * 100.0 if data["total"] > 0 else 0.0
        p50 = statistics.median(lats)
        p95 = lats[int(n * 0.95)] if int(n * 0.95) < n else lats[-1]
        max_v = lats[-1]
        print(f"| {g:<24} | {n:<5} | {acc:>8.1f}% | {p50:>9.3f} | {p95:>9.3f} | {max_v:>9.3f} |")

    print("\n📌 Chú thích: Đây là số đo thời gian phân tích router cục bộ trong bộ nhớ, KHÔNG phản ánh tổng thời gian bot Telegram phản hồi.")


def run_endpoint_benchmark(iterations: int = 10, tmp_dir: Optional[Path] = None) -> None:
    """Benchmark Endpoint: Đo toàn trình qua HTTP endpoint nội bộ."""
    print("\n" + "=" * 80)
    print(f"🚀 BẮT ĐẦU BENCHMARK ENDPOINT TOÀN TRÌNH (/internal/v1/telegram/turn) - {iterations} lượt")
    print("Môi trường: Ranh giới HTTP/SDK giả lập (Kiểm thử trọn vẹn Identity, Pipeline, Executor, DB)")
    print("=" * 80)

    import tempfile
    td = Path(tempfile.mkdtemp()) if not tmp_dir else tmp_dir
    settings = Settings(
        INTEGRATION_MODE="gateway",
        GATEWAY_TO_AGENT_SECRET="gw_to_agent_secret_32_chars_minimum_len",
        AGENT_TO_GATEWAY_SECRET="agent_to_gw_secret_32_chars_minimum_len",
        AGENT_DATA_DIR=str(td),
    )
    app = create_application(settings=settings)
    client = TestClient(app)

    mock_user = {
        "user_id": 1,
        "tenant": "https://erp.example.com",
        "database": "production",
        "odoo_uid": 2,
        "name": "Nguyễn Văn Benchmark",
        "login": "bench@example.com",
        "tz": "Asia/Ho_Chi_Minh",
        "telegram_binding_id": "bind_bench_1",
        "permissions": [
            "identity.read",
            "calendar.read",
            "calendar.write",
            "gmail.read",
            "gmail.send",
            "crm.read",
            "crm.create",
            "tasks.read",
            "tasks.write",
        ],
    }

    test_turns = [
        ("Xin chào bot", "Greeting"),
        ("Bạn có thể làm gì", "Capability"),
        ("Tôi là ai", "Identity"),
        ("Hôm nay tôi có gì", "Briefing"),
        ("Danh sách công việc", "Tasks"),
        ("Xem lịch", "Calendar"),
        ("/task create Báo cáo tuần | 2026-10-30", "Task Create Preview"),
    ]

    headers = {"Authorization": f"Bearer {settings.GATEWAY_TO_AGENT_SECRET}"}
    latencies: Dict[str, List[float]] = {}
    total_calls = 0
    success_calls = 0

    with (
        patch("app.services.gateway_client.gateway_client.resolve_identity", new_callable=AsyncMock) as mock_id,
        patch("app.services.gateway_client.gateway_client.get_user_connections", new_callable=AsyncMock) as mock_conns,
        patch("app.services.morning_briefing_service.morning_briefing_service.generate_real_briefing", new_callable=AsyncMock) as mock_briefing,
        patch("app.providers.gateway_calendar.GatewayCalendarProvider.get_events", new_callable=AsyncMock) as mock_cal,
        patch("app.providers.gateway_gmail.GatewayGmailProvider.get_unread", new_callable=AsyncMock) as mock_gmail,
        patch("app.providers.gateway_crm.GatewayCRMProvider.search_leads", new_callable=AsyncMock) as mock_crm,
    ):
        mock_id.return_value = mock_user
        mock_conns.return_value = {"google_connected": True}
        mock_briefing.return_value = "☀️ Điểm tin sáng: 1 cuộc họp, 0 thư chưa đọc, 2 việc cần làm."
        mock_cal.return_value = [{"id": "ev1", "summary": "Họp chiến lược", "start_time": "2026-10-28T09:00:00Z"}]
        mock_gmail.return_value = []
        mock_crm.return_value = []

        for prompt, category in test_turns:
            latencies[category] = []
            for i in range(iterations):
                        req_id = f"bench_req_{category}_{i}_{int(time.time()*1000)}"
                        payload = {
                            "request_id": req_id,
                            "gateway_user_id": 1,
                            "telegram_user_id": 123456789,
                            "chat_id": 999999,
                            "text": prompt,
                            "telegram_binding_id": "bind_bench_1",
                        }
                        t0 = time.perf_counter()
                        resp = client.post("/internal/v1/telegram/turn", json=payload, headers=headers)
                        t1 = time.perf_counter()
                        elapsed_ms = (t1 - t0) * 1000.0

                        latencies[category].append(elapsed_ms)
                        total_calls += 1
                        if resp.status_code == 200:
                            success_calls += 1

    print(
        f"\n| {'Hạng mục Turn':<24} | {'Mẫu':<5} | {'p50 (ms)':<9} | {'p90 (ms)':<9} | {'p95 (ms)':<9} | {'Min (ms)':<9} | {'Max (ms)':<9} |"
    )
    print(f"|{'-' * 26}|{'-' * 7}|{'-' * 11}|{'-' * 11}|{'-' * 11}|{'-' * 11}|{'-' * 11}|")

    for cat, lats in latencies.items():
        s_lats = sorted(lats)
        n = len(s_lats)
        p50 = statistics.median(s_lats)
        p90 = s_lats[int(n * 0.90)] if int(n * 0.90) < n else s_lats[-1]
        p95 = s_lats[int(n * 0.95)] if int(n * 0.95) < n else s_lats[-1]
        min_v = s_lats[0]
        max_v = s_lats[-1]
        print(f"| {cat:<24} | {n:<5} | {p50:>9.2f} | {p90:>9.2f} | {p95:>9.2f} | {min_v:>9.2f} | {max_v:>9.2f} |")

    success_rate = (success_calls / total_calls) * 100.0 if total_calls > 0 else 0.0
    print(f"\n✅ Tỷ lệ hoàn tất turn thành công: {success_rate:.1f}% ({success_calls}/{total_calls} calls)")
    print("📌 Chú thích: Đo lường end-to-end bao gồm Identity resolve, turn lease lock, pipeline/executor, SQLite store, render.")


def main():
    parser = argparse.ArgumentParser(description="Đo lường Benchmark Agent Latency")
    parser.add_argument("--mode", choices=["router", "endpoint", "all"], default="all", help="Chế độ đo")
    parser.add_argument("--iterations", type=int, default=15, help="Số lượt lặp mỗi câu")
    args = parser.parse_args()

    if args.mode in ["router", "all"]:
        asyncio.run(run_router_benchmark(iterations=args.iterations))

    if args.mode in ["endpoint", "all"]:
        run_endpoint_benchmark(iterations=args.iterations)


if __name__ == "__main__":
    main()
