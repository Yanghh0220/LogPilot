#!/usr/bin/env bash
# start_backend.sh - LogGazer FastAPI Backend 启动脚本
#
# 用法:
#   bash scripts/start_backend.sh
#
# 启动后可通过以下地址访问:
#   - API:           http://localhost:8000
#   - Swagger Docs:  http://localhost:8000/docs
#   - Health Check:  http://localhost:8000/v1/health

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "============================================"
echo "  LogGazer FastAPI Backend"
echo "  http://localhost:8000"
echo "  Docs: http://localhost:8000/docs"
echo "============================================"
echo ""

# Check if .env file exists
if [ -f ".env" ]; then
    echo "[✓] .env file found"
else
    echo "[!] Warning: .env file not found. Copy .env.example to .env and configure your API key."
fi

# Start uvicorn with reload for development
exec uvicorn api.main:app \
    --reload \
    --port 8000 \
    --host 0.0.0.0 \
    --log-level info
