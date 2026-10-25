# Конок — бэкенд

Требования: [`../docs/TZ_Konok_v2_for_Claude.md`](../docs/TZ_Konok_v2_for_Claude.md).

## Запуск в Docker (сервер)

```bash
cp .env.example .env     # задайте POSTGRES_PASSWORD, SECRET_KEY, CORS_ORIGINS, API_PORT
docker compose up -d --build
docker compose exec api python scripts/create_admin.py admin@konok.kg "Имя Фамилия"
```

Поднимаются API (миграции применяются при старте), воркер фоновых задач, PostgreSQL и Redis.
Данные — в томах `pgdata`, `redisdata`, `storage` (фото и документы). Проверка: `GET /health`.

Обновление: `git pull && docker compose up -d --build`. Логи: `docker compose logs -f api worker`.

## Локальный запуск

Нужны PostgreSQL 16+ и Redis.

```bash
cp .env.example .env      # укажите DATABASE_URL, REDIS_URL, SECRET_KEY
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Фоновые задачи (напоминания хозяину, завершение броней) — отдельный процесс:

```bash
uv run arq app.worker.WorkerSettings
```

- Swagger: http://localhost:8000/docs
- Админка: http://localhost:8000/admin (вход — пользователь с role=admin)
- Проверка: http://localhost:8000/health

## Тесты

Тесты на бизнес-правила работают с отдельной базой (`TEST_DATABASE_URL`) и Redis:

```bash
docker compose --profile test up -d db-test redis-test
uv run pytest
```

## Устройство

- `app/models/` — таблицы; `migrations/` — миграции Alembic (`0002` — стартовые данные).
- `app/routers/` — эндпоинты `/api/v1/...`; `app/schemas/` — схемы запросов и ответов.
- `app/rules.py` — бизнес-правила: места, пересечения, возвраты, комиссия, рейтинг.
- `app/admin.py` — админка SQLAdmin, экстренная отмена брони, журнал действий.
- `app/payments.py` — `PaymentProvider` и `MockPaymentProvider`.
- `app/worker.py` — фоновые задачи arq; `app/notify.py` — уведомления и web push.
- `app/errors.py`, `app/pagination.py` — единый формат ошибок и списков.
