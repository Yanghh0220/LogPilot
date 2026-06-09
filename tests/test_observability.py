# tests/test_observability.py - 可观测性模块测试
#
# 测试范围：
#   1. CostCalculator 计价精度
#   2. CostCalculator 月度累计（内存模式）
#   3. 成本熔断器状态转换
#   4. ObservabilityManager 集成
#   5. Token 估算策略

import pytest
import time
from unittest.mock import MagicMock, patch

from cost_calculator import CostCalculator, ModelPricing, PRICING_TABLE
from observability import ObservabilityManager, CircuitBreakerError


# ============================================================
#  CostCalculator 测试
# ============================================================

class TestCostCalculator:
    """CostCalculator 计价精度与月度累计测试"""

    def test_deepseek_pricing_precision(self):
        """DeepSeek 1M input + 500K output = $0.14 + $0.14 = $0.28"""
        calc = CostCalculator()
        cost = calc.calculate("deepseek-chat", input_tokens=1_000_000, output_tokens=500_000)
        # input: 1M * $0.14/M = $0.14
        # output: 500K * $0.28/M = $0.14
        # total: $0.28
        assert cost == pytest.approx(0.28, abs=0.000001)

    def test_claude_pricing_precision(self):
        """Claude Sonnet 4: 100K input + 50K output"""
        calc = CostCalculator()
        cost = calc.calculate("claude-sonnet-4-20250514", input_tokens=100_000, output_tokens=50_000)
        # input: 100K * $3/M = $0.30
        # output: 50K * $15/M = $0.75
        # total: $1.05
        assert cost == pytest.approx(1.05, abs=0.000001)

    def test_deepseek_small_request(self):
        """DeepSeek 小请求：1000 input + 500 output"""
        calc = CostCalculator()
        cost = calc.calculate("deepseek-chat", input_tokens=1000, output_tokens=500)
        # input: 1000 * $0.14/1M = $0.00014
        # output: 500 * $0.28/1M = $0.00014
        # total: $0.00028
        assert cost == pytest.approx(0.00028, abs=0.000001)

    def test_unknown_model_uses_default_pricing(self):
        """未知模型使用最高价定价（防绕过）"""
        calc = CostCalculator()
        cost = calc.calculate("unknown-model", input_tokens=1_000_000, output_tokens=1_000_000)
        # 使用默认定价: $15/M input + $75/M output
        assert cost == pytest.approx(90.0, abs=0.000001)

    def test_local_model_zero_cost(self):
        """本地模型成本为零"""
        calc = CostCalculator()
        cost = calc.calculate("qwen2.5:7b", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == 0.0

    def test_zero_tokens(self):
        """零 Token 成本为零"""
        calc = CostCalculator()
        cost = calc.calculate("deepseek-chat", input_tokens=0, output_tokens=0)
        assert cost == 0.0

    def test_pricing_table_completeness(self):
        """定价表包含所有已知模型"""
        expected_models = [
            "deepseek-chat", "deepseek-coder", "deepseek-reasoner",
            "claude-sonnet-4-20250514", "claude-opus-4-20250514",
            "claude-haiku-4-5-20251001",
            "qwen2.5:7b", "llama3:8b",
        ]
        for model in expected_models:
            assert model in PRICING_TABLE, f"模型 {model} 不在定价表中"


# ============================================================
#  CostCalculator 月度累计测试（内存模式）
# ============================================================

class TestCostCalculatorAccumulation:
    """月度成本累计测试（不依赖 Redis）"""

    def test_accumulate_returns_total(self):
        """累加后返回正确的月度总额"""
        calc = CostCalculator(redis_client=None)
        # 清理可能存在的旧数据
        calc._memory_costs.clear()

        total1 = calc.accumulate(10.0)
        assert total1 == pytest.approx(10.0, abs=0.000001)

        total2 = calc.accumulate(5.5)
        assert total2 == pytest.approx(15.5, abs=0.000001)

    def test_get_monthly_accumulated(self):
        """读取月度累计"""
        calc = CostCalculator(redis_client=None)
        calc._memory_costs.clear()

        calc.accumulate(20.0)
        calc.accumulate(10.0)

        assert calc.get_monthly_accumulated() == pytest.approx(30.0, abs=0.000001)

    def test_monthly_budget_default(self):
        """默认月度预算 $50"""
        calc = CostCalculator()
        assert calc.get_monthly_budget() == 50.0

    def test_reset_monthly_budget(self):
        """重置月度预算"""
        calc = CostCalculator()
        calc.reset_monthly_budget(100.0)
        assert calc.get_monthly_budget() == 100.0

    def test_estimate_tokens(self):
        """Token 估算：字符数 / 4"""
        assert CostCalculator.estimate_tokens("") == 0
        assert CostCalculator.estimate_tokens("hello") == 1  # 5 chars / 4 = 1
        assert CostCalculator.estimate_tokens("a" * 100) == 25  # 100 / 4 = 25
        assert CostCalculator.estimate_tokens("你好世界") == 1  # 4 chars / 4 = 1


# ============================================================
#  成本熔断器测试
# ============================================================

class TestCircuitBreaker:
    """成本熔断器状态转换测试"""

    def test_normal_below_80_percent(self):
        """低于 80% 预算 → normal"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=50.0)
        obs.cost_calculator._memory_costs.clear()
        obs.cost_calculator.accumulate(30.0)  # 60%
        assert obs.check_cost_circuit_breaker() == "normal"

    def test_warning_at_80_percent(self):
        """达到 80% 预算 → warning"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=50.0)
        obs.cost_calculator._memory_costs.clear()
        obs.cost_calculator.accumulate(40.0)  # 80%
        assert obs.check_cost_circuit_breaker() == "warning"

    def test_warning_between_80_and_100(self):
        """80%-100% 之间 → warning"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=50.0)
        obs.cost_calculator._memory_costs.clear()
        obs.cost_calculator.accumulate(45.0)  # 90%
        assert obs.check_cost_circuit_breaker() == "warning"

    def test_tripped_at_100_percent(self):
        """达到 100% 预算 → tripped"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=50.0)
        obs.cost_calculator._memory_costs.clear()
        obs.cost_calculator.accumulate(50.0)  # 100%
        assert obs.check_cost_circuit_breaker() == "tripped"

    def test_tripped_above_100_percent(self):
        """超过 100% 预算 → tripped"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=50.0)
        obs.cost_calculator._memory_costs.clear()
        obs.cost_calculator.accumulate(50.01)
        assert obs.check_cost_circuit_breaker() == "tripped"

    def test_cost_summary_structure(self):
        """成本摘要返回正确的结构"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=50.0)
        obs.cost_calculator._memory_costs.clear()
        obs.cost_calculator.accumulate(25.0)

        summary = obs.get_cost_summary()
        assert "monthly_accumulated" in summary
        assert "monthly_budget" in summary
        assert "usage_ratio" in summary
        assert "circuit_breaker_status" in summary
        assert summary["monthly_accumulated"] == pytest.approx(25.0, abs=0.000001)
        assert summary["monthly_budget"] == 50.0
        assert summary["usage_ratio"] == pytest.approx(0.5, abs=0.0001)


# ============================================================
#  ObservabilityManager 集成测试
# ============================================================

class TestObservabilityManager:
    """ObservabilityManager 核心功能测试"""

    def test_init_without_redis(self):
        """无 Redis 时正常初始化（降级到内存模式）"""
        obs = ObservabilityManager(redis_client=None)
        assert obs is not None
        assert obs.cost_calculator is not None
        assert obs.rate_limiter is not None

    def test_record_tokens_returns_cost(self):
        """record_tokens 返回正确的成本"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=100.0)
        obs.cost_calculator._memory_costs.clear()

        cost = obs.record_tokens(
            model="deepseek-chat",
            provider="deepseek",
            input_tokens=1_000_000,
            output_tokens=500_000,
            status="success",
        )
        assert cost == pytest.approx(0.28, abs=0.000001)

    def test_record_tokens_accumulates_monthly(self):
        """record_tokens 成功时累加月度成本"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=100.0)
        obs.cost_calculator._memory_costs.clear()

        obs.record_tokens("deepseek-chat", "deepseek", 1_000_000, 500_000, "success")
        obs.record_tokens("deepseek-chat", "deepseek", 1_000_000, 500_000, "success")

        monthly = obs.cost_calculator.get_monthly_accumulated()
        assert monthly == pytest.approx(0.56, abs=0.000001)

    def test_record_tokens_error_no_accumulate(self):
        """record_tokens 失败时不累加月度成本"""
        obs = ObservabilityManager(redis_client=None, monthly_budget=100.0)
        obs.cost_calculator._memory_costs.clear()

        obs.record_tokens("deepseek-chat", "deepseek", 1_000_000, 500_000, "error")

        monthly = obs.cost_calculator.get_monthly_accumulated()
        assert monthly == pytest.approx(0.0, abs=0.000001)

    def test_trace_analysis_context(self):
        """trace_analysis 上下文管理器正常工作"""
        obs = ObservabilityManager(redis_client=None)

        with obs.trace_analysis(platform="npm", cache_status="miss") as ctx:
            assert ctx["platform"] == "npm"
            assert ctx["cache_status"] == "miss"

    def test_trace_analysis_normalizes_platform(self):
        """trace_analysis 标准化平台名称"""
        obs = ObservabilityManager(redis_client=None)

        with obs.trace_analysis(platform="unknown_platform", cache_status="miss") as ctx:
            assert ctx["platform"] == "other"

    def test_check_rate_limit(self):
        """限流检查返回正确的元组"""
        obs = ObservabilityManager(redis_client=None)

        allowed, retry_after = obs.check_rate_limit("test_user", max_requests=5, window_seconds=60)
        assert isinstance(allowed, bool)
        assert isinstance(retry_after, int)

    def test_active_requests_gauge(self):
        """活跃请求计数器增减"""
        obs = ObservabilityManager(redis_client=None)
        # 不抛异常即为通过（无 Prometheus 时静默）
        obs.increment_active_requests()
        obs.decrement_active_requests()

    def test_update_cache_hit_ratio(self):
        """缓存命中率更新"""
        obs = ObservabilityManager(redis_client=None)
        # 不抛异常即为通过
        obs.update_cache_hit_ratio(0.75)
        obs.update_cache_hit_ratio(1.5)  # 超出范围应被 clamp
        obs.update_cache_hit_ratio(-0.1)  # 负数应被 clamp


# ============================================================
#  降级路径测试
# ============================================================

class TestDegradation:
    """降级路径测试：熔断状态下 AI 调用被拦截"""

    @patch("ai_engine._get_observability")
    def test_circuit_breaker_blocks_legacy_call(self, mock_get_obs):
        """熔断状态下 call_ai_legacy 返回降级提示"""
        from ai_engine import call_ai_legacy

        # 模拟熔断状态
        mock_obs = MagicMock()
        mock_obs.check_cost_circuit_breaker.return_value = "tripped"
        mock_get_obs.return_value = mock_obs

        result = call_ai_legacy("system", "user")
        assert "额度已用尽" in result
