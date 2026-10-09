# Servio — маркетплейс услуг (Android + iOS)

Приложение в духе Profi.ru: клиенты размещают заказы, специалисты откликаются,
стороны договариваются в чате, клиент выбирает исполнителя и оставляет отзыв.
Специалисты платят **подписку** — с ней можно откликаться на заказы без ограничений.

«Servio» — рабочее название. Оно задаётся в `mobile/app.json` (`name`, `slug`,
`scheme`, `bundleIdentifier`/`package`) и в `SERVIO_APP_NAME` на сервере.

```
marketplace/
├── backend/   # API на Python/FastAPI + SQLite
└── mobile/    # приложение на Expo (React Native) — один код для Android и iOS
```

## Что умеет первая версия

| Клиент | Специалист |
|---|---|
| Вход по номеру телефона (SMS-код) | То же |
| Каталог: 12 разделов, ~90 услуг, поиск | Анкета: услуги, опыт, цена, «работаю онлайн» |
| Список специалистов с рейтингом и отзывами | Лента подходящих заказов (по услугам и городу) |
| Создание заказа (услуга, описание, бюджет, город, онлайн) | Отклик с ценой → сразу открывается чат |
| Отклики, выбор исполнителя, «выполнено», отзыв | Вкладка «В работе» |
| Чат по каждому заказу | Подписка: пробный период 7 дней, тарифы 1/3/12 мес. |
| Push-уведомления о новых откликах и сообщениях | Push о новых заказах и выборе исполнителем |

Один аккаунт может переключаться между режимами «Я клиент» и «Я специалист».

## Сервер (backend)

```bash
cd marketplace/backend
pip install -r requirements.txt
cp .env.example .env        # по умолчанию всё в тестовом режиме
python -m servio            # http://localhost:8000, документация API: /docs
python -m pytest            # тесты
```

Тестовый режим (по умолчанию):
- `SERVIO_SMS_PROVIDER=dev` — SMS не отправляется, код показывается прямо в приложении;
- `SERVIO_PAYMENT_PROVIDER=dev` — подписка активируется сразу, без оплаты.

Боевой режим:
- **SMS** — `SERVIO_SMS_PROVIDER=smsru` и `SERVIO_SMSRU_API_ID` (sms.ru);
- **Оплата** — `SERVIO_PAYMENT_PROVIDER=yookassa`, `SERVIO_YOOKASSA_SHOP_ID`, `SERVIO_YOOKASSA_SECRET_KEY`.
  В личном кабинете ЮKassa укажите HTTP-уведомления на `https://<ваш-домен>/payments/yookassa/webhook`
  (событие `payment.succeeded`). Статус платежа сервер перепроверяет через API ЮKassa;
- **Push** — `SERVIO_PUSH_ENABLED=true` (через Expo Push, отдельные ключи не нужны).

Тарифы задаются в `servio/config.py` (`DEFAULT_PLANS`), каталог услуг и города — в `servio/catalog.py`.

Docker: `docker build -t servio-api . && docker run -p 8000:8000 -v servio-data:/data servio-api`.

## Приложение (mobile)

```bash
cd marketplace/mobile
npm install
EXPO_PUBLIC_API_URL=http://<IP-компьютера>:8000 npx expo start
```

Отсканируйте QR-код приложением **Expo Go** (Android/iPhone). Телефон и компьютер должны быть
в одной сети; `localhost` с телефона не работает, поэтому нужен IP компьютера.

Проверки: `npm run typecheck`.

### Сборка и публикация

Сборка идёт в облаке Expo (EAS), Mac для iOS не нужен:

```bash
npm i -g eas-cli
eas login
eas init                                  # создаст проект и projectId (нужен для push)
eas build -p android --profile preview    # APK для установки на телефон
eas build -p all --profile production     # для Google Play и App Store
eas submit -p android                     # нужен аккаунт Google Play Console ($25 один раз)
eas submit -p ios                         # нужен Apple Developer Program ($99/год)
```

Перед сборкой замените `https://api.example.com` в `eas.json` на адрес вашего сервера.

## Что дальше

- Веб-админка: модерация анкет и заказов, жалобы, блокировки.
- Фото в анкете и заказе, портфолио специалиста.
- Проверка документов (паспорт, дипломы) и значок «Документы проверены».
- Переход с SQLite на PostgreSQL при росте нагрузки.
- Чат через WebSocket вместо опроса каждые 4 секунды.
- Оплата подписки через App Store / Google Play, если магазины этого потребуют.
