#!/usr/bin/env bash
# FamilyChef: установка/обновление одной командой на любом Linux-сервере.
#
#   curl -fsSL https://raw.githubusercontent.com/shahruh8484/Agent/claude/happy-albattani-m08fna/deploy/install-familychef.sh | sudo bash
#
# Безопасно для сервера, на котором уже что-то работает (сайты, Keitaro и т.п.):
#   * ничего не трогает на портах 80/443 и в чужих контейнерах/конфигах;
#   * запускает FamilyChef в отдельном Docker-контейнере на свободном порту
#     (по умолчанию 8090) с ограничением памяти;
#   * повторный запуск той же команды = обновление до последней версии.
#
# Переменные (необязательно): FC_PORT=8090  FC_BRANCH=<ветка>  FC_DIR=/opt/familychef
set -euo pipefail

REPO="shahruh8484/Agent"
BRANCH="${FC_BRANCH:-claude/happy-albattani-m08fna}"
DIR="${FC_DIR:-/opt/familychef}"
NAME="familychef"
WANT_PORT="${FC_PORT:-}"

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
for p in 80 443; do
  if port_busy "$p"; then info "Порт $p: занят (там уже работает сайт/прокси — не трогаем)"
  else info "Порт $p: свободен"; fi
done
if [ -d /etc/keitaro ] || { command -v docker >/dev/null && docker network ls --format '{{.Name}}' 2>/dev/null | grep -qx keitaro_network; }; then
  info "Найден Keitaro — его не трогаем, FamilyChef встанет рядом на отдельном порту"
fi
if command -v docker >/dev/null; then
  info "Docker: есть ($(docker --version | sed 's/Docker version //'))"
  running="$(docker ps --format '{{.Names}} ({{.Image}})' 2>/dev/null | grep -v "^$NAME " || true)"
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
if [ -z "$WANT_PORT" ] && [ -f "$DIR/port" ]; then WANT_PORT="$(cat "$DIR/port")"; fi
WANT_PORT="${WANT_PORT:-8090}"
docker rm -f "$NAME" >/dev/null 2>&1 || true   # старая версия освобождает свой порт
PORT="$WANT_PORT"
while port_busy "$PORT"; do
  info "Порт $PORT занят другой программой, пробую $((PORT + 1))"
  PORT=$((PORT + 1))
done
echo "$PORT" > "$DIR/port"

# ----------------------------------------------------------- 5. сборка
say "Собираю и запускаю контейнер (1–3 минуты при первом запуске)"
docker build -q -t "$NAME:latest" -f "$DIR/src/Dockerfile.familychef" "$DIR/src" >/dev/null
docker run -d --name "$NAME" --restart unless-stopped \
  --memory 384m --cpus 1 \
  -p "$PORT:8080" "$NAME:latest" >/dev/null

for _ in $(seq 1 30); do
  curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 \
  || { docker logs --tail 50 "$NAME"; die "приложение не ответило — лог выше"; }
docker image prune -f >/dev/null 2>&1 || true

# ---------------------------------------------------------- 6. firewall
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow "$PORT/tcp" >/dev/null && info "ufw: открыл порт $PORT"
fi
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd -q --permanent --add-port="$PORT/tcp" && firewall-cmd -q --reload \
    && info "firewalld: открыл порт $PORT"
fi

IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
say "Готово! FamilyChef работает"
cat <<EOF

    Откройте:   http://$IP:$PORT

    Обновить до новой версии — запустите ту же команду ещё раз.
    Логи:       docker logs -f $NAME
    Остановить: docker rm -f $NAME

    Если страница не открывается, а здесь всё «Готово» — порт $PORT закрыт
    во внешнем файрволе хостинга (панель провайдера → Firewall / Security group):
    разрешите входящий TCP $PORT.
EOF
