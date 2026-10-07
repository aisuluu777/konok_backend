# Конок

Платформа социального гостеприимства Кыргызстана — MVP-бэкенд.

## Структура

```
konok/
├── backend/          код проекта
│   ├── app/              приложение FastAPI
│   ├── migrations/       миграции Alembic
│   ├── tests/            тесты pytest
│   └── docs/             схема БД и бизнес-правила (R-xx)
└── .claude/          всё для Claude Code: инструкции (CLAUDE.md), план MVP,
                      агенты, скилл /feature, хуки, настройки — только локально, не в git
```

## Запуск

```bash
cd backend
cp .env.example .env
uv sync
docker compose up -d db redis
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

Тесты: `docker compose up -d db-test redis`, затем `uv run pytest`.

## Docker

Образ приложения — `backend/Dockerfile`. При старте контейнер применяет миграции
и запускает API на порту 8000.

```bash
cd backend
docker build -t konok-backend .
docker run -d -p 8000:8000 --env-file .env -v konok-storage:/app/storage konok-backend
```

- В `.env` нужны `DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`. Адреса БД и Redis должны быть
  доступны из контейнера: `localhost` внутри контейнера — это сам контейнер.
- Том `/app/storage` хранит загруженные фото и видео — без него они пропадут
  при пересоздании контейнера.

Claude Code запускать из корня `konok/`: настройки он берёт из `.claude/`.
