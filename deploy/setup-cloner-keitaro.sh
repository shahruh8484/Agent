#!/usr/bin/env bash
# One-shot setup for the cloner service on a server that already runs
# Keitaro (nginx inside its own container, holding host ports 80/443 on
# the keitaro_network docker network — see docker-compose.cloner.keitaro.yml
# and deploy/nginx-cloner-keitaro.conf.example for the background).
#
# Run this from the repo root (e.g. /opt/Agent), as root, AFTER DNS for
# your domain already points at this server:
#
#   ./deploy/setup-cloner-keitaro.sh cloner.yourdomain.com
#
# It builds .env (prompting for secrets locally, never printing them
# back), brings the cloner_app container up on Keitaro's docker network,
# THEN sets up the nginx vhost (HTTP only — no cert yet) and reloads
# nginx — in that order, since nginx's upstream config references
# cloner_app by container name and nginx -t fails if that name doesn't
# resolve yet. It does NOT request a TLS certificate — run certbot
# yourself once this confirms working over plain HTTP, then run
# deploy/enable-https-keitaro.sh to switch the vhost to HTTPS.
set -euo pipefail

DOMAIN="${1:?Usage: $0 <domain> [nginx-container-name]}"
NGINX_CONTAINER="${2:-nginx}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f docker-compose.cloner.keitaro.yml ]]; then
  echo "Run this from the repo root (docker-compose.cloner.keitaro.yml not found here)." >&2
  exit 1
fi

set_env_var() {
  local key="$1" val="$2" file=".env"
  # docker compose interpolates $VAR inside .env (it's read both as this
  # service's env_file and as compose's own project .env), so a literal $
  # — e.g. in a bcrypt hash, which is full of them ($2b$12$...) — must be
  # doubled to $$ or everything after it silently gets dropped.
  val="${val//\$/\$\$}"
  local escaped
  escaped=$(printf '%s\n' "$val" | sed -e 's/[\/&]/\\&/g')
  if grep -q "^${key}=" "$file"; then
    sed -i "s/^${key}=.*/${key}=${escaped}/" "$file"
  else
    echo "${key}=${val}" >> "$file"
  fi
}

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

echo "==> Building .env"
set_env_var "DOMAIN" "$DOMAIN"
set_env_var "SESSION_HTTPS_ONLY" "false"   # flipped to true by enable-https-keitaro.sh

echo "LLM provider (anthropic / openai / gemini) [anthropic]: "
read -r LLM_PROVIDER
LLM_PROVIDER="${LLM_PROVIDER:-anthropic}"
set_env_var "LLM_PROVIDER" "$LLM_PROVIDER"

case "$LLM_PROVIDER" in
  anthropic) KEY_VAR="ANTHROPIC_API_KEY" ;;
  openai) KEY_VAR="OPENAI_API_KEY" ;;
  gemini) KEY_VAR="GEMINI_API_KEY" ;;
  *) echo "Unknown provider '$LLM_PROVIDER'" >&2; exit 1 ;;
esac
read -rsp "API key for $LLM_PROVIDER: " API_KEY
echo
set_env_var "$KEY_VAR" "$API_KEY"

echo "Admin username [admin]: "
read -r ADMIN_USER
ADMIN_USER="${ADMIN_USER:-admin}"
set_env_var "ADMIN_USERNAME" "$ADMIN_USER"

read -rsp "Admin password for the cloner login: " ADMIN_PASS
echo
PASS_HASH=$(docker run --rm -v "$REPO_ROOT":/app -w /app python:3.12-slim \
  bash -c "pip install -q bcrypt >/dev/null && python -m fbadsagent.web.security '$ADMIN_PASS'")
set_env_var "ADMIN_PASSWORD_HASH" "$PASS_HASH"

SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || \
  docker run --rm python:3.12-slim python3 -c "import secrets; print(secrets.token_hex(32))")
set_env_var "SECRET_KEY" "$SECRET"

echo "==> Bringing up cloner_app (on Keitaro's docker network)"
docker compose -f docker-compose.cloner.keitaro.yml up -d --build

echo "==> Vhost: /etc/keitaro/nginx/conf.d/cloner.conf for $DOMAIN"
cp deploy/nginx-cloner-keitaro.conf.example /etc/keitaro/nginx/conf.d/cloner.conf
sed -i "s/YOUR-CLONER-DOMAIN/$DOMAIN/g" /etc/keitaro/nginx/conf.d/cloner.conf

echo "==> ACME webroot dir"
mkdir -p /var/www/keitaro/cloner-acme/.well-known/acme-challenge

echo "==> Reloading $NGINX_CONTAINER"
docker exec "$NGINX_CONTAINER" nginx -t
docker exec "$NGINX_CONTAINER" nginx -s reload

echo "==> Checking http://$DOMAIN"
sleep 2
curl -sI "http://$DOMAIN" || true

echo
echo "Done. If the curl above shows a 200/302 response, it's working over HTTP."
echo "Next: get a cert, then run ./deploy/enable-https-keitaro.sh $DOMAIN"
echo "  dnf install -y epel-release certbot   # if not installed yet"
echo "  certbot certonly --webroot -w /var/www/keitaro/cloner-acme -d $DOMAIN --agree-tos -m you@example.com --no-eff-email"
