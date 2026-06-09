# tests/test_rate_limiter.py - 限流器测试
#
# 测试范围：
#   1. Token Bucket 基本功能（内存降级模式）
#   2. 突发流量控制
#   3. 令牌 refill
#   4. 用户隔离
#   5. 剩余配额查询
#   6. retry_after 计算

import pytest
import time
from unittest.mock import MagicMock, patch

from rate_limiter import TokenBucketRateLimiter, _MemorySlidingWindow


# ============================================================
#  内存滑动窗口测试（Redis 降级方案）
# ============================================================

class TestMemorySlidingWindow:
    """内存滑动窗口限流器测试"""

    def setup_method(self):
        """每个测试前重置"""
        self.window = _MemorySlidingWindow()

    def test_basic_allow(self):
        """基本请求允许通过"""
        assert self.window.is_allowed("user1", max_requests=5) is True

    def test_burst_traffic_rejection(self):
        """突发流量：超过限制后拒绝"""
        key = "burst_user"
        max_req = 5

        # 前 5 次通过
        for _ in range(max_req):
            assert self.window.is_allowed(key, max_req) is True

        # 第 6 次拒绝
        assert self.window.is_allowed(key, max_req) is False

    def test_burst_traffic_10_through_11_reject(self):
        """容量 10 的桶：前 10 次通过，第 11 次拒绝"""
        key = "burst10_user"
        max_req = 10

        for i in range(max_req):
            assert self.window.is_allowed(key, max_req) is True, f"第 {i+1} 次应通过"

        assert self.window.is_allowed(key, max_req) is False, "第 11 次应拒绝"

    def test_refill_after_window(self):
        """窗口过期后配额恢复"""
        key = "refill_user"
        max_req = 3
        window_seconds = 1  # 1 秒窗口，方便测试

        # 用完配额
        for _ in range(max_req):
            assert self.window.is_allowed(key, max_req, window_seconds) is True

        assert self.window.is_allowed(key, max_req, window_seconds) is False

        # 等待窗口过期
        time.sleep(1.1)

        # 配额恢复
        assert self.window.is_allowed(key, max_req, window_seconds) is True

    def test_user_isolation(self):
        """不同用户隔离：user_A 超限不影响 user_B"""
        max_req = 3

        # user_A 用完配额
        for _ in range(max_req):
            assert self.window.is_allowed("user_A", max_req) is True

        assert self.window.is_allowed("user_A", max_req) is False

        # user_B 仍有配额
        assert self.window.is_allowed("user_B", max_req) is True

    def test_get_remaining_quota(self):
        """剩余配额查询"""
        key = "quota_user"
        max_req = 5

        assert self.window.get_remaining(key, max_req) == 5

        self.window.is_allowed(key, max_req)
        assert self.window.get_remaining(key, max_req) == 4

        self.window.is_allowed(key, max_req)
        assert self.window.get_remaining(key, max_req) == 3

    def test_get_remaining_quota_zero(self):
        """配额用完时剩余为 0"""
        key = "zero_user"
        max_req = 2

        self.window.is_allowed(key, max_req)
        self.window.is_allowed(key, max_req)

        assert self.window.get_remaining(key, max_req) == 0

    def test_get_retry_after(self):
        """retry_after 返回正整数"""
        key = "retry_user"
        max_req = 1
        window_seconds = 60

        self.window.is_allowed(key, max_req, window_seconds)

        retry = self.window.get_retry_after(key, max_req, window_seconds)
        assert retry > 0
        # 允许 +1 的误差（整数向上取整）
        assert retry <= window_seconds + 1

    def test_get_retry_after_zero_when_allowed(self):
        """有配额时 retry_after 为 0"""
        key = "retry_zero_user"
        retry = self.window.get_retry_after(key, max_requests=5)
        assert retry == 0


# ============================================================
#  TokenBucketRateLimiter 测试（无 Redis，使用内存降级）
# ============================================================

class TestTokenBucketRateLimiter:
    """TokenBucketRateLimiter 测试（内存降级模式）"""

    def setup_method(self):
        """每个测试前创建新的限流器"""
        self.limiter = TokenBucketRateLimiter(redis_client=None)

    def test_basic_allow(self):
        """基本请求允许通过"""
        assert self.limiter.is_allowed("test_user", max_requests=5) is True

    def test_burst_5_through_6_reject(self):
        """匿名用户限流：5次/分钟，第6次拒绝"""
        user = "anon_burst"
        max_req = 5

        for i in range(max_req):
            assert self.limiter.is_allowed(user, max_req) is True, f"第 {i+1} 次应通过"

        assert self.limiter.is_allowed(user, max_req) is False, "第 6 次应拒绝"

    def test_user_isolation(self):
        """用户隔离：user_A 超限不影响 user_B"""
        max_req = 3

        # user_A 用完
        for _ in range(max_req):
            self.limiter.is_allowed("isolated_A", max_req)

        assert self.limiter.is_allowed("isolated_A", max_req) is False

        # user_B 正常
        assert self.limiter.is_allowed("isolated_B", max_req) is True

    def test_get_remaining_quota(self):
        """剩余配额查询"""
        user = "quota_test"
        max_req = 5

        remaining = self.limiter.get_remaining_quota(user, max_req)
        assert remaining == 5

        self.limiter.is_allowed(user, max_req)
        remaining = self.limiter.get_remaining_quota(user, max_req)
        assert remaining == 4

    def test_get_retry_after(self):
        """retry_after 查询"""
        user = "retry_test"
        max_req = 1

        self.limiter.is_allowed(user, max_req)
        retry = self.limiter.get_retry_after(user, max_req)
        assert retry > 0

    def test_refill_after_window(self):
        """窗口过期后配额恢复"""
        user = "refill_test"
        max_req = 2
        window_seconds = 1

        # 用完
        self.limiter.is_allowed(user, max_req, window_seconds)
        self.limiter.is_allowed(user, max_req, window_seconds)
        assert self.limiter.is_allowed(user, max_req, window_seconds) is False

        # 等待 refill
        time.sleep(1.1)
        assert self.limiter.is_allowed(user, max_req, window_seconds) is True

    def test_anonymous_user_5_per_minute(self):
        """匿名用户：5次/分钟"""
        user = "anonymous"
        max_req = 5
        window = 60

        for i in range(5):
            assert self.limiter.is_allowed(user, max_req, window) is True

        assert self.limiter.is_allowed(user, max_req, window) is False

    def test_authenticated_user_20_per_minute(self):
        """认证用户：20次/分钟"""
        user = "auth_user_123"
        max_req = 20
        window = 60

        for i in range(20):
            assert self.limiter.is_allowed(user, max_req, window) is True

        assert self.limiter.is_allowed(user, max_req, window) is False


# ============================================================
#  集成测试：模拟真实场景
# ============================================================

class TestRateLimiterIntegration:
    """集成测试：模拟真实使用场景"""

    def setup_method(self):
        self.limiter = TokenBucketRateLimiter(redis_client=None)

    def test_100_requests_in_1_second_first_5_pass(self):
        """
        验收标准：100次/分钟的匿名用户在1秒内发送20次请求
        前5次通过，后15次拒绝
        """
        user = "anonymous"
        max_req = 5
        window = 60

        passed = 0
        rejected = 0

        for _ in range(20):
            if self.limiter.is_allowed(user, max_req, window):
                passed += 1
            else:
                rejected += 1

        assert passed == 5
        assert rejected == 15

    def test_alternating_users_fairness(self):
        """交替用户应各自有独立配额"""
        max_req = 3

        # user_A 用完
        for _ in range(max_req):
            self.limiter.is_allowed("fair_A", max_req)

        # user_B 仍有配额
        for _ in range(max_req):
            assert self.limiter.is_allowed("fair_B", max_req) is True

        # user_A 被拒绝
        assert self.limiter.is_allowed("fair_A", max_req) is False
