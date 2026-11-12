# Dasturxon API

База: `http://<host>:4000/api`. Формат — JSON. Деньги — целые числа в сумах.
Авторизация: `Authorization: Bearer <accessToken>`. Ошибки: `{ error, message, details? }`, `message` на русском — можно показывать пользователю.

Access-токен живёт 15 минут, refresh-токен 30 дней (при `POST /auth/refresh` выдаётся новая пара, старый refresh отзывается).

## Realtime (Socket.io)

`io(BASE_HOST, { path: '/socket.io', auth: { token: accessToken } })`

- После подключения пользователь сам попадает в свою комнату, сотрудник — в комнату `admin`.
- `socket.emit('order:subscribe', orderId)` — подписка на трекинг заказа.
- События:
  - `order:update` `{ orderId, status }` — смена статуса (получают покупатель, повар и админы);
  - `order:tracking` `{ orderId, status, courier: { name, phone, position: {lat, lon} | null, etaSeconds } }` — каждые 5 с;
  - `notification` `{ type, title, body, data }` — дублирует push;
  - `alert` (только админы) `{ type: 'complaint' | 'verification' | 'low_rating' | 'payout_failed' | 'early_withdrawal' | 'courier_request_failed' | 'courier_cancelled' | 'autocancel', ... }`;
  - `payouts:done` (только админы).

Статусы заказа: `PENDING → CONFIRMED → COOKING → READY → COURIER_ASSIGNED → IN_DELIVERY → DELIVERED → RATED`, а также `CANCELLED`.

## Авторизация

| Метод | Путь | Тело / ответ |
|---|---|---|
| POST | `/auth/send-otp` | `{ phone: "+998901234567" }` → `{ sent, resendAfterSec: 60, devCode? }`. `devCode` приходит только в dev-режиме. Лимит: 5 SMS за 10 мин (429). |
| POST | `/auth/verify-otp` | `{ phone, code: "1234", role?: "buyer"\|"chef" }` → `{ accessToken, refreshToken, expiresIn, isNew, user: { id, phone, role, name } }`. Для нового номера без `role` ответ `{ needsRole: true }`, код при этом не расходуется: повторите запрос с `role`. 409 — номер зарегистрирован с другой ролью (код тоже не расходуется). |
| POST | `/auth/refresh` | `{ refreshToken }` → новая пара токенов |
| DELETE | `/auth/logout` | `{ refreshToken }` |
| POST | `/auth/device` | `{ token, platform: "ios"\|"android" }` — Expo push-токен (`ExponentPushToken[...]`) |
| DELETE | `/auth/device` | `{ token }` — отвязать устройство при выходе |
| POST | `/notifications/read` | `{ ids?: number[] }` — отметить прочитанными (без `ids` — все) |
| GET | `/notifications/unread-count` | `{ count }` |
| POST | `/admin/auth/login` | `{ email, password }` → `{ accessToken, refreshToken, staff: { id, name, email, role, permissions[] } }` |

## Загрузка фото

`POST /uploads`: тело — сырые байты картинки, `Content-Type: image/jpeg|png|webp|heic`, до 10 МБ. Ответ: `{ url }`. Нужен токен пользователя.

## Публичные (без токена)

- `GET /feed?lat&lon&filter=all|ready|order|breakfast|lunch|dinner|bakery|drinks|vegan&page=1` → `{ page, hasMore, items: [FeedItem] }`
  - FeedItem: `{ id, name, description, price, weight, photoUrl, type: "ready"|"order"|"both", readyNow, category, portionsLeft, isVegan, chef: { id, nick, district, photoUrl, rating, reviewsCount }, distanceKm, deliveryFee, etaMinutes }`
- `GET /search?lat&lon&q=плов&page=1` — тот же формат, что у ленты.
- `GET /feed/map?lat&lon` → `[{ id, nick, district, rating, photoUrl, readyNow, distanceKm, point: {lat, lon} }]` (точка повара округлена примерно до 500 м).
- `GET /chef/:id?lat&lon` → `{ id, nick, bio, photoUrl, coverUrl, district, status: "ready"|"order"|"offline", rating, reviewsCount, totalOrders, avgDeliveryMinutes, distanceKm, deliveryFee, lastReviews: [{ rating, tags, comment, createdAt, buyerName }] }`
- `GET /chef/:id/menu` → `{ available: boolean, dishes: [{ id, name, description, price, weight, photoUrls, category, type, prepTimeMinutes, portionsLeft, ingredients, isVegan }] }`
- `GET /chef/:id/reviews?page=1`
- `POST /delivery/estimate` `{ chefId, lat, lon }` → `{ price, distanceKm }`

Категории: `breakfast` (Завтрак), `lunch` (Обед), `dinner` (Ужин), `bakery` (Выпечка), `drinks` (Напитки), `other` (Другое).
Теги отзыва: `very_tasty` (Очень вкусно), `hot` (Горячее), `big_portion` (Большая порция), `fast` (Быстро доставили), `as_ordered` (Точно как заказал), `nice_packaging` (Красивая упаковка).

## Покупатель (role=buyer)

- `GET /buyer/profile` → `{ id, phone, name, addresses[], cards[], profileComplete }`
- `PUT /buyer/profile` `{ name }`
- `GET|POST /buyer/addresses`: POST `{ label?, lat, lon, addressText, isDefault? }`. Адрес: `{ id, label, lat, lon, addressText, isDefault }`
- `PUT /buyer/addresses/:id/default`, `DELETE /buyer/addresses/:id`
- `GET|POST /buyer/cards`: POST `{ cardToken, provider?: "payme"|"click" }`, где `cardToken` — токен из формы Payme. Карта: `{ id, maskedPan, cardType, provider, isDefault }`
- `PUT /buyer/cards/:id/default`, `DELETE /buyer/cards/:id`
- `GET /buyer/orders?status=all|active|done|cancelled&page=1` → `[Order + chef: { id, nick, photoUrl }]`
- `GET /buyer/pending-rating` → неоценённый доставленный заказ или `null` (экран оценки показывается при следующем открытии приложения)
- `GET /buyer/notifications`
- `POST /buyer/complaints` `{ orderId, type: quality|missing_items|late|wrong_order|hygiene|other, description, photoUrls[] }`
- `DELETE /buyer/account` — удаление аккаунта

## Заказы

- `POST /orders` (покупатель) `{ items: [{ dishId, qty }], addressId, cardId?, comment? }` → Order (201). Сумма холдируется на карте. Ошибки: 402 — недостаточно средств; 400 — блюда разных поваров, повар офлайн или нет карты.
- `GET /orders/:id` (покупатель или повар заказа) → Order + `history: [{ status, note, created_at }]` + `chef: { id, nick, photoUrl, district, point }`
- `GET /orders/:id/tracking` → `{ orderId, status, courier }`
- `POST /orders/:id/cancel` — покупатель, только в статусе PENDING
- `POST /orders/:id/rate` `{ rating: 1..5, tags: [], comment? }`

Order для покупателя: `{ id, status, items: [{ dishId, name, price, qty, photoUrl }], dishTotal, deliveryFee, total, comment, createdAt, confirmedAt, readyAt, deliveredAt, cancelledAt, cancelReason, paymentStatus, refundedAmount, courier: { name, phone, position, etaSeconds } | null, chefId, buyerId, deliveryAddress: { text, lat, lon } }`.
Для повара вместо `deliveryFee` и `total` приходят `chefAmount` (сумма к получению, ×0.76) и `expiresAt` (до какого момента принять заказ, PENDING).

## Повар (role=chef)

Регистрация: `PUT /chef/profile` (шаги 1–2) → `POST /chef/verification` (шаг 3) → `PUT /chef/payout-card` (шаг 4) → администратор одобряет заявку.

- `GET /chef/districts` → список районов Ташкента
- `GET /chef/profile` → профиль `{ id, phone, nick, firstName, lastName, bio, photoUrl, coverUrl, district, addressText, addressLat, addressLon, isOnline, verificationStatus: draft|pending|approved|rejected, verificationNote, kitchenPhotoUrls, isVerified, isBlocked, blockedUntil, rating, reviewsCount, totalOrders, payoutCard: { maskedPan, cardType } | null, termsAccepted }`. Если профиля ещё нет: `{ id, phone, registrationStep: 1 }`.
- `PUT /chef/profile` `{ nick, firstName, lastName, photoUrl, coverUrl, bio?, district, addressText, addressLat, addressLon }` (при первом сохранении все поля обязательны; потом можно передавать часть)
- `POST /chef/verification` `{ kitchenPhotoUrls: [1..3] }` → `{ status: "pending" }` или `{ status: "rejected", reason, retake: true }`
- `PUT /chef/payout-card` `{ cardToken, acceptTerms: true }` → `{ maskedPan, cardType }`
- `PUT /chef/status` `{ online }`
- `GET /chef/dishes`, `POST /chef/dishes`, `PUT /chef/dishes/:id`, `DELETE /chef/dishes/:id`, `PUT /chef/dishes/:id/toggle` `{ active? }`
  - Dish: `{ name, description?, category, price, weight?, photoUrls[1..5], portionsLeft?, type: ready|order|both, prepTimeMinutes? (обязательно, если не ready), ingredients?, isVegan? }`
- `GET /chef/orders?scope=new|active|today|all` → `[Order(chef) + reviewRating]`
- `GET /chef/orders/summary` → `{ new, active, done_today, earned_today }`
- `POST /chef/orders/:id/accept`, `POST /chef/orders/:id/decline` `{ reason? }`, `POST /chef/orders/:id/ready`
- `GET /chef/balance` → `{ balance, nextPayoutAt, pendingEarlyWithdrawal, week: { start, orders, turnover, avgCheck, avgRating } }`
- `GET /chef/transactions?page=1` → `[{ type: "earning"|"payout", kind: "order"|"refund"|"weekly"|"early", id, order_id, gross_amount, commission, tax, amount, created_at, title }]` (`amount < 0` — списание)
- `POST /chef/payout/request` `{ confirm?: boolean }`: без `confirm` → превью `{ preview: true, amount, fee, net }`, с `confirm: true` → заявка
- `GET /chef/reviews`, `GET /chef/stats`, `GET /chef/notifications`

## Админ-панель (staff)

Права по ролям — в `staff.permissions` (owner, accountant, support; ТЗ 7.1).

- `GET /admin/me`
- `GET /admin/dashboard` → `{ today: { orders, ordersChangePct, activeChefs, totalChefs, revenue?, revenueChangePct?, income?, incomeChangePct?, chefTax? }, week: [{ day, orders, revenue }], alerts: { complaints, lowRatingChefs[], pendingVerifications, failedPayouts, pendingEarlyWithdrawals }, topChefs: [{ id, nick, district, rating, is_online, low_rating_flag, orders, revenue }], payouts?: { amount, chefs, nextPayoutAt } }`
- `GET /admin/orders?status=all|active|done|cancelled&from&to&q&page` → `{ total, items[] }`; `GET /admin/orders/:id` (с history, refunds, review)
- `POST /admin/orders/:id/refund` `{ amount, reason, complaintId? }`; `POST /admin/orders/:id/cancel` `{ reason }`
- `GET /admin/chefs?status=all|online|offline|blocked|pending|low_rating&q&page`; `GET /admin/chefs/:id`
- `GET /admin/verifications`; `PUT /admin/chefs/:id/verify` `{ approve, reason? }`
- `PUT /admin/chefs/:id/block` `{ blocked, hours?, reason? }` (поддержка — не больше 24 ч)
- `POST /admin/chefs/:id/message` `{ text }`
- `GET /admin/buyers?q&page`, `GET /admin/buyers/:id`, `PUT /admin/buyers/:id/block` `{ blocked }`
- `GET /admin/complaints?status=open|resolved|closed|all`, `GET /admin/complaints/:id`, `PUT /admin/complaints/:id` `{ action: warn_chef|close|resolve, note? }`
- `GET /admin/finance?period=day|week|month|year` → `{ turnover, orders, commission, deliveryBuffer, deliveryCost, earlyWithdrawalFees, refunds, refundsPlatformShare, platformIncome, itParkFee, netIncome, chefTaxWithheld, paidToChefs, chart: [{ bucket, turnover, income }] }`
- `GET /admin/payouts?status&kind` → `{ nextPayoutAt, upcoming: [{ chef_id, nick, payout_card_masked, amount }], items[] }`
- `POST /admin/payouts/run` `{ confirm: true }`; `POST /admin/payouts/:id/complete` `{ ok, note? }`
- `GET /admin/reports/tax?from&to` и `GET /admin/reports/transactions?from&to` → файл .xlsx
- `GET|POST /admin/staff`, `PUT /admin/staff/:id`; `GET|PUT /admin/settings`; `GET /admin/audit`

Демо-доступы (после `npm run seed`): `owner@dasturxon.uz / admin12345`, `accountant@dasturxon.uz / accountant123`, `support@dasturxon.uz / support123`. Покупатель — `+998901234567`, повара — `+998901110001…3`. В dev-режиме OTP-код возвращается в поле `devCode`.
