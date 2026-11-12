import { bank } from '../integrations/bank.js';
import { notify } from '../integrations/push.js';
import { emit, rooms } from '../integrations/realtime.js';
import { one, pool, query, tx } from '../db.js';
import { badRequest, conflict } from '../lib/errors.js';
import { earlyWithdrawal } from '../lib/money.js';
import { nextPayoutAt, weekStart, addDays } from '../lib/time.js';

/** Невыплаченный баланс повара (сумма net по невыплаченным начислениям, с учётом возвратов). */
export async function unpaidBalance(chefId: number): Promise<number> {
  const r = await one<{ s: number }>('SELECT COALESCE(sum(net_amount),0)::int AS s FROM chef_earnings WHERE chef_id = $1 AND NOT is_paid', [chefId]);
  return r?.s ?? 0;
}

export async function chefBalance(chefId: number) {
  const ws = weekStart();
  const stats = await one<any>(
    `SELECT count(*)::int AS orders, COALESCE(sum(dish_total),0)::int AS turnover,
            COALESCE(round(avg(dish_total)),0)::int AS avg_check
       FROM orders WHERE chef_id = $1 AND status IN ('DELIVERED','RATED') AND delivered_at >= $2::date - interval '5 hours'`,
    [chefId, ws]);
  const rating = await one<any>(
    `SELECT COALESCE(round(avg(rating)::numeric, 2), 0) AS avg FROM reviews WHERE chef_id = $1 AND created_at >= $2::date - interval '5 hours'`, [chefId, ws]);
  const pendingEarly = await one<{ s: number }>(
    `SELECT COALESCE(sum(net_amount),0)::int AS s FROM chef_payouts WHERE chef_id = $1 AND kind = 'early' AND status IN ('pending','processing')`, [chefId]);
  return {
    balance: await unpaidBalance(chefId),
    nextPayoutAt: nextPayoutAt(),
    pendingEarlyWithdrawal: pendingEarly?.s ?? 0,
    week: { start: ws, orders: stats.orders, turnover: stats.turnover, avgCheck: stats.avg_check, avgRating: Number(rating.avg) },
  };
}

/** История транзакций повара (ТЗ 4.4): начисления, возвраты, выплаты. */
export async function chefTransactions(chefId: number, limit = 50, offset = 0) {
  return query<any>(
    `SELECT * FROM (
       SELECT 'earning' AS type, e.kind, e.id, e.order_id, e.gross_amount, e.commission, e.tax, e.net_amount AS amount,
              e.created_at, (SELECT string_agg((it->>'name') || ' × ' || (it->>'qty'), ', ') FROM orders o, jsonb_array_elements(o.items) it WHERE o.id = e.order_id) AS title
         FROM chef_earnings e WHERE e.chef_id = $1
       UNION ALL
       SELECT 'payout', p.kind, p.id, NULL, p.amount, p.commission_1pct, 0, -p.net_amount, COALESCE(p.paid_at, p.created_at),
              CASE p.status WHEN 'done' THEN 'Выплата на карту' WHEN 'failed' THEN 'Ошибка выплаты' ELSE 'Выплата в обработке' END
         FROM chef_payouts p WHERE p.chef_id = $1 AND p.status <> 'failed'
     ) t ORDER BY created_at DESC LIMIT $2 OFFSET $3`, [chefId, limit, offset]);
}

/**
 * Еженедельные выплаты (ТЗ 6.2): пятница 10:00 по Ташкенту или вручную из админки.
 * Защищено advisory-lock от параллельного запуска.
 */
export async function runWeeklyPayouts(): Promise<{ total: number; paid: number; failed: number; amount: number }> {
  const lock = await pool.connect();
  try {
    const got = (await lock.query('SELECT pg_try_advisory_lock(77001) AS ok')).rows[0].ok;
    if (!got) throw conflict('Выплаты уже выполняются');
    const chefs = await query<{ chef_id: number; s: number }>(
      `SELECT chef_id, sum(net_amount)::int AS s FROM chef_earnings WHERE NOT is_paid GROUP BY chef_id HAVING sum(net_amount) > 0`);
    const result = { total: chefs.length, paid: 0, failed: 0, amount: 0 };
    for (const { chef_id } of chefs) {
      const r = await payoutChef(chef_id);
      if (r === 'done') result.paid++; else if (r === 'failed') result.failed++;
    }
    result.amount = (await one<{ s: number }>(
      `SELECT COALESCE(sum(net_amount),0)::int AS s FROM chef_payouts WHERE kind='weekly' AND status='done' AND paid_at > now() - interval '1 hour'`))!.s;
    emit(rooms.admin, 'payouts:done', result);
    return result;
  } finally {
    await lock.query('SELECT pg_advisory_unlock(77001)').catch(() => {});
    lock.release();
  }
}

async function payoutChef(chefId: number): Promise<'done' | 'failed' | 'skipped'> {
  const chef = await one<any>('SELECT payout_card_token, nick FROM chef_profiles WHERE user_id = $1', [chefId]);
  const payout = await tx(async (c) => {
    const rows = await query<any>('SELECT id, net_amount, week_start FROM chef_earnings WHERE chef_id = $1 AND NOT is_paid FOR UPDATE', [chefId], c);
    const amount = rows.reduce((s, r) => s + r.net_amount, 0);
    if (amount <= 0) return null;
    const weeks = rows.map((r) => String(r.week_start)).sort();
    const p = await one<any>(
      `INSERT INTO chef_payouts (chef_id, kind, amount, net_amount, week_start, week_end, status)
       VALUES ($1,'weekly',$2,$2,$3,$4,'processing') RETURNING *`,
      [chefId, amount, weeks[0], addDays(weeks[weeks.length - 1], 6)], c);
    await query('UPDATE chef_earnings SET is_paid = TRUE, payout_id = $2 WHERE id = ANY($1::bigint[])', [rows.map((r) => r.id), p.id], c);
    return p;
  });
  if (!payout) return 'skipped';

  const fail = async (error: string) => {
    await tx(async (c) => {
      await query(`UPDATE chef_payouts SET status = 'failed', error = $2 WHERE id = $1`, [payout.id, error], c);
      // начисления возвращаются в баланс и будут выплачены при следующем запуске
      await query('UPDATE chef_earnings SET is_paid = FALSE, payout_id = NULL WHERE payout_id = $1', [payout.id], c);
    });
    emit(rooms.admin, 'alert', { type: 'payout_failed', chefId, payoutId: payout.id, error });
    await notify(chefId, 'payout_failed', 'Выплата не прошла', 'Не удалось отправить выплату на карту. Проверьте карту для выплат в профиле.', {});
    return 'failed' as const;
  };

  if (!chef?.payout_card_token) return fail('У повара не указана карта для выплат');
  const r = await bank.transfer(chef.payout_card_token, payout.net_amount, `payout-${payout.id}`).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) return fail(r.error);
  await query(`UPDATE chef_payouts SET status = 'done', bank_transaction_id = $2, paid_at = now() WHERE id = $1`, [payout.id, r.transactionId]);
  await notify(chefId, 'payout', 'Выплата', `Выплата ${payout.net_amount.toLocaleString('ru-RU')} сум отправлена на вашу карту.`, { payoutId: payout.id });
  return 'done';
}

/** Досрочный вывод (ТЗ 6.3): сумма × 0.99, ручная обработка 48–72 часа. */
export async function requestEarlyWithdrawal(chefId: number, confirm: boolean) {
  const balance = await unpaidBalance(chefId);
  if (balance <= 0) throw badRequest('Нет средств для вывода');
  const calc = earlyWithdrawal(balance);
  if (!confirm) return { preview: true, ...calc };
  const p = await tx(async (c) => {
    const rows = await query<any>('SELECT id, net_amount FROM chef_earnings WHERE chef_id = $1 AND NOT is_paid FOR UPDATE', [chefId], c);
    const amount = rows.reduce((s, r) => s + r.net_amount, 0);
    if (amount <= 0) throw badRequest('Нет средств для вывода');
    const x = earlyWithdrawal(amount);
    const p = await one<any>(
      `INSERT INTO chef_payouts (chef_id, kind, amount, commission_1pct, net_amount, week_start, week_end, status)
       VALUES ($1,'early',$2,$3,$4,$5,$5,'pending') RETURNING *`, [chefId, x.amount, x.fee, x.net, weekStart()], c);
    await query('UPDATE chef_earnings SET is_paid = TRUE, payout_id = $2 WHERE id = ANY($1::bigint[])', [rows.map((r) => r.id), p.id], c);
    return p;
  });
  emit(rooms.admin, 'alert', { type: 'early_withdrawal', chefId, payoutId: p.id, amount: p.net_amount });
  return { preview: false, payout: p };
}

/** Админ отмечает ручную выплату выполненной или отклоняет её. */
export async function completeManualPayout(payoutId: number, ok: boolean, note?: string) {
  const p = await one<any>('SELECT * FROM chef_payouts WHERE id = $1', [payoutId]);
  if (!p) throw badRequest('Выплата не найдена');
  if (!['pending', 'processing'].includes(p.status)) throw conflict('Выплата уже обработана');
  if (ok) {
    await query(`UPDATE chef_payouts SET status = 'done', paid_at = now(), bank_transaction_id = $2 WHERE id = $1`, [payoutId, note ?? null]);
    await notify(p.chef_id, 'payout', 'Выплата', `Выплата ${p.net_amount.toLocaleString('ru-RU')} сум отправлена на вашу карту.`, { payoutId });
  } else {
    await tx(async (c) => {
      await query(`UPDATE chef_payouts SET status = 'failed', error = $2 WHERE id = $1`, [payoutId, note ?? 'Отклонено'], c);
      await query('UPDATE chef_earnings SET is_paid = FALSE, payout_id = NULL WHERE payout_id = $1', [payoutId], c);
    });
  }
  return one<any>('SELECT * FROM chef_payouts WHERE id = $1', [payoutId]);
}
