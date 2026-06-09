# tests/test_prompt.py - 测试提示词构建
#
# 测试什么？
# 1. SYSTEM_PROMPT 是否包含必要的格式说明和规则
# 2. build_analysis_prompt 的输出格式是否正确
# 3. 各种参数组合是否正常工作
#
# 如何运行？
# 在项目根目录执行：pytest tests/test_prompt.py -v

from prompt import SYSTEM_PROMPT, build_analysis_prompt, build_rag_augmented_prompt


# ============================================
# 测试：系统提示词
# ============================================

class TestSystemPrompt:
    """测试 SYSTEM_PROMPT 常量"""

    def test_contains_json_format(self):
        """系统提示词包含 JSON 格式的 6 个固定章节"""
        assert "error_summary" in SYSTEM_PROMPT
        assert "root_causes" in SYSTEM_PROMPT
        assert "fix_suggestions" in SYSTEM_PROMPT
        assert "debug_commands" in SYSTEM_PROMPT
        assert "severity" in SYSTEM_PROMPT
        assert "prevention" in SYSTEM_PROMPT

    def test_contains_few_shot_example(self):
        """系统提示词包含 Few-shot 示例"""
        assert "参考示例" in SYSTEM_PROMPT
        assert "示例输入" in SYSTEM_PROMPT
        assert "示例输出" in SYSTEM_PROMPT

    def test_contains_probability_constraint(self):
        """系统提示词包含百分比之和=100%的约束"""
        assert "100%" in SYSTEM_PROMPT

    def test_contains_rules(self):
        """系统提示词包含硬性规则"""
        assert "只返回 JSON" in SYSTEM_PROMPT
        assert "severity" in SYSTEM_PROMPT


# ============================================
# 测试：用户提示词构建
# ============================================

class TestBuildAnalysisPrompt:
    """测试 build_analysis_prompt() 函数"""

    # 提供默认的 stats 参数，避免重复
    DEFAULT_STATS = {
        "error_count": 2,
        "warning_count": 1,
        "fatal_count": 0,
        "total_lines": 50,
    }

    def test_basic_usage(self):
        """最基本的用法：只传日志文本和默认 stats"""
        log = "ERROR: build failed"
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=self.DEFAULT_STATS,
            full_log_preview=log,
        )
        assert log in result
        assert "JSON" in result

    def test_with_source(self):
        """传入平台信息时，提示词包含平台名"""
        log = "some error"
        result = build_analysis_prompt(
            source="npm",
            error_lines=[],
            stats=self.DEFAULT_STATS,
            full_log_preview=log,
        )
        assert "npm" in result

    def test_with_error_lines(self):
        """传入错误行时，提示词包含错误行内容"""
        log = "full log here"
        error_lines = ["ERROR: line1", "FAILED: line2"]
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=error_lines,
            stats=self.DEFAULT_STATS,
            full_log_preview=log,
        )
        assert "ERROR: line1" in result
        assert "FAILED: line2" in result

    def test_severity_hint_critical(self):
        """存在 fatal 错误时，严重程度提示为 critical"""
        log = "FATAL: process killed"
        stats = {"error_count": 1, "warning_count": 0, "fatal_count": 1, "total_lines": 10}
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=stats,
            full_log_preview=log,
        )
        assert "critical" in result

    def test_severity_hint_high(self):
        """错误数量 >= 5 时，严重程度提示为 high"""
        log = "multiple errors"
        stats = {"error_count": 6, "warning_count": 0, "fatal_count": 0, "total_lines": 100}
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=stats,
            full_log_preview=log,
        )
        assert "high" in result

    def test_severity_hint_medium(self):
        """有少量错误时，严重程度提示为 medium"""
        log = "one error"
        stats = {"error_count": 1, "warning_count": 0, "fatal_count": 0, "total_lines": 20}
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=stats,
            full_log_preview=log,
        )
        assert "medium" in result

    def test_severity_hint_low(self):
        """没有错误时，严重程度提示为 low"""
        log = "just warnings"
        stats = {"error_count": 0, "warning_count": 3, "fatal_count": 0, "total_lines": 20}
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=stats,
            full_log_preview=log,
        )
        assert "low" in result

    def test_unknown_source_not_shown(self):
        """平台为 Unknown 时不显示平台信息"""
        log = "some log"
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=self.DEFAULT_STATS,
            full_log_preview=log,
        )
        assert "日志来源平台" not in result

    def test_empty_error_lines_not_shown(self):
        """错误行为空时不显示错误行部分"""
        log = "some log"
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=self.DEFAULT_STATS,
            full_log_preview=log,
        )
        assert "已识别的关键错误行" not in result

    def test_stats_shown(self):
        """统计信息显示在提示词中"""
        log = "some log"
        stats = {"error_count": 3, "warning_count": 2, "fatal_count": 1, "total_lines": 100}
        result = build_analysis_prompt(
            source="Unknown",
            error_lines=[],
            stats=stats,
            full_log_preview=log,
        )
        assert "100" in result  # total_lines
        assert "3" in result    # error_count
        assert "2" in result    # warning_count
        assert "1" in result    # fatal_count

    def test_full_scenario(self):
        """完整场景：平台 + 错误行 + 统计 + 大量日志"""
        log = "a" * 7000  # 超过 6000 字符，触发截断提示
        stats = {"error_count": 3, "warning_count": 1, "fatal_count": 0, "total_lines": 200}
        result = build_analysis_prompt(
            source="Docker",
            error_lines=["ERROR: failed to solve", "ERROR: no such file"],
            stats=stats,
            full_log_preview=log,
        )
        assert "Docker" in result
        assert "ERROR: failed to solve" in result
        assert "medium" in result
        assert "200" in result
        assert "100%" in result  # 百分比约束


# ============================================
#  测试：RAG 增强提示词
# ============================================

class TestBuildRAGAugmentedPrompt:
    """测试 build_rag_augmented_prompt() 函数"""

    def test_injects_rag_context(self):
        """RAG 上下文正确注入到提示词末尾"""
        base = "请分析以下日志"
        rag = "- **[npm]** 依赖冲突\n  修复命令: `npm install --legacy-peer-deps` (命中 5 次)"
        result = build_rag_augmented_prompt(rag, base)
        assert "历史相似案例参考" in result
        assert "仅作参考" in result
        assert "依赖冲突" in result
        assert "legacy-peer-deps" in result

    def test_preserves_base_prompt(self):
        """原始提示词内容完整保留"""
        base = "【日志来源平台】npm\n\n【完整日志】\nERROR: build failed"
        rag = "- **[npm]** test\n  修复命令: `cmd` (命中 1 次)"
        result = build_rag_augmented_prompt(rag, base)
        assert "日志来源平台" in result
        assert "ERROR: build failed" in result

    def test_empty_context_returns_base(self):
        """空 RAG 上下文返回原始提示词"""
        base = "original prompt"
        assert build_rag_augmented_prompt("", base) == base

    def test_whitespace_only_context_returns_base(self):
        """纯空白 RAG 上下文返回原始提示词"""
        base = "original prompt"
        assert build_rag_augmented_prompt("   \n  ", base) == base

    def test_disclaimer_present(self):
        """包含防幻觉声明"""
        base = "base"
        rag = "- **[npm]** error\n  修复: `cmd` (命中 1 次)"
        result = build_rag_augmented_prompt(rag, base)
        assert "不要直接套用命令" in result

    def test_rag_section_at_end(self):
        """RAG 部分在提示词末尾"""
        base = "【完整日志】\nlog content"
        rag = "- **[npm]** test\n  修复: `cmd` (命中 1 次)"
        result = build_rag_augmented_prompt(rag, base)
        # RAG 部分应该在最后
        rag_pos = result.index("历史相似案例参考")
        log_pos = result.index("完整日志")
        assert rag_pos > log_pos
