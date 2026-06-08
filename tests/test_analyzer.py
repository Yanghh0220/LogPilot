# tests/test_analyzer.py - 测试 AI 分析引擎
#
# 测试什么？
# 1. 自定义异常类（AuthError / RateLimitError / QuotaError）
# 2. _parse_http_error() 状态码映射
# 3. call_ai() 的异常处理（通过 mock 模拟 API 响应）
# 4. analyze_log() 的完整流程（通过 mock 模拟 AI 返回）
#
# 为什么要 mock？
# - 真实调用 DeepSeek API 需要有效的 API Key
# - CI 环境中没有 API Key，直接调用会失败
# - mock 让我们能测试"AI 返回各种结果时，代码的行为是否正确"
#
# 如何运行？
# 在项目根目录执行：pytest tests/test_analyzer.py -v

import os
import sys
from pathlib import Path

# 必须在 import analyzer 之前设置假的 API Key
# 否则模块级的 OpenAI() 初始化会报 Missing credentials
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-dummy-key-for-testing")

import pytest
from unittest.mock import patch, MagicMock

# 确保能找到项目模块
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzer import (
    AuthError,
    RateLimitError,
    QuotaError,
    _parse_http_error,
    call_ai,
    analyze_log,
)


# ============================================
# 测试：自定义异常类
# ============================================

class TestCustomExceptions:
    """测试三个自定义异常类是否正确定义"""

    def test_auth_error_is_exception(self):
        """AuthError 是 Exception 的子类"""
        assert issubclass(AuthError, Exception)

    def test_rate_limit_error_is_exception(self):
        """RateLimitError 是 Exception 的子类"""
        assert issubclass(RateLimitError, Exception)

    def test_quota_error_is_exception(self):
        """QuotaError 是 Exception 的子类"""
        assert issubclass(QuotaError, Exception)

    def test_auth_error_message(self):
        """AuthError 能携带错误信息"""
        err = AuthError("API Key 无效")
        assert "API Key" in str(err)

    def test_rate_limit_error_message(self):
        """RateLimitError 能携带错误信息"""
        err = RateLimitError("请求太频繁")
        assert "太频繁" in str(err)

    def test_quota_error_message(self):
        """QuotaError 能携带错误信息"""
        err = QuotaError("余额不足")
        assert "余额" in str(err)


# ============================================
# 测试：HTTP 错误解析
# ============================================

class TestParseHttpError:
    """测试 _parse_http_error() 状态码到异常的映射"""

    def test_401_returns_auth_error(self):
        """401 状态码应返回 AuthError"""
        result = _parse_http_error(401, "Invalid API key")
        assert isinstance(result, AuthError)
        assert "401" in str(result)

    def test_429_returns_rate_limit_error(self):
        """429 状态码应返回 RateLimitError"""
        result = _parse_http_error(429, "Rate exceeded")
        assert isinstance(result, RateLimitError)
        assert "429" in str(result)

    def test_402_returns_quota_error(self):
        """402 状态码应返回 QuotaError"""
        result = _parse_http_error(402, "Insufficient balance")
        assert isinstance(result, QuotaError)
        assert "402" in str(result)

    def test_400_returns_value_error(self):
        """400 状态码应返回 ValueError"""
        result = _parse_http_error(400, "Bad request")
        assert isinstance(result, ValueError)
        assert "400" in str(result)

    def test_500_returns_connection_error(self):
        """其他状态码应返回 ConnectionError"""
        result = _parse_http_error(500, "Internal server error")
        assert isinstance(result, ConnectionError)
        assert "500" in str(result)

    def test_error_message_preserved(self):
        """错误信息被正确传递到异常中"""
        result = _parse_http_error(401, "Key expired")
        assert "Key expired" in str(result)


# ============================================
# 测试：call_ai 函数
# ============================================

class TestCallAi:
    """测试 call_ai() 的异常处理"""

    @patch("analyzer._client")
    def test_success_returns_text(self, mock_client):
        """正常调用时返回 AI 的文本回复"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello, I am AI"
        mock_client.chat.completions.create.return_value = mock_response

        result = call_ai("test prompt")
        assert result == "Hello, I am AI"

    @patch("analyzer.DEEPSEEK_API_KEY", None)
    def test_no_api_key_returns_warning(self):
        """API Key 未配置时返回 ⚠️ 提示"""
        result = call_ai("test prompt")
        assert result.startswith("⚠️")
        assert "API Key" in result

    @patch("analyzer._client")
    def test_empty_response_returns_warning(self, mock_client):
        """AI 返回空内容时返回 ⚠️ 提示"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_client.chat.completions.create.return_value = mock_response

        result = call_ai("test prompt")
        assert result.startswith("⚠️")
        assert "空内容" in result

    @patch("analyzer._client")
    def test_auth_error_raises(self, mock_client):
        """认证失败时抛出 AuthError（不捕获，直接抛出）"""
        from openai import AuthenticationError
        mock_client.chat.completions.create.side_effect = AuthenticationError(
            message="Invalid key",
            response=MagicMock(status_code=401),
            body={"error": {"message": "Invalid key"}},
        )

        with pytest.raises(AuthError):
            call_ai("test prompt")

    @patch("analyzer._client")
    def test_generic_exception_returns_warning(self, mock_client):
        """未知异常时返回 ⚠️ 提示（不崩溃）"""
        mock_client.chat.completions.create.side_effect = RuntimeError("something broke")

        result = call_ai("test prompt")
        assert result.startswith("⚠️")
        assert "something broke" in result


# ============================================
# 测试：analyze_log 完整流程
# ============================================

class TestAnalyzeLog:
    """测试 analyze_log() 的输入验证和流程编排"""

    def test_empty_input_raises_value_error(self):
        """空输入应抛出 ValueError"""
        with pytest.raises(ValueError, match="不能为空"):
            analyze_log("")

    def test_whitespace_only_raises_value_error(self):
        """纯空格输入应抛出 ValueError"""
        with pytest.raises(ValueError, match="不能为空"):
            analyze_log("   ")

    @patch("analyzer.call_ai")
    def test_call_ai_error_raises_connection_error(self, mock_call_ai):
        """call_ai 返回 ⚠️ 提示时，应抛出 ConnectionError"""
        mock_call_ai.return_value = "⚠️ **API Key 未配置**"

        with pytest.raises(ConnectionError, match="⚠️"):
            analyze_log("ERROR: build failed")

    @patch("analyzer.call_ai")
    def test_invalid_json_raises_value_error(self, mock_call_ai):
        """call_ai 返回非法 JSON 时，应抛出 ValueError"""
        mock_call_ai.return_value = "This is not JSON at all"

        with pytest.raises(ValueError, match="无法解析"):
            analyze_log("ERROR: build failed")

    @patch("analyzer.call_ai")
    def test_valid_json_returns_dict(self, mock_call_ai):
        """call_ai 返回合法 JSON 时，应解析为字典返回"""
        mock_call_ai.return_value = '{"error_summary": "test error", "reason": "test reason"}'

        result = analyze_log("ERROR: build failed")
        assert isinstance(result, dict)
        assert result["error_summary"] == "test error"

    @patch("analyzer.call_ai")
    def test_json_with_code_fence(self, mock_call_ai):
        """call_ai 返回带 ```json 包裹的 JSON 时，应正确解析"""
        mock_call_ai.return_value = '```json\n{"error_summary": "ok"}\n```'

        result = analyze_log("ERROR: build failed")
        assert result["error_summary"] == "ok"

    @patch("analyzer.call_ai")
    def test_passes_platform_to_prompt(self, mock_call_ai):
        """应将识别到的平台信息传入提示词"""
        mock_call_ai.return_value = '{"error_summary": "ok"}'

        analyze_log("npm ERR! code ERESOLVE\nnpm ERR! could not resolve")
        # call_ai 应该被调用了一次
        assert mock_call_ai.called
        # 调用参数中应包含 npm 平台信息
        prompt = mock_call_ai.call_args[0][0]
        assert "npm" in prompt
