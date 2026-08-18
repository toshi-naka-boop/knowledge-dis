FROM python:3.12-slim

WORKDIR /app

COPY scripts/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY scripts ./scripts

ENV PYTHONPATH=/app/src:/app

CMD exec uvicorn 'knowledge_discovery.server:create_app_from_env' --factory --host 0.0.0.0 --port ${PORT:-8080}
