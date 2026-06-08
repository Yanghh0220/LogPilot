# tests/test_log_parser.py - 测试日志解析器
#
# 测试什么？
# 1. 平台识别是否准确
# 2. 错误行提取是否正确
# 3. 智能截断是否生效
#
# 如何运行？
# 在项目根目录执行：pytest tests/test_log_parser.py -v

from log_parser import detect_platform, extract_error_lines, truncate_log, parse_log


# ============================================
# 测试：平台识别
# ============================================

class TestDetectPlatform:
    """测试 detect_platform() 函数"""

    def test_npm_log(self):
        """能识别 npm 错误日志"""
        log = "npm ERR! code ERESOLVE\nnpm ERR! ERESOLVE could not resolve"
        assert detect_platform(log) == "npm"

    def test_docker_log(self):
        """能识别 Docker 构建日志"""
        log = "Step 4/8 : RUN pip install\n ---> Running in 5a3b2c1d\nThe command '/bin/sh -c pip install' returned a non-zero code: 1"
        assert detect_platform(log) == "Docker"

    def test_github_actions_log(self):
        """能识别 GitHub Actions 日志"""
        log = "##[error]Process completed with exit code 1\nRun actions/checkout@v3"
        assert detect_platform(log) == "GitHub Actions"

    def test_pytest_log(self):
        """能识别 pytest 测试失败日志"""
        log = "========================= FAILURES =========================\nassert 401 == 200"
        assert detect_platform(log) == "pytest"

    def test_unknown_log(self):
        """无法识别的日志返回 Unknown"""
        log = "Hello world\nThis is a normal text"
        assert detect_platform(log) == "Unknown"

    def test_pip_log(self):
        """能识别 pip 安装失败日志"""
        log = "ERROR: Could not find a version that satisfies the requirement\npip._internal"
        assert detect_platform(log) == "pip"


# ============================================
# 测试：错误行提取
# ============================================

class TestExtractErrorLines:
    """测试 extract_error_lines() 函数"""

    def test_extracts_error_lines(self):
        """能提取包含 error 关键词的行"""
        log = "Starting build...\nCompiling code...\nERROR: syntax error at line 10\nBuild complete"
        result = extract_error_lines(log)
        assert len(result) == 1
        assert "syntax error" in result[0]

    def test_extracts_multiple_errors(self):
        """能提取多行错误"""
        log = "line1\nERROR: first error\nline3\nFAILED: second error\nline5"
        result = extract_error_lines(log)
        assert len(result) == 2

    def test_deduplicates(self):
        """相同内容的行不会重复提取"""
        log = "ERROR: same error\nERROR: same error\nERROR: same error"
        result = extract_error_lines(log)
        assert len(result) == 1

    def test_skips_short_lines(self):
        """太短的行（< 5 字符）会被跳过"""
        log = "ERR\nThis is an error message"
        result = extract_error_lines(log)
        # "ERR" 太短，会被跳过
        assert len(result) == 1

    def test_respects_max_lines(self):
        """不会超过 max_lines 限制"""
        log = "\n".join([f"ERROR: error {i}" for i in range(50)])
        result = extract_error_lines(log, max_lines=5)
        assert len(result) == 5

    def test_empty_log(self):
        """空日志返回空列表"""
        assert extract_error_lines("") == []


# ============================================
# 测试：智能截断
# ============================================

class TestTruncateLog:
    """测试 truncate_log() 函数"""

    def test_short_log_not_truncated(self):
        """短日志不会被截断"""
        log = "short log content"
        result = truncate_log(log, max_length=100)
        assert result == log

    def test_long_log_truncated(self):
        """长日志会被截断"""
        # 创建一个超过 max_length 的日志
        log = "line\n" * 200
        result = truncate_log(log, max_length=100)
        assert len(result) < len(log)
        assert "省略" in result

    def test_truncated_preserves_head_and_tail(self):
        """截断后保留了头部和尾部内容"""
        lines = [f"line {i}" for i in range(200)]
        log = "\n".join(lines)
        result = truncate_log(log, max_length=500)
        # 头部内容应该保留
        assert "line 0" in result
        # 尾部内容应该保留
        assert "line 199" in result


# ============================================
# 测试：parse_log 主入口
# ============================================

class TestParseLog:
    """测试 parse_log() 主函数"""

    def test_returns_all_keys(self):
        """返回的字典包含所有必要字段"""
        log = "npm ERR! code ERESOLVE\nERROR: something failed"
        result = parse_log(log)
        assert "platform" in result
        assert "error_lines" in result
        assert "truncated_log" in result
        assert "is_truncated" in result

    def test_platform_detection(self):
        """parse_log 能正确识别平台"""
        log = "npm ERR! code ERESOLVE\nnpm ERR! could not resolve"
        result = parse_log(log)
        assert result["platform"] == "npm"

    def test_error_lines_extracted(self):
        """parse_log 能提取错误行"""
        log = "Starting...\nERROR: build failed\nDone"
        result = parse_log(log)
        assert len(result["error_lines"]) > 0

    def test_short_log_not_truncated(self):
        """短日志不会被截断"""
        log = "short error log"
        result = parse_log(log)
        assert result["is_truncated"] is False
