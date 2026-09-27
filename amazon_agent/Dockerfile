FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY amzagent ./amzagent

# Shown in the dashboard; set by deploy/autoupdate.sh.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

ENV PORT=8000
EXPOSE 8000

CMD ["python", "-m", "amzagent.web"]
