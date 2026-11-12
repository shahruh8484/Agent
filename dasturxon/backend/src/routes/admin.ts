import bcrypt from 'bcryptjs';
import ExcelJS from 'exceljs';
import { Router, type Request, type Response } from 'express';
import { z } from 'zod';
import { requireStaff, uid } from '../auth/middleware.js';
import { can, permissionsOf, type StaffRole } from '../auth/permissions.js';
import { config } from '../config.js';
import { one, query, tx } from '../db.js';
import { notify } from '../integrations/push.js';
import { badRequest, conflict, forbidden, notFound } from '../lib/errors.js';
import { startOfTashkentDay, nextPayoutAt } from '../lib/time.js';
import { idParam, parse } from '../lib/validate.js';
import { clearFeedCache } from '../services/feed.js';
import { adminCancelOrder, presentOrder, refundOrder } from '../services/orders.js';
import { completeManualPayout, runWeeklyPayouts } from '../services/payouts.js';
import { saveSettings, getSettings } from '../services/settings.js';

export const adminRouter = Router();
adminRouter.use('/admin', requireStaff());

const role = (req: Request) => req.principal!.role as StaffRole;

async function audit(req: Request, action: string, entity: string, entityId: unknown, data?: unknown) {
  await query('INSERT INTO audit_log (staff_id, action, entity, entity_id, data) VALUES ($1,$2,$3,$4,$5)',
    [uid(req), action, entity, String(entityId), data ?? null]);
}

function guard(perm: Parameters<typeof can>[1]) {
  return (req: Request, _res: Response, next: () => void) => {
    if (!can(role(req), perm)) throw forbidden();
    next();
  };
}

const range = z.object({
  from: z.coerce.date().optional(),
  to: z.coerce.date().optional(),
});

adminRouter.get('/admin/me', async (req, res) => {
  const s = await one<any>('SELECT id, name, email, role FROM admin_staff WHERE id = $1', [uid(req)]);
  res.json({ ...s, permissions: permissionsOf(s.role) });
});

// ---------- Дашборд (ТЗ 7.2) ----------
adminRouter.get('/admin/dashboard', guard('dashboard'), async (req, res) => {
  const today = startOfTashkentDay();
  const yesterday = startOfTashkentDay(new Date(), -1);
  const dayStats = async (from: Date, to: Date) => one<any>(
    `SELECT count(*)::int AS orders,
            COALESCE(sum(total_amount) FILTER (WHERE status <> 'CANCELLED'),0)::int AS revenue,
            COALESCE(sum(platform_commission + COALESCE(delivery_buffer,0)) FILTER (WHERE status IN ('DELIVERED','RATED')),0)::int AS income,
            COALESCE(sum(chef_tax) FILTER (WHERE status IN ('DELIVERED','RATED')),0)::int AS tax
       FROM orders WHERE created_at >= $1 AND created_at < $2`, [from, to]);
  const t = await dayStats(today, new Date(Date.now() + 1000));
  const y = await dayStats(yesterday, today);
  const pct = (a: number, b: number) => (b === 0 ? null : Math.round(((a - b) / b) * 100));
  const chefs = await one<any>(`SELECT count(*) FILTER (WHERE is_online AND is_verified AND NOT is_blocked)::int AS online,
                                       count(*) FILTER (WHERE is_verified)::int AS verified FROM chef_profiles`);

  // Заказы по дням текущей недели
  const weekStartTs = startOfTashkentDay(new Date(), -((new Date(Date.now() + 5 * 3600e3).getUTCDay() + 6) % 7));
  const week = await query<any>(
    `SELECT to_char(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM-DD') AS day, count(*)::int AS orders,
            COALESCE(sum(total_amount) FILTER (WHERE status <> 'CANCELLED'),0)::int AS revenue
       FROM orders WHERE created_at >= $1 GROUP BY 1 ORDER BY 1`, [weekStartTs]);

  const alerts = {
    complaints: (await one<any>(`SELECT count(*)::int AS n FROM complaints WHERE status = 'open'`)).n,
    lowRatingChefs: await query(`SELECT user_id AS id, nick, rating FROM chef_profiles WHERE low_rating_flag AND NOT is_blocked ORDER BY rating LIMIT 10`),
    pendingVerifications: (await one<any>(`SELECT count(*)::int AS n FROM chef_profiles WHERE verification_status = 'pending'`)).n,
    failedPayouts: (await one<any>(`SELECT count(*)::int AS n FROM chef_payouts WHERE status = 'failed' AND created_at > now() - interval '14 days'`)).n,
    pendingEarlyWithdrawals: (await one<any>(`SELECT count(*)::int AS n FROM chef_payouts WHERE kind = 'early' AND status = 'pending'`)).n,
  };
  const topChefs = await query<any>(
    `SELECT cp.user_id AS id, cp.nick, cp.district, cp.rating, cp.is_online, cp.low_rating_flag, count(o.id)::int AS orders,
            COALESCE(sum(o.dish_total),0)::int AS revenue
       FROM orders o JOIN chef_profiles cp ON cp.user_id = o.chef_id
      WHERE o.created_at >= $1 AND o.status <> 'CANCELLED'
      GROUP BY cp.user_id ORDER BY revenue DESC LIMIT 10`, [today]);
  const payouts = await one<any>(
    `SELECT COALESCE(sum(s),0)::int AS amount, count(*)::int AS chefs FROM
       (SELECT sum(net_amount) AS s FROM chef_earnings WHERE NOT is_paid GROUP BY chef_id HAVING sum(net_amount) > 0) x`);

  const full = can(role(req), 'finance'); // поддержка видит дашборд частично — без денег платформы
  res.json({
    today: {
      orders: t.orders, ordersChangePct: pct(t.orders, y.orders), activeChefs: chefs.online, totalChefs: chefs.verified,
      ...(full ? { revenue: t.revenue, revenueChangePct: pct(t.revenue, y.revenue), income: t.income, incomeChangePct: pct(t.income, y.income), chefTax: t.tax } : {}),
    },
    week, alerts, topChefs,
    ...(can(role(req), 'payouts_run') ? { payouts: { ...payouts, nextPayoutAt: nextPayoutAt() } } : {}),
  });
});

// ---------- Заказы (ТЗ 7.3) ----------
adminRouter.get('/admin/orders', guard('orders_view'), async (req, res) => {
  const q = parse(range.extend({
    status: z.enum(['all', 'active', 'done', 'cancelled']).default('all'),
    q: z.string().trim().optional(),
    page: z.coerce.number().int().min(1).default(1),
  }), req.query);
  const where: string[] = ['TRUE'];
  const params: unknown[] = [];
  const add = (sql: string, v: unknown) => { params.push(v); where.push(sql.replaceAll('?', `$${params.length}`)); };
  if (q.status === 'active') where.push(`o.status IN ('PENDING','CONFIRMED','COOKING','READY','COURIER_ASSIGNED','IN_DELIVERY')`);
  if (q.status === 'done') where.push(`o.status IN ('DELIVERED','RATED')`);
  if (q.status === 'cancelled') where.push(`o.status = 'CANCELLED'`);
  if (q.from) add('o.created_at >= ?', q.from);
  if (q.to) add('o.created_at <= ?', q.to);
  if (q.q) {
    const s = q.q.replace(/^#/, '');
    if (/^\d+$/.test(s) && s.length < 9) add('o.id = ?', Number(s));
    else add('(cp.nick ILIKE ? OR u.phone ILIKE ?)', `%${s}%`);
  }
  params.push((q.page - 1) * 50);
  const rows = await query<any>(
    `SELECT o.*, cp.nick AS chef_nick, u.phone AS buyer_phone, u.name AS buyer_name, count(*) OVER()::int AS total_count
       FROM orders o JOIN chef_profiles cp ON cp.user_id = o.chef_id JOIN users u ON u.id = o.buyer_id
      WHERE ${where.join(' AND ')} ORDER BY o.id DESC LIMIT 50 OFFSET $${params.length}`, params);
  res.json({
    total: rows[0]?.total_count ?? 0,
    items: rows.map((o) => ({ ...presentOrder(o, 'admin'), chefNick: o.chef_nick, buyerPhone: o.buyer_phone, buyerName: o.buyer_name })),
  });
});

adminRouter.get('/admin/orders/:id', guard('orders_view'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const o = await one<any>(
    `SELECT o.*, cp.nick AS chef_nick, cu.phone AS chef_phone, u.phone AS buyer_phone, u.name AS buyer_name
       FROM orders o JOIN chef_profiles cp ON cp.user_id = o.chef_id JOIN users cu ON cu.id = o.chef_id JOIN users u ON u.id = o.buyer_id
      WHERE o.id = $1`, [id]);
  if (!o) throw notFound('Заказ не найден');
  const history = await query('SELECT status, note, created_at FROM order_status_history WHERE order_id = $1 ORDER BY id', [id]);
  const refunds = await query('SELECT r.amount, r.reason, r.created_at, s.name AS staff FROM refunds r LEFT JOIN admin_staff s ON s.id = r.staff_id WHERE order_id = $1 ORDER BY r.id', [id]);
  const review = await one('SELECT rating, tags, comment, created_at FROM reviews WHERE order_id = $1', [id]);
  res.json({ ...presentOrder(o, 'admin'), chefNick: o.chef_nick, chefPhone: o.chef_phone, buyerPhone: o.buyer_phone, buyerName: o.buyer_name, history, refunds, review });
});

const refundSchema = z.object({ amount: z.number().int().positive(), reason: z.string().trim().min(3).max(500), complaintId: z.number().int().optional() });

async function doRefund(req: Request, orderId: number, b: z.infer<typeof refundSchema>) {
  // Поддержка — возвраты до 50 000 сум, свыше — только владелец (ТЗ 7.1)
  const perm = b.amount > config.business.supportRefundLimit ? 'refund_large' : 'refund_small';
  if (!can(role(req), perm)) throw forbidden(`Возврат свыше ${config.business.supportRefundLimit.toLocaleString('ru-RU')} сум может оформить только владелец`);
  const o = await refundOrder(uid(req), orderId, b.amount, b.reason);
  if (b.complaintId) {
    await query(`UPDATE complaints SET status = 'resolved', refund_amount = COALESCE(refund_amount,0) + $2, resolution_note = $3, resolved_by = $4 WHERE id = $1`,
      [b.complaintId, b.amount, b.reason, uid(req)]);
  }
  await audit(req, 'refund', 'order', orderId, b);
  return o;
}

adminRouter.post('/admin/orders/:id/refund', async (req, res) => {
  const o = await doRefund(req, parse(idParam, req.params.id), parse(refundSchema, req.body));
  res.json(presentOrder(o, 'admin'));
});

adminRouter.post('/admin/refunds', async (req, res) => {
  const b = parse(refundSchema.extend({ orderId: z.number().int() }), req.body);
  const o = await doRefund(req, b.orderId, b);
  res.json(presentOrder(o, 'admin'));
});

adminRouter.post('/admin/orders/:id/cancel', guard('refund_small'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const { reason } = parse(z.object({ reason: z.string().trim().min(3).max(300) }), req.body);
  const o = await one<any>('SELECT total_amount, refunded_amount, status FROM orders WHERE id = $1', [id]);
  if (!o) throw notFound('Заказ не найден');
  if (o.status !== 'PENDING' && o.total_amount - o.refunded_amount > config.business.supportRefundLimit && !can(role(req), 'refund_large')) {
    throw forbidden('Отмена с возвратом свыше лимита — только владелец');
  }
  const r = await adminCancelOrder(uid(req), id, reason);
  await audit(req, 'cancel', 'order', id, { reason });
  res.json(presentOrder(r, 'admin'));
});

// ---------- Повара (ТЗ 7.4) ----------
adminRouter.get('/admin/chefs', guard('chefs_view'), async (req, res) => {
  const q = parse(z.object({
    status: z.enum(['all', 'online', 'offline', 'blocked', 'pending', 'low_rating']).default('all'),
    q: z.string().trim().optional(), page: z.coerce.number().int().min(1).default(1),
  }), req.query);
  const cond = {
    all: 'TRUE', online: 'cp.is_online AND NOT cp.is_blocked', offline: 'NOT cp.is_online AND NOT cp.is_blocked', blocked: 'cp.is_blocked',
    pending: `cp.verification_status = 'pending'`, low_rating: 'cp.low_rating_flag',
  }[q.status];
  const params: unknown[] = [];
  let search = 'TRUE';
  if (q.q) { params.push(`%${q.q}%`); search = `(cp.nick ILIKE $1 OR u.phone ILIKE $1 OR u.name ILIKE $1)`; }
  params.push((q.page - 1) * 50);
  const rows = await query<any>(
    `SELECT cp.*, u.phone, u.name, count(*) OVER()::int AS total_count FROM chef_profiles cp JOIN users u ON u.id = cp.user_id
      WHERE ${cond} AND ${search} ORDER BY cp.created_at DESC LIMIT 50 OFFSET $${params.length}`, params);
  const financeOnly = role(req) === 'accountant'; // бухгалтер видит поваров «только финансы»
  res.json({
    total: rows[0]?.total_count ?? 0,
    items: rows.map((c) => ({
      id: c.user_id, nick: c.nick, name: c.name, phone: financeOnly ? undefined : c.phone, photoUrl: c.photo_url, district: c.district,
      status: c.is_blocked ? 'blocked' : c.is_online ? 'online' : 'offline', verificationStatus: c.verification_status,
      rating: c.rating, totalOrders: c.total_orders, lowRating: c.low_rating_flag, createdAt: c.created_at,
    })),
  });
});

adminRouter.get('/admin/chefs/:id', guard('chefs_view'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const c = await one<any>('SELECT cp.*, u.phone, u.name FROM chef_profiles cp JOIN users u ON u.id = cp.user_id WHERE cp.user_id = $1', [id]);
  if (!c) throw notFound('Повар не найден');
  const finance = await one<any>(
    `SELECT COALESCE(sum(gross_amount),0)::int AS turnover, COALESCE(sum(net_amount) FILTER (WHERE is_paid),0)::int AS paid,
            COALESCE(sum(net_amount) FILTER (WHERE NOT is_paid),0)::int AS balance,
            COALESCE(sum(tax),0)::int AS tax_withheld, COALESCE(sum(commission),0)::int AS commission
       FROM chef_earnings WHERE chef_id = $1`, [id]);
  const payouts = await query('SELECT id, kind, amount, commission_1pct, net_amount, week_start, week_end, status, error, created_at, paid_at FROM chef_payouts WHERE chef_id = $1 ORDER BY id DESC LIMIT 50', [id]);
  const financeOnly = role(req) === 'accountant';
  if (financeOnly) return res.json({ id, nick: c.nick, name: c.name, finance, payouts, payoutCard: c.payout_card_masked });
  const orders = await query<any>('SELECT * FROM orders WHERE chef_id = $1 ORDER BY id DESC LIMIT 50', [id]);
  const reviews = await query('SELECT id, order_id, rating, tags, comment, created_at FROM reviews WHERE chef_id = $1 ORDER BY id DESC LIMIT 50', [id]);
  const dishes = await query('SELECT id, name, price, type, is_active, photo_urls FROM dishes WHERE chef_id = $1 AND NOT is_deleted ORDER BY id', [id]);
  res.json({
    id, nick: c.nick, name: c.name, firstName: c.first_name, lastName: c.last_name, phone: c.phone, bio: c.bio,
    photoUrl: c.photo_url, coverUrl: c.cover_url, district: c.district, addressText: c.address_text,
    isOnline: c.is_online, isVerified: c.is_verified, isBlocked: c.is_blocked, blockedUntil: c.blocked_until, blockReason: c.block_reason,
    verificationStatus: c.verification_status, verificationNote: c.verification_note, kitchenPhotoUrls: c.kitchen_photo_urls, aiVerdict: c.ai_verdict,
    rating: c.rating, reviewsCount: c.reviews_count, totalOrders: c.total_orders, lowRating: c.low_rating_flag,
    payoutCard: c.payout_card_masked, createdAt: c.created_at,
    finance, payouts, reviews, dishes, orders: orders.map((o) => presentOrder(o, 'admin')),
  });
});

adminRouter.get('/admin/verifications', guard('chefs_verify'), async (_req, res) => {
  const rows = await query<any>(
    `SELECT cp.*, u.phone FROM chef_profiles cp JOIN users u ON u.id = cp.user_id
      WHERE cp.verification_status = 'pending' ORDER BY cp.created_at`);
  res.json(rows.map((c) => ({
    id: c.user_id, nick: c.nick, name: `${c.first_name ?? ''} ${c.last_name ?? ''}`.trim(), phone: c.phone, district: c.district,
    photoUrl: c.photo_url, kitchenPhotoUrls: c.kitchen_photo_urls, aiVerdict: c.ai_verdict, payoutCardAdded: !!c.payout_card_token, createdAt: c.created_at,
  })));
});

adminRouter.put('/admin/chefs/:id/verify', guard('chefs_verify'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const b = parse(z.object({ approve: z.boolean(), reason: z.string().trim().max(500).optional() }), req.body);
  if (!b.approve && !b.reason) throw badRequest('Укажите причину отклонения');
  const c = await one<any>(
    `UPDATE chef_profiles SET verification_status = $2, is_verified = $3, verification_note = $4 WHERE user_id = $1 RETURNING user_id`,
    [id, b.approve ? 'approved' : 'rejected', b.approve, b.reason ?? null]);
  if (!c) throw notFound('Повар не найден');
  await notify(id, 'verification', b.approve ? 'Профиль подтверждён' : 'Заявка отклонена',
    b.approve ? 'Поздравляем! Теперь вы можете выйти онлайн и принимать заказы.' : `Причина: ${b.reason}. Исправьте и отправьте заявку повторно.`, {});
  await audit(req, b.approve ? 'verify_approve' : 'verify_reject', 'chef', id, b);
  clearFeedCache();
  res.json({ ok: true });
});

adminRouter.put('/admin/chefs/:id/block', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const b = parse(z.object({ blocked: z.boolean(), hours: z.number().int().min(1).max(24 * 365).optional(), reason: z.string().trim().max(500).optional() }), req.body);
  if (b.blocked) {
    const permanent = !b.hours;
    // Поддержка может блокировать только временно и не более чем на 24 часа (ТЗ 7.1)
    if (permanent || b.hours! > 24) { if (!can(role(req), 'chefs_block_permanent')) throw forbidden('Можно заблокировать максимум на 24 часа'); }
    else if (!can(role(req), 'chefs_block_temporary')) throw forbidden();
    if (!b.reason) throw badRequest('Укажите причину блокировки');
  } else if (!can(role(req), 'chefs_block_temporary')) throw forbidden();
  const c = await one<any>(
    `UPDATE chef_profiles SET is_blocked = $2, is_online = CASE WHEN $2 THEN FALSE ELSE is_online END,
            blocked_until = CASE WHEN $2 AND $3::int IS NOT NULL THEN now() + make_interval(hours => $3::int) ELSE NULL END,
            block_reason = CASE WHEN $2 THEN $4 ELSE NULL END
      WHERE user_id = $1 RETURNING user_id, blocked_until`, [id, b.blocked, b.hours ?? null, b.reason ?? null]);
  if (!c) throw notFound('Повар не найден');
  await notify(id, 'block', b.blocked ? 'Аккаунт заблокирован' : 'Аккаунт разблокирован',
    b.blocked ? `Причина: ${b.reason}${b.hours ? `. Срок: ${b.hours} ч.` : ''}` : 'Вы снова можете принимать заказы.', {});
  await audit(req, b.blocked ? 'block' : 'unblock', 'chef', id, b);
  clearFeedCache();
  res.json({ ok: true, blockedUntil: c.blocked_until });
});

adminRouter.post('/admin/chefs/:id/message', guard('complaints'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const { text } = parse(z.object({ text: z.string().trim().min(1).max(1000) }), req.body);
  await notify(id, 'support_message', 'Сообщение от поддержки', text, {});
  await audit(req, 'message', 'chef', id, { text });
  res.json({ ok: true });
});

// ---------- Покупатели ----------
adminRouter.get('/admin/buyers', guard('buyers_view'), async (req, res) => {
  const q = parse(z.object({ q: z.string().trim().optional(), page: z.coerce.number().int().min(1).default(1) }), req.query);
  const params: unknown[] = [];
  let search = 'TRUE';
  if (q.q) { params.push(`%${q.q}%`); search = '(u.phone ILIKE $1 OR u.name ILIKE $1)'; }
  params.push((q.page - 1) * 50);
  const rows = await query<any>(
    `SELECT u.id, u.phone, u.name, u.is_active, u.created_at,
            (SELECT count(*) FROM orders o WHERE o.buyer_id = u.id)::int AS orders,
            (SELECT COALESCE(sum(total_amount - refunded_amount),0) FROM orders o WHERE o.buyer_id = u.id AND o.status IN ('DELIVERED','RATED'))::int AS spent,
            count(*) OVER()::int AS total_count
       FROM users u WHERE u.role = 'buyer' AND u.deleted_at IS NULL AND ${search} ORDER BY u.id DESC LIMIT 50 OFFSET $${params.length}`, params);
  res.json({ total: rows[0]?.total_count ?? 0, items: rows.map(({ total_count, ...r }) => r) });
});

adminRouter.get('/admin/buyers/:id', guard('buyers_view'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const u = await one<any>(`SELECT id, phone, name, is_active, created_at FROM users WHERE id = $1 AND role = 'buyer'`, [id]);
  if (!u) throw notFound('Покупатель не найден');
  const orders = await query<any>('SELECT * FROM orders WHERE buyer_id = $1 ORDER BY id DESC LIMIT 50', [id]);
  const complaints = await query('SELECT id, order_id, type, status, created_at FROM complaints WHERE buyer_id = $1 ORDER BY id DESC', [id]);
  res.json({ ...u, orders: orders.map((o) => presentOrder(o, 'admin')), complaints });
});

adminRouter.put('/admin/buyers/:id/block', guard('chefs_block_permanent'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const { blocked } = parse(z.object({ blocked: z.boolean() }), req.body);
  await query(`UPDATE users SET is_active = $2 WHERE id = $1 AND role = 'buyer'`, [id, !blocked]);
  if (blocked) await query(`UPDATE refresh_tokens SET revoked = TRUE WHERE subject = $1`, [`user:${id}`]);
  await audit(req, blocked ? 'block' : 'unblock', 'buyer', id);
  res.json({ ok: true });
});

// ---------- Жалобы (ТЗ 7.5) ----------
adminRouter.get('/admin/complaints', guard('complaints'), async (req, res) => {
  const { status } = parse(z.object({ status: z.enum(['all', 'open', 'resolved', 'closed']).default('open') }), req.query);
  const rows = await query<any>(
    `SELECT c.*, u.phone AS buyer_phone, u.name AS buyer_name, cp.nick AS chef_nick FROM complaints c
       JOIN users u ON u.id = c.buyer_id JOIN chef_profiles cp ON cp.user_id = c.chef_id
      WHERE ($1 = 'all' OR c.status = $1) ORDER BY c.id DESC LIMIT 200`, [status]);
  res.json(rows);
});

adminRouter.get('/admin/complaints/:id', guard('complaints'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const c = await one<any>(
    `SELECT c.*, u.phone AS buyer_phone, u.name AS buyer_name, cp.nick AS chef_nick FROM complaints c
       JOIN users u ON u.id = c.buyer_id JOIN chef_profiles cp ON cp.user_id = c.chef_id WHERE c.id = $1`, [id]);
  if (!c) throw notFound('Жалоба не найдена');
  const o = await one<any>('SELECT * FROM orders WHERE id = $1', [c.order_id]);
  res.json({ ...c, order: presentOrder(o, 'admin') });
});

adminRouter.put('/admin/complaints/:id', guard('complaints'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const b = parse(z.object({ action: z.enum(['warn_chef', 'close', 'resolve']), note: z.string().trim().max(1000).optional() }), req.body);
  const c = await one<any>('SELECT * FROM complaints WHERE id = $1', [id]);
  if (!c) throw notFound('Жалоба не найдена');
  if (b.action === 'warn_chef') {
    await notify(c.chef_id, 'warning', 'Предупреждение', b.note || `Получена жалоба по заказу #${c.order_id}. Пожалуйста, будьте внимательнее.`, { orderId: c.order_id });
  }
  const status = b.action === 'close' ? 'closed' : 'resolved';
  await query('UPDATE complaints SET status = $2, resolution_note = COALESCE($3, resolution_note), resolved_by = $4 WHERE id = $1', [id, status, b.note ?? null, uid(req)]);
  await audit(req, `complaint_${b.action}`, 'complaint', id, b);
  res.json({ ok: true, status });
});

// ---------- Финансы (ТЗ 7.6) ----------
function periodStart(period: string): Date {
  const days = { day: 0, week: 6, month: 29, year: 364 }[period] ?? 29;
  return startOfTashkentDay(new Date(), -days);
}

adminRouter.get('/admin/finance', guard('finance'), async (req, res) => {
  const q = parse(z.object({ period: z.enum(['day', 'week', 'month', 'year']).default('month') }), req.query);
  const from = periodStart(q.period);
  const s = await one<any>(
    `SELECT COALESCE(sum(total_amount - refunded_amount),0)::bigint AS turnover,
            COALESCE(sum(platform_commission),0)::bigint AS commission,
            COALESCE(sum(delivery_buffer),0)::bigint AS delivery_buffer,
            COALESCE(sum(delivery_fee_real),0)::bigint AS delivery_cost,
            COALESCE(sum(chef_tax),0)::bigint AS chef_tax,
            COALESCE(sum(refunded_amount),0)::bigint AS refunds,
            count(*)::int AS orders
       FROM orders WHERE status IN ('DELIVERED','RATED') AND delivered_at >= $1`, [from]);
  // Возвраты, удержанные с поваров, уменьшают комиссию и налог; остальное ложится на платформу
  const chefRefunds = await one<any>(
    `SELECT COALESCE(sum(-commission),0)::bigint AS commission, COALESCE(sum(-tax),0)::bigint AS tax, COALESCE(sum(-gross_amount),0)::bigint AS gross
       FROM chef_earnings WHERE kind = 'refund' AND created_at >= $1`, [from]);
  const commission = s.commission - chefRefunds.commission;
  const chefTax = s.chef_tax - chefRefunds.tax;
  const platformLoss = s.refunds - chefRefunds.gross; // часть возвратов за счёт платформы (доставка)
  const income = commission + s.delivery_buffer - platformLoss;
  const itPark = Math.round((income * config.business.itParkPct) / 100);
  const paid = await one<any>(`SELECT COALESCE(sum(net_amount),0)::bigint AS s FROM chef_payouts WHERE status = 'done' AND paid_at >= $1`, [from]);
  const earlyFees = await one<any>(`SELECT COALESCE(sum(commission_1pct),0)::bigint AS s FROM chef_payouts WHERE kind = 'early' AND status = 'done' AND paid_at >= $1`, [from]);
  const chart = await query<any>(
    `SELECT to_char(delivered_at AT TIME ZONE 'Asia/Tashkent', $2) AS bucket,
            COALESCE(sum(total_amount - refunded_amount),0)::bigint AS turnover,
            COALESCE(sum(platform_commission + COALESCE(delivery_buffer,0)),0)::bigint AS income
       FROM orders WHERE status IN ('DELIVERED','RATED') AND delivered_at >= $1 GROUP BY 1 ORDER BY 1`,
    [from, q.period === 'year' ? 'YYYY-MM' : q.period === 'day' ? 'HH24:00' : 'YYYY-MM-DD']);
  res.json({
    period: q.period, from,
    turnover: s.turnover, orders: s.orders,
    commission, deliveryBuffer: s.delivery_buffer, deliveryCost: s.delivery_cost, earlyWithdrawalFees: earlyFees.s,
    refunds: s.refunds, refundsPlatformShare: platformLoss,
    platformIncome: income + earlyFees.s, itParkFee: itPark, netIncome: income + earlyFees.s - itPark,
    chefTaxWithheld: chefTax, paidToChefs: paid.s,
    chart,
  });
});

adminRouter.get('/admin/payouts', guard('finance'), async (req, res) => {
  const q = parse(z.object({ status: z.enum(['all', 'pending', 'processing', 'done', 'failed']).default('all'), kind: z.enum(['all', 'weekly', 'early']).default('all') }), req.query);
  const rows = await query<any>(
    `SELECT p.*, cp.nick, cp.payout_card_masked FROM chef_payouts p JOIN chef_profiles cp ON cp.user_id = p.chef_id
      WHERE ($1 = 'all' OR p.status = $1) AND ($2 = 'all' OR p.kind = $2) ORDER BY p.id DESC LIMIT 300`, [q.status, q.kind]);
  const upcoming = await query<any>(
    `SELECT e.chef_id, cp.nick, cp.payout_card_masked, sum(e.net_amount)::int AS amount FROM chef_earnings e
       JOIN chef_profiles cp ON cp.user_id = e.chef_id WHERE NOT e.is_paid GROUP BY e.chef_id, cp.nick, cp.payout_card_masked
      HAVING sum(e.net_amount) > 0 ORDER BY amount DESC`);
  res.json({ nextPayoutAt: nextPayoutAt(), upcoming, items: rows });
});

adminRouter.post('/admin/payouts/run', guard('payouts_run'), async (req, res) => {
  const { confirm } = parse(z.object({ confirm: z.literal(true) }), req.body);
  const r = await runWeeklyPayouts();
  await audit(req, 'payouts_run', 'payouts', '-', { confirm, ...r });
  res.json(r);
});

adminRouter.post('/admin/payouts/:id/complete', guard('payouts_run'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const b = parse(z.object({ ok: z.boolean(), note: z.string().trim().max(300).optional() }), req.body);
  const p = await completeManualPayout(id, b.ok, b.note);
  await audit(req, b.ok ? 'payout_done' : 'payout_reject', 'payout', id, b);
  res.json(p);
});

// ---------- Отчёты Excel ----------
async function sendXlsx(res: Response, wb: ExcelJS.Workbook, name: string) {
  res.setHeader('Content-Type', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
  res.setHeader('Content-Disposition', `attachment; filename="${name}"`);
  const buf = await wb.xlsx.writeBuffer();
  res.send(Buffer.from(buf as ArrayBuffer));
}

function reportRange(req: Request) {
  const q = parse(range, req.query);
  const from = q.from ?? startOfTashkentDay(new Date(), -30);
  const to = q.to ?? new Date();
  return { from, to, label: `${from.toISOString().slice(0, 10)}_${to.toISOString().slice(0, 10)}` };
}

/** Налоговый отчёт: повар, оборот, удержанный налог 4% (ТЗ 7.6). */
adminRouter.get('/admin/reports/tax', guard('tax_reports'), async (req, res) => {
  const { from, to, label } = reportRange(req);
  const rows = await query<any>(
    `SELECT cp.nick, cp.first_name, cp.last_name, u.phone, cp.payout_card_masked,
            sum(e.gross_amount)::bigint AS turnover, sum(e.tax)::bigint AS tax, sum(e.commission)::bigint AS commission, sum(e.net_amount)::bigint AS net
       FROM chef_earnings e JOIN chef_profiles cp ON cp.user_id = e.chef_id JOIN users u ON u.id = e.chef_id
      WHERE e.created_at >= $1 AND e.created_at <= $2 GROUP BY cp.user_id, u.phone ORDER BY turnover DESC`, [from, to]);
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet('Налог поваров');
  ws.columns = [
    { header: 'Ник', key: 'nick', width: 18 }, { header: 'ФИО', key: 'fio', width: 28 }, { header: 'Телефон', key: 'phone', width: 16 },
    { header: 'Карта', key: 'card', width: 22 }, { header: 'Оборот, сум', key: 'turnover', width: 16 }, { header: 'Налог 4%, сум', key: 'tax', width: 16 },
    { header: 'Комиссия, сум', key: 'commission', width: 16 }, { header: 'К выплате, сум', key: 'net', width: 16 },
  ];
  for (const r of rows) ws.addRow({ nick: r.nick, fio: `${r.last_name ?? ''} ${r.first_name ?? ''}`.trim(), phone: r.phone, card: r.payout_card_masked, turnover: r.turnover, tax: r.tax, commission: r.commission, net: r.net });
  ws.addRow({ nick: 'ИТОГО', turnover: rows.reduce((s, r) => s + r.turnover, 0), tax: rows.reduce((s, r) => s + r.tax, 0), commission: rows.reduce((s, r) => s + r.commission, 0), net: rows.reduce((s, r) => s + r.net, 0) }).font = { bold: true };
  ws.getRow(1).font = { bold: true };
  await audit(req, 'report_tax', 'report', label);
  await sendXlsx(res, wb, `tax_report_${label}.xlsx`);
});

/** Все транзакции за период + сводный лист. */
adminRouter.get('/admin/reports/transactions', guard('tax_reports'), async (req, res) => {
  const { from, to, label } = reportRange(req);
  const orders = await query<any>(
    `SELECT o.id, o.created_at, o.delivered_at, o.status, cp.nick, u.phone AS buyer_phone, o.dish_total, o.delivery_fee_shown, o.delivery_fee_real,
            o.delivery_buffer, o.platform_commission, o.chef_tax, o.chef_net_amount, o.total_amount, o.refunded_amount, o.payment_status
       FROM orders o JOIN chef_profiles cp ON cp.user_id = o.chef_id JOIN users u ON u.id = o.buyer_id
      WHERE o.created_at >= $1 AND o.created_at <= $2 ORDER BY o.id`, [from, to]);
  const payouts = await query<any>(
    `SELECT p.id, cp.nick, p.kind, p.amount, p.commission_1pct, p.net_amount, p.status, p.bank_transaction_id, p.created_at, p.paid_at
       FROM chef_payouts p JOIN chef_profiles cp ON cp.user_id = p.chef_id WHERE p.created_at >= $1 AND p.created_at <= $2 ORDER BY p.id`, [from, to]);
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet('Заказы');
  ws.columns = [
    ['id', '№'], ['created_at', 'Создан'], ['delivered_at', 'Доставлен'], ['status', 'Статус'], ['nick', 'Повар'], ['buyer_phone', 'Покупатель'],
    ['dish_total', 'Блюда'], ['delivery_fee_shown', 'Доставка (покупатель)'], ['delivery_fee_real', 'Доставка (Яндекс)'], ['delivery_buffer', 'Буфер'],
    ['platform_commission', 'Комиссия 20%'], ['chef_tax', 'Налог 4%'], ['chef_net_amount', 'Повару'], ['total_amount', 'Итого'],
    ['refunded_amount', 'Возвращено'], ['payment_status', 'Оплата'],
  ].map(([key, header]) => ({ key, header, width: 16 }));
  orders.forEach((o) => ws.addRow(o));
  ws.getRow(1).font = { bold: true };
  const wp = wb.addWorksheet('Выплаты');
  wp.columns = [['id', '№'], ['nick', 'Повар'], ['kind', 'Тип'], ['amount', 'Сумма'], ['commission_1pct', 'Комиссия 1%'], ['net_amount', 'Выплачено'],
    ['status', 'Статус'], ['bank_transaction_id', 'Транзакция банка'], ['created_at', 'Создана'], ['paid_at', 'Выплачена']]
    .map(([key, header]) => ({ key, header, width: 18 }));
  payouts.forEach((p) => wp.addRow(p));
  wp.getRow(1).font = { bold: true };
  const done = orders.filter((o) => ['DELIVERED', 'RATED'].includes(o.status));
  const sum = (k: string) => done.reduce((s, o) => s + (o[k] ?? 0), 0);
  const wsum = wb.addWorksheet('Сводка');
  wsum.columns = [{ header: 'Показатель', key: 'k', width: 34 }, { header: 'Значение', key: 'v', width: 18 }];
  [
    ['Период', `${from.toISOString().slice(0, 10)} — ${to.toISOString().slice(0, 10)}`],
    ['Выполнено заказов', done.length], ['Отменено заказов', orders.filter((o) => o.status === 'CANCELLED').length],
    ['Оборот (итого с доставкой)', sum('total_amount')], ['Стоимость блюд', sum('dish_total')],
    ['Комиссия платформы', sum('platform_commission')], ['Буфер доставки', sum('delivery_buffer')],
    ['Налог поваров удержан', sum('chef_tax')], ['Начислено поварам', sum('chef_net_amount')], ['Возвраты', sum('refunded_amount')],
    ['Выплачено поварам', payouts.filter((p) => p.status === 'done').reduce((s, p) => s + p.net_amount, 0)],
  ].forEach(([k, v]) => wsum.addRow({ k, v }));
  wsum.getRow(1).font = { bold: true };
  await audit(req, 'report_transactions', 'report', label);
  await sendXlsx(res, wb, `transactions_${label}.xlsx`);
});

// ---------- Сотрудники и настройки (только владелец) ----------
adminRouter.get('/admin/staff', guard('staff_manage'), async (_req, res) => {
  res.json(await query('SELECT id, name, email, role, is_active, created_at FROM admin_staff ORDER BY id'));
});

adminRouter.post('/admin/staff', guard('staff_manage'), async (req, res) => {
  const b = parse(z.object({ name: z.string().trim().min(2), email: z.string().email(), password: z.string().min(8), role: z.enum(['owner', 'accountant', 'support']) }), req.body);
  const exists = await one('SELECT 1 FROM admin_staff WHERE lower(email) = lower($1)', [b.email]);
  if (exists) throw conflict('Сотрудник с таким email уже есть');
  const s = await one<any>('INSERT INTO admin_staff (name, email, password_hash, role) VALUES ($1,$2,$3,$4) RETURNING id, name, email, role, is_active, created_at',
    [b.name, b.email, await bcrypt.hash(b.password, 12), b.role]);
  await audit(req, 'staff_create', 'staff', s.id, { email: b.email, role: b.role });
  res.status(201).json(s);
});

adminRouter.put('/admin/staff/:id', guard('staff_manage'), async (req, res) => {
  const id = parse(idParam, req.params.id);
  const b = parse(z.object({ role: z.enum(['owner', 'accountant', 'support']).optional(), isActive: z.boolean().optional(), password: z.string().min(8).optional(), name: z.string().trim().min(2).optional() }), req.body);
  if (id === uid(req) && (b.isActive === false || (b.role && b.role !== 'owner'))) throw badRequest('Нельзя понизить или отключить самого себя');
  const s = await tx(async (c) => {
    const cur = await one<any>('SELECT * FROM admin_staff WHERE id = $1 FOR UPDATE', [id], c);
    if (!cur) throw notFound('Сотрудник не найден');
    const hash = b.password ? await bcrypt.hash(b.password, 12) : cur.password_hash;
    const r = await one<any>('UPDATE admin_staff SET role = $2, is_active = $3, password_hash = $4, name = $5 WHERE id = $1 RETURNING id, name, email, role, is_active',
      [id, b.role ?? cur.role, b.isActive ?? cur.is_active, hash, b.name ?? cur.name], c);
    if (b.isActive === false || b.password) await query('UPDATE refresh_tokens SET revoked = TRUE WHERE subject = $1', [`staff:${id}`], c);
    return r;
  });
  await audit(req, 'staff_update', 'staff', id, { ...b, password: b.password ? '***' : undefined });
  res.json(s);
});

adminRouter.get('/admin/settings', guard('settings'), async (_req, res) => { res.json(await getSettings()); });

adminRouter.put('/admin/settings', guard('settings'), async (req, res) => {
  const b = parse(z.object({
    commissionPct: z.number().min(0).max(50).optional(),
    deliveryBufferPct: z.number().min(0).max(100).optional(),
    earlyWithdrawPct: z.number().min(0).max(10).optional(),
    chefAcceptTimeoutSec: z.number().int().min(60).max(1800).optional(),
  }), req.body);
  const s = await saveSettings(b);
  await audit(req, 'settings_update', 'settings', '-', b);
  res.json(s);
});

adminRouter.get('/admin/audit', guard('staff_manage'), async (_req, res) => {
  res.json(await query(`SELECT a.*, s.name AS staff_name FROM audit_log a LEFT JOIN admin_staff s ON s.id = a.staff_id ORDER BY a.id DESC LIMIT 300`));
});
