# cost_calculator.py - Token 成本计算器
#
# 职责：
#   1. 根据模型定价表精确计算单次 API 调用成本（USD）
#   2. 维护月度累计成本（Redis 持久化 + 内存降级）
#   3. 支持成本熔断器阈值判断
#
# 定价来源：
#   - DeepSeek V3: https://platform.deepseek.com/api-docs/pricing
#   - Claude Sonnet 4: https://docs.anthropic.com/en/docs/about-claude/models
#
# 精度要求：保留 6 位小数（$0.000001）

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ============================================================
#  定价表
# ============================================================

@dataclass(frozen=True)
class ModelPricing:
    """单个模型的 Token 定价（USD per 1M tokens）"""
    input_per_1m: float   # 每 1M 输入 Token 的价格
    output_per_1m: float  # 每 1M 输出 Token 的价格


# 定价表：key = 模型名称，value = ModelPricing
# 更新日期：2025-06
PRICING_TABLE: Dict[str, ModelPricing] = {
    # DeepSeek 系列
    "deepseek-chat": ModelPricing(input_per_1m=0.14, output_per_1m=0.28),
    "deepseek-coder": ModelPricing(input_per_1m=0.14, output_per_1m=0.28),
    "deepseek-reasoner": ModelPricing(input_per_1m=0.55, output_per_1m=2.19),

    # Claude 系列
    "claude-sonnet-4-20250514": ModelPricing(input_per_1m=3.0, output_per_1m=15.0),
    "claude-opus-4-20250514": ModelPricing(input_per_1m=15.0, output_per_1m=75.0),
    "claude-haiku-4-5-20251001": ModelPricing(input_per_1m=0.80, output_per_1m=4.0),

    # 本地模型（免费，但记录 Token 量用于统计）
    "qwen2.5:7b": ModelPricing(input_per_1m=0.0, output_per_1m=0.0),
    "llama3:8b": ModelPricing(input_per_1m=0.0, output_per_1m=0.0),
}


# 默认定价（未知模型时使用最高价，防止绕过成本控制）
_DEFAULT_PRICING = ModelPricing(input_per_1m=15.0, output_per_1m=75.0)


# ============================================================
#  成本计算器
# ============================================================

class CostCalculator:
    """
    Token 成本计算器

    职责：
    1. 根据模型和 Token 数计算单次调用成本
    2. 累加月度总成本（Redis 持久化或内存降级）
    3. 提供月度预算查询

    Redis key 设计：
    - loggazer:cost:monthly:{YYYY-MM} = 累计成本（float，INCRBYFLOAT）
    - loggazer:cost:budget = 月度预算阈值（float）
    """

    def __init__(
        self,
        redis_client=None,
        monthly_budget: float = 50.0,
    ):
        """
        参数:
            redis_client: redis.Redis 实例，None 时使用内存降级
            monthly_budget: 月度预算阈值（USD），默认 $50
        """
        self._redis = redis_client
        self._monthly_budget = monthly_budget

        # 内存降级存储
        self._memory_costs: Dict[str, float] = {}  # key: "YYYY-MM", value: 累计成本

    def calculate(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        计算单次 API 调用成本

        参数:
            model: 模型名称（如 "deepseek-chat"）
            input_tokens: 输入 Token 数
            output_tokens: 输出 Token 数

        返回:
            成本（USD），保留 6 位小数
        """
        pricing = PRICING_TABLE.get(model, _DEFAULT_PRICING)

        cost = (
            (input_tokens / 1_000_000) * pricing.input_per_1m
            + (output_tokens / 1_000_000) * pricing.output_per_1m
        )

        # 保留 6 位小数
        return round(cost, 6)

    def accumulate(self, cost_usd: float) -> float:
        """
        累加月度成本并返回最新总额

        参数:
            cost_usd: 本次调用成本

        返回:
            累加后的月度总成本
        """
        month_key = self._get_month_key()

        if self._redis is not None:
            try:
                self._redis.incrbyfloat(
                    f"loggazer:cost:monthly:{month_key}",
                    cost_usd,
                )
                total = float(
                    self._redis.get(f"loggazer:cost:monthly:{month_key}") or 0
                )
                return round(total, 6)
            except Exception as e:
                logger.warning("Redis 成本累加失败，降级到内存: %s", e)

        # 内存降级
        current = self._memory_costs.get(month_key, 0.0)
        new_total = current + cost_usd
        self._memory_costs[month_key] = new_total
        return round(new_total, 6)

    def get_monthly_accumulated(self) -> float:
        """
        读取本月累计成本

        返回:
            本月累计成本（USD）
        """
        month_key = self._get_month_key()

        if self._redis is not None:
            try:
                total = self._redis.get(f"loggazer:cost:monthly:{month_key}")
                return round(float(total or 0), 6)
            except Exception as e:
                logger.warning("Redis 成本读取失败，降级到内存: %s", e)

        return round(self._memory_costs.get(month_key, 0.0), 6)

    def get_monthly_budget(self) -> float:
        """获取月度预算阈值"""
        return self._monthly_budget

    def reset_monthly_budget(self, budget_usd: float = 50.0) -> None:
        """
        重置月度预算（通常在月初或配置变更时调用）

        参数:
            budget_usd: 新的预算阈值
        """
        self._monthly_budget = budget_usd

        if self._redis is not None:
            try:
                self._redis.set("loggazer:cost:budget", str(budget_usd))
            except Exception as e:
                logger.warning("Redis 预算设置失败: %s", e)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """
        估算文本的 Token 数量

        策略：字符数 / 4（中英文混合场景的粗略估算）
        适用于 API 响应中没有 usage 字段时的回退方案。

        参数:
            text: 输入文本

        返回:
            估算的 Token 数
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    @staticmethod
    def _get_month_key() -> str:
        """获取当前月份 key（格式：YYYY-MM）"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m")
