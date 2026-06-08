# prompt.py - Prompt 工程模块
#
# 职责：
# 1. 定义 Few-shot 示例（让 AI 知道期望的输出长什么样）
# 2. 构建用户提示词（把日志预处理结果填进模板）
#
# 为什么单独放一个文件？
# - Prompt 是 AI 项目的核心资产，值得单独管理
# - 方便调试和迭代，不用去业务代码里翻

from typing import Optional


# ============================================================
#  Few-shot 示例
# ============================================================
# 什么是 Few-shot？给 AI 一个"参考答案"，让它知道期望的输出格式
# 为什么用 npm 依赖冲突？这是最常见的构建错误之一，覆盖度高
#
# 这个示例展示了分析报告的完整结构：
# - 错误摘要
# - 根因分析（带可能性百分比）
# - 修复步骤（带可执行命令）
# - 排查命令
# - 严重程度
# - 预防建议

FEW_SHOT_EXAMPLE = """
===== 参考示例 =====

【示例输入】
平台: npm
错误行:
npm ERR! code ERESOLVE
npm ERR! ERESOLVE could not resolve
npm ERR! Conflicting peer dependency: react@17.0.2

日志:
npm ERR! code ERESOLVE
npm ERR! ERESOLVE could not resolve
npm ERR! While resolving: react-scripts@5.0.1
npm ERR! Found: react@18.2.0
npm ERR! node_modules/react
npm ERR!   react@"^18.2.0" from the root project
npm ERR!
npm ERR! Conflicting peer dependency: react@17.0.2
npm ERR! node_modules/react
npm ERR!   peer react@"^17.0.0" from @testing-library/react@11.2.7
npm ERR!
npm ERR! Fix the upstream dependency conflict, or retry
npm ERR! this command with --force or --legacy-peer-deps

【示例输出】
{
    "error_summary": "npm 依赖解析冲突：react 版本不兼容",
    "error_detail": "npm ERR! ERESOLVE could not resolve\\nnpm ERR! Conflicting peer dependency: react@17.0.2",
    "root_causes": [
        {
            "cause": "react-scripts@5.0.1 要求 react@^18.2.0，但 @testing-library/react@11.2.7 要求 react@^17.0.0，两个依赖对 react 的版本要求互相矛盾",
            "probability": 90
        },
        {
            "cause": "package-lock.json 中锁定了旧版本的依赖树，与当前 package.json 不一致",
            "probability": 7
        },
        {
            "cause": "npm 版本过低，依赖解析算法不完善",
            "probability": 3
        }
    ],
    "fix_suggestions": [
        {
            "title": "使用 --legacy-peer-deps 跳过 peer dependency 检查",
            "description": "这是最快的解决方式，跳过严格的版本兼容性检查",
            "command": "npm install --legacy-peer-deps"
        },
        {
            "title": "升级 @testing-library/react 到兼容 react 18 的版本",
            "description": "从根本上解决版本冲突，推荐这种方式",
            "command": "npm install @testing-library/react@latest --save-dev"
        },
        {
            "title": "降级 react 到 17.x 以匹配 testing-library",
            "description": "如果项目允许使用旧版 react，这是一种保守的解决方案",
            "command": "npm install react@17.0.2 react-dom@17.0.2"
        }
    ],
    "debug_commands": [
        "npm ls react",
        "npm why react",
        "npm outdated"
    ],
    "severity": "medium",
    "prevention": "建议在 package.json 中使用更宽松的版本范围（如 ^ 而非 ~），并定期运行 npm outdated 检查依赖更新"
}
"""


# ============================================================
#  系统提示词
# ============================================================
# 系统提示词告诉 AI：
# 1. 它的角色是什么（资深 DevOps 工程师）
# 2. 它的任务是什么（分析构建日志）
# 3. 输出必须遵循什么格式（JSON）
# 4. 有哪些硬性约束（百分比之和=100%、命令放代码块等）

SYSTEM_PROMPT = """你是一名资深的 DevOps 工程师和 CI/CD 专家，拥有 10 年以上的构建系统调试经验。

用户会给你一段构建失败的日志，你需要输出一份结构化的分析报告。

## 输出格式（严格 JSON，6 个章节，标题不可改变）

```json
{
    "error_summary": "一句话概括错误（20字以内）",
    "error_detail": "关键错误信息原文（保留英文原文，方便对照）",
    "root_causes": [
        {"cause": "原因描述", "probability": 70},
        {"cause": "原因描述", "probability": 20},
        {"cause": "原因描述", "probability": 10}
    ],
    "fix_suggestions": [
        {"title": "修复方案标题", "description": "详细说明", "command": "可执行的修复命令"},
        {"title": "修复方案标题", "description": "详细说明", "command": "可执行的修复命令"},
        {"title": "修复方案标题", "description": "详细说明", "command": "可执行的修复命令"}
    ],
    "debug_commands": ["排查命令1", "排查命令2", "排查命令3"],
    "severity": "critical | high | medium | low",
    "prevention": "预防建议（一句话）"
}
```

## 硬性规则

1. **只返回 JSON**，不要有任何其他文字、解释、markdown 标记
2. **root_causes 的 probability 之和必须等于 100%**，这是最重要的规则
3. **所有命令必须放在代码块中**，且可以直接复制执行
4. **error_detail 保留英文原文**，方便用户对照原始日志
5. **其他字段用中文**，且新手能看懂
6. **如果日志信息不足**，诚实说明"日志信息不足，无法确定根因"，不要编造
7. **severity 判断标准**：
   - critical: 构建完全阻断，无法产出任何产物
   - high: 核心功能失败，但有 workaround
   - medium: 非核心功能失败（如测试、lint）
   - low: 警告或非致命问题
8. **fix_suggestions 必须恰好 3 条**，按可能性从高到低排列
9. **debug_commands 至少 2 条**，帮助用户进一步排查
""" + FEW_SHOT_EXAMPLE


# ============================================================
#  用户提示词构建函数
# ============================================================

def build_analysis_prompt(
    source: str,
    error_lines: list[str],
    stats: dict,
    full_log_preview: str,
) -> str:
    """
    构建发送给 AI 的用户提示词

    把日志预处理的结果（平台、错误行、统计信息、日志原文）
    按照模板拼接成完整的提示词。

    参数:
        source: 识别出的日志来源平台，如 "npm"、"GitHub Actions"
        error_lines: 预提取的关键错误行列表
        stats: 日志统计信息，包含:
            - error_count: 错误关键词出现次数
            - warning_count: 警告关键词出现次数
            - fatal_count: 致命错误次数
            - total_lines: 日志总行数
        full_log_preview: 截断后的日志原文

    返回:
        拼接好的用户提示词字符串
    """
    parts: list[str] = []

    # ---- 平台信息 ----
    # 让 AI 知道日志来源，给出更有针对性的分析
    if source and source != "Unknown":
        parts.append(f"【日志来源平台】{source}")

    # ---- 日志统计 ----
    # 让 AI 快速了解日志的整体情况
    fatal_count: int = stats.get("fatal_count", 0)
    error_count: int = stats.get("error_count", 0)
    warning_count: int = stats.get("warning_count", 0)
    total_lines: int = stats.get("total_lines", 0)

    stats_text: str = (
        f"总行数: {total_lines} | "
        f"致命错误: {fatal_count} | "
        f"错误: {error_count} | "
        f"警告: {warning_count}"
    )
    parts.append(f"【日志统计】{stats_text}")

    # ---- 动态严重程度提示 ----
    # 根据错误数量给 AI 一个初步判断，帮助它更准确地评估 severity
    if fatal_count > 0:
        severity_hint = "critical（存在致命错误，构建完全阻断）"
    elif error_count >= 5:
        severity_hint = "high（错误数量较多，核心功能可能受影响）"
    elif error_count >= 1:
        severity_hint = "medium（存在错误，需要修复）"
    else:
        severity_hint = "low（仅警告，可能不影响构建）"
    parts.append(f"【严重程度提示】{severity_hint}")

    # ---- 预提取的错误行 ----
    # 帮 AI 快速定位关键错误，不用从头读完整日志
    if error_lines:
        error_text: str = "\n".join(error_lines[:10])  # 最多展示 10 行
        parts.append(f"【已识别的关键错误行】\n{error_text}")

    # ---- 截断提示 ----
    if len(full_log_preview) < 6000:
        # 日志较短，不做截断提示
        pass
    else:
        parts.append(
            "【注意】原始日志较长，以下为截断后的版本（保留了头部和尾部的关键信息），"
            "请基于可见内容进行分析。"
        )

    # ---- 日志正文 ----
    parts.append(f"【完整日志】\n{full_log_preview}")

    # ---- 最终指令 ----
    parts.append(
        "请按照系统提示中的 JSON 格式返回分析结果。\n"
        "特别注意：root_causes 的 probability 之和必须等于 100%。"
    )

    return "\n\n".join(parts)
