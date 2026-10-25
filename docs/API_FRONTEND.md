# API «Конок» — для фронтенда

## 1. Адреса

| Что | Адрес |
|---|---|
| API | `http://<сервер>/api/v1/...` (локально — `http://localhost:8000/api/v1`) |
| Swagger (все эндпоинты, схемы, «Try it out») | `http://<сервер>/docs` |
| OpenAPI JSON (для генерации клиента) | `http://<сервер>/openapi.json` |
| Фото | `http://<сервер>/media/<имя>` — в ответах API поле `url` уже содержит путь `/media/...` |
| Админ-панель (только admin) | `http://<сервер>/admin` |
| Проверка сервера | `GET /health` → `{"status": "ok"}` |

Запросы с другого домена разрешены только для доменов из `CORS_ORIGINS` на сервере.

## 2. Общие правила

- **JSON** в запросах и ответах; загрузка файлов — `multipart/form-data`, поле `file`.
- **Деньги** — строки с двумя знаками: `"1500.00"`. Валюта всегда KGS, поля валюты нет.
- **Даты** — `YYYY-MM-DD`; **время** — ISO 8601 в UTC (`2026-10-24T17:54:18Z`).
  Правила (заезд 13:00, выезд 12:00, отмена) считаются по времени Бишкека (`Asia/Bishkek`, UTC+6).
- **Период брони** `[start_date, end_date)`: `end_date` — день выезда / возврата авто,
  в оплату не входит. 2 ночи = `start_date: 2026-11-10`, `end_date: 2026-11-12`.
- **Переводы** — отдельные поля на каждый язык: `name_ky`, `name_ru`, `name_en`,
  `description_ky` … Любое поле может быть `null` (не заполнено) — показывайте другой язык,
  например: текущий → `ru` → `ky` → `en`. Пользователь сам заполняет свои тексты на нужных
  языках, недостающие переводы дописывает admin.
- **Город** — только через `locality_id` (список: `GET /localities`).

## 3. Авторизация: регистрация → код из письма → JWT

```http
POST /api/v1/auth/register
{"email": "aigul@example.com", "password": "password123", "full_name": "Айгуль"}
→ 201 {"message": "Код подтверждения отправлен на email"}
```

На email приходит 6-значный код (действует 10 минут).

```http
POST /api/v1/auth/verify-email
{"email": "aigul@example.com", "code": "463521"}
→ 200 {"access_token": "eyJ...", "refresh_token": "kq3...", "token_type": "bearer"}
```

Дальше во всех запросах: `Authorization: Bearer <access_token>`.

| Действие | Запрос |
|---|---|
| Вход | `POST /auth/login` `{"email", "password"}` → пара токенов |
| Код ещё раз | `POST /auth/resend-code` `{"email"}` → 202 |
| Обновить токены | `POST /auth/refresh` `{"refresh_token"}` → новая пара; старый refresh больше не работает |
| Выход | `POST /auth/logout` `{"refresh_token"}` → 204 |
| Мой профиль | `GET /users/me` |

- `access_token` живёт 30 минут, `refresh_token` — 30 дней. На 401 — вызовите `/auth/refresh`;
  если и он 401 — отправьте пользователя на вход.
- Вход до подтверждения email → 403 с `code: "email_not_confirmed"` (покажите ввод кода).
- Заблокированный admin пользователь → 403 `user_blocked` при входе и 401 на любой запрос.
- Ограничения: 10 попыток входа за 15 минут, код — не чаще раза в минуту и до 5 писем в час,
  5 попыток ввода кода. Превышение → 429.

## 4. Формат ошибок

Все ошибки — один формат:

```json
{"error": {"code": "conflict", "message": "Недостаточно свободных мест на эти даты"}}
```

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `bad_request` | нарушено правило (дата в прошлом, неверный PIN …) |
| 401 | `unauthorized` | нет токена / токен недействителен |
| 403 | `forbidden`, `email_not_confirmed`, `user_blocked` | нет прав |
| 404 | `not_found` | не найдено или чужой ресурс |
| 409 | `conflict` | конфликт с состоянием: нет мест, даты закрыты, бронь уже отменена |
| 413 | `file_too_large` | файл больше 10 МБ |
| 422 | `validation_error` | неверные поля; список в `details` |
| 429 | `too_many_requests` | слишком много попыток |

```json
{"error": {"code": "validation_error", "message": "Ошибка в данных запроса",
  "details": [{"type": "missing", "loc": ["body", "guests_count"], "msg": "Field required"}]}}
```

`message` — по-русски, для отладки. В интерфейсе показывайте свой текст по `code` и HTTP-статусу.

## 5. Пагинация

Все списки: `?limit=20&offset=0` (`limit` от 1 до 100, по умолчанию 20).

```json
{"items": [ ... ], "total": 57, "limit": 20, "offset": 0}
```

Не пагинируются (возвращают массив / объект): календари `availability` и закрытые даты хозяина.

## 6. Статусы и значения

**Бронь** (`booking.status`):

| Статус | Значение |
|---|---|
| `pending_payment` | идёт оплата; места уже заняты. Не оплачена за 30 минут — бронь удаляется |
| `confirmed` | оплачена |
| `in_progress` | заселение / передача авто подтверждены PIN |
| `completed` | завершена (в 12:00 дня выезда) |
| `cancelled_by_guest` | отменена гостем |
| `cancelled_by_admin` | экстренная отмена через поддержку |
| `failed` | к 12:00 дня выезда заселения по PIN не было |

| Поле | Значения |
|---|---|
| `booking.type` | `stay` (жильё), `vehicle` (авто) |
| `payment.status` | `pending`, `paid`, `failed` |
| `ticket.status` | `new`, `in_progress`, `waiting_reply`, `resolved`, `closed` |
| `user.role` | `user`, `admin` |
| `language` (отзыв) | `ky`, `ru`, `en` |

**Уведомления** (`notification.type` и `data`):

| `type` | Кому | `data` |
|---|---|---|
| `host_reminder` | хозяину: сразу после оплаты и каждые 8 ч, пока не нажал «Вижу, жду гостя» | `{"booking_id"}` |
| `booking_cancelled` | при отмене гостем — хозяину / владельцу авто; admin — гостю и хозяину | `{"booking_id", "by": "guest" \| "admin"}` |
| `new_message` | второму участнику чата | `{"conversation_id", "message_id"}` |
| `support_reply` | автору обращения, когда ответил admin | `{"ticket_id"}` |

## 7. Сценарии гостя

### 7.1. Документы (KYC)

Бронировать можно только после проверки документа admin; авто — ещё и с проверенными правами.

```http
POST /api/v1/users/me/id-document          (multipart, поле file: JPEG / PNG / WebP / PDF до 10 МБ)
POST /api/v1/users/me/driver-license       (для аренды авто)
GET  /api/v1/users/me
→ {"is_verified": false, "has_id_document": true, "is_driver_license_verified": false,
   "has_driver_license": false, "checkin_pin": "568136", ...}
```

Пока `is_verified: false` — бронирование вернёт 403. `checkin_pin` — PIN заселения, который гость
называет хозяину при заезде (показывайте в личном кабинете).

### 7.2. Поиск жилья

```http
GET /api/v1/localities                                   → локации (Каракол и др.)
GET /api/v1/hosts?locality_id=1                          → хозяева (карточки с фото и рейтингом)
GET /api/v1/hosts/3                                      → карточка хозяина
GET /api/v1/hosts/3/availability?start_date=2026-11-10&end_date=2026-11-12
→ [{"date": "2026-11-10", "free_seats": 3, "is_blocked": false},
   {"date": "2026-11-11", "free_seats": 1, "is_blocked": false}]
GET /api/v1/hosts/3/activities                           → активности хозяина
GET /api/v1/hosts/3/reviews                              → отзывы
```

В карточке: `rating` — средняя оценка (`null`, пока нет ни одного отзыва; тогда показывайте
«0 отзывов»), `reviews_count`, `on_platform_since` — «на платформе с». Точного адреса в каталоге нет.

### 7.3. Бронь и оплата

```http
POST /api/v1/bookings/stay
{"host_id": 3, "start_date": "2026-11-10", "end_date": "2026-11-12", "guests_count": 2,
 "activities": [{"activity_id": 1, "persons": 2}]}
→ 201 {
  "booking": {"id": 1, "status": "pending_payment", "amount": "6000.00", ...},
  "payment": {"id": 1, "amount": "7600.00", "status": "pending", ...},
  "payment_url": null
}
```

- Стоимость = цена за человека × гости × ночи; активности — цена × люди (не больше гостей).
- 409, если не хватает мест, даты закрыты хозяином или у гостя уже есть проживание на эти даты.
- Места заняты сразу. Оплатите в течение 30 минут, иначе бронь удалится.

**Оплата (сейчас — имитация).** `payment_url` = `null`; вместо платёжной страницы вызовите:

```http
POST /api/v1/payments/1/mock-pay
→ 200 {"id": 1, "status": "confirmed", "address": "ул. Абдрахманова 1", ...}
```

Работает только при `PAYMENT_PROVIDER=mock` на сервере. Повторный вызов возвращает то же.
После оплаты гость видит точный адрес хозяина (`address`).

Передумал до оплаты — `DELETE /api/v1/bookings/1` → 204 (бронь удаляется, места освобождаются).

### 7.4. Мои брони, отмена

```http
GET  /api/v1/bookings/my?booking_status=confirmed
GET  /api/v1/bookings/1
POST /api/v1/bookings/1/cancel   {"reason": "изменились планы"}
→ 200 {"status": "cancelled_by_guest", "refund_amount": "...", ...}
```

Возврат за проживание / аренду — от 13:00 дня заезда (время Бишкека):

| До заезда | Возврат |
|---|---|
| 72 ч и больше | 100% |
| от 48 до 72 ч | 80% |
| меньше 48 ч | 60% |

Активности возвращаются полностью. С 13:00 дня заезда отменить нельзя (409).
Шкала одинаковая для жилья и авто.

### 7.5. Докупить активности

```http
POST /api/v1/bookings/1/activities   {"activities": [{"activity_id": 2, "persons": 1}]}
→ 201 {"booking": {...}, "payment": {"id": 2, ...}, "payment_url": null}
POST /api/v1/payments/2/mock-pay
```

Только к оплаченной брони проживания (`confirmed` / `in_progress`).

### 7.6. Аренда авто

```http
GET  /api/v1/vehicles?locality_id=1
GET  /api/v1/vehicles/5/availability?start_date=2026-11-10&end_date=2026-11-13
     → [{"date": "2026-11-10", "is_free": true}, ...]
POST /api/v1/bookings/vehicle   {"vehicle_id": 5, "start_date": "2026-11-10", "end_date": "2026-11-13"}
POST /api/v1/payments/{payment.id}/mock-pay
```

Цена — за день. Нужны `is_verified` и `is_driver_license_verified`. Время получения авто гость
и владелец обсуждают в чате. Передача авто — по PIN гостя, как заселение.

### 7.7. Чат, жалоба

```http
POST /api/v1/conversations   {"owner_id": 3}             → чат с хозяином / владельцем авто (или существующий)
GET  /api/v1/conversations                               → мои чаты
POST /api/v1/conversations/1/messages   {"text": "Салам! Приедем в 15:00"}
GET  /api/v1/conversations/1/messages                    → новые первыми
POST /api/v1/complaints   {"user_id": 3, "message_id": 10, "text": "Спам"}   (message_id необязателен)
```

У сообщения: `text` — оригинал, `text_ky` / `text_ru` / `text_en` — автоперевод.
Перевод `null` (ошибка перевода) — показывайте оригинал.

### 7.8. Отзывы, избранное, поддержка

```http
POST /api/v1/hosts/3/reviews      {"rating": 5, "text": "Отлично!", "language": "ru"}
POST /api/v1/vehicles/5/reviews   {"rating": 4, "text": "...", "language": "ru"}
```

Отзыв может оставить любой пользователь; нельзя — о себе / своём авто. Текст пишется на одном
языке (`language`), переводы добавляет admin.

```http
POST   /api/v1/favorites   {"place_id": 1}      (ровно одно: host_id, place_id или vehicle_id)
GET    /api/v1/favorites
DELETE /api/v1/favorites/7

POST /api/v1/support/tickets   {"description": "Вопрос по брони", "booking_id": 1}   (booking_id необязателен)
GET  /api/v1/support/tickets/1                   → обращение с историей сообщений
POST /api/v1/support/tickets/1/messages   {"text": "..."}
```

### 7.9. Уведомления и web push

```http
GET  /api/v1/notifications?unread_only=true
POST /api/v1/notifications/15/read
```

Web push (работает только на HTTPS-сайте или на `localhost`):

1. `GET /api/v1/notifications/push/public-key` → `{"public_key": "BF9K..."}`
2. В браузере: `registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: <public_key>})`
3. `POST /api/v1/notifications/push/subscriptions` — тело = `subscription.toJSON()`:
   `{"endpoint": "https://...", "keys": {"p256dh": "...", "auth": "..."}}` → 204
4. Service worker получает JSON `{"id", "type", "data"}` — те же поля, что в `GET /notifications`.
5. При выходе: `POST /api/v1/notifications/push/unsubscribe` `{"endpoint": "..."}`.

## 8. Сценарии хозяина и владельца авто

### 8.1. Профиль хозяина

```http
POST /api/v1/hosts/me
{"locality_id": 1, "address": "ул. Абдрахманова 1", "capacity": 3, "price_per_person": "1500.00",
 "description_ru": "Юрта у реки", "description_en": "Yurt by the river"}
→ 201 {"user_id": 3, "is_published": true, "photos": [], ...}
```

Анкета отправляется целиком одним запросом (черновиков нет). В каталоге хозяин появится, когда
admin одобрит его документы. Изменить — `PATCH /hosts/me` (только нужные поля).

```http
POST   /api/v1/hosts/me/photos          (multipart, поле file: JPEG / PNG / WebP)
DELETE /api/v1/hosts/me/photos/4
```

### 8.2. Календарь

```http
GET    /api/v1/hosts/me/blocked-dates                  → {"dates": ["2026-12-31"]}
POST   /api/v1/hosts/me/blocked-dates   {"dates": ["2026-12-30", "2026-12-31"]}
DELETE /api/v1/hosts/me/blocked-dates/2026-12-30
```

Закрыть можно только свободную дату — без броней (иначе 409).

### 8.3. Активности

```http
GET  /api/v1/tags                                       → теги (создаёт admin)
POST /api/v1/activities
{"title_ru": "Конная прогулка", "description_ru": "...", "price_per_person": "800.00", "tag_ids": [1]}
GET   /api/v1/activities/my
PATCH /api/v1/activities/1   {"is_active": false}      → скрыть
POST  /api/v1/activities/1/photos
```

Название — хотя бы на одном языке. Цена обязательна (больше 0).

### 8.4. Авто

```http
POST  /api/v1/vehicles   {"locality_id": 1, "title": "Toyota RAV4", "price_per_day": "3000.00", "description_ru": "..."}
GET   /api/v1/vehicles/my
PATCH /api/v1/vehicles/5   {"is_published": false}
POST  /api/v1/vehicles/5/photos
```

### 8.5. Входящие брони

```http
GET  /api/v1/bookings/incoming?booking_status=confirmed     → оплаченные брони моего жилья и авто
POST /api/v1/bookings/1/acknowledge                          → «Вижу, жду гостя»
POST /api/v1/bookings/1/check-in   {"pin": "568136"}          → заселение / передача авто
```

- Неоплаченные брони (`pending_payment`) хозяину не видны.
- `host_amount` в брони — сколько причитается хозяину: оплачено − возвращено гостю − 10% комиссии.
- «Вижу, жду гостя» — отметка (`host_acknowledged_at`), статус брони не меняется.
  Пока её нет, хозяину каждые 8 часов приходит напоминание.
- PIN гостя принимается с дня заезда до дня выезда; попыток сколько угодно.
  Неверный PIN → 400.
- Хозяин не может отменить бронь. Если не может принять гостя — обращение в поддержку
  (`POST /support/tickets` с `booking_id`); отменяет только admin.

## 9. Admin — админ-панель `/admin`

Вход — email и пароль пользователя с ролью `admin` (первого admin создаёт скрипт
`scripts/create_admin.py`). Все действия пишутся в «Журнал действий».

| Раздел | Что делает admin |
|---|---|
| Пользователи | галочки «Документ (ID) одобрен», «Права одобрены», «Активен» (снять — заблокировать); ссылки на загруженные документы |
| Брони | фильтр «Хозяин не нажал «Вижу, жду гостя»» (позвонить хозяину); кнопки экстренной отмены — «причина уважительная» / «неуважительная» |
| Обращения, Ответы в обращения | статус обращения; ответ — создать «Ответ в обращение» (автору придёт уведомление) |
| Жалобы | отметка «решено» |
| Места, Фото мест | создание мест с фото |
| Теги, Регионы, Локации | справочники (переводы на 3 языка) |
| Хозяева, Активности, Авто, Отзывы | переводы текстов, которые пользователь не заполнил |
| Настройки | `commission_rate` (комиссия), `checkin_time`, `checkout_time`, `payment_minutes` |

Экстренная отмена (только оплаченная бронь до заселения): гостю — полный возврат, хозяину — ничего.
При неуважительной причине — скрытая оценка 1 в рейтинг хозяина / авто (входит в среднее, не
видна как отзыв и не входит в счётчик) и даты брони закрываются в его календаре.
