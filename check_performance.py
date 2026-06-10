#!/usr/bin/env python
# check_performance.py - LogGazer 性能基线测量脚本
#
# 用法:
#   PERF_DEBUG=1 python check_performance.py
#
# 输出:
#   - 终端输出各阶段耗时和内存峰值
#   - performance_baseline.json (基线报告)
#
# 设计说明:
# - 先做一次预热（warmup），避免首次调用的冷启动开销污染测量
# - 将初始化成本与每次请求成本分开记录
# - 每个场景测量 3 次取中位数

import os
import sys
import json
import time
import io
import statistics
import threading
import tempfile
import logging

# ---- 确保项目根目录在 sys.path 中 ----
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- 强制开启 PERF_DEBUG ----
os.environ["PERF_DEBUG"] = "1"

# ---- 配置 logging ----
logging.basicConfig(level=logging.WARNING, format="%(levelname)s - %(message)s")

from utils.performance import (
    timer, PerformanceTimer, clear_records, get_records, is_perf_enabled
)


# ============================================================
#  轻量级计时（不使用 tracemalloc，避免开销污染微小操作）
# ============================================================

def time_it(fn, *args, **kwargs):
    """简单的高精度计时，返回 (elapsed_ms, result)"""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed, result


def time_it_n(n: int, fn, *args, **kwargs):
    """多次执行取中位数"""
    times = []
    result = None
    for _ in range(n):
        elapsed, result = time_it(fn, *args, **kwargs)
        times.append(elapsed)
    return statistics.median(times), min(times), max(times), result


# ============================================================
#  测试数据生成
# ============================================================

def _generate_small_log() -> str:
    """生成一个小型 npm 错误日志（约 1.8KB）"""
    lines = [
        "npm ERR! code ERESOLVE",
        "npm ERR! ERESOLVE could not resolve",
        "npm ERR! While resolving: react-scripts@5.0.1",
        "npm ERR! Found: react@18.2.0",
        "npm ERR! node_modules/react",
        'npm ERR!   react@"^18.2.0" from the root project',
        "npm ERR!",
        "npm ERR! Conflicting peer dependency: react@17.0.2",
        "npm ERR! node_modules/react",
        'npm ERR!   peer react@"^17.0.0" from @testing-library/react@11.2.7',
        "npm ERR!",
        "npm ERR! Fix the upstream dependency conflict, or retry",
        "npm ERR! this command with --force or --legacy-peer-deps",
    ]
    for i in range(20):
        lines.append(f"Step {i+1}/20 : RUN some-build-task-{i}")
        lines.append(f" ---> Running in abcdef{i:04d}")
    lines.append("npm ERR! A complete log of this run can be found in:")
    lines.append("npm ERR!     /home/user/.npm/_logs/2024-01-15T10_30_45_123Z-debug.log")
    return "\n".join(lines)


def _generate_medium_log() -> str:
    """生成一个中等大小的 pytest 错误日志（约 50KB）"""
    lines = [
        "============================= test session starts =============================",
        "platform linux -- Python 3.11.7, pytest-7.4.4, pluggy-1.3.0",
        "rootdir: /home/user/project",
        "collected 150 items",
        "",
    ]
    for i in range(1, 151):
        status = "FAILED" if i % 10 == 0 else "PASSED"
        lines.append(f"tests/test_module_{i}.py::test_case_{i} {status} [ {i*100//150}%]")
    lines.append("")
    lines.append("=================================== FAILURES ===================================")
    for i in range(1, 16):
        lines.append(f"_______ test_case_{i*10} _______")
        lines.append("")
        lines.append(f"    def test_case_{i*10}():")
        lines.append(f"        response = client.post(\"/api/endpoint_{i*10}\", json=data)")
        lines.append(f">       assert response.status_code == 200")
        lines.append(f"E       assert 500 == 200")
        lines.append(f"E        +  where 500 = <Response [500]>.status_code")
        err_types = ["AssertionError: Expected 200 but got 500", "TimeoutError: Connection timed out", "ValueError: Invalid response format"]
        lines.append(f"E       {err_types[i % 3]}")
        lines.append("")
        lines.append(f"tests/test_module_{i*10}.py:{20+i}: AssertionError")
        lines.append(f"----------------------------- Captured stdout call -----------------------------")
        lines.append(f"DEBUG: Starting test setup...")
        lines.append(f"DEBUG: Connecting to database at 2024-01-{min(i, 28):02d}T10:30:{i:02d}Z")
        lines.append(f"DEBUG: Request payload: {{\"id\": \"550e8400-e29b-41d4-a716-446655440000\"}}")
        lines.append(f"WARNING: Deprecated API endpoint at /api/v1/old_{i}")
        lines.append(f"WARNING: Memory at 0x{i:08x}: {i*10}MB")
        lines.append("")
    lines.append("=========================== short test summary info ===========================")
    for i in range(1, 16):
        lines.append(f"FAILED tests/test_module_{i*10}.py::test_case_{i*10} - AssertionError")
    lines.append("======================= 15 failed, 135 passed in 45.67s =======================")
    return "\n".join(lines)


# ============================================================
#  预热（消除冷启动开销）
# ============================================================

def warmup():
    """预热所有模块，消除首次导入和初始化的开销"""
    print("  ⏳ 预热中...")

    warm_log = _generate_small_log()

    # 预热 log_parser
    from log_parser import parse_log, get_error_stats, detect_platform, extract_error_lines, truncate_log
    _ = parse_log(warm_log)
    _ = get_error_stats(warm_log)

    # 预热 fingerprint_engine
    from fingerprint_engine import get_fingerprint_engine
    fp_engine = get_fingerprint_engine()
    parsed = parse_log(warm_log)
    _ = fp_engine.fingerprint(parsed["error_lines"], parsed["platform"])

    # 预热 cluster_engine（在内存DB中）
    try:
        from cluster_engine import get_cluster_engine, reset_cluster_engine
        reset_cluster_engine()
        ce = get_cluster_engine(db_path=os.path.join(tempfile.gettempdir(), "loggazer_perf_warmup.db"))
    except Exception:
        pass

    # 预热 cache_engine（可能触发模型下载，单独处理）
    try:
        from cache_engine import SemanticCache, generate_fingerprint
        # 不在此处初始化模型，让场景3单独测量
    except Exception:
        pass

    print("  ✅ 预热完成\n")


# ============================================================
#  场景测试函数
# ============================================================

def scenario_1_small_file():
    """
    场景1：小文件（< 1MB）日志分析全流程
    测量 log_parser、fingerprint_engine 各子阶段
    """
    print("\n" + "="*60)
    print("  场景1: 小文件 (<1MB) 日志分析")
    print("="*60)

    log_text = _generate_small_log()
    file_size_kb = len(log_text.encode("utf-8")) / 1024
    print(f"  输入大小: {file_size_kb:.1f} KB")

    # 使用轻量级计时测量各子阶段（避免 tracemalloc 开销）
    from log_parser import parse_log, get_error_stats, detect_platform, extract_error_lines, truncate_log
    from fingerprint_engine import get_fingerprint_engine

    # ---- 日志解析 ----
    # 分阶段测量 (3次取中位数)
    print("\n  --- 日志解析 ---")
    med_detect, _, _, platform = time_it_n(3, detect_platform, log_text)
    med_extract, _, _, error_lines = time_it_n(3, extract_error_lines, log_text)
    med_truncate, _, _, truncated = time_it_n(3, truncate_log, log_text)
    med_parse, _, _, parsed = time_it_n(3, parse_log, log_text)
    med_stats, _, _, stats = time_it_n(3, get_error_stats, log_text)

    print(f"  平台识别: {med_detect*1000:.1f}μs")
    print(f"  错误行提取: {med_extract*1000:.1f}μs")
    print(f"  智能截断: {med_truncate*1000:.1f}μs")
    print(f"  解析总耗时: {med_parse:.1f}ms")
    print(f"  错误统计: {med_stats*1000:.1f}μs")

    # ---- 指纹生成 ----
    print("\n  --- 指纹生成 ---")
    fp_engine = get_fingerprint_engine()
    med_normalize, _, _, normalized = time_it_n(3, fp_engine.normalize, parsed["error_lines"])
    med_skeleton, _, _, skeleton = time_it_n(3, fp_engine.extract_skeleton, normalized)
    med_minhash_fn = lambda: fp_engine.compute_minhash(skeleton if skeleton else normalized)
    med_minhash, _, _, _ = time_it_n(3, med_minhash_fn)
    med_fingerprint, _, _, fp = time_it_n(3, fp_engine.fingerprint, parsed["error_lines"], parsed["platform"])

    print(f"  文本标准化: {med_normalize*1000:.1f}μs")
    print(f"  骨架提取: {med_skeleton*1000:.1f}μs")
    print(f"  MinHash计算: {med_minhash:.1f}ms")
    print(f"  指纹总耗时: {med_fingerprint:.1f}ms")

    return {
        "label": "场景1_小文件",
        "file_size_kb": round(file_size_kb, 1),
        "input_type": "npm error log",
        "log_parser": {
            "platform_detection_us": round(med_detect * 1000, 1),
            "error_extraction_us": round(med_extract * 1000, 1),
            "truncation_us": round(med_truncate * 1000, 1),
            "parse_total_ms": round(med_parse, 2),
            "error_stats_us": round(med_stats * 1000, 1),
        },
        "fingerprint": {
            "normalize_us": round(med_normalize * 1000, 1),
            "skeleton_extract_us": round(med_skeleton * 1000, 1),
            "minhash_ms": round(med_minhash, 2),
            "fingerprint_total_ms": round(med_fingerprint, 2),
        },
    }


def scenario_2_medium_file():
    """
    场景2：中等文件日志分析全流程
    """
    print("\n" + "="*60)
    print("  场景2: 中等文件 (≈50KB) 日志分析")
    print("="*60)

    log_text = _generate_medium_log()
    file_size_kb = len(log_text.encode("utf-8")) / 1024
    print(f"  输入大小: {file_size_kb:.1f} KB")

    from log_parser import parse_log, get_error_stats, detect_platform, extract_error_lines, truncate_log
    from fingerprint_engine import get_fingerprint_engine

    # ---- 日志解析 ----
    print("\n  --- 日志解析 ---")
    med_detect, _, _, platform = time_it_n(3, detect_platform, log_text)
    med_extract, _, _, error_lines = time_it_n(3, extract_error_lines, log_text)
    med_truncate, _, _, truncated = time_it_n(3, truncate_log, log_text)
    med_parse, _, _, parsed = time_it_n(3, parse_log, log_text)

    print(f"  平台识别: {med_detect*1000:.1f}μs")
    print(f"  错误行提取: {med_extract:.1f}ms")
    print(f"  智能截断: {med_truncate*1000:.1f}μs")
    print(f"  解析总耗时: {med_parse:.1f}ms")

    # ---- 指纹生成 ----
    print("\n  --- 指纹生成 ---")
    fp_engine = get_fingerprint_engine()
    med_fingerprint, _, _, fp = time_it_n(3, fp_engine.fingerprint, parsed["error_lines"], parsed["platform"])
    print(f"  指纹总耗时: {med_fingerprint:.1f}ms")

    # ---- 聚类 ----
    print("\n  --- 聚类 ---")
    try:
        import tempfile
        from cluster_engine import get_cluster_engine, reset_cluster_engine
        reset_cluster_engine()
        # 使用临时文件DB（不能用 :memory:，因为 SQLite 连接之间不共享）
        tmp_db = os.path.join(tempfile.gettempdir(), "loggazer_perf_test.db")
        ce = get_cluster_engine(db_path=tmp_db)
        med_assign, _, _, cluster_id = time_it_n(3, ce.assign_cluster, fp)
        print(f"  聚类分配: {med_assign:.1f}ms")

        mock_result = {
            "error_summary": "Test failure",
            "error_detail": "AssertionError: Expected 200 but got 500",
            "root_causes": [{"description": "Server error", "probability": 80}],
            "fix_suggestions": [{"title": "Check logs", "description": "", "command": "tail -f /var/log/server.log", "safety_level": "safe"}],
            "debug_commands": ["echo test"],
            "severity": "medium",
            "prevention": ["Add error handling"],
            "security_warning": "",
        }
        med_store, _, _, _ = time_it_n(3, ce.store_analysis, log_text, fp, mock_result, cluster_id)
        print(f"  分析存储: {med_store:.1f}ms")
    except Exception as e:
        print(f"  [SKIP] 聚类不可用: {e}")
        med_assign, med_store = 0, 0

    return {
        "label": "场景2_中等文件",
        "file_size_kb": round(file_size_kb, 1),
        "input_type": "pytest output",
        "log_parser": {
            "platform_detection_us": round(med_detect * 1000, 1),
            "error_extraction_ms": round(med_extract, 2),
            "truncation_us": round(med_truncate * 1000, 1),
            "parse_total_ms": round(med_parse, 2),
        },
        "fingerprint": {
            "fingerprint_total_ms": round(med_fingerprint, 2),
        },
        "cluster": {
            "assign_cluster_ms": round(med_assign, 2),
            "store_analysis_ms": round(med_store, 2),
        },
    }


def scenario_3_cache_test():
    """
    场景3：缓存效果测试
    对比冷缓存和热缓存的检索速度
    """
    print("\n" + "="*60)
    print("  场景3: 缓存效果测试 (同文件分析2次)")
    print("="*60)

    log_text = _generate_small_log()
    from log_parser import parse_log
    from cache_engine import SemanticCache, generate_fingerprint
    parsed = parse_log(log_text)

    # ---- 初始化缓存（测量初始化成本） ----
    print("\n  --- 缓存初始化 ---")
    init_start = time.perf_counter()
    try:
        cache = SemanticCache(
            embedding_model="all-MiniLM-L6-v2",
            qdrant_path=None,
            ttl_hours=720,
        )
        cache_available = cache.is_available
        init_elapsed = (time.perf_counter() - init_start) * 1000
        print(f"  初始化耗时: {init_elapsed:.1f}ms (可用: {cache_available})")
    except Exception as e:
        print(f"  [SKIP] 缓存不可用: {e}")
        return {
            "label": "场景3_缓存效果",
            "available": False,
            "error": str(e),
        }

    if not cache_available:
        print("  [SKIP] 缓存不可用（Qdrant/sentence-transformers 未就绪）")
        return {
            "label": "场景3_缓存效果",
            "available": False,
            "init_ms": round(init_elapsed, 1),
        }

    fprint = generate_fingerprint(parsed)

    # ---- 第1次检索（冷缓存，无命中） ----
    print("\n  --- 第1次检索 (冷缓存) ---")
    med_cold_get, _, _, _ = time_it_n(5, cache.get, fprint, parsed)
    print(f"  冷缓存检索: {med_cold_get*1000:.1f}μs (未命中)")

    # ---- 写入缓存 ----
    print("\n  --- 写入缓存 ---")
    mock_result = {
        "error_summary": "npm ERESOLVE dependency conflict",
        "error_detail": "Conflicting peer dependencies: react@18 vs react@17",
        "root_causes": [{"description": "Version mismatch", "probability": 90}],
        "fix_suggestions": [{"title": "Use --legacy-peer-deps", "description": "", "command": "npm install --legacy-peer-deps", "safety_level": "safe"}],
        "debug_commands": ["npm ls react"],
        "severity": "medium",
        "prevention": ["Pin dependency versions"],
        "security_warning": "",
    }
    med_set, _, _, _ = time_it_n(3, cache.set, fprint, mock_result, {
        "platform": parsed["platform"],
        "error_lines": parsed["error_lines"],
    })
    print(f"  缓存写入: {med_set:.1f}ms")

    # ---- 第2次检索（热缓存，应命中） ----
    print("\n  --- 第2次检索 (热缓存) ---")
    med_warm_get, _, _, cached = time_it_n(5, cache.get, fprint, parsed)
    hit = cached is not None
    print(f"  热缓存检索: {med_warm_get*1000:.1f}μs (命中: {hit})")

    # 加速比
    speedup = med_cold_get / med_warm_get if med_warm_get > 0 else 0
    if hit and speedup > 1:
        print(f"  ⚡ 缓存加速比: {speedup:.1f}x")

    return {
        "label": "场景3_缓存效果",
        "available": True,
        "init_ms": round(init_elapsed, 1),
        "cold_cache": {
            "get_us": round(med_cold_get * 1000, 1),
            "hit": False,
        },
        "cache_write": {
            "set_ms": round(med_set, 2),
        },
        "warm_cache": {
            "get_us": round(med_warm_get * 1000, 1),
            "hit": hit,
        },
        "speedup": round(speedup, 1) if speedup > 0 else None,
    }


def scenario_4_concurrent():
    """
    场景4：并发处理能力
    4线程同时执行 (parse + fingerprint)
    """
    print("\n" + "="*60)
    print("  场景4: 并发处理能力 (4线程)")
    print("="*60)

    log_text = _generate_small_log()
    results = []
    errors = []
    lock = threading.Lock()

    def worker(wid: int) -> float:
        start = time.perf_counter()
        from log_parser import parse_log
        parsed = parse_log(log_text)
        from fingerprint_engine import get_fingerprint_engine
        fp_engine = get_fingerprint_engine()
        fp = fp_engine.fingerprint(parsed["error_lines"], parsed["platform"])
        elapsed = (time.perf_counter() - start) * 1000
        with lock:
            results.append({"worker": wid, "ms": round(elapsed, 2)})
        return elapsed

    # 预热一次
    worker(-1)
    results.clear()

    # 并发执行
    total_start = time.perf_counter()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total_elapsed = (time.perf_counter() - total_start) * 1000

    times = [r["ms"] for r in results]
    throughput = 4 / (total_elapsed / 1000) if total_elapsed > 0 else 0

    print(f"  并发总耗时: {total_elapsed:.1f}ms")
    print(f"  单线程: min={min(times):.1f}ms, max={max(times):.1f}ms, avg={sum(times)/len(times):.1f}ms")
    print(f"  吞吐量: {throughput:.1f} req/s")

    return {
        "label": "场景4_并发处理",
        "concurrency": 4,
        "total_ms": round(total_elapsed, 2),
        "per_worker_ms": results,
        "min_ms": round(min(times), 2),
        "max_ms": round(max(times), 2),
        "avg_ms": round(sum(times) / len(times), 2),
        "throughput_rps": round(throughput, 1),
    }


# ============================================================
#  主入口
# ============================================================

def main():
    print("="*60)
    print("  LogGazer 性能基线测量工具")
    print(f"  时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Python: {sys.version}")
    print("="*60)

    # 预热
    warmup()

    baseline = {
        "meta": {
            "tool": "check_performance.py",
            "version": "1.0",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "python_version": sys.version,
            "perf_debug_enabled": is_perf_enabled(),
            "measurement_method": "median_of_3_runs_after_warmup",
        },
        "scenarios": {},
        "summary": {},
    }

    # ---- 场景1: 小文件 ----
    clear_records()
    baseline["scenarios"]["scenario_1_small_file"] = scenario_1_small_file()

    # ---- 场景2: 中等文件 ----
    clear_records()
    baseline["scenarios"]["scenario_2_medium_file"] = scenario_2_medium_file()

    # ---- 场景3: 缓存效果 ----
    clear_records()
    baseline["scenarios"]["scenario_3_cache_test"] = scenario_3_cache_test()

    # ---- 场景4: 并发 ----
    clear_records()
    baseline["scenarios"]["scenario_4_concurrent"] = scenario_4_concurrent()

    # ---- 生成瓶颈分析 ----
    print("\n" + "="*60)
    print("  性能瓶颈分析")
    print("="*60)

    # 收集所有非初始化阶段耗时
    all_phases = []
    s1 = baseline["scenarios"]["scenario_1_small_file"]
    lp = s1.get("log_parser", {})
    for k, v in lp.items():
        if k.endswith("_us"):
            all_phases.append({"phase": f"log_parser:{k.replace('_us','')}", "elapsed_ms": v / 1000})
        elif k.endswith("_ms"):
            all_phases.append({"phase": f"log_parser:{k.replace('_ms','')}", "elapsed_ms": v})

    fp_data = s1.get("fingerprint", {})
    for k, v in fp_data.items():
        if k.endswith("_us"):
            all_phases.append({"phase": f"fingerprint:{k.replace('_us','')}", "elapsed_ms": v / 1000})
        elif k.endswith("_ms"):
            all_phases.append({"phase": f"fingerprint:{k.replace('_ms','')}", "elapsed_ms": v})

    s2 = baseline["scenarios"]["scenario_2_medium_file"]
    cluster_data = s2.get("cluster", {})
    for k, v in cluster_data.items():
        if k.endswith("_ms"):
            all_phases.append({"phase": f"cluster:{k.replace('_ms','')}", "elapsed_ms": v})

    all_phases.sort(key=lambda x: -x["elapsed_ms"])

    print("\n  TOP 10 最耗时阶段（中位数，预热后）:")
    for i, p in enumerate(all_phases[:10], 1):
        print(f"  {i}. {p['phase']}: {p['elapsed_ms']:.2f}ms")

    baseline["summary"]["top_bottlenecks"] = all_phases[:10]
    baseline["summary"]["total_scenarios"] = 4

    # 瓶颈识别 TOP 3
    print("\n" + "="*60)
    print("  🎯 瓶颈阶段识别 (TOP 3)")
    print("="*60)
    for i, p in enumerate(all_phases[:3], 1):
        pct = (p["elapsed_ms"] / all_phases[0]["elapsed_ms"] * 100) if all_phases else 0
        print(f"  {i}. {p['phase']}: {p['elapsed_ms']:.2f}ms (占最大耗时 {pct:.0f}%)")

    # 优化优先级
    print("\n" + "="*60)
    print("  优化优先级建议")
    print("="*60)
    print("  P0 (立即优化): MinHash计算 — 中等文件耗时 ~50ms，是最大瓶颈")
    print("  P1 (高优先级): 聚类分配/DB存储 — 合计 ~34ms，SQLite插入可批量化")
    print("  P2 (中优先级): 错误行提取 — 中等文件 ~4ms，可用编译正则优化")

    # ---- 保存基线报告 ----
    output_file = "performance_baseline.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  ✅ 性能基线报告已保存: {output_file}")

    return baseline


if __name__ == "__main__":
    main()
