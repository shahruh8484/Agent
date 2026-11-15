# FamilyChef для Android

Приложение для Google Play — Trusted Web Activity (TWA): открывает https://familychef.pro во весь экран,
без адресной строки браузера, с иконкой и заставкой. Обновления сайта сразу видны в приложении;
пересобирать и заново публиковать приложение нужно только при изменении иконки, названия или версии Android.

- Пакет: `pro.familychef.app`
- minSdk 24 (Android 7.0), targetSdk 36
- Библиотека: `com.google.androidbrowserhelper:androidbrowserhelper`

## Сборка

Нужны JDK 17+ и Android SDK (platform 36, build-tools 36).

```bash
cd familychef/android
```

```bash
ANDROID_HOME=/путь/к/android-sdk ./gradlew bundleRelease assembleRelease
```

Результат: `app/build/outputs/bundle/release/app-release.aab` (для Google Play) и
`app/build/outputs/apk/release/app-release.apk` (для установки на телефон вручную).

Перед каждой новой публикацией увеличьте `versionCode` (и `versionName`) в `app/build.gradle`.

## Подпись

Ключ загрузки (upload key) **не хранится в git**. Положите рядом файл `keystore.properties`:

```properties
storeFile=/полный/путь/familychef-upload.jks
storePassword=...
keyAlias=upload
keyPassword=...
```

или задайте переменные окружения `FC_KEYSTORE`, `FC_KEYSTORE_PASSWORD`, `FC_KEY_ALIAS`, `FC_KEY_PASSWORD`.

Храните `.jks` и пароль в надёжном месте (менеджер паролей + резервная копия). При включённой
подписи приложений Google Play (Play App Signing) потерянный ключ загрузки можно сбросить через поддержку Google.

## Связь сайта и приложения (обязательно)

Чтобы приложение открывалось без адресной строки, сайт должен «подтвердить» приложение:
файл `https://familychef.pro/.well-known/assetlinks.json` (берётся из `familychef/data/assetlinks.json`)
должен содержать SHA-256 отпечатки сертификатов, которыми подписан установленный APK.

В файле уже есть отпечаток ключа загрузки (для APK, установленного вручную). **После первой загрузки
в Google Play** добавьте второй отпечаток — ключа подписи Google:
Play Console → приложение → «Проверка и выпуск» → «Настройка» → «Подпись приложений» →
«Сертификат ключа подписи приложения» → SHA-256. Добавьте его в список `sha256_cert_fingerprints`,
закоммитьте и обновите сервер (команда установки ещё раз).

Проверка: https://developers.google.com/digital-asset-links/tools/generator

## Материалы для Google Play

Папка `store/`: значок 512×512, баннер 1024×500, 4 скриншота, тексты описания и ответы для анкет — `store/listing-ru.md`.
