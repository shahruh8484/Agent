import { query } from '../db.js';
import { emit, rooms } from './realtime.js';

/**
 * Push-уведомления (ТЗ 10). Каждое уведомление сохраняется в notifications (история в приложении),
 * уходит по WebSocket и отправляется на устройства через Expo Push Service — он сам доставляет
 * в FCM (Android) и APNs (iOS); ключи FCM/APNs настраиваются в EAS (`eas credentials`).
 * Выключить отправку на устройства: PUSH_ENABLED=false.
 */
export async function notify(userId: number, type: string, title: string, body: string, data: Record<string, unknown> = {}) {
  await query('INSERT INTO notifications (user_id, type, title, body, data) VALUES ($1,$2,$3,$4,$5)', [userId, type, title, body, data]);
  emit(rooms.user(userId), 'notification', { type, title, body, data });
  if (process.env.NODE_ENV === 'test' || process.env.PUSH_ENABLED === 'false') return;
  const tokens = await query<{ token: string }>('SELECT token FROM device_tokens WHERE user_id = $1', [userId]);
  const expoTokens = tokens.map((t) => t.token).filter((t) => t.startsWith('ExponentPushToken['));
  if (expoTokens.length === 0) {
    console.log(`[push] user ${userId}: ${title} — ${body} (нет зарегистрированных устройств)`);
    return;
  }
  try {
    const r = await fetch('https://exp.host/--/api/v2/push/send', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(process.env.EXPO_ACCESS_TOKEN ? { Authorization: `Bearer ${process.env.EXPO_ACCESS_TOKEN}` } : {}),
      },
      body: JSON.stringify(expoTokens.map((to) => ({ to, title, body, data: { type, ...data }, sound: 'default', channelId: 'orders', priority: 'high' }))),
    });
    const j: any = await r.json().catch(() => null);
    // Удаляем токены удалённых приложений
    const tickets: any[] = j?.data ?? [];
    for (let i = 0; i < tickets.length; i++) {
      if (tickets[i]?.details?.error === 'DeviceNotRegistered') {
        await query('DELETE FROM device_tokens WHERE token = $1', [expoTokens[i]]);
      }
    }
  } catch (e) {
    console.error('[push] Expo push error', e);
  }
}
