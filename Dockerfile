FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fbadsagent ./fbadsagent

ENV PORT=8000
EXPOSE 8000

CMD ["python", "-m", "fbadsagent.web"]
