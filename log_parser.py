# log_parser.py - 日志预处理：平台识别 + 错误行提取 + 智能截断
#
# 为什么需要这个文件？
# 1. 用户粘贴的日志可能有几千行，直接发给 AI 会浪费 token 且效果差
# 2. 先提取关键错误行，AI 分析更精准
# 3. 自动识别平台，可以在 prompt 中给出更有针对性的提示

import re
from typing import Optional
from models import ParsedLog

# ============================================
# 日志截断的最大字符数
# ============================================
# 为什么是 6000？
# DeepSeek 的上下文窗口很大，但日志太长会稀释关键信息
# 6000 字符大约 150-200 行，足够覆盖大多数错误场景
MAX_LOG_LENGTH = 6000

# 头部保留行数（构建开始的上下文）
HEAD_LINES = 50
# 尾部保留行数（错误通常在最后）
TAIL_LINES = 100


# ============================================
# 平台识别规则
# ============================================
# 每个平台有若干特征关键词，匹配到任意一个就判定为该平台
# 为什么用列表？因为同一平台可能有多种日志格式
PLATFORM_SIGNATURES: dict[str, list[str]] = {
    "GitHub Actions": [
        "##[error]",
        "##[group]",
        "##[warning]",
        "Run actions/",
        "Error: Process completed with exit code",
    ],
    "Jenkins": [
        "Finished: FAILURE",
        "Finished: SUCCESS",
        "[Pipeline] }",
        "ERROR: Build step",
        "Started by user",
    ],
    "Docker": [
        "Step ",
        " ---> Running in",
        "The command '/bin/sh -c",
        "returned a non-zero code",
        "ERROR: failed to solve",
    ],
    "npm": [
        "npm ERR!",
        "npm error",
        "npm WARN",
        "ERESOLVE could not resolve",
        "npm install",
    ],
    "pip": [
        "ERROR: Could not find a version",
        "ERROR: No matching distribution",
        "pip install",
        "ResolutionImpossible",
        "pip._internal",
    ],
    "cargo": [
        "error[E0",
        "could not compile",
        "cargo build",
        "aborting due to",
    ],
    "pytest": [
        "FAILURES",
        "PASSED",
        "ERRORS",
        "short test summary",
        "assert ",
        "AssertionError",
    ],
    "jest": [
        "FAIL ",
        "Tests:",
        "Test Suites:",
        "● ",
        "expect(received)",
    ],
    "Gradle": [
        "BUILD FAILED",
        "BUILD SUCCESSFUL",
        "> Task :",
        "Execution failed for task",
    ],
    "Maven": [
        "BUILD FAILURE",
        "BUILD SUCCESS",
        "[ERROR] Failed to execute goal",
        "[INFO] BUILD FAILURE",
    ],
}


# ============================================
# 错误行关键词
# ============================================
# 包含这些关键词的行大概率是关键错误信息
# 为什么用小写？因为比较时会统一转小写，忽略大小写差异
ERROR_KEYWORDS: list[str] = [
    "error",
    "failed",
    "fatal",
    "exception",
    "traceback",
    "panic",
    "denied",
    "timeout",
    "not found",
    "no such file",
    "permission denied",
    "exit code",
    "non-zero code",
    "assertion",
    "abort",
    "critical",
    "segmentation fault",
    "oom",           # out of memory
    "killed",
]


def detect_platform(log_text: str) -> str:
    """
    自动识别日志来源平台

    参数:
        log_text: 原始日志文本

    返回:
        平台名称字符串，如 "GitHub Actions"、"npm" 等
        如果无法识别，返回 "Unknown"

    为什么这样设计？
    - 用关键词匹配而不是正则，因为日志格式变化多端
    - 统计匹配次数，选匹配最多的平台，避免误判
    """
    log_lower = log_text.lower()

    # 统计每个平台的匹配关键词数
    scores: dict[str, int] = {}
    for platform, signatures in PLATFORM_SIGNATURES.items():
        score = sum(1 for sig in signatures if sig.lower() in log_lower)
        if score > 0:
            scores[platform] = score

    if not scores:
        return "Unknown"

    # 返回匹配次数最多的平台
    return max(scores, key=scores.get)  # type: ignore


def extract_error_lines(log_text: str, max_lines: int = 30) -> list[str]:
    """
    从日志中提取包含错误关键词的行

    参数:
        log_text: 原始日志文本
        max_lines: 最多提取多少行（避免太多噪音）

    返回:
        错误行列表，保持原始顺序，去重

    为什么限制行数？
    - 太多错误行反而会让 AI 抓不住重点
    - 30 行足够覆盖主要错误，又不会太多
    """
    lines = log_text.splitlines()
    error_lines: list[str] = []
    seen: set[str] = set()  # 去重用

    for line in lines:
        line_stripped = line.strip()
        # 跳过空行和太短的行（通常不是关键信息）
        if not line_stripped or len(line_stripped) < 5:
            continue

        line_lower = line_stripped.lower()
        # 检查是否包含错误关键词
        if any(kw in line_lower for kw in ERROR_KEYWORDS):
            # 去重：同一行内容不重复添加
            if line_stripped not in seen:
                seen.add(line_stripped)
                error_lines.append(line_stripped)

        if len(error_lines) >= max_lines:
            break

    return error_lines


def truncate_log(log_text: str, max_length: int = MAX_LOG_LENGTH) -> str:
    """
    智能截断过长的日志

    策略：保留头部 + 尾部，中间用省略标记
    为什么这样截断？
    - 日志头部通常有环境信息（OS、版本等），对诊断有帮助
    - 日志尾部通常有最终错误信息
    - 中间大多是正常执行过程，可以省略

    参数:
        log_text: 原始日志文本
        max_length: 最大字符数

    返回:
        截断后的日志（如果不需要截断，原样返回）
    """
    # 如果日志不长，直接返回
    if len(log_text) <= max_length:
        return log_text

    lines = log_text.splitlines()

    # 如果行数不多（每行很长），按字符截断
    if len(lines) <= HEAD_LINES + TAIL_LINES:
        half = max_length // 2
        return (
            log_text[:half]
            + "\n\n... [日志过长，中间部分已省略] ...\n\n"
            + log_text[-half:]
        )

    # 正常情况：保留头部和尾部的行
    head = lines[:HEAD_LINES]
    tail = lines[-TAIL_LINES:]

    return (
        "\n".join(head)
        + f"\n\n... [省略了 {len(lines) - HEAD_LINES - TAIL_LINES} 行] ...\n\n"
        + "\n".join(tail)
    )


def get_error_stats(log_text: str) -> dict[str, int]:
    """
    统计日志中的错误、警告、致命错误数量

    参数:
        log_text: 原始日志文本

    返回:
        包含以下字段的字典:
        - total_lines: 日志总行数
        - error_count: 包含 "error" 关键词的行数
        - warning_count: 包含 "warn" 关键词的行数
        - fatal_count: 包含 "fatal" 关键词的行数
    """
    lines = log_text.splitlines()
    total_lines = len(lines)
    error_count = 0
    warning_count = 0
    fatal_count = 0

    for line in lines:
        line_lower = line.lower()
        if "fatal" in line_lower:
            fatal_count += 1
        if "error" in line_lower:
            error_count += 1
        if "warn" in line_lower:
            warning_count += 1

    return {
        "total_lines": total_lines,
        "error_count": error_count,
        "warning_count": warning_count,
        "fatal_count": fatal_count,
    }


def parse_log(log_text: str) -> ParsedLog:
    """
    日志预处理的主入口函数

    把上面的三个功能串起来：
    1. 识别平台
    2. 提取错误行
    3. 智能截断

    参数:
        log_text: 用户粘贴的原始日志

    返回:
        包含以下字段的字典:
        - platform: 识别出的平台名称
        - error_lines: 提取的关键错误行
        - truncated_log: 截断后的日志（用于发给 AI）
        - is_truncated: 是否进行了截断
    """
    # 1. 识别平台
    platform = detect_platform(log_text)

    # 2. 提取错误行
    error_lines = extract_error_lines(log_text)

    # 3. 智能截断
    original_length = len(log_text)
    truncated_log = truncate_log(log_text)
    is_truncated = len(truncated_log) < original_length

    return {
        "platform": platform,
        "error_lines": error_lines,
        "truncated_log": truncated_log,
        "is_truncated": is_truncated,
    }
