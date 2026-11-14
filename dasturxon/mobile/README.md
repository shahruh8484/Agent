# Dasturxon — мобильное приложение

Одно приложение для двух ролей — **покупатель** и **повар** (роль выбирается при первом входе по номеру телефона).
Стек: Expo SDK 58, React Native 0.88, TypeScript, React Navigation (native stack + bottom tabs), Zustand, socket.io-client.

```
src/
  api/        клиент API (refresh токена при 401), эндпоинты, сокет, Payme, геокодер
  store/      Zustand: auth, cart, buyer, chef, toast
  screens/    auth/, buyer/, chef/, common/
  components/ UI-кит, карточки, карта (WebView + Яндекс.Карты), формы карты/адреса
  navigation/ корневой навигатор и вкладки
  theme/      цвета, отступы
```

## Запуск

```bash
cd mobile
npm install
npx expo start
```

Бэкенд должен быть запущен (`backend/`, по умолчанию `http://localhost:4000`).
На телефоне `localhost` — это сам телефон, поэтому укажите IP компьютера в локальной сети:

```bash
EXPO_PUBLIC_API_URL=http://192.168.1.10:4000 npx expo start
```

### Expo Go

Сканируйте QR-код из `npx expo start` приложением Expo Go. Работает всё, кроме push-уведомлений
(в Expo Go на Android удалённые уведомления недоступны — приложение просто пропускает регистрацию токена).

### Development build (рекомендуется)

Нужен для push-уведомлений (FCM) и проверки нативного поведения:

```bash
npx expo run:android          # локально, нужен Android Studio
npx expo run:ios              # локально, нужен Xcode (macOS)
# или в облаке:
npx eas-cli@latest build --profile development --platform android
```

### Веб (только для разработки и смоук-тестов)

```bash
npx expo export --platform web   # сборка в dist/
npx serve dist                    # или любой статический сервер
```

В вебе токены хранятся в `localStorage` (вместо Keychain/Keystore), карта показывается через `<iframe>`, push отключены.

### Демо-доступы (dev)

Покупатель `+998 90 123 45 67`, повара `+998 90 111 00 01…03`. В dev-режиме бэкенд возвращает OTP-код,
и экран ввода кода показывает его подсказкой «Тестовый код».

## Переменные окружения

Задаются при запуске/сборке (`EXPO_PUBLIC_*` встраиваются в бандл), например в `.env.local`:

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `EXPO_PUBLIC_API_URL` | `http://localhost:4000` | Адрес бэкенда (API — `/api`, Socket.io — `/socket.io`) |
| `EXPO_PUBLIC_PAYMENT_MODE` | `mock` | `mock` — тестовый токен карты `mock-1234` / `humo-1234`; `payme` — привязка через Payme Subscribe API (cards.create → SMS → cards.verify) |
| `EXPO_PUBLIC_PAYME_MERCHANT_ID` | — | ID кассы Payme (заголовок `X-Auth`), нужен при `payme` |
| `EXPO_PUBLIC_PAYME_API_URL` | `https://checkout.paycom.uz/api` | Для теста: `https://checkout.test.paycom.uz/api` |
| `EXPO_PUBLIC_YANDEX_MAPS_KEY` | — | Ключ JS API Яндекс.Карт 2.1. Без ключа вместо карты — список поваров с расстоянием |
| `EXPO_PUBLIC_YANDEX_GEOCODER_KEY` | — | Ключ HTTP Геокодера: поиск адреса. Без ключа — ручной ввод + геолокация |
| `EXPO_PUBLIC_SUPPORT_PHONE` | `+998712000000` | Телефон поддержки |
| `EXPO_PUBLIC_SUPPORT_TELEGRAM` | `https://t.me/dasturxon_support` | Telegram поддержки |

## Сборка через EAS

```bash
npm install -g eas-cli        # или npx eas-cli@latest …
eas login
eas build:configure           # создаст eas.json и projectId
eas build --platform android --profile preview      # APK для тестирования
eas build --platform all --profile production       # AAB / IPA для сторов
eas submit --platform android                        # публикация
```

Переменные `EXPO_PUBLIC_*` для сборок задаются в `eas.json` (`build.<profile>.env`) или через `eas env:create`.
Для push на Android добавьте `google-services.json` (Firebase) и укажите путь в `app.json` → `android.googleServicesFile`.

## Проверки

```bash
npx tsc --noEmit                 # типы
npx expo export --platform web   # бандл собирается
```

Скриншоты смоук-теста (Playwright, веб-сборка, вьюпорт телефона) — в `screenshots/`.

## Сборка APK без EAS (локально)

```bash
npx expo prebuild --platform android
cd android
echo "sdk.dir=$ANDROID_HOME" > local.properties
./gradlew assembleRelease        # нужен JDK 17 и Android SDK (platform 37, build-tools 37, NDK 28)
# результат: android/app/build/outputs/apk/release/app-release.apk
```

Такой APK подписан отладочным ключом — он годится для установки на телефоны тестировщиков, но не для Google Play.
Для магазина используйте `eas build -p android --profile production` (EAS хранит ключ подписи) и выключите поле
«Адрес сервера» на экране входа: `EXPO_PUBLIC_ALLOW_SERVER_CHANGE=false`, `EXPO_PUBLIC_API_URL=https://api.ваш-домен`.
