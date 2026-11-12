// Регистрация push-токена (FCM/APNs) на бэкенде: POST /auth/device
import Constants, { ExecutionEnvironment } from 'expo-constants';
import * as Device from 'expo-device';
import { Platform } from 'react-native';
import { authApi } from '../api/endpoints';

let registered = false;

export async function registerPush(): Promise<void> {
  if (registered || Platform.OS === 'web') return;
  // В Expo Go (Android, SDK 53+) удалённые уведомления недоступны — нужен dev build
  if (Constants.executionEnvironment === ExecutionEnvironment.StoreClient) return;
  if (!Device.isDevice) return;
  try {
    const Notifications = await import('expo-notifications');
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
        shouldPlaySound: true,
        shouldSetBadge: true,
        shouldShowBanner: true,
        shouldShowList: true,
      }),
    });
    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync('orders', {
        name: 'Заказы',
        importance: Notifications.AndroidImportance.HIGH,
        sound: 'default',
      });
    }
    let perm = await Notifications.getPermissionsAsync();
    if (!perm.granted) perm = await Notifications.requestPermissionsAsync();
    if (!perm.granted) return;
    // Бэкенд шлёт push через FCM — регистрируем «сырой» токен устройства
    const t = await Notifications.getDevicePushTokenAsync();
    const token = typeof t.data === 'string' ? t.data : JSON.stringify(t.data);
    await authApi.registerDevice(token, Platform.OS === 'ios' ? 'ios' : 'android');
    registered = true;
  } catch (e) {
    console.warn('[push] registration skipped:', e);
  }
}

export function resetPush() {
  registered = false;
}
