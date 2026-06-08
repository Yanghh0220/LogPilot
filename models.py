# types.py - 类型定义，让代码有类型提示
#
# 为什么需要这个文件？
# 1. Python 的 dict 没有"结构"，IDE 不知道有哪些 key
# 2. 用 TypedDict 定义后，IDE 能自动补全、检查拼写错误
# 3. 方便团队协作，一看类型就知道数据长什么样
#
# TypedDict vs dataclass vs Pydantic？
# - TypedDict：最轻量，只是类型标注，运行时还是普通 dict
# - dataclass：会创建真正的类实例，需要额外转换
# - Pydantic：功能最强，但对这个小项目来说太重了
# 我们选 TypedDict，够用且不引入新依赖

from typing import TypedDict


class FixSuggestion(TypedDict):
    """一条修复建议"""
    title: str        # 建议标题，如 "使用 --legacy-peer-deps 安装"
    description: str  # 详细说明
    command: str      # 可执行的修复命令


class AnalysisResult(TypedDict):
    """
    AI 分析日志后的结构化返回结果

    这就是 analyze_log() 返回值的"形状"
    """
    error_summary: str                 # 一句话概括错误
    error_detail: str                  # 关键错误信息原文（英文）
    reason: str                        # 用中文解释报错原因
    fix_suggestions: list[FixSuggestion]  # Top 3 修复建议
    debug_commands: list[str]          # 排查命令列表


class ParsedLog(TypedDict):
    """
    日志预处理的结果

    这就是 parse_log() 返回值的"形状"
    """
    platform: str          # 识别出的平台，如 "npm"、"GitHub Actions"
    error_lines: list[str] # 提取的关键错误行
    truncated_log: str     # 截断后的日志文本
    is_truncated: bool     # 是否进行了截断
