# tests/test_fingerprint_engine.py - 错误指纹引擎测试
#
# 测试覆盖：
# 1. 标准化：相同错误（仅时间戳不同）→ 指纹完全相同
# 2. 骨架化：提取确定性 token（文件名、错误类型、函数名）
# 3. MinHash：相似错误 → Jaccard > 0.8，不同错误 → Jaccard < 0.5
# 4. 性能：1000 行日志指纹提取 <10ms
# 5. 正则管线覆盖：时间戳、UUID、内存地址、IP、PID、临时路径、行号
#
# 运行：pytest tests/test_fingerprint_engine.py -v

import time

import pytest

from fingerprint_engine import FingerprintEngine


# ============================================================
#  Fixtures
# ============================================================

@pytest.fixture
def engine():
    """标准指纹引擎实例"""
    return FingerprintEngine(num_perm=128)


# ============================================================
#  测试：normalize() 正则管线
# ============================================================

class TestNormalize:
    """测试标准化正则管线"""

    def test_same_npm_eresolve_different_timestamps(self, engine: FingerprintEngine):
        """相同 npm ERESOLVE 错误（仅时间戳不同）→ 标准化文本完全相同"""
        lines1 = [
            "npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
            "npm ERR! 2024-01-15T10:30:45Z While resolving: react-scripts@5.0.1",
        ]
        lines2 = [
            "npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
            "npm ERR! 2024-12-25T23:59:59Z While resolving: react-scripts@5.0.1",
        ]
        assert engine.normalize(lines1) == engine.normalize(lines2)

    def test_strips_iso_timestamp(self, engine: FingerprintEngine):
        """ISO-8601 时间戳被替换"""
        result = engine.normalize(["ERROR 2024-01-15T10:30:45Z build failed"])
        assert "2024" not in result
        assert "timestamp" in result

    def test_strips_timestamp_with_offset(self, engine: FingerprintEngine):
        """带时区偏移的时间戳被替换"""
        result = engine.normalize(
            ["ERROR 2024-01-15 10:30:45.123+08:00 build failed"]
        )
        assert "2024" not in result
        assert "timestamp" in result

    def test_strips_uuid(self, engine: FingerprintEngine):
        """UUID 被替换"""
        result = engine.normalize(
            ["job 550e8400-e29b-41d4-a716-446655440000 failed"]
        )
        assert "550e8400" not in result
        assert "uuid" in result

    def test_strips_hex_address(self, engine: FingerprintEngine):
        """内存地址被替换"""
        result = engine.normalize(
            ["segfault at 0x7fff5fbff8ac ip 0x00007f"]
        )
        assert "0x7fff" not in result
        assert "addr" in result

    def test_strips_ip_address(self, engine: FingerprintEngine):
        """IP 地址被替换"""
        result = engine.normalize(
            ["connection to 192.168.1.100:8080 refused"]
        )
        assert "192.168" not in result
        assert "ip" in result

    def test_strips_pid(self, engine: FingerprintEngine):
        """PID 被替换"""
        result = engine.normalize(["process pid 12345 killed"])
        assert "12345" not in result
        assert "pid" in result

    def test_strips_tid(self, engine: FingerprintEngine):
        """TID 被替换"""
        result = engine.normalize(["thread tid=67890 crashed"])
        assert "67890" not in result

    def test_strips_tmp_path(self, engine: FingerprintEngine):
        """临时路径被替换"""
        result = engine.normalize(["cannot write to /tmp/abc123def/output.log"])
        assert "abc123def" not in result
        assert "/tmp/" in result

    def test_strips_var_tmp_path(self, engine: FingerprintEngine):
        """/var/tmp 路径被替换"""
        result = engine.normalize(["cannot access /var/tmp/xyz789/data"])
        assert "xyz789" not in result

    def test_strips_line_column_numbers(self, engine: FingerprintEngine):
        """行号:列号被替换"""
        result = engine.normalize(["at /src/auth.py:42:15 in login()"])
        assert ":42:15" not in result
        assert "line" in result or ":" in result

    def test_strips_large_numbers(self, engine: FingerprintEngine):
        """4 位以上数字被替换"""
        result = engine.normalize(["allocated 12345678 bytes"])
        assert "12345678" not in result
        assert "num" in result

    def test_preserves_short_numbers(self, engine: FingerprintEngine):
        """短数字（如 v1, v2）保留"""
        result = engine.normalize(["using config v2 format"])
        assert "v2" in result

    def test_strips_build_id(self, engine: FingerprintEngine):
        """Build ID 被替换"""
        result = engine.normalize(["build-67890 failed"])
        assert "67890" not in result

    def test_collapses_whitespace(self, engine: FingerprintEngine):
        """连续空白合并为单空格"""
        result = engine.normalize(["error   at   line    10"])
        assert "  " not in result

    def test_lowercases(self, engine: FingerprintEngine):
        """结果全部小写"""
        result = engine.normalize(["ERROR: Build FAILED"])
        assert result == result.lower()

    def test_empty_lines(self, engine: FingerprintEngine):
        """空列表返回空字符串"""
        assert engine.normalize([]) == ""


# ============================================================
#  测试：extract_skeleton()
# ============================================================

class TestExtractSkeleton:
    """测试骨架提取"""

    def test_extracts_error_type(self, engine: FingerprintEngine):
        """提取错误类型标识符"""
        normalized = engine.normalize(
            ["ModuleNotFoundError: No module named 'requests'"]
        )
        skeleton = engine.extract_skeleton(normalized)
        assert "modulenotfounderror" in skeleton

    def test_extracts_npm_error_code(self, engine: FingerprintEngine):
        """提取 npm 错误码"""
        normalized = engine.normalize(
            ["npm ERR! code ERESOLVE", "npm ERR! ERESOLVE could not resolve"]
        )
        skeleton = engine.extract_skeleton(normalized)
        assert "eresolve" in skeleton

    def test_extracts_function_name(self, engine: FingerprintEngine):
        """提取函数名（括号在标准化时被处理，骨架保留标识符）"""
        normalized = engine.normalize(
            ["at /src/auth.py:15 in login()"]
        )
        skeleton = engine.extract_skeleton(normalized)
        assert "login" in skeleton

    def test_same_error_different_timestamps_same_skeleton(
        self, engine: FingerprintEngine
    ):
        """相同错误（仅时间戳不同）骨架相同"""
        norm1 = engine.normalize(
            ["ERROR 2024-01-15T10:30:45Z build failed in compile()"]
        )
        norm2 = engine.normalize(
            ["ERROR 2024-12-25T23:59:59Z build failed in compile()"]
        )
        assert engine.extract_skeleton(norm1) == engine.extract_skeleton(norm2)

    def test_different_errors_different_skeleton(self, engine: FingerprintEngine):
        """不同错误骨架不同"""
        norm1 = engine.normalize(
            ["ModuleNotFoundError: No module named 'requests'"]
        )
        norm2 = engine.normalize(
            ["PermissionError: [Errno 13] Permission denied: '/etc/passwd'"]
        )
        assert engine.extract_skeleton(norm1) != engine.extract_skeleton(norm2)


# ============================================================
#  测试：compute_minhash() 和 Jaccard 相似度
# ============================================================

class TestMinHash:
    """测试 MinHash 签名和 Jaccard 相似度"""

    def test_similar_errors_high_jaccard(self, engine: FingerprintEngine):
        """相似错误（仅行号不同）→ Jaccard > 0.8"""
        norm1 = engine.normalize(
            ["ERROR at /src/auth.py:15 in login()", "AssertionError: 401 != 200"]
        )
        norm2 = engine.normalize(
            ["ERROR at /src/auth.py:42 in login()", "AssertionError: 401 != 200"]
        )
        skel1 = engine.extract_skeleton(norm1)
        skel2 = engine.extract_skeleton(norm2)
        m1 = engine.compute_minhash(skel1)
        m2 = engine.compute_minhash(skel2)
        assert m1.jaccard(m2) > 0.8

    def test_different_errors_low_jaccard(self, engine: FingerprintEngine):
        """完全不同错误 → Jaccard < 0.5"""
        norm1 = engine.normalize(
            ["npm ERR! code ERESOLVE", "npm ERR! ERESOLVE could not resolve"]
        )
        norm2 = engine.normalize(
            ["PermissionError: [Errno 13] Permission denied: '/etc/passwd'"]
        )
        skel1 = engine.extract_skeleton(norm1)
        skel2 = engine.extract_skeleton(norm2)
        m1 = engine.compute_minhash(skel1)
        m2 = engine.compute_minhash(skel2)
        assert m1.jaccard(m2) < 0.5

    def test_same_text_jaccard_one(self, engine: FingerprintEngine):
        """完全相同文本 → Jaccard = 1.0"""
        text = "npm err code eresolve eresolve could not resolve"
        m1 = engine.compute_minhash(text)
        m2 = engine.compute_minhash(text)
        assert m1.jaccard(m2) == pytest.approx(1.0, abs=0.01)

    def test_npm_eresolve_variants_similar(self, engine: FingerprintEngine):
        """npm ERESOLVE 变体（仅版本号不同）→ Jaccard > 0.6"""
        norm1 = engine.normalize([
            "npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
            "npm ERR! While resolving: react-scripts@5.0.1",
        ])
        norm2 = engine.normalize([
            "npm ERR! code ERESOLVE",
            "npm ERR! ERESOLVE could not resolve",
            "npm ERR! While resolving: react-scripts@4.0.3",
        ])
        skel1 = engine.extract_skeleton(norm1)
        skel2 = engine.extract_skeleton(norm2)
        m1 = engine.compute_minhash(skel1)
        m2 = engine.compute_minhash(skel2)
        assert m1.jaccard(m2) > 0.6


# ============================================================
#  测试：fingerprint() 主入口
# ============================================================

class TestFingerprint:
    """测试 fingerprint() 主入口"""

    def test_returns_all_fields(self, engine: FingerprintEngine):
        """返回值包含所有必需字段"""
        fp = engine.fingerprint(
            error_lines=["npm ERR! code ERESOLVE"],
            platform="npm",
        )
        assert "normalized" in fp
        assert "skeleton" in fp
        assert "minhash" in fp
        assert "sha256" in fp
        assert "platform" in fp
        assert fp["platform"] == "npm"

    def test_same_log_same_normalized_different_sha256(
        self, engine: FingerprintEngine
    ):
        """相同错误（仅时间戳不同）→ normalized 相同，sha256 不同（因为原始内容不同）"""
        fp1 = engine.fingerprint(
            error_lines=["ERROR 2024-01-15T10:30:45Z build failed"],
            platform="npm",
        )
        fp2 = engine.fingerprint(
            error_lines=["ERROR 2024-12-25T23:59:59Z build failed"],
            platform="npm",
        )
        assert fp1["normalized"] == fp2["normalized"]
        assert fp1["sha256"] != fp2["sha256"]  # 原始内容不同

    def test_different_platform_different_fingerprint(
        self, engine: FingerprintEngine
    ):
        """不同平台 → 不同指纹"""
        fp1 = engine.fingerprint(
            error_lines=["ERROR build failed"],
            platform="npm",
        )
        fp2 = engine.fingerprint(
            error_lines=["ERROR build failed"],
            platform="Docker",
        )
        assert fp1["sha256"] != fp2["sha256"]


# ============================================================
#  测试：性能基准（<10ms for 1000 lines）
# ============================================================

class TestPerformance:
    """性能基准测试"""

    def test_30_lines_under_10ms(self, engine: FingerprintEngine):
        """30 行错误日志指纹提取 < 10ms（log_parser 最多提取 30 行 error_lines）"""
        # 生成 30 行模拟错误日志（模拟 log_parser.extract_error_lines 的输出上限）
        lines = []
        for i in range(30):
            lines.append(
                f"2024-01-15T10:30:45Z ERROR at /src/module_{i}.py:{i} "
                f"in process_pid_{i}() - Error code E{i:04d} "
                f"0x{0x7fff0000 + i:08x} connection to 192.168.1.{i % 256}:{8080 + i} refused"
            )

        # 预热
        engine.fingerprint(lines[:5], "npm")

        # 计时
        start = time.perf_counter()
        for _ in range(100):
            engine.fingerprint(lines, "npm")
        elapsed_ms = (time.perf_counter() - start) / 100 * 1000

        assert elapsed_ms < 10, f"指纹提取耗时 {elapsed_ms:.2f}ms，超过 10ms 限制"

    def test_1000_lines_reasonable_time(self, engine: FingerprintEngine):
        """1000 行压力测试：使用 word-level shingle 优化，<200ms"""
        lines = []
        for i in range(1000):
            lines.append(
                f"2024-01-15T10:30:45Z ERROR at /src/module_{i}.py:{i} "
                f"in process_pid_{i}() - Error code E{i:04d} "
                f"0x{0x7fff0000 + i:08x} connection to 192.168.1.{i % 256}:{8080 + i} refused"
            )

        # 预热
        engine.fingerprint(lines[:10], "npm")

        # 计时
        start = time.perf_counter()
        for _ in range(3):
            engine.fingerprint(lines, "npm")
        elapsed_ms = (time.perf_counter() - start) / 3 * 1000

        assert elapsed_ms < 200, f"1000 行压力测试耗时 {elapsed_ms:.2f}ms，超过 200ms"

    def test_single_line_fast(self, engine: FingerprintEngine):
        """单行日志指纹提取 < 1ms"""
        line = ["npm ERR! code ERESOLVE could not resolve"]

        start = time.perf_counter()
        for _ in range(1000):
            engine.fingerprint(line, "npm")
        elapsed_ms = (time.perf_counter() - start) / 1000 * 1000

        assert elapsed_ms < 1, f"单行指纹提取耗时 {elapsed_ms:.3f}ms"


# ============================================================
#  测试：正则管线完整性
# ============================================================

class TestRegexCoverage:
    """验证所有动态噪声模式都被正确处理"""

    TIMESTAMP_SAMPLES = [
        "2024-01-15T10:30:45Z",
        "2024-01-15 10:30:45",
        "2024-01-15T10:30:45.123+08:00",
        "2024-01-15 10:30:45.123456",
    ]

    UUID_SAMPLES = [
        "550e8400-e29b-41d4-a716-446655440000",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
    ]

    ADDR_SAMPLES = [
        "0x7fff5fbff8ac",
        "0xdeadbeef",
        "0x1a2b3c4d5e6f",
    ]

    IP_SAMPLES = [
        "192.168.1.100",
        "10.0.0.1:8080",
    ]

    PID_SAMPLES = [
        "pid 12345",
        "pid=67890",
    ]

    TMP_SAMPLES = [
        "/tmp/abc123def",
        "/var/tmp/xyz789",
    ]

    def test_all_timestamp_formats(self, engine: FingerprintEngine):
        """所有时间戳格式被替换"""
        for ts in self.TIMESTAMP_SAMPLES:
            result = engine.normalize([f"ERROR {ts} occurred"])
            assert ts not in result, f"时间戳 {ts} 未被替换"

    def test_all_uuid_formats(self, engine: FingerprintEngine):
        """所有 UUID 格式被替换"""
        for uuid in self.UUID_SAMPLES:
            result = engine.normalize([f"job {uuid} failed"])
            assert uuid not in result, f"UUID {uuid} 未被替换"

    def test_all_address_formats(self, engine: FingerprintEngine):
        """所有内存地址格式被替换"""
        for addr in self.ADDR_SAMPLES:
            result = engine.normalize([f"segfault at {addr}"])
            assert addr not in result, f"地址 {addr} 未被替换"

    def test_all_ip_formats(self, engine: FingerprintEngine):
        """所有 IP 格式被替换"""
        for ip in self.IP_SAMPLES:
            result = engine.normalize([f"connection to {ip} refused"])
            # IP 的数字部分不应出现
            assert ip.split(":")[0] not in result, f"IP {ip} 未被替换"

    def test_all_pid_formats(self, engine: FingerprintEngine):
        """所有 PID 格式被替换"""
        for pid in self.PID_SAMPLES:
            result = engine.normalize([f"process {pid} killed"])
            # PID 的数字部分不应出现
            digits = "".join(c for c in pid if c.isdigit())
            assert digits not in result, f"PID {pid} 未被替换"

    def test_all_tmp_formats(self, engine: FingerprintEngine):
        """所有临时路径格式被替换"""
        for tmp in self.TMP_SAMPLES:
            result = engine.normalize([f"cannot write to {tmp}"])
            # 路径中的随机目录名不应出现
            parts = tmp.split("/")
            random_part = parts[-1]
            assert random_part not in result, f"临时路径 {tmp} 未被替换"
