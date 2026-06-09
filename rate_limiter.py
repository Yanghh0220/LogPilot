# rate_limiter.py - 基于 Redis 的 Token Bucket 限流器
#
# 职责：
#   1. 用户级限流（匿名用户 5次/分钟，认证用户 20次/分钟）
#   2. Redis 不可用时降级到内存滑动窗口
#   3. 提供剩余配额查询（用于前端提示）
#
# 算法：Token Bucket（令牌桶）
#   - 桶容量 = max_requests
#   - 令牌以 (max_requests / window_seconds) 的速率补充
#   - 每次请求消耗 1 个令牌
#   - 桶空时拒绝请求
#
# Redis 实现：使用 Lua 脚本保证原子性

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================
#  Redis Lua 脚本（原子性 Token Bucket）
# ============================================================

# KEYS[1] = 令牌桶 key
# ARGV[1] = 桶容量（max_requests）
# ARGV[2] = 令牌补充速率（tokens per second）
# ARGV[3] = 当前时间戳（秒，float）
# ARGV[4] = 请求消耗的令牌数（通常为 1）
#
# 返回值：[allowed(1/0), remaining_tokens]
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])

-- 获取当前桶状态
local bucket = redis.call('hmget', key, 'tokens', 'last_refill')
local tokens = tonumber(bucket[1])
local last_refill = tonumber(bucket[2])

-- 首次请求：初始化桶
if tokens == nil then
    tokens = capacity
    last_refill = now
end

-- 计算自上次补充以来应添加的令牌数
local elapsed = now - last_refill
local refill = elapsed * rate
tokens = math.min(capacity, tokens + refill)

-- 尝试消耗令牌
local allowed = 0
local remaining = tokens
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
    remaining = tokens
else
    remaining = tokens
end

-- 更新桶状态（设置 TTL = 2 * window 以自动清理过期 key）
redis.call('hset', key, 'tokens', tostring(tokens), 'last_refill', tostring(now))
redis.call('expire', key, math.ceil(capacity / rate) * 2)

return {allowed, math.floor(remaining)}
"""


# ============================================================
#  内存滑动窗口（Redis 不可用时的降级方案）
# ============================================================

class _MemorySlidingWindow:
    """
    内存滑动窗口限流器

    当 Redis 不可用时的降级实现。
    使用时间戳列表实现滑动窗口，精度为毫秒。
    """

    def __init__(self):
        self._windows: Dict[str, List[float]] = {}

    def is_allowed(
        self,
        key: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> bool:
        """检查是否允许请求"""
        now = time.time()
        cutoff = now - window_seconds

        # 清理过期时间戳
        timestamps = self._windows.get(key, [])
        timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) >= max_requests:
            self._windows[key] = timestamps
            return False

        timestamps.append(now)
        self._windows[key] = timestamps
        return True

    def get_remaining(
        self,
        key: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> int:
        """获取剩余配额"""
        now = time.time()
        cutoff = now - window_seconds

        timestamps = self._windows.get(key, [])
        timestamps = [t for t in timestamps if t > cutoff]

        return max(0, max_requests - len(timestamps))

    def get_retry_after(
        self,
        key: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> int:
        """获取需要等待的秒数"""
        now = time.time()
        cutoff = now - window_seconds

        timestamps = self._windows.get(key, [])
        timestamps = [t for t in timestamps if t > cutoff]

        if len(timestamps) < max_requests:
            return 0

        # 最早的时间戳过期后才能通过
        oldest = timestamps[0]
        return max(1, int(oldest + window_seconds - now) + 1)


# ============================================================
#  Token Bucket 限流器
# ============================================================

class TokenBucketRateLimiter:
    """
    基于 Redis 的 Token Bucket 限流器

    特性：
    - 原子性：Redis Lua 脚本保证并发安全
    - 降级：Redis 不可用时自动切换到内存滑动窗口
    - 用户隔离：每个 user_id 独立的令牌桶

    Redis key 设计：
    - loggazer:rl:{user_id} = Hash {tokens, last_refill}
    """

    def __init__(self, redis_client=None, key_prefix: str = "loggazer:rl"):
        """
        参数:
            redis_client: redis.Redis 实例，None 时使用内存降级
            key_prefix: Redis key 前缀
        """
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._memory_fallback = _MemorySlidingWindow()

        # 注册 Lua 脚本（如果有 Redis）
        self._lua_script = None
        if self._redis is not None:
            try:
                self._lua_script = self._redis.register_script(_TOKEN_BUCKET_LUA)
            except Exception as e:
                logger.warning("Redis Lua 脚本注册失败，降级到内存: %s", e)

    def is_allowed(
        self,
        user_id: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> bool:
        """
        检查请求是否被允许

        参数:
            user_id: 用户标识（"anonymous" 或实际用户 ID）
            max_requests: 窗口内最大请求数
            window_seconds: 窗口大小（秒）

        返回:
            True = 允许通过，False = 触发限流
        """
        # 尝试 Redis Token Bucket
        if self._redis is not None and self._lua_script is not None:
            try:
                key = f"{self._key_prefix}:{user_id}"
                rate = max_requests / window_seconds  # tokens per second
                now = time.time()

                result = self._lua_script(
                    keys=[key],
                    args=[max_requests, rate, now, 1],
                )

                allowed = bool(result[0])
                return allowed

            except Exception as e:
                logger.warning("Redis 限流失败，降级到内存: %s", e)

        # 内存降级
        key = f"{self._key_prefix}:{user_id}"
        return self._memory_fallback.is_allowed(key, max_requests, window_seconds)

    def get_remaining_quota(
        self,
        user_id: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> int:
        """
        获取用户剩余配额

        参数:
            user_id: 用户标识
            max_requests: 窗口内最大请求数
            window_seconds: 窗口大小（秒）

        返回:
            剩余可用请求数
        """
        if self._redis is not None:
            try:
                key = f"{self._key_prefix}:{user_id}"
                rate = max_requests / window_seconds
                now = time.time()

                result = self._lua_script(
                    keys=[key],
                    args=[max_requests, rate, now, 0],  # 消耗 0 个令牌，仅查询
                )

                return int(result[1])

            except Exception as e:
                logger.warning("Redis 配额查询失败，降级到内存: %s", e)

        key = f"{self._key_prefix}:{user_id}"
        return self._memory_fallback.get_remaining(key, max_requests, window_seconds)

    def get_retry_after(
        self,
        user_id: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> int:
        """
        获取限流后需要等待的秒数

        参数:
            user_id: 用户标识
            max_requests: 窗口内最大请求数
            window_seconds: 窗口大小（秒）

        返回:
            需要等待的秒数（0 表示无需等待）
        """
        key = f"{self._key_prefix}:{user_id}"
        return self._memory_fallback.get_retry_after(key, max_requests, window_seconds)
