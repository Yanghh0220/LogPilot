#!/usr/bin/env bash
# start_all.sh - LogGazer 一键启动 (Linux/macOS)
#
# 用法:
#   bash scripts/start_all.sh
#
# 自动启动 FastAPI Backend (localhost:8000) + Streamlit (localhost:8501)
# 按 Ctrl+C 后所有服务自动停止

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "============================================"
echo "  LogGazer — 一键启动"
echo "  Backend:  http://localhost:8000"
echo "  Frontend: http://localhost:8501"
echo "  Docs:     http://localhost:8000/docs"
echo "============================================"
echo ""

# Check .env
if [ -f ".env" ]; then
    echo "[✓] .env file found"
else
    echo "[!] Warning: .env file not found. Copy .env.example to .env."
fi

# Check Python
if ! command -v python &> /dev/null; then
    echo "[✗] Python not found. Please install Python 3.10+."
    exit 1
fi
echo "[✓] Python found: $(python --version)"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "Stopping LogGazer services..."
    if [ -n "${BACKEND_PID:-}" ]; then
        kill "$BACKEND_PID" 2>/dev/null || true
        wait "$BACKEND_PID" 2>/dev/null || true
    fi
    echo "Done."
}
trap cleanup EXIT INT TERM

# Start backend in background
echo "[1/2] Starting FastAPI Backend..."
python -m api.main &
BACKEND_PID=$!

# Wait for backend to be ready
echo "[*] Waiting for backend to start..."
until curl -s http://localhost:8000/v1/health > /dev/null 2>&1; do
    sleep 2
done

echo "[✓] Backend is ready at http://localhost:8000"
echo ""

echo "[2/2] Starting Streamlit Frontend..."
echo ""
echo "  Opening http://localhost:8501 ..."
echo "  Press Ctrl+C to stop all services."
echo ""

# Run Streamlit in foreground
streamlit run app.py --server.port 8501 --server.address 0.0.0.0
