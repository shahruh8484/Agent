#!/usr/bin/env bash
# Pull the latest code from GitHub and rebuild the agent when it changed.
# Run by cron every 5 minutes (see install-autoupdate.sh); --force rebuilds
# even without new commits.
set -euo pipefail
cd "$(dirname "$0")/.."  # amazon_agent/

exec 9>/tmp/amzagent-update.lock
flock -n 9 || exit 0  # an update is already running

branch=$(git rev-parse --abbrev-ref HEAD)
git fetch -q origin "$branch"
if [ "$(git rev-parse HEAD)" = "$(git rev-parse "origin/$branch")" ] && [ "${1:-}" != "--force" ]; then
  exit 0
fi

# Don't cut off a running agent cycle: try again on the next run, but
# never put an update off for more than an hour.
deferred=/tmp/amzagent-update.deferred
if [ "${1:-}" != "--force" ] && docker compose exec -T app python -m amzagent.busy >/dev/null 2>&1; then
  [ -f "$deferred" ] || date +%s > "$deferred"
  if [ $(( $(date +%s) - $(cat "$deferred") )) -lt 3600 ]; then
    echo "$(date '+%F %T') agent cycle running, update postponed"
    exit 0
  fi
fi
rm -f "$deferred"

echo "$(date '+%F %T') updating $(git rev-parse --short HEAD) -> $(git rev-parse --short "origin/$branch")"
git merge -q --ff-only "origin/$branch"
export APP_VERSION
# Commit time in the panel's time zone (PANEL_TIMEZONE in .env, else Tashkent).
tz=$(grep -E '^PANEL_TIMEZONE=' .env 2>/dev/null | cut -d= -f2- | tr -d "\"' " || true)
APP_VERSION="$(TZ="${tz:-Asia/Tashkent}" git log -1 --format='%h от %cd' --date=format-local:'%d.%m %H:%M')"
docker compose up -d --build
docker image prune -f >/dev/null
echo "$(date '+%F %T') done: $APP_VERSION"
