FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    STORAGE_DIR=/app/storage

WORKDIR /app
RUN pip install --no-cache-dir uv==0.11.32

# Сначала зависимости — этот слой пересобирается только при смене uv.lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

EXPOSE 8000
# При старте применяются миграции, затем запускается API.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2"]
