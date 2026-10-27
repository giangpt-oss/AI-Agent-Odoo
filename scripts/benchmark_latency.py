#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Script benchmark và đo lường độ trễ (latency) của Natural Language Agent Pipeline.

Theo yêu cầu chuẩn hóa trong kế hoạch cải tiến:
- Nhóm 1: Chào hỏi / Năng lực (Target: p95 <= 2.0s)
- Nhóm 2: Đọc 1 nguồn dữ liệu (Target: p95 <= 6.0s)
- Nhóm 3: Điểm tin / Xem trước (Target: p95 <= 8.0s)

Cách chạy:
    python scripts/benchmark_latency.py [--iterations 30] [--live]
"""

import argparse
import asyncio
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Đảm bảo UTF-8 encoding trên Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Đảm bảo import được module app khi chạy trực tiếp từ thư mục bất kỳ
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.services.natural_pipeline import NaturalPipeline


GROUP_PROMPTS = {
    "Group 1 (Chào hỏi / Năng lực)": {
        "target_p95": 2.0,
        "prompts": ["hello", "chào bot", "bạn có thể làm gì", "hướng dẫn sử dụng", "tính năng"],
    },
    "Group 2 (Đọc 1 nguồn dữ liệu)": {
        "target_p95": 6.0,
        "prompts": ["tôi là ai", "hồ sơ của tôi", "xem lịch", "thư chưa đọc", "danh sách công việc", "trạng thái kết nối"],
    },
    "Group 3 (Điểm tin / Tổng hợp)": {
        "target_p95": 8.0,
        "prompts": ["hôm nay tôi có gì", "hôm nay tôi cần làm gì", "điểm tin", "lịch hôm nay", "công việc hôm nay"],
    },
}


def create_mock_context() -> GatewayUserContext:
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
            "tasks.read",
            "tasks.write",
        },
    )


async def run_benchmark(iterations: int = 30) -> Dict[str, Any]:
    pipeline = NaturalPipeline()
    context = create_mock_context()
    results = {}

    print("=" * 80)
    print(f"🚀 BẮT ĐẦU BENCHMARK PIPELINE ĐỘ TRỄ ({iterations} lượt mỗi nhóm)")
    print("=" * 80)

    all_passed = True

    for group_name, cfg in GROUP_PROMPTS.items():
        prompts = cfg["prompts"]
        target_p95 = cfg["target_p95"]
        latencies_ms: List[float] = []

        print(f"\n▶ Đang đo {group_name}...")
        for i in range(iterations):
            prompt = prompts[i % len(prompts)]
            t0 = time.perf_counter()
            res = await pipeline.analyze_input(prompt, context)
            t1 = time.perf_counter()
            elapsed_ms = (t1 - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

        # Tính toán phân vị
        latencies_ms.sort()
        n = len(latencies_ms)
        p50 = statistics.median(latencies_ms)
        p90 = latencies_ms[int(n * 0.90)]
        p95 = latencies_ms[int(n * 0.95)] if int(n * 0.95) < n else latencies_ms[-1]
        min_ms = latencies_ms[0]
        max_ms = latencies_ms[-1]
        mean_ms = statistics.mean(latencies_ms)
        stdev_ms = statistics.stdev(latencies_ms) if n > 1 else 0.0

        p95_sec = p95 / 1000.0
        passed = p95_sec <= target_p95
        if not passed:
            all_passed = False

        results[group_name] = {
            "n": n,
            "min_ms": min_ms,
            "p50_ms": p50,
            "mean_ms": mean_ms,
            "p90_ms": p90,
            "p95_ms": p95,
            "max_ms": max_ms,
            "stdev_ms": stdev_ms,
            "target_p95_sec": target_p95,
            "passed": passed,
        }

    # In báo cáo bảng Markdown
    print("\n" + "=" * 80)
    print("📊 KẾT QUẢ ĐO LƯỜNG ĐỘ TRỄ (LATENCY BENCHMARK)")
    print("=" * 80)
    print(
        f"| {'Nhóm yêu cầu':<30} | {'Mẫu':<4} | {'p50 (ms)':<9} | {'p90 (ms)':<9} | {'p95 (ms)':<9} | {'Target p95':<10} | {'Trạng thái':<8} |"
    )
    print(
        f"|{'-' * 32}|{'-' * 6}|{'-' * 11}|{'-' * 11}|{'-' * 11}|{'-' * 12}|{'-' * 10}|"
    )

    for group_name, r in results.items():
        status = "✅ PASS" if r["passed"] else "❌ FAIL"
        target_str = f"<= {r['target_p95_sec']:.1f}s"
        print(
            f"| {group_name:<30} | {r['n']:<4} | {r['p50_ms']:<9.2f} | {r['p90_ms']:<9.2f} | {r['p95_ms']:<9.2f} | {target_str:<10} | {status:<8} |"
        )
    print("=" * 80)

    if all_passed:
        print("🎉 TẤT CẢ CÁC NHÓM ĐỀU ĐẠT CHỈ TIÊU ĐỘ TRỄ (P95 TARGET MET)!")
    else:
        print("⚠️ CÓ NHÓM KHÔNG ĐẠT CHỈ TIÊU ĐỘ TRỄ!")

    return {"results": results, "all_passed": all_passed}


def main():
    parser = argparse.ArgumentParser(description="Đo latency Natural Language Agent Pipeline")
    parser.add_argument("--iterations", type=int, default=30, help="Số lần chạy cho mỗi nhóm (mặc định: 30)")
    args = parser.parse_args()

    bench = asyncio.run(run_benchmark(iterations=args.iterations))
    sys.exit(0 if bench["all_passed"] else 1)


if __name__ == "__main__":
    main()
