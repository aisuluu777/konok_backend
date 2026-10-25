# Конок

Платформа социального гостеприимства Кыргызстана — бэкенд.

## Структура

```
konok_backend/
├── docs/TZ_Konok_v2_for_Claude.md   техническое задание (с уточнениями владельца)
├── docs/API_FRONTEND.md             API для фронтенда: авторизация, форматы, сценарии
└── backend/                         весь код проекта
    ├── app/                         приложение FastAPI, админка SQLAdmin, воркер arq
    ├── migrations/                  миграции Alembic
    ├── tests/                       тесты pytest на бизнес-правила
    └── scripts/                     служебные скрипты (VAPID-ключи)
```

Запуск, тесты и устройство кода — в [`backend/README.md`](backend/README.md).
