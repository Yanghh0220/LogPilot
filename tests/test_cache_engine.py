# tests/test_cache_engine.py - 语义缓存引擎测试
#
# 测试什么？
# 1. 指纹生成：相同错误 → 相同指纹；时间戳变化 → 相同指纹
# 2. 缓存命中：相同日志二次分析命中缓存，API 调用次数为 0
# 3. RAG 上下文：相似日志（仅时间戳不同）命中 RAG 上下文
# 4. 缓存未命中：完全不同日志走 AI 调用
# 5. 降级策略：Qdrant 不可用时优雅降级
# 6. TTL 过期：过期缓存重新分析
#
# 如何运行？
# 在项目根目录执行：pytest tests/test_cache_engine.py -v
#
# 注意：所有测试使用 Mock Embedding，不依赖外部服务

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from models import AnalysisResult, ParsedLog


# ============================================================
#  Mock Fixtures
# ============================================================

class _MockNumpyArray:
    """模拟 numpy 数组，支持 .tolist() 方法"""

    def __init__(self, data: list[float]):
        self._data = data

    def tolist(self) -> list[float]:
        return self._data


def _make_mock_embedding(text: str) -> _MockNumpyArray:
    """
    生成确定性的 Mock Embedding 向量（384 维）

    使用文本哈希生成伪向量，保证：
    - 相同文本 → 相同向量（余弦相似度 = 1.0）
    - 不同文本 → 不同向量（余弦相似度 < 1.0）
    """
    import hashlib
    h = hashlib.md5(text.encode()).hexdigest()
    # 用哈希生成 384 维向量，值域 [-1, 1]
    vector = []
    for i in range(384):
        byte_val = int(h[i % 32], 16)
        vector.append((byte_val - 7.5) / 7.5)
    return _MockNumpyArray(vector)


def _make_parsed_log(
    platform: str = "npm",
    error_lines: list[str] | None = None,
) -> ParsedLog:
    """构造测试用的 ParsedLog"""
    return {
        "platform": platform,
        "error_lines": error_lines or [
            "npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
        ],
        "truncated_log": "full log content here",
        "is_truncated": False,
    }


def _make_analysis_result() -> AnalysisResult:
    """构造测试用的 AnalysisResult"""
    return {
        "error_summary": "npm 依赖解析冲突",
        "error_detail": "npm ERR! ERESOLVE could not resolve",
        "reason": "react 版本不兼容",
        "fix_suggestions": [
            {
                "title": "使用 --legacy-peer-deps",
                "description": "跳过 peer dependency 检查",
                "command": "npm install --legacy-peer-deps",
            },
        ],
        "debug_commands": ["npm ls react", "npm why react"],
    }


# ============================================================
#  测试：指纹生成
# ============================================================

class TestGenerateFingerprint:
    """测试 generate_fingerprint() 函数"""

    def test_same_log_same_fingerprint(self):
        """相同日志生成相同指纹"""
        from cache_engine import generate_fingerprint
        log1 = _make_parsed_log()
        log2 = _make_parsed_log()
        assert generate_fingerprint(log1) == generate_fingerprint(log2)

    def test_different_platform_different_fingerprint(self):
        """不同平台生成不同指纹"""
        from cache_engine import generate_fingerprint
        npm_log = _make_parsed_log(platform="npm")
        docker_log = _make_parsed_log(platform="Docker")
        assert generate_fingerprint(npm_log) != generate_fingerprint(docker_log)

    def test_different_errors_different_fingerprint(self):
        """不同错误行生成不同指纹"""
        from cache_engine import generate_fingerprint
        log1 = _make_parsed_log(error_lines=["ERROR: build failed"])
        log2 = _make_parsed_log(error_lines=["FATAL: out of memory"])
        assert generate_fingerprint(log1) != generate_fingerprint(log2)

    def test_timestamp_normalized(self):
        """时间戳差异不影响指纹"""
        from cache_engine import generate_fingerprint
        log1 = _make_parsed_log(
            error_lines=["ERROR 2024-01-15T10:30:45 build failed"]
        )
        log2 = _make_parsed_log(
            error_lines=["ERROR 2024-12-25T23:59:59 build failed"]
        )
        assert generate_fingerprint(log1) == generate_fingerprint(log2)

    def test_hex_address_normalized(self):
        """内存地址差异不影响指纹"""
        from cache_engine import generate_fingerprint
        log1 = _make_parsed_log(
            error_lines=["segfault at 0x7fff5fbff8ac ip 0x00007f"]
        )
        log2 = _make_parsed_log(
            error_lines=["segfault at 0xdeadbeef ip 0x0000ff"]
        )
        assert generate_fingerprint(log1) == generate_fingerprint(log2)

    def test_uuid_normalized(self):
        """UUID 差异不影响指纹"""
        from cache_engine import generate_fingerprint
        log1 = _make_parsed_log(
            error_lines=[
                "job 550e8400-e29b-41d4-a716-446655440000 failed"
            ]
        )
        log2 = _make_parsed_log(
            error_lines=[
                "job 6ba7b810-9dad-11d1-80b4-00c04fd430c8 failed"
            ]
        )
        assert generate_fingerprint(log1) == generate_fingerprint(log2)

    def test_pid_normalized(self):
        """PID 差异不影响指纹"""
        from cache_engine import generate_fingerprint
        log1 = _make_parsed_log(
            error_lines=["process pid=12345 killed"]
        )
        log2 = _make_parsed_log(
            error_lines=["process pid=99999 killed"]
        )
        assert generate_fingerprint(log1) == generate_fingerprint(log2)


# ============================================================
#  测试：_normalize_text
# ============================================================

class TestNormalizeText:
    """测试 _normalize_text() 内部函数"""

    def test_strips_timestamps(self):
        from cache_engine import _normalize_text
        result = _normalize_text("error at 2024-01-15T10:30:45Z occurred")
        assert "2024" not in result
        assert "<ts>" in result

    def test_strips_hex_addresses(self):
        from cache_engine import _normalize_text
        result = _normalize_text("address 0x7fff5fbff8ac invalid")
        assert "0x7fff" not in result
        assert "<hex>" in result

    def test_strips_uuids(self):
        from cache_engine import _normalize_text
        result = _normalize_text(
            "id 550e8400-e29b-41d4-a716-446655440000 not found"
        )
        assert "550e8400" not in result
        assert "<uuid>" in result

    def test_collapses_whitespace(self):
        from cache_engine import _normalize_text
        result = _normalize_text("error   at   line    10")
        assert "  " not in result

    def test_lowercases(self):
        from cache_engine import _normalize_text
        result = _normalize_text("ERROR: Build FAILED")
        assert result == result.lower()


# ============================================================
#  测试：SemanticCache 核心功能
# ============================================================

class TestSemanticCacheGet:
    """测试 SemanticCache.get() 缓存检索"""

    def test_exact_hit_returns_cached_result(self):
        """相同日志二次分析命中缓存"""
        from cache_engine import SemanticCache, generate_fingerprint

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._similarity_high = 0.92
        cache._similarity_low = 0.80
        cache._ttl_seconds = 720 * 3600
        cache._collection_name = "log_analysis_cache"
        cache._vector_size = 384
        cache._embedder = MagicMock()
        cache._embedder.encode = MagicMock(
            side_effect=_make_mock_embedding
        )

        # Mock Qdrant client
        mock_client = MagicMock()
        cache._client = mock_client

        parsed = _make_parsed_log()
        result = _make_analysis_result()
        fingerprint = generate_fingerprint(parsed)

        # 模拟精确匹配命中
        mock_point = MagicMock()
        mock_point.id = 1
        mock_point.payload = {
            "fingerprint": fingerprint,
            "result_json": json.dumps(result),
            "created_at": time.time(),
            "hit_count": 1,
        }
        mock_client.scroll.return_value = ([mock_point], None)

        cached = cache.get(fingerprint, parsed)
        assert cached is not None
        assert cached["error_summary"] == result["error_summary"]

    def test_miss_returns_none(self):
        """完全不同日志未命中缓存"""
        from cache_engine import SemanticCache, generate_fingerprint

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._similarity_high = 0.92
        cache._similarity_low = 0.80
        cache._ttl_seconds = 720 * 3600
        cache._collection_name = "log_analysis_cache"
        cache._vector_size = 384
        cache._embedder = MagicMock()
        cache._embedder.encode = MagicMock(
            side_effect=_make_mock_embedding
        )

        mock_client = MagicMock()
        cache._client = mock_client

        parsed = _make_parsed_log()
        fingerprint = generate_fingerprint(parsed)

        # 精确匹配未命中
        mock_client.scroll.return_value = ([], None)

        # 向量检索也未命中
        mock_search_result = MagicMock()
        mock_search_result.points = []
        mock_client.query_points.return_value = mock_search_result

        cached = cache.get(fingerprint, parsed)
        assert cached is None


class TestSemanticCacheSet:
    """测试 SemanticCache.set() 缓存写入"""

    def test_writes_to_qdrant(self):
        """写入缓存时调用 Qdrant upsert"""
        from cache_engine import SemanticCache, generate_fingerprint

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._collection_name = "log_analysis_cache"
        cache._embedder = MagicMock()
        cache._embedder.encode = MagicMock(
            side_effect=_make_mock_embedding
        )

        mock_client = MagicMock()
        cache._client = mock_client

        parsed = _make_parsed_log()
        result = _make_analysis_result()
        fingerprint = generate_fingerprint(parsed)

        cache.set(fingerprint, result, {
            "platform": parsed["platform"],
            "error_lines": parsed["error_lines"],
        })

        mock_client.upsert.assert_called_once()

    def test_skips_when_unavailable(self):
        """缓存不可用时跳过写入"""
        from cache_engine import SemanticCache

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = False

        # 不应抛出异常
        cache.set("fp", _make_analysis_result(), {"platform": "npm"})


class TestSemanticCacheDegradation:
    """测试降级策略"""

    def test_qdrant_unavailable_returns_none(self):
        """Qdrant 不可用时 get() 返回 None"""
        from cache_engine import SemanticCache

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = False
        cache._embedding_available = False

        result = cache.get("fingerprint", _make_parsed_log())
        assert result is None

    def test_embedding_unavailable_skips_vector_search(self):
        """Embedding 不可用时跳过向量检索，仅精确匹配"""
        from cache_engine import SemanticCache, generate_fingerprint

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = False
        cache._similarity_high = 0.92
        cache._similarity_low = 0.80
        cache._ttl_seconds = 720 * 3600
        cache._collection_name = "log_analysis_cache"

        mock_client = MagicMock()
        cache._client = mock_client

        parsed = _make_parsed_log()
        fingerprint = generate_fingerprint(parsed)

        # 精确匹配未命中
        mock_client.scroll.return_value = ([], None)

        # 应直接返回 None，不尝试向量检索
        result = cache.get(fingerprint, parsed)
        assert result is None
        mock_client.query_points.assert_not_called()

    def test_exception_during_get_returns_none(self):
        """get() 内部异常返回 None，不向上传播"""
        from cache_engine import SemanticCache

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._similarity_high = 0.92
        cache._similarity_low = 0.80
        cache._ttl_seconds = 720 * 3600
        cache._collection_name = "log_analysis_cache"
        cache._embedder = MagicMock()

        mock_client = MagicMock()
        mock_client.scroll.side_effect = RuntimeError("Qdrant crashed")
        cache._client = mock_client

        # 不应抛出异常
        result = cache.get("fp", _make_parsed_log())
        assert result is None

    def test_exception_during_set_silently_ignored(self):
        """set() 内部异常静默忽略"""
        from cache_engine import SemanticCache

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._collection_name = "log_analysis_cache"
        cache._embedder = MagicMock()
        cache._embedder.encode = MagicMock(
            side_effect=_make_mock_embedding
        )

        mock_client = MagicMock()
        mock_client.upsert.side_effect = RuntimeError("disk full")
        cache._client = mock_client

        # 不应抛出异常
        cache.set("fp", _make_analysis_result(), {
            "platform": "npm",
            "error_lines": ["error"],
        })


class TestSemanticCacheTTL:
    """测试 TTL 过期机制"""

    def test_expired_entry_returns_none(self):
        """过期缓存条目返回 None"""
        from cache_engine import SemanticCache, generate_fingerprint

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._similarity_high = 0.92
        cache._similarity_low = 0.80
        cache._ttl_seconds = 3600  # 1 小时 TTL
        cache._collection_name = "log_analysis_cache"
        cache._embedder = MagicMock()

        mock_client = MagicMock()
        cache._client = mock_client

        parsed = _make_parsed_log()
        fingerprint = generate_fingerprint(parsed)

        # 模拟过期条目（created_at 是 2 小时前）
        mock_point = MagicMock()
        mock_point.id = 1
        mock_point.payload = {
            "fingerprint": fingerprint,
            "result_json": json.dumps(_make_analysis_result()),
            "created_at": time.time() - 7200,  # 2 小时前
            "hit_count": 1,
        }
        mock_client.scroll.return_value = ([mock_point], None)

        result = cache.get(fingerprint, parsed)
        assert result is None
        # 应该删除过期条目
        mock_client.delete.assert_called_once()

    def test_fresh_entry_returns_result(self):
        """未过期条目正常返回"""
        from cache_engine import SemanticCache, generate_fingerprint

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._similarity_high = 0.92
        cache._similarity_low = 0.80
        cache._ttl_seconds = 3600
        cache._collection_name = "log_analysis_cache"
        cache._embedder = MagicMock()

        mock_client = MagicMock()
        cache._client = mock_client

        parsed = _make_parsed_log()
        result = _make_analysis_result()
        fingerprint = generate_fingerprint(parsed)

        mock_point = MagicMock()
        mock_point.id = 1
        mock_point.payload = {
            "fingerprint": fingerprint,
            "result_json": json.dumps(result),
            "created_at": time.time(),  # 刚刚创建
            "hit_count": 1,
        }
        mock_client.scroll.return_value = ([mock_point], None)

        cached = cache.get(fingerprint, parsed)
        assert cached is not None
        assert cached["error_summary"] == result["error_summary"]


class TestRAGContext:
    """测试 RAG 上下文检索"""

    def test_returns_markdown_format(self):
        """返回 Markdown 格式的上下文"""
        from cache_engine import SemanticCache

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._similarity_low = 0.80
        cache._collection_name = "log_analysis_cache"

        mock_client = MagicMock()
        cache._client = mock_client

        # 模拟 scroll 返回当前指纹的向量
        mock_self_point = MagicMock()
        mock_self_point.vector = _make_mock_embedding("npm error").tolist()
        mock_client.scroll.return_value = ([mock_self_point], None)

        # 模拟 query_points 返回相似案例
        mock_similar = MagicMock()
        mock_similar.payload = {
            "fingerprint": "other_fingerprint",
            "platform": "npm",
            "error_summary": "依赖版本冲突",
            "fix_commands": ["npm install --legacy-peer-deps"],
            "hit_count": 5,
        }
        mock_similar.score = 0.85

        mock_search = MagicMock()
        mock_search.points = [mock_similar]
        mock_client.query_points.return_value = mock_search

        context = cache.get_rag_context("test_fingerprint")
        assert "npm" in context
        assert "依赖版本冲突" in context
        assert "legacy-peer-deps" in context
        assert "5" in context

    def test_returns_empty_when_unavailable(self):
        """缓存不可用时返回空字符串"""
        from cache_engine import SemanticCache

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = False
        cache._embedding_available = False

        assert cache.get_rag_context("fp") == ""

    def test_returns_empty_on_exception(self):
        """异常时返回空字符串"""
        from cache_engine import SemanticCache

        cache = SemanticCache.__new__(SemanticCache)
        cache._available = True
        cache._embedding_available = True
        cache._similarity_low = 0.80
        cache._collection_name = "log_analysis_cache"

        mock_client = MagicMock()
        mock_client.scroll.side_effect = RuntimeError("crash")
        cache._client = mock_client

        assert cache.get_rag_context("fp") == ""
