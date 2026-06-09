# observability.py - 可观测性中心管理器
#
# 职责：
#   1. OpenTelemetry 全链路追踪（Tracer + Span 管理）
#   2. Prometheus 指标暴露（Histogram / Counter / Gauge）
#   3. Token 成本计算与月度预算管控
#   4. 成本熔断器（80% 告警 / 100% 强制降级）
#   5. 用户级限流（委托给 RateLimiter）
#
# 设计原则：
#   - 依赖注入：通过构造函数注入 Redis 客户端，不使用全局变量
#   - 降级优先：任何组件失败都不阻塞主流程
#   - 零商业 APM：纯开源栈（OpenTelemetry + Prometheus）

import logging
import time
from contextlib import contextmanager
from typing import Literal, Optional

from cost_calculator import CostCalculator
from rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)


# ============================================================
#  平台标签标准化（防 Cardinality 爆炸）
# ============================================================

# 已知平台列表，低频平台归入 "other"
_KNOWN_PLATFORMS = frozenset({
    "npm", "GitHub Actions", "Jenkins", "Docker",
    "pytest", "jest", "cargo", "pip", "Gradle", "Maven",
})


def _normalize_platform(platform: str) -> str:
    """将平台名称标准化，低频平台归入 'other'"""
    if platform in _KNOWN_PLATFORMS:
        return platform
    return "other"


# ============================================================
#  自定义异常
# ============================================================

class CircuitBreakerError(Exception):
    """成本熔断器触发：月度预算已耗尽"""
    pass


# ============================================================
#  可观测性管理器
# ============================================================

class ObservabilityManager:
    """
    可观测性中心管理器

    统一管理：
    - OpenTelemetry Tracer（链路追踪）
    - Prometheus Metrics（指标暴露）
    - CostCalculator（成本计算）
    - TokenBucketRateLimiter（限流）
    - 成本熔断器

    使用方式：
        obs = ObservabilityManager(redis_url="redis://localhost:6379")
        with obs.trace_analysis(platform="npm", cache_status="miss") as ctx:
            # 执行分析
            cost = obs.record_tokens("deepseek-chat", "deepseek", 1000, 500, "success")
    """

    def __init__(
        self,
        redis_client=None,
        metrics_port: int = 9090,
        monthly_budget: float = 50.0,
        sampling_rate: float = 0.1,
    ):
        """
        初始化可观测性管理器

        参数:
            redis_client: redis.Redis 实例，None 时所有子组件降级到内存模式
            metrics_port: Prometheus metrics 端口（由 metrics_server.py 管理）
            monthly_budget: 月度 Token 预算（USD）
            sampling_rate: OpenTelemetry 采样率（0.0-1.0）
        """
        self._redis = redis_client

        # ---- 初始化子组件 ----
        self.cost_calculator = CostCalculator(
            redis_client=redis_client,
            monthly_budget=monthly_budget,
        )
        self.rate_limiter = TokenBucketRateLimiter(
            redis_client=redis_client,
        )

        # ---- 初始化 OpenTelemetry ----
        self._tracer = self._init_tracer(sampling_rate)

        # ---- 初始化 Prometheus 指标 ----
        self._init_prometheus_metrics()

        logger.info(
            "ObservabilityManager 初始化完成 (redis=%s, budget=$%.2f, sampling=%.0f%%)",
            "connected" if redis_client else "degraded",
            monthly_budget,
            sampling_rate * 100,
        )

    # ============================================================
    #  OpenTelemetry 初始化
    # ============================================================

    @staticmethod
    def _init_tracer(sampling_rate: float):
        """初始化 OpenTelemetry Tracer"""
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
            from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
            from opentelemetry.trace import Status, StatusCode

            # 采样策略
            sampler = TraceIdRatioBased(sampling_rate)

            # 创建 TracerProvider
            provider = TracerProvider(sampler=sampler)

            # 控制台 Exporter（开发/演示用）
            # 生产环境可替换为 OTLPSpanExporter
            provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )

            trace.set_tracer_provider(provider)
            tracer = trace.get_tracer("loggazer")

            logger.info("OpenTelemetry Tracer 初始化成功 (sampling=%.0f%%)", sampling_rate * 100)
            return tracer

        except ImportError:
            logger.warning("OpenTelemetry SDK 未安装，链路追踪不可用")
            return None
        except Exception as e:
            logger.warning("OpenTelemetry 初始化失败: %s", e)
            return None

    # ============================================================
    #  Prometheus 指标初始化
    # ============================================================

    def _init_prometheus_metrics(self):
        """初始化所有 Prometheus 指标"""
        try:
            from prometheus_client import Counter, Histogram, Gauge

            # 分析耗时（Histogram）
            self.analysis_duration = Histogram(
                "loggazer_analysis_duration_seconds",
                "端到端分析耗时",
                labelnames=["platform", "cache_status"],
                buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
            )

            # Token 消耗（Counter）
            self.token_consumption = Counter(
                "loggazer_token_consumption_total",
                "Token 消耗总量",
                labelnames=["model", "provider", "status"],
            )

            # 错误计数（Counter）
            self.analysis_errors = Counter(
                "loggazer_analysis_errors_total",
                "分析错误计数",
                labelnames=["error_type"],
            )

            # 缓存命中率（Gauge）
            self.cache_hit_ratio = Gauge(
                "loggazer_cache_hit_ratio",
                "缓存命中率（0-1）",
            )

            # 活跃请求数（Gauge）
            self.active_requests = Gauge(
                "loggazer_active_requests",
                "当前并发分析请求数",
            )

            # 月度成本（Gauge）
            self.monthly_cost = Gauge(
                "loggazer_monthly_cost_usd",
                "本月累计预估成本（USD）",
            )

            self._prometheus_available = True
            logger.info("Prometheus 指标初始化成功")

        except ImportError:
            logger.warning("prometheus_client 未安装，指标不可用")
            self._prometheus_available = False
        except Exception as e:
            logger.warning("Prometheus 指标初始化失败: %s", e)
            self._prometheus_available = False

    # ============================================================
    #  链路追踪
    # ============================================================

    @contextmanager
    def trace_analysis(self, platform: str, cache_status: str):
        """
        分析全链路追踪上下文管理器

        自动记录：
        - Span 名称：loggazer.analysis
        - 属性：platform, cache_status
        - 耗时（通过 Span 的 start/end 时间）

        参数:
            platform: 日志来源平台
            cache_status: 缓存状态（hit/miss/rag）

        用法:
            with obs.trace_analysis("npm", "miss") as ctx:
                result = do_analysis()
        """
        normalized_platform = _normalize_platform(platform)

        if self._tracer is not None:
            with self._tracer.start_as_current_span("loggazer.analysis") as span:
                span.set_attribute("platform", normalized_platform)
                span.set_attribute("cache_status", cache_status)

                start_time = time.time()
                try:
                    yield {
                        "platform": normalized_platform,
                        "cache_status": cache_status,
                        "span": span,
                    }
                except Exception as e:
                    span.set_attribute("error", True)
                    span.set_attribute("error.type", type(e).__name__)
                    raise
                finally:
                    duration = time.time() - start_time
                    span.set_attribute("duration_seconds", round(duration, 3))

                    # 记录 Prometheus 指标
                    if self._prometheus_available:
                        try:
                            self.analysis_duration.labels(
                                platform=normalized_platform,
                                cache_status=cache_status,
                            ).observe(duration)
                        except Exception:
                            pass
        else:
            # OpenTelemetry 不可用，仅记录 Prometheus 指标
            start_time = time.time()
            try:
                yield {
                    "platform": normalized_platform,
                    "cache_status": cache_status,
                    "span": None,
                }
            finally:
                duration = time.time() - start_time
                if self._prometheus_available:
                    try:
                        self.analysis_duration.labels(
                            platform=normalized_platform,
                            cache_status=cache_status,
                        ).observe(duration)
                    except Exception:
                        pass

    @contextmanager
    def trace_ai_call(self, provider: str, model: str, **attributes):
        """
        AI 调用子 Span 追踪

        参数:
            provider: AI 提供商（deepseek/claude/ollama）
            model: 模型名称
            **attributes: 附加属性（temperature, prompt_length 等）
        """
        if self._tracer is not None:
            with self._tracer.start_as_current_span(f"ai_engine.call.{provider}") as span:
                span.set_attribute("model", model)
                span.set_attribute("provider", provider)
                for key, value in attributes.items():
                    span.set_attribute(key, value)

                try:
                    yield span
                except Exception as e:
                    span.set_attribute("error", True)
                    span.set_attribute("error.type", type(e).__name__)
                    raise
        else:
            yield None

    # ============================================================
    #  Token 记录与成本计算
    # ============================================================

    def record_tokens(
        self,
        model: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        status: str = "success",
    ) -> float:
        """
        记录 Token 消耗并返回预估成本

        参数:
            model: 模型名称
            provider: AI 提供商
            input_tokens: 输入 Token 数
            output_tokens: 输出 Token 数
            status: 调用状态（success/error）

        返回:
            预估成本（USD）
        """
        # 计算成本
        cost = self.cost_calculator.calculate(model, input_tokens, output_tokens)

        # 累加月度成本
        if status == "success" and cost > 0:
            self.cost_calculator.accumulate(cost)

        # 更新 Prometheus 指标
        if self._prometheus_available:
            try:
                self.token_consumption.labels(
                    model=model,
                    provider=provider,
                    status=status,
                ).inc(input_tokens + output_tokens)

                # 更新月度成本 Gauge
                monthly = self.cost_calculator.get_monthly_accumulated()
                self.monthly_cost.set(monthly)
            except Exception:
                pass

        return cost

    # ============================================================
    #  错误记录
    # ============================================================

    def record_error(self, error_type: str) -> None:
        """
        记录分析错误

        参数:
            error_type: 错误类型（auth/rate_limit/quota/network/parse/validation）
        """
        if self._prometheus_available:
            try:
                self.analysis_errors.labels(error_type=error_type).inc()
            except Exception:
                pass

        logger.info("Error recorded: type=%s", error_type)

    # ============================================================
    #  活跃请求管理
    # ============================================================

    def increment_active_requests(self) -> None:
        """增加活跃请求计数"""
        if self._prometheus_available:
            try:
                self.active_requests.inc()
            except Exception:
                pass

    def decrement_active_requests(self) -> None:
        """减少活跃请求计数"""
        if self._prometheus_available:
            try:
                self.active_requests.dec()
            except Exception:
                pass

    # ============================================================
    #  缓存命中率更新
    # ============================================================

    def update_cache_hit_ratio(self, ratio: float) -> None:
        """
        更新缓存命中率

        参数:
            ratio: 命中率（0.0-1.0）
        """
        if self._prometheus_available:
            try:
                self.cache_hit_ratio.set(max(0.0, min(1.0, ratio)))
            except Exception:
                pass

    # ============================================================
    #  限流
    # ============================================================

    def get_rate_limiter(self, user_id: str = "anonymous") -> TokenBucketRateLimiter:
        """获取限流器实例"""
        return self.rate_limiter

    def check_rate_limit(
        self,
        user_id: str = "anonymous",
        max_requests: int = 5,
        window_seconds: int = 60,
    ) -> tuple[bool, int]:
        """
        检查限流状态

        参数:
            user_id: 用户标识
            max_requests: 窗口内最大请求数
            window_seconds: 窗口大小（秒）

        返回:
            (is_allowed, retry_after_seconds)
        """
        allowed = self.rate_limiter.is_allowed(user_id, max_requests, window_seconds)
        retry_after = 0 if allowed else self.rate_limiter.get_retry_after(
            user_id, max_requests, window_seconds
        )
        return allowed, retry_after

    # ============================================================
    #  成本熔断器
    # ============================================================

    def check_cost_circuit_breaker(self) -> Literal["normal", "warning", "tripped"]:
        """
        检查成本熔断状态

        返回:
            "normal"  - 正常（< 80% 预算）
            "warning" - 警告（>= 80% 且 < 100% 预算）
            "tripped" - 熔断（>= 100% 预算，强制降级到本地模型）
        """
        accumulated = self.cost_calculator.get_monthly_accumulated()
        budget = self.cost_calculator.get_monthly_budget()

        if budget <= 0:
            return "normal"

        ratio = accumulated / budget

        if ratio >= 1.0:
            return "tripped"
        elif ratio >= 0.8:
            return "warning"
        else:
            return "normal"

    def get_cost_summary(self) -> dict:
        """
        获取成本摘要（用于前端展示）

        返回:
            {
                "monthly_accumulated": float,
                "monthly_budget": float,
                "usage_ratio": float,
                "circuit_breaker_status": str,
            }
        """
        accumulated = self.cost_calculator.get_monthly_accumulated()
        budget = self.cost_calculator.get_monthly_budget()

        return {
            "monthly_accumulated": round(accumulated, 6),
            "monthly_budget": round(budget, 2),
            "usage_ratio": round(accumulated / budget, 4) if budget > 0 else 0.0,
            "circuit_breaker_status": self.check_cost_circuit_breaker(),
        }
