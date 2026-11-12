-- Dasturxon: начальная схема (ТЗ, раздел 9 + служебные таблицы)
-- Все денежные суммы — целые числа в сумах.

CREATE TABLE IF NOT EXISTS users (
  id            BIGSERIAL PRIMARY KEY,
  phone         TEXT NOT NULL UNIQUE,
  role          TEXT NOT NULL CHECK (role IN ('buyer','chef')),
  name          TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  deleted_at    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS otp_codes (
  id          BIGSERIAL PRIMARY KEY,
  phone       TEXT NOT NULL,
  code_hash   TEXT NOT NULL,
  attempts    INT NOT NULL DEFAULT 0,
  used        BOOLEAN NOT NULL DEFAULT FALSE,
  expires_at  TIMESTAMPTZ NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS otp_codes_phone_idx ON otp_codes (phone, created_at DESC);

CREATE TABLE IF NOT EXISTS refresh_tokens (
  id          BIGSERIAL PRIMARY KEY,
  subject     TEXT NOT NULL,              -- 'user:<id>' или 'staff:<id>'
  token_hash  TEXT NOT NULL UNIQUE,
  expires_at  TIMESTAMPTZ NOT NULL,
  revoked     BOOLEAN NOT NULL DEFAULT FALSE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS addresses (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label         TEXT,
  lat           DOUBLE PRECISION NOT NULL,
  lon           DOUBLE PRECISION NOT NULL,
  address_text  TEXT NOT NULL,
  is_default    BOOLEAN NOT NULL DEFAULT FALSE,
  is_deleted    BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Только токены платёжной системы, номера карт не храним (ТЗ 13)
CREATE TABLE IF NOT EXISTS payment_cards (
  id           BIGSERIAL PRIMARY KEY,
  user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider     TEXT NOT NULL CHECK (provider IN ('payme','click')),
  token        TEXT NOT NULL,
  masked_pan   TEXT NOT NULL,
  card_type    TEXT NOT NULL CHECK (card_type IN ('uzcard','humo','payme')),
  is_default   BOOLEAN NOT NULL DEFAULT FALSE,
  is_deleted   BOOLEAN NOT NULL DEFAULT FALSE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chef_profiles (
  user_id              BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  nick                 TEXT NOT NULL UNIQUE,
  first_name           TEXT,
  last_name            TEXT,
  bio                  TEXT,
  photo_url            TEXT,
  cover_url            TEXT,
  district             TEXT,
  address_text         TEXT,
  address_lat          DOUBLE PRECISION,
  address_lon          DOUBLE PRECISION,
  is_online            BOOLEAN NOT NULL DEFAULT FALSE,
  is_verified          BOOLEAN NOT NULL DEFAULT FALSE,
  is_blocked           BOOLEAN NOT NULL DEFAULT FALSE,
  blocked_until        TIMESTAMPTZ,
  block_reason         TEXT,
  verification_status  TEXT NOT NULL DEFAULT 'draft'
                       CHECK (verification_status IN ('draft','pending','approved','rejected')),
  verification_note    TEXT,
  kitchen_photo_urls   TEXT[] NOT NULL DEFAULT '{}',
  ai_verdict           JSONB,
  rating               NUMERIC(3,2) NOT NULL DEFAULT 0,
  reviews_count        INT NOT NULL DEFAULT 0,
  total_orders         INT NOT NULL DEFAULT 0,
  low_rating_flag      BOOLEAN NOT NULL DEFAULT FALSE,
  payout_card_token    TEXT,
  payout_card_masked   TEXT,
  card_type            TEXT CHECK (card_type IN ('uzcard','humo')),
  terms_accepted_at    TIMESTAMPTZ,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dishes (
  id                 BIGSERIAL PRIMARY KEY,
  chef_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name               TEXT NOT NULL,
  description        TEXT,
  category           TEXT NOT NULL CHECK (category IN ('breakfast','lunch','dinner','bakery','drinks','other')),
  price              INT NOT NULL CHECK (price > 0),
  weight             TEXT,
  photo_urls         TEXT[] NOT NULL DEFAULT '{}',
  portions_left      INT,
  type               TEXT NOT NULL CHECK (type IN ('ready','order','both')),
  prep_time_minutes  INT,
  ingredients        TEXT,
  is_vegan           BOOLEAN NOT NULL DEFAULT FALSE,
  is_active          BOOLEAN NOT NULL DEFAULT TRUE,
  is_deleted         BOOLEAN NOT NULL DEFAULT FALSE,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS dishes_chef_idx ON dishes (chef_id);

CREATE TABLE IF NOT EXISTS orders (
  id                    BIGSERIAL PRIMARY KEY,
  buyer_id              BIGINT NOT NULL REFERENCES users(id),
  chef_id               BIGINT NOT NULL REFERENCES users(id),
  status                TEXT NOT NULL CHECK (status IN
                        ('PENDING','CONFIRMED','COOKING','READY','COURIER_ASSIGNED',
                         'IN_DELIVERY','DELIVERED','RATED','CANCELLED')),
  items                 JSONB NOT NULL,
  dish_total            INT NOT NULL,
  delivery_fee_shown    INT NOT NULL,
  delivery_fee_real     INT,
  delivery_buffer       INT,
  platform_commission   INT NOT NULL,
  chef_tax              INT NOT NULL,
  chef_net_amount       INT NOT NULL,
  total_amount          INT NOT NULL,
  address_id            BIGINT REFERENCES addresses(id),
  delivery_lat          DOUBLE PRECISION NOT NULL,
  delivery_lon          DOUBLE PRECISION NOT NULL,
  delivery_address_text TEXT NOT NULL,
  comment               TEXT,
  card_id               BIGINT REFERENCES payment_cards(id),
  payment_id            TEXT,
  payment_status        TEXT NOT NULL DEFAULT 'none'
                        CHECK (payment_status IN ('none','held','charged','cancelled','refunded','partially_refunded')),
  refunded_amount       INT NOT NULL DEFAULT 0,
  yandex_request_id     TEXT,
  courier_name          TEXT,
  courier_phone         TEXT,
  courier_lat           DOUBLE PRECISION,
  courier_lon           DOUBLE PRECISION,
  courier_eta_seconds   INT,
  courier_near_notified BOOLEAN NOT NULL DEFAULT FALSE,
  reminder_sent         BOOLEAN NOT NULL DEFAULT FALSE,
  cancel_reason         TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  confirmed_at          TIMESTAMPTZ,
  ready_at              TIMESTAMPTZ,
  delivered_at          TIMESTAMPTZ,
  cancelled_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS orders_buyer_idx ON orders (buyer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_chef_idx ON orders (chef_id, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders (status);

CREATE TABLE IF NOT EXISTS order_status_history (
  id          BIGSERIAL PRIMARY KEY,
  order_id    BIGINT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  status      TEXT NOT NULL,
  note        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS reviews (
  id          BIGSERIAL PRIMARY KEY,
  order_id    BIGINT NOT NULL UNIQUE REFERENCES orders(id),
  buyer_id    BIGINT NOT NULL REFERENCES users(id),
  chef_id     BIGINT NOT NULL REFERENCES users(id),
  rating      INT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  tags        TEXT[] NOT NULL DEFAULT '{}',
  comment     TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS chef_payouts (
  id                 BIGSERIAL PRIMARY KEY,
  chef_id            BIGINT NOT NULL REFERENCES users(id),
  kind               TEXT NOT NULL CHECK (kind IN ('weekly','early')),
  amount             INT NOT NULL,
  commission_1pct    INT NOT NULL DEFAULT 0,
  net_amount         INT NOT NULL,
  week_start         DATE,
  week_end           DATE,
  status             TEXT NOT NULL CHECK (status IN ('pending','processing','done','failed')),
  bank_transaction_id TEXT,
  error              TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  paid_at            TIMESTAMPTZ
);

-- kind: order — начисление за заказ, refund — удержание при возврате
CREATE TABLE IF NOT EXISTS chef_earnings (
  id            BIGSERIAL PRIMARY KEY,
  chef_id       BIGINT NOT NULL REFERENCES users(id),
  order_id      BIGINT REFERENCES orders(id),
  kind          TEXT NOT NULL DEFAULT 'order' CHECK (kind IN ('order','refund')),
  gross_amount  INT NOT NULL,
  commission    INT NOT NULL,
  tax           INT NOT NULL,
  net_amount    INT NOT NULL,
  week_start    DATE NOT NULL,
  is_paid       BOOLEAN NOT NULL DEFAULT FALSE,
  payout_id     BIGINT REFERENCES chef_payouts(id),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chef_earnings_chef_idx ON chef_earnings (chef_id, is_paid);
CREATE UNIQUE INDEX IF NOT EXISTS chef_earnings_order_uniq ON chef_earnings (order_id) WHERE kind = 'order';

CREATE TABLE IF NOT EXISTS admin_staff (
  id             BIGSERIAL PRIMARY KEY,
  name           TEXT NOT NULL,
  email          TEXT NOT NULL UNIQUE,
  password_hash  TEXT NOT NULL,
  role           TEXT NOT NULL CHECK (role IN ('owner','accountant','support')),
  is_active      BOOLEAN NOT NULL DEFAULT TRUE,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS complaints (
  id               BIGSERIAL PRIMARY KEY,
  order_id         BIGINT NOT NULL REFERENCES orders(id),
  buyer_id         BIGINT NOT NULL REFERENCES users(id),
  chef_id          BIGINT NOT NULL REFERENCES users(id),
  type             TEXT NOT NULL,
  description      TEXT NOT NULL,
  photo_urls       TEXT[] NOT NULL DEFAULT '{}',
  status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','closed')),
  refund_amount    INT,
  resolution_note  TEXT,
  resolved_by      BIGINT REFERENCES admin_staff(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS refunds (
  id          BIGSERIAL PRIMARY KEY,
  order_id    BIGINT NOT NULL REFERENCES orders(id),
  amount      INT NOT NULL,
  reason      TEXT,
  staff_id    BIGINT REFERENCES admin_staff(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notifications (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type        TEXT NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  data        JSONB,
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notifications_user_idx ON notifications (user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS device_tokens (
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token       TEXT NOT NULL,
  platform    TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, token)
);

CREATE TABLE IF NOT EXISTS settings (
  key    TEXT PRIMARY KEY,
  value  JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id          BIGSERIAL PRIMARY KEY,
  staff_id    BIGINT REFERENCES admin_staff(id),
  action      TEXT NOT NULL,
  entity      TEXT,
  entity_id   TEXT,
  data        JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS schema_migrations (
  name        TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
