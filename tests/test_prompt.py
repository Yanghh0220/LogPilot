# tests/test_prompt.py - 测试提示词构建
#
# 测试什么？
# 1. build_user_prompt 的输出格式是否正确
# 2. 各种参数组合是否正常工作
#
# 如何运行？
# 在项目根目录执行：pytest tests/test_prompt.py -v

from prompt import SYSTEM_PROMPT, build_user_prompt


# ============================================
# 测试：系统提示词
# ============================================

class TestSystemPrompt:
    """测试 SYSTEM_PROMPT 常量"""

    def test_contains_json_format(self):
        """系统提示词包含 JSON 格式说明"""
        assert "error_summary" in SYSTEM_PROMPT
        assert "fix_suggestions" in SYSTEM_PROMPT
        assert "debug_commands" in SYSTEM_PROMPT

    def test_contains_few_shot_example(self):
        """系统提示词包含 Few-shot 示例"""
        assert "示例输入" in SYSTEM_PROMPT
        assert "示例输出" in SYSTEM_PROMPT

    def test_contains_rules(self):
        """系统提示词包含规则约束"""
        assert "只返回 JSON" in SYSTEM_PROMPT


# ============================================
# 测试：用户提示词构建
# ============================================

class TestBuildUserPrompt:
    """测试 build_user_prompt() 函数"""

    def test_basic_usage(self):
        """最基本的用法：只传日志文本"""
        log = "ERROR: build failed"
        result = build_user_prompt(log)
        assert log in result
        assert "JSON" in result

    def test_with_platform(self):
        """传入平台信息时，提示词包含平台名"""
        log = "some error"
        result = build_user_prompt(log, platform="npm")
        assert "npm" in result

    def test_with_error_lines(self):
        """传入错误行时，提示词包含错误行内容"""
        log = "full log here"
        error_lines = ["ERROR: line1", "FAILED: line2"]
        result = build_user_prompt(log, error_lines=error_lines)
        assert "ERROR: line1" in result
        assert "FAILED: line2" in result

    def test_with_truncation_warning(self):
        """日志被截断时，提示词包含截断警告"""
        log = "truncated log"
        result = build_user_prompt(log, is_truncated=True)
        assert "截断" in result

    def test_unknown_platform_not_shown(self):
        """平台为 Unknown 时不显示平台信息"""
        log = "some log"
        result = build_user_prompt(log, platform="Unknown")
        # "Unknown" 不应该出现在提示词中
        assert "日志来源平台" not in result

    def test_empty_error_lines_not_shown(self):
        """错误行为空时不显示错误行部分"""
        log = "some log"
        result = build_user_prompt(log, error_lines=[])
        assert "已识别的关键错误行" not in result

    def test_full_scenario(self):
        """完整场景：平台 + 错误行 + 截断"""
        log = "very long log..."
        result = build_user_prompt(
            log,
            platform="Docker",
            error_lines=["ERROR: failed to solve"],
            is_truncated=True,
        )
        assert "Docker" in result
        assert "ERROR: failed to solve" in result
        assert "截断" in result
        assert log in result
