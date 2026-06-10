# api/__init__.py - LogGazer FastAPI Backend
#
# Backend-for-Frontend (BFF) architecture:
#   - FastAPI serves as the core analysis service
#   - Streamlit, VS Code, MCP Server, GitHub App all consume this API
#   - Fully decoupled from any UI framework
#
# Lazy import: avoids circular import when running `python -m api.main`.
# Use `from api import app` or `from api.main import app` directly.


def _get_app():
    """Lazy-load the FastAPI app instance (avoids circular import on startup)."""
    from api.main import app as _app
    return _app


def __getattr__(name):
    """Module-level lazy attribute access — enables `from api import app`."""
    if name == "app":
        from api.main import app as _app
        return _app
    raise AttributeError(f"module 'api' has no attribute {name!r}")


__all__ = ["app"]
