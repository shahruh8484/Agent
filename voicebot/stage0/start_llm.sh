#!/usr/bin/env bash
# Запуск модели (vLLM) в фоне. Первый запуск качает модель — 5–20 минут.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
for v in /workspace/venv .venv; do [ -f "$v/bin/activate" ] && source "$v/bin/activate" && break; done
mkdir -p logs "${HF_HOME:-/workspace/hf}"
export HF_HOME HF_TOKEN
if curl -sf localhost:8000/v1/models >/dev/null; then
  echo "Модель уже запущена:"; curl -s localhost:8000/v1/models | python3 -c "import sys,json;print(json.load(sys.stdin)['data'][0]['id'])"
  echo "Чтобы сменить модель: bash stage0/stop_llm.sh, поправьте MODEL в .env и запустите снова."; exit 0
fi
echo "Запускаю $MODEL ... (лог: logs/vllm.log)"
nohup vllm serve "$MODEL" --port 8000 \
  --max-model-len "${MAX_MODEL_LEN:-8192}" \
  --gpu-memory-utilization "${GPU_MEM:-0.90}" \
  ${EXTRA_VLLM_ARGS:-} > logs/vllm.log 2>&1 &
echo $! > logs/vllm.pid
for i in $(seq 1 240); do
  if curl -sf localhost:8000/v1/models >/dev/null; then echo; echo "✅ Модель готова к работе."; exit 0; fi
  if ! kill -0 "$(cat logs/vllm.pid)" 2>/dev/null; then
    echo; echo "❌ vLLM остановился с ошибкой. Последние строки лога:"; tail -n 30 logs/vllm.log; exit 1
  fi
  printf "."; sleep 5
done
echo; echo "⏳ Модель всё ещё загружается (20 мин). Смотрите: tail -f logs/vllm.log"
