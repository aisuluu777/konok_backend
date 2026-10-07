-- =====================================================================
-- «Конок» — схема БД MVP
-- СУБД: PostgreSQL 14+
-- Версия 0.4. Основа: ТЗ v1.0, решения из обсуждения, дизайн KON-1…KON-24,
-- задачи бэкенда KON-25…KON-40.
-- =====================================================================
--
-- ИЗМЕНЕНИЯ В 0.4
--  • Роли аккаунта: user, moderator, admin. «Хозяин» и «владелец машины» — не роли,
--    а возможности: user с host_profile и/или с машинами. Любой user может бронировать.
--  • Запрещена бронь проживания у самого себя (bookings_not_self_chk).
--
-- ИЗМЕНЕНИЯ В 0.3
--  • Брони хозяина и машины — одна таблица bookings с полем type (stay | vehicle).
--    Таблица vehicle_bookings удалена; в payments, payouts, reviews,
--    support_tickets вместо двух ссылок — одна booking_id.
--  • Занятия создаёт сам хозяин (activities.host_id, название, описание, цена).
--    Справочник занятий и host_activities удалены.
--  • Теги: список ведёт администратор (tags), хозяин выбирает теги для своих
--    занятий (activity_tags). Фильтр каталога по занятиям = фильтр по тегам.
--  • В купленном занятии хранится снимок названия и цены.
--  • reviews.vehicle_id удалён: машина берётся из брони.
--
-- ИЗМЕНЕНИЯ В 0.2
--  • Удалены таблицы: languages, user_languages, sms_codes, host_photos,
--    host_blocked_dates, booking_nights, vehicle_photos, insurance_policies, reports.
--  • Языки, фото, заблокированные даты — массивы. SMS-коды — в кэше (Redis).
--  • У брони может быть несколько платежей (докупка занятий); комиссия на платёж;
--    выплата объединяет платежи.
--  • Чат: guest_id + provider_id. Жалобы — через support_tickets.
--
-- ЗАЛОЖЕННЫЕ РЕШЕНИЯ
--  • Роли аккаунта: user, moderator, admin. Хозяин = user с host_profile,
--    владелец машины = user с машиной; один пользователь может быть и тем, и другим.
--  • KYC проходят все пользователи после регистрации.
--    В MVP — ручная проверка модератором (паспорт + селфи).
--    Түндүк и внешний KYC-провайдер — позже (поле provider готово).
--  • Визит всегда с ночёвкой, питание включено.
--    Цена хозяина — за человека за ночь, в сомах.
--  • Бронь подтверждается автоматически после оплаты.
--    Хозяин не может отклонить или отменить бронь.
--  • На время оплаты места удерживаются (статус hold, срок в settings).
--  • Заезд с 13:00, выезд до 11:00 — одинаково для всех хозяев (settings).
--    Гость указывает время прибытия не раньше 13:00.
--  • Точный адрес хозяина виден гостю только после оплаты.
--  • Хозяин блокирует только целый день и только если на него нет активных броней.
--  • Пересекающиеся брони запрещены туристу только для проживания:
--    машину можно арендовать одновременно с проживанием.
--  • Занятия:
--      – создаёт хозяин, цена за человека > 0 (бесплатное занятие хозяин
--        упоминает в «О себе»); количество не ограничено;
--      – купленное занятие не удаляется, а скрывается (is_active = false);
--      – занятие не привязано к дню визита;
--      – покупается вместе с бронью или докупается, пока бронь confirmed / in_progress;
--      – отдельно не отменяется; деньги возвращаются только при отмене всей брони.
--  • Комиссия удерживается из выплаты хозяину (single-fee).
--    Ставка фиксируется в брони при создании и действует для всех её платежей.
--  • Заселение: гость получает PIN, хозяин его вводит.
--    В этот момент создаётся выплата по всем оплаченным платежам брони.
--    Занятие, докупленное после заселения, выплачивается отдельной выплатой.
--  • Неявка (не отменил и не приехал) — хозяин получает оплату.
--  • Отмена туристом в MVP — полный возврат всех платежей брони.
--  • Страховки в демо нет. Полис будет принадлежать туристу и покрывать период,
--    а не отдельную бронь.
--  • Чат 1-на-1 доступен до брони; один диалог на пару «гость — хозяин/владелец машины».
--  • Жалобы и обращения хозяина на туриста — через поддержку.
--  • Транспорт: каталог и бронь есть в демо, хотя по ТЗ это за MVP.
--
-- НЕ РЕШЕНО (в схеме допущение, помечено «ОТКРЫТО»)
--  • Срок бесплатной отмены (сейчас полный возврат всегда).
--  • Логика кнопки «Добавить точку» на странице места — таблицы нет.
--  • Процент комиссии, валюта оплаты и конвертация.
--  • Страховка: кого покрывает полис, как продлевается при новой брони, кто платит.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;  -- нужен для запрета пересекающихся броней

-- ---------------------------------------------------------------------
-- 1. Типы
-- ---------------------------------------------------------------------
CREATE TYPE user_role        AS ENUM ('user', 'moderator', 'admin');  -- хозяин и владелец машины — не роли
CREATE TYPE user_status      AS ENUM ('active', 'blocked');
CREATE TYPE kyc_status       AS ENUM ('not_started', 'pending', 'approved', 'rejected', 'resubmission');
CREATE TYPE resident_type    AS ENUM ('kg_resident', 'foreigner');

CREATE TYPE booking_type     AS ENUM ('stay', 'vehicle');   -- проживание у хозяина | аренда машины

CREATE TYPE booking_status AS ENUM (
  'hold',                  -- места удержаны, ждём оплату
  'confirmed',             -- оплачено, подтверждено автоматически
  'in_progress',           -- PIN введён, гость заселён (для машины — аренда идёт)
  'completed',             -- визит / аренда завершены
  'cancelled_by_guest',    -- отмена туристом, полный возврат
  'cancelled_by_support',  -- экстренная отмена сотрудником
  'expired',               -- удержание истекло без оплаты
  'no_show'                -- гость не приехал и не отменил, хозяин получает оплату
);

CREATE TYPE payment_status   AS ENUM ('pending', 'succeeded', 'failed', 'refunded', 'partially_refunded');
CREATE TYPE refund_status    AS ENUM ('pending', 'succeeded', 'failed');
CREATE TYPE payout_status    AS ENUM ('pending', 'paid', 'failed');
CREATE TYPE review_status    AS ENUM ('published', 'hidden');
CREATE TYPE ticket_status    AS ENUM ('new', 'in_progress', 'waiting_reply', 'resolved', 'closed');

-- ---------------------------------------------------------------------
-- 2. Справочники и настройки
-- ---------------------------------------------------------------------
CREATE TABLE regions (
  id    SMALLSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

-- Теги занятий. Список ведёт администратор, хозяин только выбирает.
CREATE TABLE tags (
  id    SMALLSERIAL PRIMARY KEY,
  name  TEXT NOT NULL UNIQUE
);

-- Настройки платформы. Изменение не влияет на уже созданные брони
-- (цена и ставка комиссии копируются в бронь).
CREATE TABLE settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  updated_by  BIGINT,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 3. Пользователи и KYC
-- ---------------------------------------------------------------------
-- SMS-коды подтверждения телефона хранятся в кэше (Redis):
--   ключ по телефону, TTL = срок жизни кода, счётчик попыток,
--   отдельный ключ с TTL 45 с — запрет повторной отправки.
CREATE TABLE users (
  id                 BIGSERIAL PRIMARY KEY,
  role               user_role   NOT NULL DEFAULT 'user',
  status             user_status NOT NULL DEFAULT 'active',
  first_name         TEXT NOT NULL,
  last_name          TEXT NOT NULL,
  email              TEXT NOT NULL,
  phone              TEXT NOT NULL,
  password_hash      TEXT NOT NULL,             -- только хеш, пароль открыто не хранится
  phone_verified_at  TIMESTAMPTZ,
  terms_accepted_at  TIMESTAMPTZ NOT NULL,      -- согласие с условиями и политикой
  kyc_status         kyc_status NOT NULL DEFAULT 'not_started',  -- копия статуса последней проверки
  checkin_pin        TEXT CHECK (checkin_pin ~ '^[0-9]{6}$'),  -- PIN заселения (R-23), выдаётся при одобрении KYC;
                                                               -- хранится открыто — решение MVP, не окончательное
  languages          TEXT[] NOT NULL DEFAULT '{}',  -- коды языков: ky, ru, en …; список допустимых — в коде
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX users_email_uq ON users (lower(email));
CREATE UNIQUE INDEX users_phone_uq ON users (phone);

ALTER TABLE settings
  ADD CONSTRAINT settings_updated_by_fk FOREIGN KEY (updated_by) REFERENCES users(id);

-- Проверки личности. Файлы документов отдаются только владельцу и модератору.
CREATE TABLE kyc_verifications (
  id                  BIGSERIAL PRIMARY KEY,
  user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  resident_type       resident_type,              -- у хозяина обязателен, у гостя может быть пустым
  provider            TEXT NOT NULL DEFAULT 'manual',  -- manual | tunduk | внешний провайдер (позже)
  provider_reference  TEXT,
  status              kyc_status NOT NULL DEFAULT 'pending',
  passport_file_url   TEXT,
  selfie_file_url     TEXT,
  rejection_reason    TEXT,
  reviewed_by         BIGINT REFERENCES users(id),
  submitted_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  checked_at          TIMESTAMPTZ
);
CREATE INDEX kyc_user_idx   ON kyc_verifications (user_id, submitted_at DESC);
CREATE INDEX kyc_queue_idx  ON kyc_verifications (status) WHERE status = 'pending';

-- ---------------------------------------------------------------------
-- 4. Профили и занятия
-- ---------------------------------------------------------------------
CREATE TABLE guest_profiles (
  user_id     BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  country     TEXT,
  birth_date  DATE,                 -- хозяин видит только возраст, вычисленный из даты
  avatar_url  TEXT,
  interests   TEXT[] NOT NULL DEFAULT '{}',
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Профиль создаётся одним запросом, когда анкета заполнена целиком (черновиков нет, R-29).
CREATE TABLE host_profiles (
  user_id                 BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  region_id               SMALLINT NOT NULL REFERENCES regions(id),
  settlement              TEXT NOT NULL,             -- населённый пункт, виден публично
  address                 TEXT NOT NULL,             -- точный адрес: гостю только после оплаты
  latitude                NUMERIC(9,6),
  longitude               NUMERIC(9,6),
  about                   TEXT,
  avatar_url              TEXT,
  photos                  TEXT[] NOT NULL DEFAULT '{}',  -- URL фото; порядок в массиве = порядок показа
  video_url               TEXT,                      -- MP4 до 2 минут, необязательно
  video_duration_sec      SMALLINT CHECK (video_duration_sec BETWEEN 1 AND 120),
  capacity                SMALLINT NOT NULL CHECK (capacity > 0),   -- максимум гостей на ночь
  price_per_person_night  NUMERIC(12,2) NOT NULL CHECK (price_per_person_night > 0),  -- сом, питание включено
  blocked_dates           DATE[] NOT NULL DEFAULT '{}',  -- дни, закрытые хозяином по личным причинам
  is_guide                BOOLEAN NOT NULL DEFAULT false,   -- метка «гид» в каталоге
  is_published            BOOLEAN NOT NULL DEFAULT false,   -- в каталоге только при users.kyc_status = approved
  rating_avg              NUMERIC(3,2) NOT NULL DEFAULT 0,
  reviews_count           INTEGER NOT NULL DEFAULT 0,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX host_profiles_catalog_idx ON host_profiles (region_id) WHERE is_published;

-- Изменения, влияющие на доступность (blocked_dates, capacity), делаются
-- под той же блокировкой строки хозяина, что и бронирование (см. раздел 6):
--   1) SELECT … FROM host_profiles WHERE user_id = :host FOR UPDATE;
--   2) отдельным запросом проверить активные брони:
--      – блокировка дня: на этот день нет броней hold / confirmed / in_progress;
--      – уменьшение capacity: ни на одну будущую ночь не забронировано больше новой capacity;
--   3) UPDATE host_profiles.
-- При записи blocked_dates удаляются даты раньше текущей, чтобы массив не рос.

-- Занятия создаёт сам хозяин. Количество не ограничено.
-- Купленное занятие не удаляется, а скрывается: is_active = false.
CREATE TABLE activities (
  id                BIGSERIAL PRIMARY KEY,
  host_id           BIGINT NOT NULL REFERENCES host_profiles(user_id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  description       TEXT,
  price_per_person  NUMERIC(12,2) NOT NULL CHECK (price_per_person > 0),  -- сом; бесплатных занятий нет
  is_active         BOOLEAN NOT NULL DEFAULT true,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX activities_host_idx ON activities (host_id) WHERE is_active;

-- Теги занятия: хозяин выбирает из tags.
CREATE TABLE activity_tags (
  activity_id  BIGINT   NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
  tag_id       SMALLINT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  PRIMARY KEY (activity_id, tag_id)
);
CREATE INDEX activity_tags_tag_idx ON activity_tags (tag_id);

-- Фильтр каталога по тегам: хозяева, у которых есть активное занятие с одним из тегов.
--   … WHERE EXISTS (
--         SELECT 1 FROM activities a
--         JOIN activity_tags t ON t.activity_id = a.id
--         WHERE a.host_id = hp.user_id AND a.is_active AND t.tag_id = ANY (:tag_ids))

-- ---------------------------------------------------------------------
-- 5. Места (заполняются сидами, без админки)
-- ---------------------------------------------------------------------
CREATE TABLE places (
  id                 BIGSERIAL PRIMARY KEY,
  slug               TEXT NOT NULL UNIQUE,         -- tash-rabat, issyk-kul …
  name               TEXT NOT NULL,
  region_id          SMALLINT NOT NULL REFERENCES regions(id),
  short_description  TEXT,
  description        TEXT,
  cover_url          TEXT,
  stops              JSONB NOT NULL DEFAULT '[]',  -- «Места для остановки»
  travel_options     JSONB NOT NULL DEFAULT '[]',  -- «Способы путешествия»
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX places_region_idx ON places (region_id);
-- «Хозяева в регионе» = host_profiles с тем же region_id.

-- ---------------------------------------------------------------------
-- 6. Транспорт (в демо; по ТЗ — за пределами MVP)
-- ---------------------------------------------------------------------
CREATE TABLE vehicles (
  id             BIGSERIAL PRIMARY KEY,
  owner_id       BIGINT NOT NULL REFERENCES users(id),   -- любой user
  region_id      SMALLINT NOT NULL REFERENCES regions(id),
  model          TEXT NOT NULL,
  year           SMALLINT,
  seats          SMALLINT,
  transmission   TEXT,
  fuel           TEXT,
  specs          JSONB NOT NULL DEFAULT '{}',    -- прочие характеристики
  included       JSONB NOT NULL DEFAULT '[]',    -- «Что включено»
  photos         TEXT[] NOT NULL DEFAULT '{}',   -- URL фото; порядок в массиве = порядок показа
  price_per_day  NUMERIC(12,2) NOT NULL CHECK (price_per_day > 0),  -- сом
  with_driver    BOOLEAN NOT NULL DEFAULT false,
  is_published   BOOLEAN NOT NULL DEFAULT false,
  rating_avg     NUMERIC(3,2) NOT NULL DEFAULT 0,
  reviews_count  INTEGER NOT NULL DEFAULT 0,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX vehicles_catalog_idx ON vehicles (region_id) WHERE is_published;

-- ---------------------------------------------------------------------
-- 7. Бронирование: проживание у хозяина и аренда машины
-- ---------------------------------------------------------------------
CREATE TABLE bookings (
  id                    BIGSERIAL PRIMARY KEY,
  type                  booking_type NOT NULL,
  guest_id              BIGINT NOT NULL REFERENCES users(id),
  host_id               BIGINT REFERENCES host_profiles(user_id),  -- только stay
  vehicle_id            BIGINT REFERENCES vehicles(id),            -- только vehicle
  start_date            DATE NOT NULL,   -- stay: дата заезда;  vehicle: начало аренды
  end_date              DATE NOT NULL,   -- stay: дата выезда;  vehicle: конец аренды
                                         -- ночей / дней = end_date − start_date
  -- Снимок условий: изменение цены или комиссии не влияет на созданную бронь
  unit_price            NUMERIC(12,2) NOT NULL CHECK (unit_price > 0),
                                         -- stay: за человека за ночь; vehicle: за день
  base_amount           NUMERIC(12,2) NOT NULL,  -- проживание или аренда, без занятий
  commission_rate       NUMERIC(5,4) NOT NULL CHECK (commission_rate >= 0 AND commission_rate < 1),
                                         -- действует для всех платежей брони, включая докупку
  currency              CHAR(3) NOT NULL DEFAULT 'KGS',
  status                booking_status NOT NULL DEFAULT 'hold',
  hold_expires_at       TIMESTAMPTZ NOT NULL,        -- now() + settings.hold_minutes
  confirmed_at          TIMESTAMPTZ,                 -- момент оплаты; с него виден адрес хозяина
  completed_at          TIMESTAMPTZ,
  cancelled_at          TIMESTAMPTZ,
  cancelled_by          BIGINT REFERENCES users(id),
  cancel_reason         TEXT,

  -- Только проживание
  guests_count          SMALLINT CHECK (guests_count > 0),
  arrival_time          TIME,                        -- не раньше settings.checkin_time (13:00)
  host_acknowledged_at  TIMESTAMPTZ,                 -- хозяин нажал «Вижу, жду гостя»
  checkin_pin_attempts  SMALLINT NOT NULL DEFAULT 0,
  checked_in_at         TIMESTAMPTZ,                 -- PIN введён хозяином

  -- Только аренда машины
  pickup_location       TEXT,
  return_location       TEXT,

  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Заполнены ровно поля своего типа
  CONSTRAINT bookings_host_chk     CHECK ((type = 'stay')    = (host_id IS NOT NULL)),
  CONSTRAINT bookings_guests_chk   CHECK ((type = 'stay')    = (guests_count IS NOT NULL)),
  CONSTRAINT bookings_arrival_chk  CHECK ((type = 'stay')    = (arrival_time IS NOT NULL)),
  CONSTRAINT bookings_vehicle_chk  CHECK ((type = 'vehicle') = (vehicle_id IS NOT NULL)),
  CONSTRAINT bookings_pickup_chk   CHECK ((type = 'vehicle') = (pickup_location IS NOT NULL)),
  CONSTRAINT bookings_return_chk   CHECK ((type = 'vehicle') = (return_location IS NOT NULL)),

  CONSTRAINT bookings_dates_chk    CHECK (end_date > start_date),
  CONSTRAINT bookings_not_self_chk CHECK (guest_id <> host_id),   -- нельзя бронировать у себя
  -- stay: цена × гости × ночи;  vehicle: цена × дни (guests_count пуст → 1)
  CONSTRAINT bookings_base_amount_chk
    CHECK (base_amount = unit_price * COALESCE(guests_count, 1) * (end_date - start_date)),

  -- У туриста не может быть пересекающихся активных проживаний (у любых хозяев).
  -- Дата выезда не входит в период, поэтому «выезд 5-го / заезд 5-го» допустим.
  -- Аренда машины в этом запрете не участвует.
  CONSTRAINT bookings_guest_no_overlap EXCLUDE USING gist (
    guest_id WITH =,
    daterange(start_date, end_date, '[)') WITH &&
  ) WHERE (type = 'stay' AND status IN ('hold', 'confirmed', 'in_progress')),

  -- Одну машину нельзя забронировать дважды на пересекающиеся дни
  CONSTRAINT bookings_vehicle_no_overlap EXCLUDE USING gist (
    vehicle_id WITH =,
    daterange(start_date, end_date, '[)') WITH &&
  ) WHERE (type = 'vehicle' AND status IN ('hold', 'confirmed', 'in_progress'))
);
CREATE INDEX bookings_host_idx     ON bookings (host_id, start_date)    WHERE type = 'stay';
CREATE INDEX bookings_vehicle_idx  ON bookings (vehicle_id, start_date) WHERE type = 'vehicle';
CREATE INDEX bookings_guest_idx    ON bookings (guest_id, start_date DESC);
CREATE INDEX bookings_hold_idx     ON bookings (hold_expires_at) WHERE status = 'hold';   -- фоновое истечение удержания
CREATE INDEX bookings_ack_idx      ON bookings (start_date)
  WHERE type = 'stay' AND status = 'confirmed' AND host_acknowledged_at IS NULL;          -- напоминание хозяину / автотикет

-- Свободные места хозяина по ночам периода [:in, :out):
--
--   SELECT n::date                                        AS night,
--          hp.capacity - COALESCE(SUM(b.guests_count), 0) AS free_seats,
--          n::date = ANY (hp.blocked_dates)               AS is_blocked
--   FROM host_profiles hp
--   CROSS JOIN generate_series(:in::date, :out::date - 1, interval '1 day') AS n
--   LEFT JOIN bookings b
--          ON b.type = 'stay'
--         AND b.host_id = hp.user_id
--         AND b.status IN ('hold', 'confirmed', 'in_progress')
--         AND b.start_date <= n::date AND n::date < b.end_date
--   WHERE hp.user_id = :host
--   GROUP BY n, hp.user_id;
--
-- Бронь возможна, если на каждую ночь free_seats >= guests_count и is_blocked = false.
--
-- Защита от перепродажи: в одной транзакции
--   1) SELECT … FROM host_profiles WHERE user_id = :host FOR UPDATE;
--   2) перевести в expired просроченные hold этого хозяина и этого гостя;
--   3) посчитать свободные места запросом выше;
--   4) если везде хватает — вставить bookings.
-- Блокировка строки хозяина не даёт двум запросам одновременно занять последнее место
-- и не даёт хозяину заблокировать день во время бронирования.
-- Для машины перепродажу запрещает bookings_vehicle_no_overlap.

-- Купленные занятия (только у броней type = 'stay'). Не привязаны к дню визита.
-- Строки создаются вместе с платежом; занятие считается купленным, если платёж succeeded.
-- Проверки в API:
--   – бронь типа stay, занятие принадлежит хозяину брони и is_active;
--   – persons <= bookings.guests_count;
--   – докупка только при статусе брони confirmed / in_progress.
-- Отдельно не отменяется: возврат только вместе с отменой брони.
CREATE TABLE booking_activities (
  id                BIGSERIAL PRIMARY KEY,
  booking_id        BIGINT NOT NULL REFERENCES bookings(id),
  activity_id       BIGINT NOT NULL REFERENCES activities(id),
  activity_name     TEXT NOT NULL,                                        -- снимок названия
  persons           SMALLINT NOT NULL CHECK (persons > 0),
  price_per_person  NUMERIC(12,2) NOT NULL CHECK (price_per_person > 0),  -- снимок цены
  amount            NUMERIC(12,2) NOT NULL,
  payment_id        BIGINT NOT NULL,                                      -- FK ниже, после payments
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT booking_activities_amount_chk CHECK (amount = price_per_person * persons)
);
CREATE INDEX booking_activities_booking_idx ON booking_activities (booking_id);
CREATE INDEX booking_activities_payment_idx ON booking_activities (payment_id);

-- ---------------------------------------------------------------------
-- 8. Деньги: выплаты, оплата, возвраты
-- ---------------------------------------------------------------------
-- Выплата объединяет все оплаченные и ещё не выплаченные платежи брони.
-- Проживание: создаётся при вводе PIN (или при неявке гостя); занятие,
-- докупленное после заселения, выплачивается отдельной выплатой сразу после оплаты.
CREATE TABLE payouts (
  id                  BIGSERIAL PRIMARY KEY,
  booking_id          BIGINT NOT NULL REFERENCES bookings(id),
  recipient_id        BIGINT NOT NULL REFERENCES users(id),     -- хозяин или владелец машины
  amount              NUMERIC(12,2) NOT NULL CHECK (amount > 0), -- Σ (payments.amount − payments.commission_amount)
  currency            CHAR(3) NOT NULL DEFAULT 'KGS',
  status              payout_status NOT NULL DEFAULT 'pending',
  provider_reference  TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  paid_at             TIMESTAMPTZ
);
CREATE INDEX payouts_booking_idx ON payouts (booking_id);

-- Проживание: первый платёж = проживание + выбранные занятия, последующие — докупка занятий.
-- Аренда машины: один успешный платёж (проверка в API).
-- commission_amount = round(amount × bookings.commission_rate, 2).
CREATE TABLE payments (
  id                       BIGSERIAL PRIMARY KEY,
  booking_id               BIGINT NOT NULL REFERENCES bookings(id),
  payer_id                 BIGINT NOT NULL REFERENCES users(id),
  provider                 TEXT NOT NULL DEFAULT 'mock',   -- mock (демо) | mbank
  provider_transaction_id  TEXT,
  idempotency_key          TEXT NOT NULL UNIQUE,           -- повторный запрос не создаёт второй платёж
  amount                   NUMERIC(12,2) NOT NULL CHECK (amount > 0),
  commission_amount        NUMERIC(12,2) NOT NULL CHECK (commission_amount >= 0),
  currency                 CHAR(3) NOT NULL DEFAULT 'KGS', -- ОТКРЫТО: валюта гостя и конвертация
  status                   payment_status NOT NULL DEFAULT 'pending',
  payout_id                BIGINT REFERENCES payouts(id),  -- пусто = ещё не выплачен
  paid_at                  TIMESTAMPTZ,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT payments_commission_chk CHECK (commission_amount < amount)
);
CREATE INDEX payments_booking_idx ON payments (booking_id);
CREATE INDEX payments_unpaid_out_idx ON payments (booking_id)
  WHERE status = 'succeeded' AND payout_id IS NULL;        -- что войдёт в следующую выплату

ALTER TABLE booking_activities
  ADD CONSTRAINT booking_activities_payment_fk FOREIGN KEY (payment_id) REFERENCES payments(id);

-- При отмене брони возвращается каждый её успешный платёж.
CREATE TABLE refunds (
  id            BIGSERIAL PRIMARY KEY,
  payment_id    BIGINT NOT NULL REFERENCES payments(id),
  amount        NUMERIC(12,2) NOT NULL CHECK (amount > 0),  -- в MVP — полная сумма платежа
  reason        TEXT,
  status        refund_status NOT NULL DEFAULT 'pending',
  initiated_by  BIGINT REFERENCES users(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at  TIMESTAMPTZ
);
CREATE INDEX refunds_payment_idx ON refunds (payment_id);

-- ---------------------------------------------------------------------
-- 9. Чат (без перевода в демо; поля под перевод готовы)
-- ---------------------------------------------------------------------
-- Один диалог на пару «гость — хозяин или владелец машины».
-- Роли участников проверяются в API.
CREATE TABLE conversations (
  id               BIGSERIAL PRIMARY KEY,
  guest_id         BIGINT NOT NULL REFERENCES users(id),
  provider_id      BIGINT NOT NULL REFERENCES users(id),   -- хозяин или владелец машины
  last_message_at  TIMESTAMPTZ,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT conversations_not_self_chk CHECK (guest_id <> provider_id),
  CONSTRAINT conversations_pair_uq UNIQUE (guest_id, provider_id)
);
CREATE INDEX conversations_provider_idx ON conversations (provider_id);

CREATE TABLE messages (
  id                   BIGSERIAL PRIMARY KEY,
  conversation_id      BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  sender_id            BIGINT NOT NULL REFERENCES users(id),
  original_text        TEXT NOT NULL,     -- оригинал хранится всегда
  original_language    VARCHAR(8),        -- позже: для автоперевода
  translated_text      TEXT,              -- позже: ошибка перевода не удаляет оригинал
  translated_language  VARCHAR(8),
  is_flagged           BOOLEAN NOT NULL DEFAULT false,  -- подозрение на обход платформы
  read_at              TIMESTAMPTZ,       -- прочитано получателем (диалог 1-на-1)
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation_idx ON messages (conversation_id, created_at);
CREATE INDEX messages_unread_idx ON messages (conversation_id) WHERE read_at IS NULL;

-- ---------------------------------------------------------------------
-- 10. Отзывы и избранное
-- ---------------------------------------------------------------------
-- Отзыв о хозяине или машине — по брони; машина и хозяин берутся из брони.
CREATE TABLE reviews (
  id              BIGSERIAL PRIMARY KEY,
  booking_id      BIGINT NOT NULL REFERENCES bookings(id),
  author_id       BIGINT NOT NULL REFERENCES users(id),
  target_user_id  BIGINT NOT NULL REFERENCES users(id),  -- хозяин или владелец машины
  rating          SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  text            TEXT,
  status          review_status NOT NULL DEFAULT 'published',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT reviews_booking_author_uq UNIQUE (booking_id, author_id)
);
CREATE INDEX reviews_target_idx ON reviews (target_user_id) WHERE status = 'published';
-- Ответный отзыв хозяина туристу — позже (структура позволяет: author = хозяин, target = гость).

CREATE TABLE favorites (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  host_id     BIGINT REFERENCES host_profiles(user_id) ON DELETE CASCADE,
  place_id    BIGINT REFERENCES places(id) ON DELETE CASCADE,
  vehicle_id  BIGINT REFERENCES vehicles(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT favorites_one_target_chk CHECK (num_nonnulls(host_id, place_id, vehicle_id) = 1)
);
CREATE UNIQUE INDEX favorites_host_uq    ON favorites (user_id, host_id)    WHERE host_id IS NOT NULL;
CREATE UNIQUE INDEX favorites_place_uq   ON favorites (user_id, place_id)   WHERE place_id IS NOT NULL;
CREATE UNIQUE INDEX favorites_vehicle_uq ON favorites (user_id, vehicle_id) WHERE vehicle_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 11. Поддержка, журнал, уведомления
-- ---------------------------------------------------------------------
-- Обращения, в том числе жалобы (category = 'complaint'): пользователь
-- сам пишет, на кого и за что жалуется. Хозяин жалуется на туриста так же.
-- Автотикет создаётся, если хозяин не нажал «Вижу, жду гостя» после напоминания.
CREATE TABLE support_tickets (
  id           BIGSERIAL PRIMARY KEY,
  user_id      BIGINT REFERENCES users(id),      -- пусто для автотикета системы
  booking_id   BIGINT REFERENCES bookings(id),
  category     TEXT NOT NULL,                    -- host_unreachable, emergency, booking_problem, complaint …
  description  TEXT,
  status       ticket_status NOT NULL DEFAULT 'new',
  assigned_to  BIGINT REFERENCES users(id),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX support_tickets_queue_idx ON support_tickets (status, created_at);

-- Журнал критических действий: KYC, отмены, возвраты, блокировки, смена настроек.
-- Заменяет ModerationAction из ТЗ.
CREATE TABLE audit_logs (
  id           BIGSERIAL PRIMARY KEY,
  actor_id     BIGINT REFERENCES users(id),   -- пусто = действие системы
  action       TEXT NOT NULL,                 -- kyc_approved, booking_cancelled, refund_created …
  entity_type  TEXT NOT NULL,                 -- user, booking, payment …
  entity_id    BIGINT,
  details      JSONB NOT NULL DEFAULT '{}',
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_logs_entity_idx ON audit_logs (entity_type, entity_id, created_at DESC);

CREATE TABLE notifications (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        TEXT NOT NULL,      -- new_booking, booking_reminder, payment, message, refund …
  channel     TEXT NOT NULL DEFAULT 'in_app',   -- in_app | email | sms
  payload     JSONB NOT NULL DEFAULT '{}',
  sent_at     TIMESTAMPTZ,
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notifications_user_idx ON notifications (user_id, created_at DESC);

-- ---------------------------------------------------------------------
-- 12. Начальные данные
-- ---------------------------------------------------------------------
INSERT INTO regions (name) VALUES
  ('г. Бишкек'), ('г. Ош'),
  ('Чуйская область'), ('Иссык-Кульская область'), ('Нарынская область'),
  ('Таласская область'), ('Джалал-Абадская область'), ('Ошская область'),
  ('Баткенская область');

-- Стартовые теги — бывшие категории занятий из дизайна KON-15.
-- Дальше список ведёт администратор.
INSERT INTO tags (name) VALUES
  ('Верховая езда'), ('Готовка'), ('Ремёсла'), ('Жизнь в юрте'), ('Походы'),
  ('Рыбалка'), ('Культура'), ('Фототуры'), ('Природа');

INSERT INTO settings (key, value) VALUES
  ('hold_minutes', '15'),            -- удержание мест на время оплаты
  ('host_ack_reminder_hours', '24'), -- напоминание хозяину до заезда
  ('checkin_time', '13:00'),         -- заезд с 13:00
  ('checkout_time', '11:00'),        -- выезд до 11:00
  ('commission_rate', '0.1000');     -- ТЕСТОВОЕ значение для имитации (R-33); реальный процент не утверждён
