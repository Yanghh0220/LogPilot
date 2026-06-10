# api/dependencies.py - FastAPI 依赖注入
#
# 职责：
#   1. 提供 FastAPI 依赖注入函数（Depends）
#   2. 管理全局单例（analyzer, rate_limiter, observability）
#   3. 延迟初始化以避免模块级循环导入
#
# 设计原则：
#   - 所有重量级对象延迟加载（避免 import 时就需要 API Key）
#   - 单例模式避免重复初始化
#   - 与 api/main.py 解耦，可独立测试

import logging
import os
import uuid
from typing import Optional

from fastapi import Header, HTTPException, Depends

from api.schemas import ProblemDetail

logger = logging.getLogger("api")


# ============================================================
#  Analyzer 单例（延迟加载）
# ============================================================

_analyze_log = None


def get_analyzer():
    """
    获取 analyze_log 函数单例（延迟加载）

    延迟加载的原因：
    - analyzer 模块在 import 时会创建 OpenAI 客户端
    - 如果 API Key 未配置，模块级 import 会失败
    - 延迟到第一次 API 调用时才加载，允许服务先启动
    """
    global _analyze_log
    if _analyze_log is None:
        from analyzer import analyze_log
        _analyze_log = analyze_log
    return _analyze_log


# ============================================================
#  Request ID 提取/生成
# ============================================================


async def get_request_id(
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
) -> str:
    """
    提取或生成 Request ID 用于链路追踪

    优先使用客户端传入的 X-Request-ID，
    如果没有则自动生成 UUID v4。
    """
    return x_request_id or str(uuid.uuid4())


# ============================================================
#  API Key 认证
# ============================================================


async def get_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[str]:
    """从请求头提取 API Key。本地模式下返回 None。"""
    return x_api_key


def verify_api_key(api_key: Optional[str] = Depends(get_api_key)) -> Optional[str]:
    """
    验证 API Key（Cloud 模式）

    逻辑：
    - 如果服务端未配置 LOGGAZER_API_KEY → 本地模式，放行所有请求
    - 如果服务端配置了 LOGGAZER_API_KEY → Cloud 模式，校验 X-API-Key 头
    """
    configured_key = os.getenv("LOGGAZER_API_KEY")

    if not configured_key:
        # Local mode: no authentication required
        return None

    if not api_key:
        raise HTTPException(
            status_code=401,
            detail=ProblemDetail(
                type="https://loggazer.dev/errors/unauthorized",
                title="Authentication Required",
                status=401,
                detail=(
                    "X-API-Key header is required in cloud mode. "
                    "Set LOGGAZER_API_KEY environment variable on the server, "
                    "and pass it as X-API-Key header."
                ),
                instance="/v1/analyze",
            ).model_dump(),
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key != configured_key:
        raise HTTPException(
            status_code=401,
            detail=ProblemDetail(
                type="https://loggazer.dev/errors/unauthorized",
                title="Invalid API Key",
                status=401,
                detail="The provided X-API-Key is invalid.",
                instance="/v1/analyze",
            ).model_dump(),
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key


# ============================================================
#  Rate Limiter 单例（延迟加载）
# ============================================================

_rate_limiter = None


def get_rate_limiter():
    """获取或初始化 TokenBucketRateLimiter 单例"""
    global _rate_limiter
    if _rate_limiter is None:
        from rate_limiter import TokenBucketRateLimiter
        _rate_limiter = TokenBucketRateLimiter(redis_client=None)
    return _rate_limiter


def reset_rate_limiter():
    """重置限流器单例（仅供测试使用）"""
    global _rate_limiter
    _rate_limiter = None


# ============================================================
#  Observability 单例（延迟加载）
# ============================================================

_obs = None


def get_observability():
    """获取或初始化 ObservabilityManager 单例"""
    global _obs
    if _obs is None:
        try:
            from observability import ObservabilityManager
            _obs = ObservabilityManager(
                redis_client=None,
                monthly_budget=float(os.getenv("LOGGAZER_MONTHLY_BUDGET", "50")),
                sampling_rate=float(os.getenv("LOGGAZER_SAMPLING_RATE", "0.1")),
            )
        except Exception as e:
            logger.warning("ObservabilityManager init failed: %s", e)
            _obs = None
    return _obs


def reset_observability():
    """重置可观测性单例（仅供测试使用）"""
    global _obs
    _obs = None
