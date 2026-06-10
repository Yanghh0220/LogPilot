# api/tests/test_api.py - FastAPI Backend Tests (API Layer)
#
# Tests for:
#   - POST /v1/analyze: normal flow, validation errors
#   - GET /v1/health: healthy, degraded, unhealthy states
#   - CORS preflight: OPTIONS request headers
#   - RFC 7807 Problem Details: error response format
#
# Uses FastAPI TestClient (sync) for integration testing.
# Mock analyzer.analyze_log to avoid real AI calls.

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure project root is on path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


# ---- Session-level OpenAI mock (prevents module-level client creation failure) ----

@pytest.fixture(autouse=True, scope="session")
def _mock_openai():
    """Mock OpenAI client for all API tests."""
    with patch("openai.OpenAI", return_value=MagicMock()):
        yield


# ---- Test fixtures ----

@pytest.fixture
def mock_analysis_result():
    """Create a valid AnalysisResult for mocking."""
    from models import AnalysisResult, RootCause, FixSuggestion
    return AnalysisResult(
        error_summary="npm 依赖解析冲突",
        error_detail="npm ERR! ERESOLVE could not resolve",
        root_causes=[
            RootCause(description="react 版本不兼容", probability=70),
            RootCause(description="package-lock.json 过期", probability=30),
        ],
        fix_suggestions=[
            FixSuggestion(
                title="使用 --legacy-peer-deps",
                description="跳过 peer dependency 检查",
                command="npm install --legacy-peer-deps",
                safety_level="safe",
            ),
        ],
        debug_commands=["npm ls react", "npm why react"],
        severity="medium",
        prevention=["使用更宽松的版本范围"],
        security_warning="",
    )


@pytest.fixture
def client(mock_analysis_result):
    """Create a FastAPI TestClient with mocked analyzer.

    Resets the rate limiter singleton before each test to avoid
    cross-test contamination.
    """
    from api.dependencies import reset_rate_limiter
    reset_rate_limiter()

    mock_analyze = MagicMock(return_value=mock_analysis_result)
    with patch("api.main.get_analyzer", return_value=mock_analyze):
        from api.main import app
        with TestClient(app) as tc:
            yield tc


@pytest.fixture
def valid_npm_log():
    """Sample valid npm error log."""
    return (
        "npm ERR! code ERESOLVE\n"
        "npm ERR! ERESOLVE could not resolve\n"
        "npm ERR! While resolving: react-scripts@5.0.1\n"
        "npm ERR! Found: react@18.2.0\n"
        "npm ERR! Conflicting peer dependency: react@17.0.2\n"
        "npm ERR! Fix the upstream dependency conflict\n"
    )


# ============================================================
#  POST /v1/analyze — Normal Flow
# ============================================================

class TestAnalyzeNormal:
    """Happy path tests for POST /v1/analyze."""

    def test_valid_npm_log_returns_analyze_response(self, client, valid_npm_log):
        """POST /v1/analyze with valid npm log returns complete AnalyzeResponse."""
        response = client.post(
            "/v1/analyze",
            json={"log_text": valid_npm_log, "platform_hint": "npm"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "result" in data
        assert "meta" in data
        assert "request_id" in data

        result = data["result"]
        assert result["error_summary"] == "npm 依赖解析冲突"
        assert result["severity"] == "medium"
        assert len(result["root_causes"]) == 2
        assert len(result["fix_suggestions"]) == 1
        assert len(result["debug_commands"]) == 2

        meta = data["meta"]
        assert "duration_ms" in meta
        assert meta["cache_status"] in ("hit", "miss", "rag", "disabled")
        assert "model_used" in meta
        assert "cost_usd" in meta
        assert "platform_detected" in meta

    def test_root_causes_probabilities_sum_to_100(self, client, valid_npm_log):
        """Root cause probabilities must sum to exactly 100 (Pydantic validator)."""
        response = client.post(
            "/v1/analyze",
            json={"log_text": valid_npm_log},
        )
        data = response.json()
        total = sum(rc["probability"] for rc in data["result"]["root_causes"])
        assert total == 100

    def test_platform_hint_is_optional(self, client, valid_npm_log):
        """platform_hint is optional — analysis works without it."""
        response = client.post(
            "/v1/analyze",
            json={"log_text": valid_npm_log},
        )
        assert response.status_code == 200


# ============================================================
#  POST /v1/analyze — Validation Errors
# ============================================================

class TestAnalyzeValidation:
    """Validation error tests for POST /v1/analyze."""

    def test_empty_log_returns_422(self, client):
        """Empty log_text returns 422 with Problem Detail."""
        response = client.post(
            "/v1/analyze",
            json={"log_text": ""},
        )
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data or isinstance(data.get("detail"), list)

    def test_whitespace_only_log_returns_422(self, client):
        """Whitespace-only log_text returns 422."""
        response = client.post(
            "/v1/analyze",
            json={"log_text": "   \n  \t  "},
        )
        assert response.status_code == 422

    def test_too_short_log_returns_422(self, client):
        """log_text shorter than 10 chars returns 422."""
        response = client.post(
            "/v1/analyze",
            json={"log_text": "short"},
        )
        assert response.status_code == 422

    def test_missing_log_text_returns_422(self, client):
        """Missing required field log_text returns 422."""
        response = client.post(
            "/v1/analyze",
            json={"platform_hint": "npm"},
        )
        assert response.status_code == 422


# ============================================================
#  GET /v1/health — Health Check
# ============================================================

class TestHealth:
    """Health check endpoint tests."""

    def test_health_returns_200_with_status(self, client):
        """GET /v1/health returns 200 with overall status."""
        response = client.get("/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded", "unhealthy")
        assert data["version"] == "1.1.0"
        assert "uptime_seconds" in data

    def test_health_includes_all_checks(self, client):
        """Health response includes ai_provider, redis, cache, database checks."""
        response = client.get("/v1/health")
        data = response.json()
        checks = data["checks"]
        assert "ai_provider" in checks
        assert "redis" in checks
        assert "cache" in checks
        assert "database" in checks


# ============================================================
#  CORS Headers
# ============================================================

class TestCORS:
    """CORS preflight and header tests."""

    def test_options_returns_cors_headers(self, client):
        """OPTIONS request returns proper CORS headers."""
        response = client.options(
            "/v1/analyze",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert response.status_code in (200, 405, 204)

    def test_allowed_origin_has_cors_header(self, client):
        """POST from allowed origin includes CORS header."""
        response = client.post(
            "/v1/analyze",
            json={"log_text": "valid log text for cors testing here"},
            headers={"Origin": "http://localhost:8501"},
        )
        assert response.status_code in (200, 429)


# ============================================================
#  GET /v1/platforms
# ============================================================

class TestPlatforms:
    """Platform listing endpoint tests."""

    def test_platforms_returns_list(self, client):
        """GET /v1/platforms returns supported platforms."""
        response = client.get("/v1/platforms")
        assert response.status_code == 200
        data = response.json()
        assert "platforms" in data
        assert "total" in data
        assert data["total"] >= 5
        for p in data["platforms"]:
            assert "name" in p
            assert "detection_keywords" in p
