#!/bin/sh
# Starts the EVEDEX bot from TRADER_* variables (see amazon_agent/.env.example).
# Real money needs both TRADER_ENV=prod and TRADER_REAL_MONEY=yes.
set -eu

if [ "${TRADER_ENV:-demo}" = "prod" ]; then
  if [ "${TRADER_REAL_MONEY:-}" != "yes" ]; then
    echo "TRADER_ENV=prod trades REAL money: set TRADER_REAL_MONEY=yes to confirm. Not starting." >&2
    sleep 3600  # don't crash-loop; the operator has to fix .env
    exit 1
  fi
  set -- --env prod --real-money --yes
else
  set -- --env demo
fi

exec python -m cryptobot.main evedex "$@" \
  --instrument "${TRADER_INSTRUMENT:-BTCUSDT:DEMO}" \
  --signal-symbol "${TRADER_SIGNAL_SYMBOL:-BTC/USDT}" \
  --signal-exchange "${TRADER_SIGNAL_EXCHANGE:-binance}" \
  --timeframe "${TRADER_TIMEFRAME:-1h}" \
  --leverage "${TRADER_LEVERAGE:-1}" \
  --max-usd "${TRADER_MAX_USD:-100}" \
  --risk "${TRADER_RISK:-0.01}" \
  --state-dir "${TRADER_STATE_DIR:-/data/trading}"
