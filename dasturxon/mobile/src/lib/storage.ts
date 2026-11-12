// Нативная версия: токены и настройки хранятся в Keychain (iOS) / Keystore (Android)
import * as SecureStore from 'expo-secure-store';

export const storage = {
  get: (key: string) => SecureStore.getItemAsync(key).catch(() => null),
  set: (key: string, value: string) => SecureStore.setItemAsync(key, value).catch(() => undefined),
  remove: (key: string) => SecureStore.deleteItemAsync(key).catch(() => undefined),
};
