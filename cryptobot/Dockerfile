# Trading bot for EVEDEX. Build from the repository root:
#   docker build -f cryptobot/Dockerfile .
FROM node:22-bookworm-slim AS bridge
WORKDIR /bridge
COPY cryptobot/evedex_bridge/package.json cryptobot/evedex_bridge/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim
# Node runs the official EVEDEX SDK that signs orders (cryptobot/evedex_bridge).
COPY --from=bridge /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY cryptobot/requirements.txt cryptobot/requirements.txt
RUN pip install --no-cache-dir -r cryptobot/requirements.txt
COPY cryptobot cryptobot
COPY --from=bridge /bridge/node_modules cryptobot/evedex_bridge/node_modules
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["sh", "cryptobot/docker-entrypoint.sh"]
