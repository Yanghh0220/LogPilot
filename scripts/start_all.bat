@echo off
REM start_all.bat - LogGazer 一键启动 (Windows)
REM
REM 用法:
REM   scripts\start_all.bat
REM
REM 自动启动 FastAPI Backend (localhost:8000) + Streamlit (localhost:8501)
REM 关闭此窗口后所有服务自动停止

cd /d "%~dp0\.."

echo ============================================
echo   LogGazer — 一键启动
echo   Backend:  http://localhost:8000
echo   Frontend: http://localhost:8501
echo   Docs:    http://localhost:8000/docs
echo ============================================
echo.

REM Check .env
if exist ".env" (
    echo [√] .env file found
) else (
    echo [!] Warning: .env file not found. Copy .env.example to .env.
)

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [X] Python not found. Please install Python 3.10+.
    pause
    exit /b 1
)

echo [√] Python found
echo.

echo [1/2] Starting FastAPI Backend...
start "LogGazer-Backend" /MIN python -m api.main

REM Wait for backend to be ready
echo [*] Waiting for backend to start...
:wait_backend
timeout /t 2 /nobreak >nul
curl -s http://localhost:8000/v1/health >nul 2>&1
if errorlevel 1 goto wait_backend

echo [√] Backend is ready at http://localhost:8000
echo.

echo [2/2] Starting Streamlit Frontend...
echo.
echo   Opening http://localhost:8501 in your browser...
echo   Press Ctrl+C to stop all services.
echo.

start http://localhost:8501

REM Run Streamlit in foreground (closing it stops everything)
streamlit run app.py --server.port 8501 --server.address 0.0.0.0

REM Cleanup: kill backend when Streamlit exits
echo.
echo Stopping LogGazer Backend...
taskkill /FI "WINDOWTITLE eq LogGazer-Backend*" /T >nul 2>&1
echo Done.
