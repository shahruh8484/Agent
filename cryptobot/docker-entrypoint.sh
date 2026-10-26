#!/bin/sh
# Starts the EVEDEX bot from TRADER_* variables (see amazon_agent/.env.example).
# Real money needs both TRADER_ENV=prod and TRADER_REAL_MONEY=yes.
set -eu

# "options": the Deribit put seller (OPTIONS_* variables); anything else: the EVEDEX bot.
if [ "${1:-}" = "options" ]; then
  if [ "${OPTIONS_ENV:-testnet}" = "prod" ]; then
    if [ "${OPTIONS_REAL_MONEY:-}" != "yes" ]; then
      echo "OPTIONS_ENV=prod trades REAL money: set OPTIONS_REAL_MONEY=yes to confirm. Not starting." >&2
      sleep 3600
      exit 1
    fi
    set -- --env prod --real-money --yes
  else
    set -- --env testnet
  fi
  exec python -m cryptobot.main options "$@" \
    --currencies "${OPTIONS_CURRENCIES:-BTC,ETH}" \
    --state-dir "${TRADER_STATE_DIR:-/data/trading}"
fi

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

# TRADER_INSTRUMENTS (several coins) wins over TRADER_INSTRUMENT + TRADER_SIGNAL_SYMBOL.
exec python -m cryptobot.main evedex "$@" \
  --instrument "${TRADER_INSTRUMENT:-BTCUSDT:DEMO}" \
  --signal-symbol "${TRADER_SIGNAL_SYMBOL:-BTC/USDT}" \
  --instruments "${TRADER_INSTRUMENTS:-}" \
  --max-positions "${TRADER_MAX_POSITIONS:-2}" \
  --news "${TRADER_NEWS:-shadow}" \
  --news-block "${TRADER_NEWS_BLOCK:--0.3}" \
  --news-exit "${TRADER_NEWS_EXIT:--0.7}" \
  --signal-exchange "${TRADER_SIGNAL_EXCHANGE:-binance}" \
  --timeframe "${TRADER_TIMEFRAME:-1h}" \
  --leverage "${TRADER_LEVERAGE:-1}" \
  --max-usd "${TRADER_MAX_USD:-100}" \
  --risk "${TRADER_RISK:-0.01}" \
  --state-dir "${TRADER_STATE_DIR:-/data/trading}"
