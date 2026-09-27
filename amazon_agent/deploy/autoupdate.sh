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

echo "$(date '+%F %T') updating $(git rev-parse --short HEAD) -> $(git rev-parse --short "origin/$branch")"
git merge -q --ff-only "origin/$branch"
export APP_VERSION
APP_VERSION="$(git log -1 --format='%h от %cd' --date=format:'%d.%m %H:%M')"
docker compose up -d --build
docker image prune -f >/dev/null
echo "$(date '+%F %T') done: $APP_VERSION"
