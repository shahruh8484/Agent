#!/usr/bin/env bash
# One-time setup: check GitHub every 5 minutes and install new versions.
set -euo pipefail
dir="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$dir/autoupdate.sh"
line="*/5 * * * * $dir/autoupdate.sh >> /var/log/amzagent-update.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'autoupdate.sh' || true; echo "$line" ) | crontab -
echo "Автообновление включено: сервер проверяет GitHub каждые 5 минут."
echo "Журнал обновлений: /var/log/amzagent-update.log"
echo "Сейчас ставлю текущую версию..."
"$dir/autoupdate.sh" --force
