FROM python:3.12-slim

# uv той же версии, что и локально
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv

WORKDIR /app

# Зависимости — отдельным слоем: пересобираются, только когда меняются pyproject.toml или uv.lock
ENV UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-cache

COPY . .

# Запуск не от root. storage/ — загруженные файлы, сюда подключается том
RUN useradd --no-create-home app && mkdir storage && chown app storage
USER app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# Перед стартом API применяются миграции
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
