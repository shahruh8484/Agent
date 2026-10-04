#!/usr/bin/env bash
# Run AFTER deploy/setup-cloner-keitaro.sh and AFTER you've successfully
# run certbot for the domain (certonly --webroot, see that script's final
# printout). Switches the vhost from HTTP-only to HTTPS + redirect, and
# flips SESSION_HTTPS_ONLY on in .env.
#
#   ./deploy/enable-https-keitaro.sh cloner.yourdomain.com
set -euo pipefail

DOMAIN="${1:?Usage: $0 <domain> [nginx-container-name]}"
NGINX_CONTAINER="${2:-nginx}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CONF=/etc/keitaro/nginx/conf.d/cloner.conf
CERT="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"

if [[ ! -f "$CONF" ]]; then
  echo "$CONF not found — run deploy/setup-cloner-keitaro.sh first." >&2
  exit 1
fi
if [[ ! -f "$CERT" ]]; then
  echo "$CERT not found — run certbot for $DOMAIN first (see setup script's printout)." >&2
  exit 1
fi

echo "==> Uncommenting HTTPS server block and redirect"
sed -i '/^# server {/,/^# }/ { s/^# //; s/^#$//; }' "$CONF"
sed -i -E 's/^([[:space:]]*)# return 301/\1return 301/' "$CONF"

set_env_var() {
  local key="$1" val="$2" file=".env"
  # see setup-cloner-keitaro.sh's set_env_var for why: compose
  # interpolates $VAR in .env, so literal $ must be doubled to $$.
  val="${val//\$/\$\$}"
  local escaped
  escaped=$(printf '%s\n' "$val" | sed -e 's/[\/&]/\\&/g')
  sed -i "s/^${key}=.*/${key}=${escaped}/" "$file"
}
set_env_var "SESSION_HTTPS_ONLY" "true"

# Recreate cloner_app BEFORE reloading nginx: nginx resolves the
# cloner_app hostname to a container IP at reload time (static
# proxy_pass), so if the container were recreated afterwards, nginx
# would keep proxying to the old/gone IP and every request would 502.
echo "==> Restarting cloner_app with SESSION_HTTPS_ONLY=true"
docker compose -f docker-compose.cloner.keitaro.yml up -d --build

echo "==> Reloading $NGINX_CONTAINER"
docker exec "$NGINX_CONTAINER" nginx -t
docker exec "$NGINX_CONTAINER" nginx -s reload

echo "==> Checking https://$DOMAIN"
sleep 2
curl -skI "https://$DOMAIN" || true
echo
echo "Done. Open https://$DOMAIN in a browser to confirm."
