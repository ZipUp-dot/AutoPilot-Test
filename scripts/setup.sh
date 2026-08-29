#!/usr/bin/env bash
# ============================================================
# AutoPilot — 一键环境初始化 (Linux / macOS)
# 用法：./scripts/setup.sh
# 完成：venv 创建 → 后端依赖安装 → Playwright Chromium → 前端依赖
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$ROOT/backend/.venv"

echo "==> [1/4] 创建后端虚拟环境: $VENV_DIR"
if [ ! -d "$VENV_DIR" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
VENV_PLAYWRIGHT="$VENV_DIR/bin/playwright"

echo "==> [2/4] 安装后端依赖（生产 + 测试）"
"$VENV_PIP" install --upgrade pip
"$VENV_PIP" install -r "$ROOT/backend/requirements.txt"
"$VENV_PIP" install -r "$ROOT/backend/requirements-dev.txt"

echo "==> [3/4] 安装 Playwright Chromium 浏览器（与 playwright==1.56.0 对应 revision）"
"$VENV_PLAYWRIGHT" install chromium

echo "==> [4/4] 安装前端依赖"
if command -v npm >/dev/null 2>&1; then
    (cd "$ROOT/frontend" && npm ci)
else
    echo "    [skip] npm 未安装，请手动执行: cd frontend && npm ci"
fi

cat <<EOF

============================================================
AutoPilot 环境初始化完成
  后端启动: cd backend && .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
  运行测试: cd backend && .venv/bin/python -m pytest
  前端开发: cd frontend && npm run dev   # http://localhost:5173
  (注意: 后端禁止 --reload，避免沙箱拦截 Playwright)
============================================================
EOF
