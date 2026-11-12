#!/usr/bin/env bash
# Установка всего нужного на GPU-сервер (один раз на новый сервер, ~5–10 минут).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
# Окружение Python кладём в /workspace — на RunPod эта папка переживает Stop сервера.
VENV=/workspace/venv; [ -d /workspace ] || VENV="$PWD/.venv"
[ -d "$VENV" ] || python3 -m venv "$VENV"
source "$VENV/bin/activate"
pip install --upgrade pip
pip install "vllm==${VLLM_VERSION:-0.33.0}"
pip install -r stage0/requirements.txt
echo
echo "✅ Установка завершена."
echo "В каждом новом окне терминала сначала выполняйте: source $VENV/bin/activate"
echo "Дальше: bash stage0/start_llm.sh"
