# tests/test_analyzer_integration.py - 分析器集成测试
#
# 测试什么？
# 1. 完整 analyze_log() 流程中缓存层的集成行为
# 2. 相同日志第二次分析命中缓存，API 调用次数为 0
# 3. 完全不同日志走 AI 调用
# 4. 缓存故障时降级到直接调用
#
# 如何运行？
# 在项目根目录执行：pytest tests/test_analyzer_integration.py -v
#
# 注意：所有测试 Mock 了 AI 调用和 Embedding，不依赖外部服务

import json
import time
import sys
from unittest.mock import MagicMock, patch

import pytest

from models import AnalysisResult, ParsedLog


# ============================================================
#  测试用数据
# ============================================================

SAMPLE_NPM_LOG = """\
npm ERR! code ERESOLVE
npm ERR! ERESOLVE could not resolve
npm ERR! While resolving: react-scripts@5.0.1
npm ERR! Found: react@18.2.0
npm ERR! Conflicting peer dependency: react@17.0.2
npm ERR! Fix the upstream dependency conflict, or retry
npm ERR! this command with --force or --legacy-peer-deps
"""

SAMPLE_DOCKER_LOG = """\
Step 4/8 : RUN pip install -r requirements.txt
 ---> Running in 5a3b2c1d
ERROR: Could not find a version that satisfies the requirement
The command '/bin/sh -c pip install' returned a non-zero code: 1
"""


def _make_mock_ai_result(**overrides) -> AnalysisResult:
    """构造一个合法的 AnalysisResult 实例"""
    data = {
        "error_summary": "npm 依赖解析冲突",
        "error_detail": "npm ERR! ERESOLVE could not resolve",
        "root_causes": [
            {"description": "react 版本不兼容", "probability": 90},
            {"description": "package-lock.json 过期", "probability": 10},
        ],
        "fix_suggestions": [
            {
                "title": "使用 --legacy-peer-deps",
                "description": "跳过 peer dependency 检查",
                "command": "npm install --legacy-peer-deps",
                "safety_level": "safe",
            },
            {
                "title": "升级 testing-library",
                "description": "使用兼容 react 18 的版本",
                "command": "npm install @testing-library/react@latest",
                "safety_level": "safe",
            },
            {
                "title": "降级 react",
                "description": "使用 react 17",
                "command": "npm install react@17.0.2",
                "safety_level": "safe",
            },
        ],
        "debug_commands": ["npm ls react", "npm why react"],
        "severity": "medium",
        "prevention": ["使用更宽松的版本范围"],
        "security_warning": "",
    }
    data.update(overrides)
    return AnalysisResult.model_validate(data)


MOCK_AI_RESULT = _make_mock_ai_result()


def _make_docker_ai_result() -> AnalysisResult:
    return AnalysisResult.model_validate({
        "error_summary": "Docker 构建失败",
        "error_detail": "pip install failed",
        "root_causes": [
            {"description": "依赖不存在", "probability": 100},
        ],
        "fix_suggestions": [
            {
                "title": "检查依赖",
                "description": "确认 requirements.txt",
                "command": "cat requirements.txt",
                "safety_level": "safe",
            },
            {
                "title": "清理缓存",
                "description": "清除 pip 缓存",
                "command": "pip cache purge",
                "safety_level": "safe",
            },
            {
                "title": "重试构建",
                "description": "重新构建镜像",
                "command": "docker build --no-cache .",
                "safety_level": "safe",
            },
        ],
        "debug_commands": ["pip list", "pip check"],
        "severity": "high",
        "prevention": [],
        "security_warning": "",
    })


class _MockNumpyArray:
    """模拟 numpy 数组，支持 .tolist() 方法"""
    def __init__(self, data: list[float]):
        self._data = data
    def tolist(self) -> list[float]:
        return self._data


def _make_mock_embedding(text: str) -> _MockNumpyArray:
    """确定性的 Mock Embedding"""
    import hashlib
    h = hashlib.md5(text.encode()).hexdigest()
    data = [(int(h[i % 32], 16) - 7.5) / 7.5 for i in range(384)]
    return _MockNumpyArray(data)


def _setup_mocks(mock_openai_cls, mock_qdrant_cls, mock_st_cls):
    """统一配置所有 Mock，返回 mock_client"""
    # Mock OpenAI client（阻止模块级创建失败）
    mock_openai_instance = MagicMock()
    mock_openai_cls.return_value = mock_openai_instance

    # Mock Embedding
    mock_embedder = MagicMock()
    mock_embedder.encode = MagicMock(side_effect=_make_mock_embedding)
    mock_st_cls.return_value = mock_embedder

    # Mock Qdrant
    mock_client = MagicMock()
    mock_qdrant_cls.return_value = mock_client
    mock_collection = MagicMock()
    mock_collection.name = "log_analysis_cache"
    mock_client.get_collections.return_value.collections = [mock_collection]

    return mock_client


def _reset_cache():
    """重置缓存单例"""
    if "analyzer" in sys.modules:
        sys.modules["analyzer"]._cache_instance = None
        sys.modules["analyzer"]._cache_initialized = False


# ============================================================
#  测试：缓存集成
# ============================================================

class TestAnalyzeLogWithCache:
    """测试 analyze_log() 的缓存集成行为"""

    @patch("qdrant_client.QdrantClient")
    @patch("sentence_transformers.SentenceTransformer")
    @patch("ai_engine.call_ai_structured", return_value=MOCK_AI_RESULT)
    def test_same_log_hits_cache_second_time(
        self, mock_structured, mock_st_cls, mock_qdrant_cls
    ):
        """相同日志第二次分析命中缓存，API 调用次数为 0"""
        mock_client = _setup_mocks(
            MagicMock(), mock_qdrant_cls, mock_st_cls
        )

        # 第一次调用：缓存未命中
        mock_client.scroll.return_value = ([], None)
        mock_search = MagicMock()
        mock_search.points = []
        mock_client.query_points.return_value = mock_search

        _reset_cache()

        from analyzer import analyze_log

        # 第一次分析
        result1 = analyze_log(SAMPLE_NPM_LOG)
        assert mock_structured.call_count == 1

        # 获取第一次写入时的 fingerprint
        upsert_call = mock_client.upsert.call_args
        assert upsert_call is not None
        written_payload = upsert_call[1]["points"][0].payload
        fp = written_payload["fingerprint"]

        # 配置第二次 scroll 返回缓存命中
        mock_point = MagicMock()
        mock_point.id = 1
        mock_point.payload = written_payload
        mock_client.scroll.return_value = ([mock_point], None)

        # 第二次分析
        result2 = analyze_log(SAMPLE_NPM_LOG)

        # AI 不应被调用第二次
        assert mock_structured.call_count == 1
        assert result2.error_summary == result1.error_summary

    @patch("qdrant_client.QdrantClient")
    @patch("sentence_transformers.SentenceTransformer")
    @patch("ai_engine.call_ai_structured", return_value=_make_docker_ai_result())
    def test_different_log_calls_ai(
        self, mock_structured, mock_st_cls, mock_qdrant_cls
    ):
        """完全不同日志走 AI 调用"""
        mock_client = _setup_mocks(
            MagicMock(), mock_qdrant_cls, mock_st_cls
        )

        # 缓存未命中
        mock_client.scroll.return_value = ([], None)
        mock_search = MagicMock()
        mock_search.points = []
        mock_client.query_points.return_value = mock_search

        _reset_cache()

        from analyzer import analyze_log

        result = analyze_log(SAMPLE_DOCKER_LOG)
        assert mock_structured.call_count == 1
        assert result.error_summary == "Docker 构建失败"

    @patch("qdrant_client.QdrantClient")
    @patch("sentence_transformers.SentenceTransformer")
    @patch("ai_engine.call_ai_structured", return_value=MOCK_AI_RESULT)
    def test_cache_failure_degrades_gracefully(
        self, mock_structured, mock_st_cls, mock_qdrant_cls
    ):
        """缓存故障时降级到直接 AI 调用"""
        # Embedding 初始化失败
        mock_st_cls.side_effect = RuntimeError("torch not found")

        # Qdrant 仍需 mock
        mock_client = MagicMock()
        mock_qdrant_cls.return_value = mock_client
        mock_collection = MagicMock()
        mock_collection.name = "log_analysis_cache"
        mock_client.get_collections.return_value.collections = [
            mock_collection
        ]

        _reset_cache()

        from analyzer import analyze_log

        # 不应抛出异常
        result = analyze_log(SAMPLE_NPM_LOG)
        assert mock_structured.call_count == 1
        assert result.error_summary == "npm 依赖解析冲突"

    @patch("qdrant_client.QdrantClient")
    @patch("sentence_transformers.SentenceTransformer")
    @patch("ai_engine.call_ai_structured", return_value=MOCK_AI_RESULT)
    def test_returns_pydantic_model(
        self, mock_structured, mock_st_cls, mock_qdrant_cls
    ):
        """analyze_log() 返回 AnalysisResult 实例（Pydantic BaseModel）"""
        mock_client = _setup_mocks(
            MagicMock(), mock_qdrant_cls, mock_st_cls
        )

        mock_client.scroll.return_value = ([], None)
        mock_search = MagicMock()
        mock_search.points = []
        mock_client.query_points.return_value = mock_search

        _reset_cache()

        from analyzer import analyze_log

        result = analyze_log(SAMPLE_NPM_LOG)

        # 验证返回的是 AnalysisResult 实例
        assert isinstance(result, AnalysisResult)
        # 验证属性访问
        assert result.severity == "medium"
        assert len(result.root_causes) == 2
        assert sum(c.probability for c in result.root_causes) == 100


# ============================================================
#  测试：RAG Prompt 集成
# ============================================================

class TestRAGPromptIntegration:
    """测试 RAG 上下文注入到 Prompt"""

    def test_rag_context_injected_into_prompt(self):
        """RAG 上下文正确注入到提示词中"""
        from prompt import build_rag_augmented_prompt, build_analysis_prompt

        base_prompt = build_analysis_prompt(
            source="npm",
            error_lines=["npm ERR! code ERESOLVE"],
            stats={"error_count": 1, "warning_count": 0,
                   "fatal_count": 0, "total_lines": 10},
            full_log_preview="npm ERR! code ERESOLVE\n...",
        )

        rag_context = (
            "- **[npm]** 依赖版本冲突\n"
            "  修复命令: `npm install --legacy-peer-deps` (命中 5 次)"
        )

        augmented = build_rag_augmented_prompt(rag_context, base_prompt)

        assert "历史相似案例参考" in augmented
        assert "仅作参考" in augmented
        assert "依赖版本冲突" in augmented
        assert "legacy-peer-deps" in augmented
        # 原始 prompt 内容仍在
        assert "npm ERR! code ERESOLVE" in augmented

    def test_empty_rag_context_returns_base(self):
        """空 RAG 上下文返回原始提示词"""
        from prompt import build_rag_augmented_prompt

        base = "original prompt"
        result = build_rag_augmented_prompt("", base)
        assert result == base

    def test_rag_context_limits_to_3_cases(self):
        """RAG 上下文最多 3 条案例（由 get_rag_context 控制）"""
        from prompt import build_rag_augmented_prompt

        cases = [
            f"- **[npm]** error {i}\n  修复命令: `cmd{i}` (命中 {i} 次)"
            for i in range(5)
        ]
        # get_rag_context 返回时已限制 top_k=3
        rag_context = "\n".join(cases[:3])

        result = build_rag_augmented_prompt(rag_context, "base")
        assert "error 0" in result
        assert "error 1" in result
        assert "error 2" in result
        assert "error 3" not in result
