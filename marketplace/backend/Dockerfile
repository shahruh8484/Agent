FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY servio ./servio
ENV SERVIO_DATABASE_PATH=/data/servio.db
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "servio"]
