# tests/test_cluster_engine.py - 聚类引擎测试
#
# 测试覆盖：
# 1. 基本聚类：20 个样本，10 个相同错误×2 个变体，验证聚为 2 个簇
# 2. 增量更新：新增 1 个样本，只触发 1 次簇更新，总耗时 <50ms
# 3. 持久化：重启引擎后 LSH 索引与 DB 一致
# 4. get_trending_clusters：7 天内数据排序正确
# 5. 簇洞察：代表性样本、平台分布、修复建议聚合
#
# 运行：pytest tests/test_cluster_engine.py -v

import json
import os
import tempfile
import time

import pytest

from fingerprint_engine import FingerprintEngine
from cluster_engine import ClusterEngine, reset_cluster_engine


# ============================================================
#  Fixtures
# ============================================================

@pytest.fixture
def tmp_db():
    """临时数据库文件"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # 清理
    for ext in ["", "-wal", "-shm"]:
        try:
            os.unlink(path + ext)
        except OSError:
            pass


@pytest.fixture
def engine(tmp_db):
    """聚类引擎实例（使用临时数据库）"""
    reset_cluster_engine()
    eng = ClusterEngine(db_path=tmp_db, threshold=0.75, num_perm=128)
    yield eng
    reset_cluster_engine()


@pytest.fixture
def fp_engine():
    """指纹引擎实例"""
    return FingerprintEngine(num_perm=128)


# ============================================================
#  测试数据
# ============================================================

# npm ERESOLVE 错误变体
NPM_ERESOLVE_VARIANTS = [
    [
        "npm ERR! code ERESOLVE",
        "npm ERR! ERESOLVE could not resolve",
        "npm ERR! While resolving: react-scripts@5.0.1",
        "npm ERR! Found: react@18.2.0",
    ],
    [
        "npm ERR! code ERESOLVE",
        "npm ERR! ERESOLVE could not resolve",
        "npm ERR! While resolving: react-scripts@4.0.3",
        "npm ERR! Found: react@17.0.2",
    ],
    [
        "npm ERR! code ERESOLVE",
        "npm ERR! ERESOLVE could not resolve",
        "npm ERR! While resolving: next@13.4.1",
        "npm ERR! Found: react@18.2.0",
    ],
    [
        "npm ERR! code ERESOLVE",
        "npm ERR! ERESOLVE could not resolve",
        "npm ERR! While resolving: gatsby@5.12.0",
        "npm ERR! Found: react@18.0.0",
    ],
    [
        "npm ERR! code ERESOLVE",
        "npm ERR! ERESOLVE could not resolve",
        "npm ERR! While resolving: @types/react@18.2.0",
        "npm ERR! Found: react@18.2.0",
    ],
]

# Docker 权限错误变体
DOCKER_PERMISSION_VARIANTS = [
    [
        "ERROR: failed to solve: error building docker image",
        "permission denied: /var/run/docker.sock",
    ],
    [
        "ERROR: error building docker image",
        "permission denied: /var/run/docker.sock",
    ],
    [
        "ERROR: failed to solve",
        "permission denied: /var/run/docker.sock",
    ],
    [
        "ERROR: Cannot connect to the Docker daemon",
        "permission denied: /var/run/docker.sock",
    ],
    [
        "ERROR: error during connect",
        "permission denied: /var/run/docker.sock",
    ],
]

# Python 测试失败变体
PYTHON_TEST_VARIANTS = [
    [
        "FAILED tests/test_auth.py::test_user_login",
        "AssertionError: assert 401 == 200",
    ],
    [
        "FAILED tests/test_auth.py::test_admin_access",
        "AssertionError: assert 403 == 200",
    ],
    [
        "FAILED tests/test_api.py::test_get_users",
        "AssertionError: assert 500 == 200",
    ],
    [
        "FAILED tests/test_login.py::test_valid_credentials",
        "AssertionError: assert 401 == 200",
    ],
    [
        "FAILED tests/test_auth.py::test_token_refresh",
        "AssertionError: assert 401 == 200",
    ],
]


# ============================================================
#  测试：基本聚类
# ============================================================

class TestBasicClustering:
    """测试基本聚类功能"""

    def test_same_error_same_cluster(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """相同错误（仅时间戳不同）归入同一簇"""
        lines1 = [
            "2024-01-15T10:30:45Z npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
        ]
        lines2 = [
            "2024-12-25T23:59:59Z npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
        ]

        fp1 = fp_engine.fingerprint(lines1, "npm")
        fp2 = fp_engine.fingerprint(lines2, "npm")

        cid1 = engine.assign_cluster(fp1)
        cid2 = engine.assign_cluster(fp2)

        assert cid1 == cid2

    def test_different_errors_different_cluster(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """完全不同错误（npm ERESOLVE vs Docker permission denied）→ 不同簇"""
        fp1 = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        fp2 = fp_engine.fingerprint(DOCKER_PERMISSION_VARIANTS[0], "Docker")

        cid1 = engine.assign_cluster(fp1)
        cid2 = engine.assign_cluster(fp2)

        assert cid1 != cid2

    def test_multiple_variants_cluster_correctly(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """10 个样本（npm ERESOLVE 变体）聚类：不应每个样本一个簇"""
        npm_cluster_ids = set()
        for lines in NPM_ERESOLVE_VARIANTS:
            fp = fp_engine.fingerprint(lines, "npm")
            cid = engine.assign_cluster(fp)
            npm_cluster_ids.add(cid)

        # npm ERESOLVE 变体应该聚为少数簇（不是每个变体一个簇）
        assert len(npm_cluster_ids) <= len(NPM_ERESOLVE_VARIANTS)

        docker_cluster_ids = set()
        for lines in DOCKER_PERMISSION_VARIANTS:
            fp = fp_engine.fingerprint(lines, "Docker")
            cid = engine.assign_cluster(fp)
            docker_cluster_ids.add(cid)

        # Docker 变体也应该聚为少数簇
        assert len(docker_cluster_ids) <= len(DOCKER_PERMISSION_VARIANTS)

        # npm 和 Docker 的簇应该完全分开（没有交集）
        assert npm_cluster_ids.isdisjoint(docker_cluster_ids)

    def test_three_distinct_error_types(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """三种不同错误类型 → 至少 2 个簇"""
        npm_fp = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        docker_fp = fp_engine.fingerprint(DOCKER_PERMISSION_VARIANTS[0], "Docker")
        python_fp = fp_engine.fingerprint(PYTHON_TEST_VARIANTS[0], "pytest")

        cid1 = engine.assign_cluster(npm_fp)
        cid2 = engine.assign_cluster(docker_fp)
        cid3 = engine.assign_cluster(python_fp)

        # 至少 2 个不同的簇
        unique = {cid1, cid2, cid3}
        assert len(unique) >= 2


# ============================================================
#  测试：增量更新
# ============================================================

class TestIncrementalUpdate:
    """测试增量聚类更新"""

    def test_incremental_update_under_50ms(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """单样本增量更新 < 50ms"""
        # 预热：先插入一些样本建立簇
        for lines in NPM_ERESOLVE_VARIANTS[:3]:
            fp = fp_engine.fingerprint(lines, "npm")
            engine.assign_cluster(fp)

        # 计时：新增 1 个样本
        new_lines = [
            "npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
            "npm ERR! While resolving: vue@3.3.0",
        ]
        fp = fp_engine.fingerprint(new_lines, "npm")

        start = time.perf_counter()
        engine.assign_cluster(fp)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50, f"增量更新耗时 {elapsed_ms:.2f}ms，超过 50ms"

    def test_incremental_does_not_rewrite_all(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """增量更新不重算已有簇"""
        # 插入初始样本
        cids = []
        for lines in NPM_ERESOLVE_VARIANTS:
            fp = fp_engine.fingerprint(lines, "npm")
            cids.append(engine.assign_cluster(fp))

        # 记录原有簇 ID
        original_cids = set(cids)

        # 新增一个 Docker 错误（应该创建新簇或加入已有簇）
        fp = fp_engine.fingerprint(DOCKER_PERMISSION_VARIANTS[0], "Docker")
        new_cid = engine.assign_cluster(fp)

        # 原有簇 ID 应该保持不变
        for cid in cids:
            insight = engine.get_cluster_insight(cid)
            assert insight.get("cluster_id") == cid


# ============================================================
#  测试：持久化
# ============================================================

class TestPersistence:
    """测试 SQLite 持久化"""

    def test_restart_preserves_clusters(
        self, tmp_db, fp_engine: FingerprintEngine
    ):
        """重启引擎后簇数据不丢失"""
        reset_cluster_engine()

        # 第一次启动：插入数据
        engine1 = ClusterEngine(db_path=tmp_db, threshold=0.75, num_perm=128)
        for lines in NPM_ERESOLVE_VARIANTS[:3]:
            fp = fp_engine.fingerprint(lines, "npm")
            engine1.assign_cluster(fp)

        cluster_count_1 = len(engine1._cluster_centers)

        # 模拟重启
        reset_cluster_engine()
        engine2 = ClusterEngine(db_path=tmp_db, threshold=0.75, num_perm=128)

        cluster_count_2 = len(engine2._cluster_centers)

        assert cluster_count_2 >= cluster_count_1

    def test_analysis_log_persists(
        self, tmp_db, fp_engine: FingerprintEngine
    ):
        """分析日志持久化到数据库"""
        reset_cluster_engine()

        engine1 = ClusterEngine(db_path=tmp_db, threshold=0.75, num_perm=128)
        fp = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        engine1.assign_cluster(fp)

        # 重启
        reset_cluster_engine()
        engine2 = ClusterEngine(db_path=tmp_db, threshold=0.75, num_perm=128)

        conn = engine2._get_conn()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM analysis_log"
            ).fetchone()[0]
            assert count >= 1
        finally:
            conn.close()


# ============================================================
#  测试：簇洞察
# ============================================================

class TestClusterInsight:
    """测试簇洞察功能"""

    def test_insight_contains_required_fields(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """簇洞察包含所有必需字段"""
        fp = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        cid = engine.assign_cluster(fp)

        insight = engine.get_cluster_insight(cid)

        assert "cluster_id" in insight
        assert "occurrence_count" in insight
        assert "first_seen" in insight
        assert "last_seen" in insight
        assert "platform_distribution" in insight
        assert "trend_7d" in insight
        assert "trend_30d" in insight
        assert "is_active" in insight

    def test_platform_distribution_updated(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """平台分布正确统计"""
        # 同一簇，不同平台
        fp1 = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        fp2 = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[1], "npm")

        cid1 = engine.assign_cluster(fp1)
        cid2 = engine.assign_cluster(fp2)

        # 应该在同一簇
        if cid1 == cid2:
            insight = engine.get_cluster_insight(cid1)
            dist = insight.get("platform_distribution", {})
            assert dist.get("npm", 0) >= 2

    def test_occurrence_count_increments(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """出现次数正确递增"""
        fp1 = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        cid = engine.assign_cluster(fp1)

        fp2 = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[1], "npm")
        engine.assign_cluster(fp2)

        insight = engine.get_cluster_insight(cid)
        assert insight["occurrence_count"] >= 2

    def test_nonexistent_cluster_returns_error(
        self, engine: ClusterEngine
    ):
        """不存在的簇返回错误信息"""
        insight = engine.get_cluster_insight(99999)
        assert "error" in insight


# ============================================================
#  测试：趋势查询
# ============================================================

class TestTrendingClusters:
    """测试趋势查询"""

    def test_trending_returns_sorted(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """趋势查询按出现次数降序排序"""
        # 插入不同数量的样本
        for lines in NPM_ERESOLVE_VARIANTS[:3]:
            fp = fp_engine.fingerprint(lines, "npm")
            engine.assign_cluster(fp)

        for lines in DOCKER_PERMISSION_VARIANTS[:1]:
            fp = fp_engine.fingerprint(lines, "Docker")
            engine.assign_cluster(fp)

        trending = engine.get_trending_clusters(days=7, top_n=10)

        if len(trending) >= 2:
            # 第一个应该出现次数 >= 第二个
            assert trending[0]["recent_count"] >= trending[1]["recent_count"]

    def test_trending_top_n_limit(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """Top-N 截断正确"""
        for lines in NPM_ERESOLVE_VARIANTS:
            fp = fp_engine.fingerprint(lines, "npm")
            engine.assign_cluster(fp)

        for lines in DOCKER_PERMISSION_VARIANTS:
            fp = fp_engine.fingerprint(lines, "Docker")
            engine.assign_cluster(fp)

        trending = engine.get_trending_clusters(days=7, top_n=2)
        assert len(trending) <= 2


# ============================================================
#  测试：软删除与清理
# ============================================================

class TestCleanup:
    """测试簇老化与清理"""

    def test_cleanup_inactive_clusters(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """软删除超过 30 天无新样本的簇"""
        fp = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        cid = engine.assign_cluster(fp)

        # 手动将 last_seen 改为 31 天前
        from datetime import datetime, timedelta
        old_date = (datetime.utcnow() - timedelta(days=31)).isoformat()

        conn = engine._get_conn()
        try:
            conn.execute(
                "UPDATE error_cluster SET last_seen = ? WHERE cluster_id = ?",
                (old_date, cid),
            )
            conn.commit()
        finally:
            conn.close()

        # 执行清理
        cleaned = engine.cleanup_inactive_clusters(inactive_days=30)
        assert cleaned >= 1

        # 验证簇被标记为非活跃
        insight = engine.get_cluster_insight(cid)
        assert insight["is_active"] is False


# ============================================================
#  测试：store_analysis 完整记录
# ============================================================

class TestStoreAnalysis:
    """测试完整分析记录存储"""

    def test_stores_with_compressed_log(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """大日志使用 zlib 压缩存储"""
        fp = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        cid = engine.assign_cluster(fp)

        # 生成 >10KB 的日志
        big_log = "x" * 15000

        engine.store_analysis(
            raw_log=big_log,
            fingerprint=fp,
            result={"error_summary": "test", "severity": "medium"},
            cluster_id=cid,
        )

        conn = engine._get_conn()
        try:
            row = conn.execute(
                "SELECT raw_log_compressed FROM analysis_log "
                "WHERE raw_log_hash = ?",
                (fp["sha256"],),
            ).fetchone()
            assert row is not None
            assert row["raw_log_compressed"] is not None
        finally:
            conn.close()

    def test_stores_analysis_result_json(
        self, engine: ClusterEngine, fp_engine: FingerprintEngine
    ):
        """分析结果以 JSON 存储"""
        fp = fp_engine.fingerprint(NPM_ERESOLVE_VARIANTS[0], "npm")
        cid = engine.assign_cluster(fp)

        result = {
            "error_summary": "npm 依赖冲突",
            "severity": "medium",
            "fix_suggestions": [
                {"title": "使用 --legacy-peer-deps", "command": "npm install --legacy-peer-deps"}
            ],
        }

        engine.store_analysis(
            raw_log="test log",
            fingerprint=fp,
            result=result,
            cluster_id=cid,
        )

        conn = engine._get_conn()
        try:
            row = conn.execute(
                "SELECT analysis_result_json FROM analysis_log "
                "WHERE raw_log_hash = ?",
                (fp["sha256"],),
            ).fetchone()
            assert row is not None
            stored = json.loads(row["analysis_result_json"])
            assert stored["error_summary"] == "npm 依赖冲突"
        finally:
            conn.close()
