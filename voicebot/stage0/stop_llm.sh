#!/usr/bin/env bash
# Остановить модель (видеопамять освободится). Сам сервер RunPod этим НЕ останавливается!
cd "$(dirname "$0")/.."
[ -f logs/vllm.pid ] && kill "$(cat logs/vllm.pid)" 2>/dev/null
pkill -f "vllm serve" 2>/dev/null
sleep 3; echo "Модель остановлена. Не забудьте нажать Stop у сервера в RunPod, если закончили."
