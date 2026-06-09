# metrics_server.py - Prometheus Metrics HTTP Server
#
# 职责：
#   1. 在独立线程中运行 HTTP Server，暴露 /metrics 端点
#   2. 供 Prometheus 抓取（scrape interval 建议 15s）
#   3. 不阻塞 Streamlit 主线程
#
# 使用方式：
#   方式 1（自动启动）：import metrics_server; metrics_server.start()
#   方式 2（独立运行）：python metrics_server.py（调试用）
#
# 端口：默认 9090（可通过 METRICS_PORT 环境变量覆盖）

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# 模块级状态
_server_thread: Optional[threading.Thread] = None
_server_started = False


def start(port: int = 9090, addr: str = "0.0.0.0") -> bool:
    """
    在独立线程中启动 Prometheus Metrics HTTP Server

    参数:
        port: 监听端口（默认 9090）
        addr: 监听地址（默认 0.0.0.0）

    返回:
        True = 启动成功或已启动，False = 启动失败
    """
    global _server_thread, _server_started

    if _server_started:
        logger.debug("Metrics server 已启动，跳过")
        return True

    try:
        from prometheus_client import start_http_server

        # start_http_server 是阻塞式的，需要在独立线程中运行
        def _run_server():
            try:
                start_http_server(port, addr=addr)
                logger.info("Prometheus Metrics server 启动: http://%s:%d/metrics", addr, port)
                # 保持线程存活
                threading.Event().wait()  # 永久阻塞
            except Exception as e:
                logger.error("Metrics server 运行失败: %s", e)

        _server_thread = threading.Thread(
            target=_run_server,
            name="metrics-server",
            daemon=True,  # 主进程退出时自动终止
        )
        _server_thread.start()
        _server_started = True

        logger.info("Metrics server 线程已启动 (port=%d)", port)
        return True

    except ImportError:
        logger.warning("prometheus_client 未安装，Metrics server 不可用")
        return False
    except Exception as e:
        logger.error("Metrics server 启动失败: %s", e)
        return False


def is_running() -> bool:
    """检查 Metrics server 是否正在运行"""
    return _server_started and _server_thread is not None and _server_thread.is_alive()


def get_metrics_url(port: int = 9090) -> str:
    """获取 Metrics 端点 URL"""
    return f"http://localhost:{port}/metrics"


# ============================================================
#  独立运行入口（调试用）
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    port = int(os.getenv("METRICS_PORT", "9090"))

    from prometheus_client import start_http_server

    print(f"Starting Prometheus Metrics server on port {port}...")
    print(f"Metrics available at: http://localhost:{port}/metrics")
    print("Press Ctrl+C to stop.")

    start_http_server(port)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
