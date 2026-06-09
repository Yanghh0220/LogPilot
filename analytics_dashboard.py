# analytics_dashboard.py - 分析洞察仪表板
#
# 职责：
# 1. 生成 Markdown 格式的错误分析周报
# 2. 为 Streamlit 提供图表数据
# 3. 聚合簇洞察为可消费的格式

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from cluster_engine import ClusterEngine


def generate_weekly_report(cluster_engine: Optional[ClusterEngine] = None) -> str:
    """
    生成 Markdown 格式的错误分析周报

    包含：
    - 本周新增簇数、总分析次数
    - Top-5 高频错误簇（出现次数、平台分布、平均严重度）
    - 平台故障分布（柱状图数据）
    - 修复建议聚合（Top-3 最常用修复命令）
    - 趋势数据（7 天内每天的分析次数）

    参数:
        cluster_engine: 聚类引擎实例，None 则使用默认实例

    返回:
        Markdown 格式的周报字符串
    """
    if cluster_engine is None:
        from cluster_engine import get_cluster_engine
        cluster_engine = get_cluster_engine()

    conn = cluster_engine._get_conn()
    try:
        now = datetime.utcnow()
        week_ago = (now - timedelta(days=7)).isoformat()
        two_weeks_ago = (now - timedelta(days=14)).isoformat()

        lines = [
            "# 📊 LogGazer 错误分析周报",
            "",
            f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M UTC')}",
            f"**统计周期**: { (now - timedelta(days=7)).strftime('%Y-%m-%d') } ~ {now.strftime('%Y-%m-%d')}",
            "",
            "---",
            "",
        ]

        # ---- 总览指标 ----
        total_analyses = conn.execute(
            "SELECT COUNT(*) FROM analysis_log WHERE created_at >= ?",
            (week_ago,),
        ).fetchone()[0]

        prev_analyses = conn.execute(
            "SELECT COUNT(*) FROM analysis_log "
            "WHERE created_at >= ? AND created_at < ?",
            (two_weeks_ago, week_ago),
        ).fetchone()[0]

        new_clusters = conn.execute(
            "SELECT COUNT(*) FROM error_cluster WHERE first_seen >= ?",
            (week_ago,),
        ).fetchone()[0]

        active_clusters = conn.execute(
            "SELECT COUNT(*) FROM error_cluster WHERE is_active = 1",
        ).fetchone()[0]

        # 分析次数变化趋势
        trend_icon = "📈" if total_analyses > prev_analyses else "📉"
        trend_pct = (
            ((total_analyses - prev_analyses) / prev_analyses * 100)
            if prev_analyses > 0
            else 0
        )

        lines.extend([
            "## 📋 总览",
            "",
            f"| 指标 | 数值 | 变化 |",
            f"|------|------|------|",
            f"| 本周分析次数 | {total_analyses} "
            f"| {trend_icon} {trend_pct:+.1f}% |",
            f"| 新增错误簇 | {new_clusters} | - |",
            f"| 活跃错误簇 | {active_clusters} | - |",
            "",
        ])

        # ---- Top-5 高频错误簇 ----
        trending = cluster_engine.get_trending_clusters(days=7, top_n=5)

        lines.extend([
            "## 🔥 Top-5 高频错误簇",
            "",
        ])

        for i, cluster in enumerate(trending, 1):
            dist = cluster.get("platform_distribution", {})
            platforms = ", ".join(
                f"{k}({v})" for k, v in sorted(
                    dist.items(), key=lambda x: -x[1]
                )
            )
            severity = cluster.get("avg_severity_score", 0) or 0
            severity_icon = (
                "🔴" if severity >= 3.5
                else "🟠" if severity >= 2.5
                else "🟡" if severity >= 1.5
                else "🟢"
            )

            lines.extend([
                f"### {i}. 簇 #{cluster['cluster_id']} "
                f"{severity_icon} (出现 {cluster.get('recent_count', 0)} 次)",
                "",
                f"- **平台**: {platforms or 'N/A'}",
                f"- **总出现次数**: {cluster.get('occurrence_count', 0)}",
                f"- **首次出现**: {cluster.get('first_seen', 'N/A')}",
                f"- **最近出现**: {cluster.get('last_seen', 'N/A')}",
            ])

            # 代表性样本
            samples = cluster.get("representative_samples", [])
            if samples:
                lines.append(f"- **代表性错误**:")
                for s in samples[:2]:
                    lines.append(
                        f"  - `{s.get('fingerprint', 'N/A')[:80]}...`"
                    )

            # Top 修复建议
            fixes = cluster.get("top_fix_suggestions", [])
            if fixes:
                lines.append(f"- **常用修复命令**:")
                for fix in fixes[:3]:
                    cmd = fix.get("command", "N/A")
                    count = fix.get("count", 0)
                    lines.append(f"  - `{cmd}` (使用 {count} 次)")

            lines.append("")

        # ---- 每日趋势 ----
        lines.extend([
            "## 📈 每日分析趋势",
            "",
            "```",
        ])

        for day_offset in range(6, -1, -1):
            day = now - timedelta(days=day_offset)
            day_start = day.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            day_end = day.replace(
                hour=23, minute=59, second=59, microsecond=999999
            ).isoformat()

            count = conn.execute(
                "SELECT COUNT(*) FROM analysis_log "
                "WHERE created_at >= ? AND created_at <= ?",
                (day_start, day_end),
            ).fetchone()[0]

            bar = "█" * min(count, 30)
            day_label = day.strftime("%m-%d")
            lines.append(f"  {day_label} | {bar} {count}")

        lines.extend(["```", ""])

        # ---- 平台分布 ----
        lines.extend([
            "## 🖥️ 平台故障分布",
            "",
        ])

        platform_totals: dict[str, int] = {}
        rows = conn.execute(
            "SELECT platform_distribution FROM error_cluster WHERE is_active = 1"
        ).fetchall()
        for row in rows:
            dist = json.loads(row["platform_distribution"] or "{}")
            for p, c in dist.items():
                platform_totals[p] = platform_totals.get(p, 0) + c

        for platform, count in sorted(
            platform_totals.items(), key=lambda x: -x[1]
        ):
            bar = "█" * min(count, 30)
            lines.append(f"- **{platform}**: {count} 次 {bar}")

        lines.extend(["", "---", ""])
        lines.append("*由 LogGazer 自动生成 · Powered by Error Fingerprinting Engine*")

        return "\n".join(lines)

    finally:
        conn.close()


def get_trend_chart_data(
    cluster_engine: Optional[ClusterEngine] = None, days: int = 7
) -> dict[str, Any]:
    """
    获取趋势图表数据（供 Streamlit st.line_chart 使用）

    返回:
        {
            "dates": ["06-03", "06-04", ...],
            "counts": [5, 12, ...],
        }
    """
    if cluster_engine is None:
        from cluster_engine import get_cluster_engine
        cluster_engine = get_cluster_engine()

    conn = cluster_engine._get_conn()
    try:
        now = datetime.utcnow()
        dates = []
        counts = []

        for day_offset in range(days - 1, -1, -1):
            day = now - timedelta(days=day_offset)
            day_start = day.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            day_end = day.replace(
                hour=23, minute=59, second=59, microsecond=999999
            ).isoformat()

            count = conn.execute(
                "SELECT COUNT(*) FROM analysis_log "
                "WHERE created_at >= ? AND created_at <= ?",
                (day_start, day_end),
            ).fetchone()[0]

            dates.append(day.strftime("%m-%d"))
            counts.append(count)

        return {"dates": dates, "counts": counts}
    finally:
        conn.close()


def get_platform_distribution(
    cluster_engine: Optional[ClusterEngine] = None,
) -> dict[str, int]:
    """
    获取平台故障分布（供 Streamlit st.bar_chart 使用）

    返回:
        {"npm": 45, "Docker": 23, ...}
    """
    if cluster_engine is None:
        from cluster_engine import get_cluster_engine
        cluster_engine = get_cluster_engine()

    conn = cluster_engine._get_conn()
    try:
        rows = conn.execute(
            "SELECT platform_distribution FROM error_cluster WHERE is_active = 1"
        ).fetchall()

        totals: dict[str, int] = {}
        for row in rows:
            dist = json.loads(row["platform_distribution"] or "{}")
            for p, c in dist.items():
                totals[p] = totals.get(p, 0) + c

        return totals
    finally:
        conn.close()
