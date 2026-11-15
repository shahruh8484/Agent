#!/usr/bin/env bash
# FamilyChef: установка/обновление одной командой на любом Linux-сервере.
#
#   curl -fsSL https://raw.githubusercontent.com/shahruh8484/Agent/claude/happy-albattani-m08fna/deploy/install-familychef.sh | sudo bash
#
# Без домена: на чистом сервере приложение встаёт на порт 80 (http://IP);
# если 80 занят — на свободный порт начиная с 8090.
#
# С доменом (HTTPS-сертификат Let's Encrypt получается автоматически; A-запись
# домена должна уже указывать на IP сервера, порты 80/443 должны быть свободны):
#
#   curl -fsSL .../install-familychef.sh | sudo FC_DOMAIN=familychef.ru bash
#
# Безопасно для сервера, на котором уже что-то работает: не трогает чужие
# контейнеры и конфиги, не занимает уже занятые порты.
# Повторный запуск той же команды = обновление до последней версии.
#
# Переменные (необязательно): FC_DOMAIN=  FC_PORT=  FC_BRANCH=<ветка>  FC_DIR=/opt/familychef
#
# Вход и кабинет (сохраняются в $FC_DIR/familychef.env и действуют при следующих обновлениях):
#   FC_GOOGLE_CLIENT_ID=...apps.googleusercontent.com   — вход через Google
#   FC_EMAIL_PROVIDER=resend RESEND_API_KEY=... FC_EMAIL_FROM="FamilyChef <login@домен>" — вход по почте
#   FC_REQUIRE_LOGIN=1                                   — пользоваться можно только после входа
# База пользователей хранится в docker-томе familychef_data и не теряется при обновлениях.
set -euo pipefail

REPO="shahruh8484/Agent"
BRANCH="${FC_BRANCH:-claude/happy-albattani-m08fna}"
DIR="${FC_DIR:-/opt/familychef}"
NAME="familychef"
CADDY="familychef-caddy"
NET="familychef-net"
WANT_PORT="${FC_PORT:-}"
DOMAIN="${FC_DOMAIN:-}"

say()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[1;31mОшибка:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "запустите от root: добавьте sudo перед bash"
command -v curl >/dev/null || die "нужен curl (apt install curl / yum install curl)"
command -v tar  >/dev/null || die "нужен tar"

port_busy() {
  if command -v ss >/dev/null; then ss -ltnH "( sport = :$1 )" 2>/dev/null | grep -q .
  else (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; fi
}

# ---------------------------------------------------------------- 1. обзор
say "Что уже есть на сервере"
info "Система: $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || uname -a)"
info "Память: $(free -h 2>/dev/null | awk '/Mem:/ {print $2 " всего, " $7 " свободно"}')"
info "Диск /: $(df -h / | awk 'NR==2 {print $4 " свободно из " $2}')"
command -v docker >/dev/null && docker rm -f "$NAME" "$CADDY" >/dev/null 2>&1 || true  # наша старая версия
for p in 80 443; do
  if port_busy "$p"; then info "Порт $p: занят (там уже работает сайт/прокси — не трогаем)"
  else info "Порт $p: свободен"; fi
done
if [ -d /etc/keitaro ] || { command -v docker >/dev/null && docker network ls --format '{{.Name}}' 2>/dev/null | grep -qx keitaro_network; }; then
  info "Найден Keitaro — его не трогаем"
fi
if command -v docker >/dev/null; then
  info "Docker: есть ($(docker --version | sed 's/Docker version //'))"
  running="$(docker ps --format '{{.Names}} ({{.Image}})' 2>/dev/null || true)"
  if [ -n "$running" ]; then info "Запущенные контейнеры:"; echo "$running" | sed 's/^/      - /'; fi
else
  info "Docker: нет — установлю"
fi

# ------------------------------------------------------------- 2. docker
if ! command -v docker >/dev/null; then
  say "Устанавливаю Docker"
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker 2>/dev/null || service docker start || true
fi
docker info >/dev/null 2>&1 || die "Docker установлен, но не запущен: systemctl start docker"

# ---------------------------------------------------------- 3. код
say "Скачиваю FamilyChef (ветка $BRANCH)"
mkdir -p "$DIR"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" -o "$tmp/src.tgz" \
  || die "не удалось скачать код с GitHub"
tar -xzf "$tmp/src.tgz" -C "$tmp"
src="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[ -f "$src/Dockerfile.familychef" ] || die "в архиве нет Dockerfile.familychef"
rm -rf "$DIR/src"
mkdir -p "$DIR/src"
cp -r "$src/familychef" "$src/Dockerfile.familychef" "$DIR/src/"

# ------------------------------------------------------------ 4. порт
if [ -n "$DOMAIN" ]; then
  DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"
  MY_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  DNS_IPS="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)"
  info "DNS: $DOMAIN → ${DNS_IPS:-(не найден)}; IP этого сервера: ${MY_IP:-?}"
  if [ -n "$MY_IP" ] && [ "${FC_SKIP_DNS_CHECK:-}" != "1" ] && ! echo " $DNS_IPS " | grep -q " $MY_IP "; then
    die "домен $DOMAIN пока не указывает на этот сервер ($MY_IP).
    Проверьте A-запись @ → $MY_IP у регистратора и подождите 5–30 минут, затем запустите команду снова.
    (Пока можно запустить без домена: та же команда без FC_DOMAIN=…)"
  fi
  for p in 80 443; do
    port_busy "$p" && die "порт $p занят другой программой — для режима с доменом нужны свободные 80 и 443"
  done
  rm -f "$DIR/port"
else
  if [ -z "$WANT_PORT" ] && [ -f "$DIR/port" ]; then WANT_PORT="$(cat "$DIR/port")"; fi
  if [ -z "$WANT_PORT" ]; then
    if port_busy 80; then WANT_PORT=8090; else WANT_PORT=80; fi
  fi
  PORT="$WANT_PORT"
  while port_busy "$PORT"; do
    next=$((PORT < 8090 ? 8090 : PORT + 1))
    info "Порт $PORT занят другой программой, пробую $next"
    PORT="$next"
  done
  echo "$PORT" > "$DIR/port"
fi

# ----------------------------------------------------------- 5. сборка
say "Собираю и запускаю контейнер (1–3 минуты при первом запуске)"
docker build -q -t "$NAME:latest" -f "$DIR/src/Dockerfile.familychef" "$DIR/src" >/dev/null
docker network inspect "$NET" >/dev/null 2>&1 || docker network create "$NET" >/dev/null
LOGS="--log-opt max-size=10m --log-opt max-file=3"  # журналы не растут бесконечно

# Настройки приложения: файл $DIR/familychef.env (создаётся один раз, дальше только дополняется).
ENV_FILE="$DIR/familychef.env"
if [ ! -f "$ENV_FILE" ]; then
  umask 077
  cat > "$ENV_FILE" <<ENVEOF
# FamilyChef settings. Edit and re-run the installer to apply.
FC_SECRET=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
FC_GOOGLE_CLIENT_ID=
FC_EMAIL_PROVIDER=
FC_EMAIL_FROM=FamilyChef <login@${DOMAIN:-familychef.pro}>
RESEND_API_KEY=
FC_REQUIRE_LOGIN=0
ENVEOF
  umask 022
fi
set_env() {  # set_env NAME VALUE — replace or append a line in the env file
  local name="$1" value="$2" tmpf
  tmpf="$(mktemp)"
  grep -v "^${name}=" "$ENV_FILE" > "$tmpf" || true
  printf '%s=%s\n' "$name" "$value" >> "$tmpf"
  cat "$tmpf" > "$ENV_FILE"; rm -f "$tmpf"
}
for var in FC_GOOGLE_CLIENT_ID FC_EMAIL_PROVIDER FC_EMAIL_FROM RESEND_API_KEY FC_REQUIRE_LOGIN \
           SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD; do
  if [ -n "${!var+x}" ]; then set_env "$var" "${!var}"; info "Настройка $var сохранена"; fi
done
chmod 600 "$ENV_FILE"
DATA="-v familychef_data:/app/var --env-file $ENV_FILE"

if [ -n "$DOMAIN" ]; then
  # Снаружи приложение доступно только через Caddy (HTTPS); 127.0.0.1 — для проверки.
  CHECK_PORT=8090
  while port_busy "$CHECK_PORT"; do CHECK_PORT=$((CHECK_PORT + 1)); done
  docker run -d --name "$NAME" --restart unless-stopped --network "$NET" \
    --memory 384m --cpus 1 $LOGS $DATA -p "127.0.0.1:$CHECK_PORT:8080" "$NAME:latest" >/dev/null
else
  CHECK_PORT="$PORT"
  docker run -d --name "$NAME" --restart unless-stopped --network "$NET" \
    --memory 384m --cpus 1 $LOGS $DATA -p "$PORT:8080" "$NAME:latest" >/dev/null
fi

for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$CHECK_PORT/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$CHECK_PORT/healthz" >/dev/null 2>&1 \
  || { docker logs --tail 50 "$NAME"; die "приложение не ответило — лог выше"; }

if [ -n "$DOMAIN" ]; then
  say "Включаю HTTPS для $DOMAIN"
  DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"
  printf '%s {\n\tencode gzip\n\treverse_proxy %s:8080\n}\n' "$DOMAIN" "$NAME" > "$DIR/Caddyfile"
  case "$DOMAIN" in
    www.*) ;;
    *) # www.домен → домен (сертификат для www выдаётся, если для www есть DNS-запись)
       printf '\nwww.%s {\n\tredir https://%s{uri} permanent\n}\n' "$DOMAIN" "$DOMAIN" >> "$DIR/Caddyfile" ;;
  esac
  docker run -d --name "$CADDY" --restart unless-stopped --network "$NET" $LOGS \
    -p 80:80 -p 443:443 -p 443:443/udp \
    -v "$DIR/Caddyfile:/etc/caddy/Caddyfile:ro" \
    -v familychef_caddy_data:/data -v familychef_caddy_config:/config \
    caddy:2-alpine >/dev/null
  OPEN_PORTS="80 443"
else
  OPEN_PORTS="$PORT"
fi
docker image prune -f >/dev/null 2>&1 || true

# ---------------------------------------------------------- 6. firewall
for p in $OPEN_PORTS; do
  if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$p/tcp" >/dev/null && info "ufw: открыл порт $p"
  fi
  if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd -q --permanent --add-port="$p/tcp" && info "firewalld: открыл порт $p"
  fi
done
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd -q --reload
fi

IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
if [ -n "$DOMAIN" ]; then URL="https://$DOMAIN"
elif [ "$PORT" = "80" ]; then URL="http://$IP"
else URL="http://$IP:$PORT"; fi

say "Готово! FamilyChef работает"
cat <<DONE

    Откройте:   $URL

    Обновить до новой версии — запустите ту же команду ещё раз.
    Логи:       docker logs -f $NAME
    Остановить: docker rm -f $NAME $CADDY

    Если страница не открывается, а здесь всё «Готово» — порт(ы) $OPEN_PORTS
    закрыты во внешнем файрволе хостинга (панель провайдера → Firewall /
    Security group): разрешите входящий TCP $OPEN_PORTS.
DONE
if [ -n "$DOMAIN" ]; then
  cat <<DONE
    HTTPS-сертификат выдаётся за 10–60 секунд. Если $URL не открывается,
    проверьте, что A-запись $DOMAIN указывает на $IP, и посмотрите: docker logs $CADDY
DONE
fi
