import { query } from '../db.js';
import { emit, rooms } from './realtime.js';

/**
 * Push-уведомления (ТЗ 10). Каждое уведомление сохраняется в notifications (история в приложении)
 * и отправляется через FCM, если настроен FCM_SERVER_KEY; иначе — только в лог и по WebSocket.
 */
export async function notify(userId: number, type: string, title: string, body: string, data: Record<string, unknown> = {}) {
  await query('INSERT INTO notifications (user_id, type, title, body, data) VALUES ($1,$2,$3,$4,$5)', [userId, type, title, body, data]);
  emit(rooms.user(userId), 'notification', { type, title, body, data });
  const key = process.env.FCM_SERVER_KEY;
  if (!key) {
    if (process.env.NODE_ENV !== 'test') console.log(`[push:mock] user ${userId}: ${title} — ${body}`);
    return;
  }
  const tokens = await query<{ token: string }>('SELECT token FROM device_tokens WHERE user_id = $1', [userId]);
  await Promise.all(tokens.map((t) =>
    fetch('https://fcm.googleapis.com/fcm/send', {
      method: 'POST',
      headers: { Authorization: `key=${key}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ to: t.token, notification: { title, body }, data: { type, ...data } }),
    }).catch((e) => console.error('[push] FCM error', e)),
  ));
}
