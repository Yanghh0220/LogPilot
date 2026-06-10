@echo off
REM start_backend.bat - LogGazer FastAPI Backend 启动脚本 (Windows)
REM
REM 用法:
REM   scripts\start_backend.bat
REM
REM 启动后可通过以下地址访问:
REM   - API:           http://localhost:8000
REM   - Swagger Docs:  http://localhost:8000/docs
REM   - Health Check:  http://localhost:8000/v1/health

cd /d "%~dp0\.."

echo ============================================
echo   LogGazer FastAPI Backend
echo   http://localhost:8000
echo   Docs: http://localhost:8000/docs
echo ============================================
echo.

REM Check if .env file exists
if exist ".env" (
    echo [✓] .env file found
) else (
    echo [!] Warning: .env file not found. Copy .env.example to .env and configure your API key.
)

REM Start uvicorn with reload for development
uvicorn api.main:app --reload --port 8000 --host 0.0.0.0 --log-level info
