#!/usr/bin/env bash
# 一键启动「小暖」后端（含老人端/子女端前端，由后端同源托管）。
#
# 用法：
#   ./run.sh              # 零凭证 fake 模式，直接跑通整条链路（推荐首次体验）
#   ./run.sh --real       # 接真实火山语音（需先在 .env 填 VOLC_*）
#
# 即填即用：fake 模式无需任何 API 凭证即可启动；接真实语音只需填 .env 的 VOLC_*。
set -euo pipefail

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"
ENGINE="fake"   # 默认零凭证模式

for arg in "$@"; do
  case "$arg" in
    --real) ENGINE="seeduplex" ;;
    --fake) ENGINE="fake" ;;
    *) echo "未知参数：$arg（可用 --real / --fake）"; exit 1 ;;
  esac
done

# 1. 检查 Python
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "❌ 未找到 $PY，请先安装 Python 3.9+"; exit 1
fi

# 2. 创建/复用虚拟环境
if [ ! -d "$VENV" ]; then
  echo "📦 创建虚拟环境 $VENV …"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

# 3. 安装依赖（幂等，已装则极快）
echo "📥 安装后端依赖 …"
pip install -q -r backend/requirements.txt

# 4. 准备 .env（首次从模板复制）
if [ ! -f ".env" ]; then
  echo "📝 未发现 .env，已从 .env.example 复制一份。"
  echo "   fake 模式无需改动即可运行；接真实语音请填入 VOLC_* 后用 ./run.sh --real"
  cp .env.example .env
fi

# 5. 启动
PORT="$(grep -E '^PORT=' .env | head -1 | cut -d= -f2 | tr -d '[:space:]')"
PORT="${PORT:-8000}"

echo ""
echo "🚀 启动中（引擎：${ENGINE}）…"
echo "   老人端：http://127.0.0.1:${PORT}/elder/"
echo "   子女端：http://127.0.0.1:${PORT}/parent/"
echo "   健康检查：http://127.0.0.1:${PORT}/healthz"
echo ""

# fake 模式覆盖 VOICE_ENGINE，不依赖 .env 里的设置；真实模式沿用 .env。
if [ "$ENGINE" = "fake" ]; then
  VOICE_ENGINE=fake exec "$VENV/bin/python" -m backend.server
else
  exec "$VENV/bin/python" -m backend.server
fi
