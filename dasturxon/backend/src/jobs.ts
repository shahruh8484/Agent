import { config } from './config.js';
import { query } from './db.js';
import { delivery } from './integrations/delivery.js';
import { notify } from './integrations/push.js';
import { TASHKENT_OFFSET_MIN } from './lib/time.js';
import { applyTracking, autoCancel, requestCourier } from './services/orders.js';
import { runWeeklyPayouts } from './services/payouts.js';

/** Один проход задач по заказам (вызывается таймером каждые 5 с; в тестах — вручную). */
export async function tickOrders() {
  const timeout = config.business.chefAcceptTimeoutSec;
  // 1. Напоминание повару за 2 минуты до автоотмены
  const remind = await query<any>(
    `UPDATE orders SET reminder_sent = TRUE WHERE status = 'PENDING' AND NOT reminder_sent
       AND created_at < now() - make_interval(secs => $1) RETURNING id, chef_id`, [timeout - 120]);
  for (const o of remind) await notify(o.chef_id, 'order_reminder', 'Новый заказ ждёт', 'Осталось 2 минуты, чтобы принять заказ!', { orderId: o.id });

  // 2. Автоотмена заказов, которые повар не принял за 5 минут
  const expired = await query<{ id: number }>(`SELECT id FROM orders WHERE status = 'PENDING' AND created_at < now() - make_interval(secs => $1)`, [timeout]);
  for (const o of expired) await autoCancel(o.id).catch((e) => { if (e?.status !== 409) console.error('[jobs] autocancel', o.id, e); });

  // 3. Повторный вызов курьера, если заявка не создалась или курьер отменил
  const noCourier = await query<any>(`SELECT * FROM orders WHERE status = 'READY' AND yandex_request_id IS NULL`);
  for (const o of noCourier) await requestCourier(o);

  // 4. Трекинг курьеров (ТЗ: каждые 5 секунд)
  const tracking = await query<any>(`SELECT id, yandex_request_id FROM orders WHERE status IN ('READY','COURIER_ASSIGNED','IN_DELIVERY') AND yandex_request_id IS NOT NULL`);
  await Promise.all(tracking.map(async (o) => {
    try { await applyTracking(o.id, await delivery.track(o.yandex_request_id)); } catch (e) { console.error('[jobs] tracking', o.id, e); }
  }));

  // 5. Снятие истёкших временных блокировок
  await query(`UPDATE chef_profiles SET is_blocked = FALSE, blocked_until = NULL, block_reason = NULL WHERE is_blocked AND blocked_until IS NOT NULL AND blocked_until < now()`);
}

let lastPayoutRun = '';

/** Пятница 10:00 по Ташкенту → автоматические выплаты (ТЗ 6.2). */
export async function tickPayouts(now = new Date()) {
  const local = new Date(now.getTime() + TASHKENT_OFFSET_MIN * 60000);
  const key = local.toISOString().slice(0, 10);
  if (local.getUTCDay() !== 5 || local.getUTCHours() !== 10 || lastPayoutRun === key) return false;
  lastPayoutRun = key;
  const r = await runWeeklyPayouts().catch((e) => { console.error('[jobs] payouts', e); return null; });
  if (r) console.log('[jobs] weekly payouts', r);
  return true;
}

export function startJobs() {
  let busy = false;
  const loop = setInterval(async () => {
    if (busy) return;
    busy = true;
    try { await tickOrders(); await tickPayouts(); } catch (e) { console.error('[jobs]', e); } finally { busy = false; }
  }, 5000);
  return () => clearInterval(loop);
}
