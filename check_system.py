#!/usr/bin/env python
# check_system.py — LogGazer System Readiness Check
#
# 用法:
#   python check_system.py
#   python check_system.py --verbose
#   python check_system.py --start-backend  # 检查并自动启动后端
#
# 检查项:
#   1. Python 版本 (≥ 3.10)
#   2. 关键依赖是否可导入
#   3. .env 文件是否存在
#   4. API Key 是否配置
#   5. 后端能否启动（可选）
#   6. 后端 health check（如果已运行）
#
# 退出码 0 = 一切就绪; 非 0 = 存在问题

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
EXIT_OK = 0
EXIT_WARN = 1
EXIT_ERR = 2

# Colors for terminal output
_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def color(text: str, code: str) -> str:
    """Wrap text in color codes (noop on Windows without ANSI support)."""
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return text  # ANSI not supported, return plain
    return f"{code}{text}{_RESET}"


def ok(msg: str) -> None:
    print(f"  {color('✓', _GREEN)} {msg}")


def warn(msg: str) -> None:
    print(f"  {color('⚠', _YELLOW)} {msg}")


def fail(msg: str) -> None:
    print(f"  {color('✗', _RED)} {msg}")


def section(title: str) -> None:
    print(f"\n{color(title, _BOLD + _CYAN)}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="LogGazer System Readiness Check")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--start-backend", action="store_true",
                        help="Auto-start backend if not running")
    parser.add_argument("--url", default=None,
                        help="Backend API URL (default from LOGGAZER_API_URL or http://127.0.0.1:8000)")
    args = parser.parse_args()

    warnings = 0
    errors = 0

    print(color("=" * 55, _CYAN))
    print(color("  LogGazer — System Readiness Check", _BOLD))
    print(color("=" * 55, _CYAN))

    # ── 1. Python version ────────────────────────────────
    section("1. Python Version")
    py_version = sys.version_info
    version_str = f"Python {py_version.major}.{py_version.minor}.{py_version.micro}"
    if py_version >= (3, 10):
        ok(f"{version_str} (≥ 3.10)")
    elif py_version >= (3, 8):
        warn(f"{version_str} (≥ 3.10 recommended, 3.8+ may work)")
        warnings += 1
    else:
        fail(f"{version_str} (requires ≥ 3.10)")
        errors += 1

    if args.verbose:
        print(f"    Executable: {sys.executable}")
        print(f"    Platform: {sys.platform}")

    # ── 2. Key dependencies ─────────────────────────────
    section("2. Key Dependencies")
    deps = {
        "streamlit": "streamlit",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "httpx": "httpx",
        "openai": "openai",
        "pydantic": "pydantic",
        "dotenv": "python-dotenv",
    }
    for mod_name, pkg_name in deps.items():
        try:
            __import__(mod_name)
            ok(f"{pkg_name}")
        except ImportError:
            fail(f"{pkg_name} — not installed. Run: pip install {pkg_name}")
            errors += 1

    # Optional dependencies
    optional = {
        "sentence_transformers": "sentence-transformers (cache)",
        "qdrant_client": "qdrant-client (cache)",
        "langgraph": "langgraph (multi-agent)",
        "redis": "redis (observability)",
        "prometheus_client": "prometheus-client (metrics)",
    }
    for mod_name, label in optional.items():
        try:
            __import__(mod_name)
            if args.verbose:
                ok(f"{label}")
        except ImportError:
            if args.verbose:
                warn(f"{label} — optional, not installed")

    # ── 3. Environment config ───────────────────────────
    section("3. Environment Configuration")
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        ok(".env file found")
    else:
        warn(".env file missing. Copy .env.example → .env and configure API key.")
        warnings += 1

    # Check API Key
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("API_KEY")
    if api_key:
        masked = api_key[:8] + "..." + api_key[-4:] if len(api_key) > 12 else "***"
        ok(f"API Key configured: {masked}")
    else:
        warn("DEEPSEEK_API_KEY not set — analysis will show fallback messages.")
        warnings += 1

    ai_provider = os.getenv("AI_PROVIDER", "openai")
    if args.verbose:
        print(f"    AI Provider: {ai_provider}")
        print(f"    Model: {os.getenv('DEEPSEEK_MODEL', 'deepseek-chat')}")

    # ── 4. Backend health check ─────────────────────────
    section("4. Backend Status")

    from backend_manager import BackendManager, DEFAULT_BACKEND_URL
    backend_url = args.url or DEFAULT_BACKEND_URL
    mgr = BackendManager(backend_url=backend_url)

    if mgr.is_backend_running():
        health = mgr.is_backend_running()
        ok(f"Backend is healthy at {backend_url}")

        from backend_manager import check_backend_health
        h = check_backend_health(backend_url)
        if h:
            print(f"    Status: {h.get('status', '?')}")
            print(f"    Version: {h.get('version', '?')}")
            print(f"    Uptime: {h.get('uptime_seconds', '?')}s")
    else:
        pid = mgr._read_pid_file()
        if pid:
            warn(f"Backend not reachable. PID file exists ({pid}) but health check failed.")
            warnings += 1
        else:
            warn(f"Backend not running at {backend_url}.")
            warnings += 1

        if args.start_backend:
            print(f"\n  {color('Starting backend...', _CYAN)}")
            if mgr.ensure_backend(timeout=30.0):
                ok(f"Backend started successfully at {backend_url}")
                warnings -= 1
            else:
                fail("Backend failed to start. Check .backend_stderr.log for details.")
                errors += 1
        else:
            print(f"  Run with {color('--start-backend', _YELLOW)} to auto-start, or:")
            print(f"    {color(f'{sys.executable} -m api.main', _YELLOW)}")
            print(f"    {color(f'python backend_manager.py start', _YELLOW)}")

    # ── 5. Summary ──────────────────────────────────────
    section("5. Summary")
    if errors == 0 and warnings == 0:
        print(f"  {color('✓ All checks passed! System is ready.', _GREEN)}")
        print(f"  Run: {color('streamlit run app.py', _BOLD)}")
        return EXIT_OK
    elif errors == 0:
        print(f"  {color(f'✓ Ready with {warnings} warning(s).', _YELLOW)}")
        print(f"  Run: {color('streamlit run app.py', _BOLD)}")
        return EXIT_WARN
    else:
        print(f"  {color(f'✗ {errors} error(s), {warnings} warning(s).', _RED)}")
        print(f"  Fix the errors above before running the app.")
        return EXIT_ERR


if __name__ == "__main__":
    sys.exit(main())
