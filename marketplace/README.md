# Servio — маркетплейс услуг для Узбекистана (Android + iOS)

Приложение в духе Profi.ru: клиенты размещают заказы, специалисты откликаются,
стороны договариваются в чате, клиент выбирает исполнителя и оставляет отзыв.
Специалисты платят **подписку** (Payme или Click) — с ней можно откликаться на заказы без ограничений.

Адаптация под Узбекистан:
- интерфейс на **узбекском (латиница)** и **русском**, переключатель на экране входа и в профиле;
  каталог, города и ошибки сервера тоже приходят на выбранном языке;
- номера **+998**, SMS через **Eskiz.uz**;
- цены в **сумах**, оплата подписки через **Payme** и **Click**;
- 20 городов (Ташкент, Самарканд, Бухара, Наманган, Андижан, Фергана, Нукус…);
- местные услуги: подготовка к IELTS/CEFR и DTM, тои и ошпазы, метан/пропан (ГБО),
  перевозки на Damas/Labo, Telegram-боты, регистрация ЯТТ.

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
| Каталог: 12 разделов, ~95 услуг, поиск | Анкета: услуги, опыт, цена, «работаю онлайн» |
| Список специалистов с рейтингом и отзывами | Лента подходящих заказов (по услугам и городу) |
| Создание заказа (услуга, описание, бюджет, город, онлайн) | Отклик с ценой → сразу открывается чат |
| Отклики, выбор исполнителя, «выполнено», отзыв | Вкладка «В работе» |
| Чат по каждому заказу | Подписка: 7 дней бесплатно, затем 99 000 / 249 000 / 799 000 сум за 1 / 3 / 12 мес. |
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
- `SERVIO_PAYMENT_MODE=dev` — подписка активируется сразу, без оплаты.

Боевой режим:
- **SMS (Eskiz.uz)** — `SERVIO_SMS_PROVIDER=eskiz`, `SERVIO_ESKIZ_EMAIL`, `SERVIO_ESKIZ_PASSWORD`.
  Eskiz отправляет только тексты по шаблону, одобренному в его кабинете: зарегистрируйте
  шаблон ровно как в `SERVIO_SMS_TEMPLATE` (по умолчанию `Servio: tasdiqlash kodi {code}`).
- **Оплата** — `SERVIO_PAYMENT_MODE=live`. В приложении появятся кнопки тех систем, ключи которых заданы:
  - **Payme** (Merchant API): `SERVIO_PAYME_MERCHANT_ID`, `SERVIO_PAYME_KEY`.
    В кабинете Payme Business укажите endpoint `https://<ваш-домен>/payments/payme`
    и поле счёта `order_id`. Пока идёт проверка в песочнице — `SERVIO_PAYME_TEST=true`,
    после — `false` (переключает checkout.paycom.uz).
  - **Click** (SHOP API): `SERVIO_CLICK_SERVICE_ID`, `SERVIO_CLICK_MERCHANT_ID`, `SERVIO_CLICK_SECRET_KEY`.
    В кабинете Click укажите Prepare URL `https://<ваш-домен>/payments/click/prepare`
    и Complete URL `https://<ваш-домен>/payments/click/complete`.
- **Push** — `SERVIO_PUSH_ENABLED=true` (через Expo Push, отдельные ключи не нужны).
  Уведомления приходят на языке, выбранном в приложении у получателя.

Тарифы (в сумах) задаются в `servio/config.py` (`DEFAULT_PLANS`), каталог услуг и города на двух языках —
в `servio/catalog.py`, переводы ответов сервера — в `servio/i18n.py`, переводы приложения — в `mobile/src/lib/i18n.tsx`.

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
- Оплата подписки через App Store / Google Play, если магазины этого потребуют (см. ниже).
- Кириллическая версия узбекского языка.

## Важно перед публикацией

Apple и Google обычно требуют, чтобы подписки, открывающие функции внутри приложения,
оплачивались через их собственные системы. Payme/Click внутри приложения могут не пройти
проверку магазина. Частые решения: продавать подписку на сайте (а в приложении только
показывать её статус) или подключить покупки через App Store / Google Play.
Это стоит решить до отправки в магазины.
