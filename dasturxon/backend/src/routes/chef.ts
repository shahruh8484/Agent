import { Router, type Request } from 'express';
import { z } from 'zod';
import { requireUser, uid } from '../auth/middleware.js';
import { one, query } from '../db.js';
import { verifyKitchenPhotos } from '../integrations/ai.js';
import { payments } from '../integrations/payments.js';
import { emit, rooms } from '../integrations/realtime.js';
import { badRequest, conflict, forbidden, notFound } from '../lib/errors.js';
import { idParam, parse } from '../lib/validate.js';
import { clearFeedCache } from '../services/feed.js';
import { acceptOrder, ACTIVE_STATUSES, declineOrder, markReady, presentOrder } from '../services/orders.js';
import { chefBalance, chefTransactions, requestEarlyWithdrawal } from '../services/payouts.js';

export const chefRouter = Router();
chefRouter.use('/chef', requireUser('chef'));

// Районы Ташкента для выбора района работы (ТЗ 4.1)
export const TASHKENT_DISTRICTS = ['Алмазар', 'Бектемир', 'Мирабад', 'Мирзо-Улугбек', 'Сергели', 'Учтепа', 'Чиланзар',
  'Шайхантахур', 'Юнусабад', 'Яккасарай', 'Яшнабад', 'Янгихаёт'];

async function myProfile(req: Request) {
  const p = await one<any>('SELECT * FROM chef_profiles WHERE user_id = $1', [uid(req)]);
  if (!p) throw badRequest('Сначала заполните профиль повара');
  return p;
}

async function requireApproved(req: Request) {
  const p = await myProfile(req);
  if (!p.is_verified) throw forbidden('Профиль ещё не прошёл верификацию');
  if (p.is_blocked) throw forbidden('Аккаунт заблокирован');
  return p;
}

function presentProfile(p: any, u: any) {
  return {
    id: p.user_id, phone: u.phone, nick: p.nick, firstName: p.first_name, lastName: p.last_name, bio: p.bio,
    photoUrl: p.photo_url, coverUrl: p.cover_url, district: p.district, addressText: p.address_text,
    addressLat: p.address_lat, addressLon: p.address_lon, isOnline: p.is_online,
    verificationStatus: p.verification_status, verificationNote: p.verification_note, kitchenPhotoUrls: p.kitchen_photo_urls,
    isVerified: p.is_verified, isBlocked: p.is_blocked, blockedUntil: p.blocked_until,
    rating: p.rating, reviewsCount: p.reviews_count, totalOrders: p.total_orders,
    payoutCard: p.payout_card_masked ? { maskedPan: p.payout_card_masked, cardType: p.card_type } : null,
    termsAccepted: !!p.terms_accepted_at, createdAt: p.created_at,
  };
}

chefRouter.get('/chef/districts', (_req, res) => { res.json(TASHKENT_DISTRICTS); });

chefRouter.get('/chef/profile', async (req, res) => {
  const u = await one<any>('SELECT phone FROM users WHERE id = $1', [uid(req)]);
  const p = await one<any>('SELECT * FROM chef_profiles WHERE user_id = $1', [uid(req)]);
  res.json(p ? presentProfile(p, u) : { id: uid(req), phone: u.phone, registrationStep: 1 });
});

// Шаги 1–2 регистрации: ник, имя, фото, описание, район и адрес (ТЗ 4.1)
const profileSchema = z.object({
  nick: z.string().trim().regex(/^[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9_]{3,30}$/, 'Ник: 3–30 символов, буквы/цифры, без пробелов'),
  firstName: z.string().trim().min(1).max(50),
  lastName: z.string().trim().min(1).max(50),
  photoUrl: z.string().url(),
  coverUrl: z.string().url(),
  bio: z.string().trim().max(500).optional(),
  district: z.enum(TASHKENT_DISTRICTS as [string, ...string[]]),
  addressText: z.string().trim().min(3).max(300),
  addressLat: z.number(),
  addressLon: z.number(),
});

chefRouter.put('/chef/profile', async (req, res) => {
  const existing = await one<any>('SELECT * FROM chef_profiles WHERE user_id = $1', [uid(req)]);
  const b = parse(existing ? profileSchema.partial() : profileSchema, req.body);
  if (b.nick) {
    const taken = await one('SELECT 1 FROM chef_profiles WHERE lower(nick) = lower($1) AND user_id <> $2', [b.nick, uid(req)]);
    if (taken) throw conflict('Этот ник уже занят');
  }
  const map: Record<string, string> = {
    nick: 'nick', firstName: 'first_name', lastName: 'last_name', photoUrl: 'photo_url', coverUrl: 'cover_url', bio: 'bio',
    district: 'district', addressText: 'address_text', addressLat: 'address_lat', addressLon: 'address_lon',
  };
  const entries = Object.entries(b).filter(([, v]) => v !== undefined);
  if (!existing) {
    const cols = entries.map(([k]) => map[k]);
    await query(`INSERT INTO chef_profiles (user_id, ${cols.join(', ')}) VALUES ($1, ${cols.map((_, i) => `$${i + 2}`).join(', ')})`,
      [uid(req), ...entries.map(([, v]) => v)]);
  } else if (entries.length) {
    await query(`UPDATE chef_profiles SET ${entries.map(([k], i) => `${map[k]} = $${i + 2}`).join(', ')} WHERE user_id = $1`,
      [uid(req), ...entries.map(([, v]) => v)]);
  }
  if (b.firstName || b.lastName) {
    const p = await one<any>('SELECT first_name, last_name FROM chef_profiles WHERE user_id = $1', [uid(req)]);
    await query('UPDATE users SET name = $2 WHERE id = $1', [uid(req), `${p.first_name} ${p.last_name}`]);
  }
  clearFeedCache();
  const u = await one<any>('SELECT phone FROM users WHERE id = $1', [uid(req)]);
  res.json(presentProfile(await myProfile(req), u));
});

// Шаг 3: фото кухни → AI-проверка → «На проверке» (финально решает администратор)
chefRouter.post('/chef/verification', async (req, res) => {
  const p = await myProfile(req);
  if (p.verification_status === 'approved') throw conflict('Профиль уже подтверждён');
  const { kitchenPhotoUrls } = parse(z.object({ kitchenPhotoUrls: z.array(z.string().url()).min(1).max(3) }), req.body);
  const verdict = await verifyKitchenPhotos(kitchenPhotoUrls).catch((e) => {
    console.error('[ai] verification failed', e);
    return { approved: true, reason: 'Ошибка AI-проверки — требуется ручная проверка', source: 'skipped' as const };
  });
  if (!verdict.approved) {
    await query(`UPDATE chef_profiles SET kitchen_photo_urls = $2, ai_verdict = $3, verification_status = 'rejected', verification_note = $4 WHERE user_id = $1`,
      [uid(req), kitchenPhotoUrls, verdict, verdict.reason]);
    return res.json({ status: 'rejected', reason: verdict.reason, retake: true });
  }
  await query(`UPDATE chef_profiles SET kitchen_photo_urls = $2, ai_verdict = $3, verification_status = 'pending', verification_note = NULL WHERE user_id = $1`,
    [uid(req), kitchenPhotoUrls, verdict]);
  emit(rooms.admin, 'alert', { type: 'verification', chefId: uid(req) });
  res.json({ status: 'pending', aiChecked: verdict.source === 'ai' });
});

// Шаг 4: карта для выплат + согласие с условиями (налог 4% удерживается платформой)
chefRouter.put('/chef/payout-card', async (req, res) => {
  await myProfile(req);
  const b = parse(z.object({ cardToken: z.string().min(4), acceptTerms: z.literal(true) }), req.body);
  const bound = await payments.bindCard(b.cardToken);
  if (bound.cardType === 'payme') throw badRequest('Для выплат нужна карта UZCARD или HUMO');
  await query('UPDATE chef_profiles SET payout_card_token = $2, payout_card_masked = $3, card_type = $4, terms_accepted_at = COALESCE(terms_accepted_at, now()) WHERE user_id = $1',
    [uid(req), bound.token, bound.maskedPan, bound.cardType]);
  res.json({ maskedPan: bound.maskedPan, cardType: bound.cardType });
});

chefRouter.put('/chef/status', async (req, res) => {
  const p = await requireApproved(req);
  const { online } = parse(z.object({ online: z.boolean() }), req.body);
  if (online && !p.payout_card_token) throw badRequest('Добавьте карту для выплат, чтобы выйти онлайн');
  await query('UPDATE chef_profiles SET is_online = $2 WHERE user_id = $1', [uid(req), online]);
  clearFeedCache();
  res.json({ online });
});

// ----- Блюда (ТЗ 4.3) -----
const dishSchema = z.object({
  name: z.string().trim().min(2).max(100),
  description: z.string().trim().max(300).optional(),
  category: z.enum(['breakfast', 'lunch', 'dinner', 'bakery', 'drinks', 'other']),
  price: z.number().int().min(1000).max(10_000_000),
  weight: z.string().trim().max(50).optional(),
  photoUrls: z.array(z.string().url()).min(1).max(5),
  portionsLeft: z.number().int().min(0).max(1000).nullable().optional(),
  type: z.enum(['ready', 'order', 'both']),
  prepTimeMinutes: z.number().int().min(5).max(24 * 60).nullable().optional(),
  ingredients: z.string().trim().max(500).optional(),
  isVegan: z.boolean().optional(),
}).refine((d) => d.type === 'ready' || d.prepTimeMinutes, { message: 'Укажите время приготовления для блюд «На заказ»', path: ['prepTimeMinutes'] });

const presentDish = (d: any) => ({
  id: d.id, name: d.name, description: d.description, category: d.category, price: d.price, weight: d.weight,
  photoUrls: d.photo_urls, portionsLeft: d.portions_left, type: d.type, prepTimeMinutes: d.prep_time_minutes,
  ingredients: d.ingredients, isVegan: d.is_vegan, isActive: d.is_active, createdAt: d.created_at,
});

chefRouter.get('/chef/dishes', async (req, res) => {
  res.json((await query<any>('SELECT * FROM dishes WHERE chef_id = $1 AND NOT is_deleted ORDER BY id', [uid(req)])).map(presentDish));
});

chefRouter.post('/chef/dishes', async (req, res) => {
  await myProfile(req);
  const d = parse(dishSchema, req.body);
  const row = await one<any>(
    `INSERT INTO dishes (chef_id, name, description, category, price, weight, photo_urls, portions_left, type, prep_time_minutes, ingredients, is_vegan)
     VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) RETURNING *`,
    [uid(req), d.name, d.description ?? null, d.category, d.price, d.weight ?? null, d.photoUrls, d.portionsLeft ?? null, d.type,
      d.prepTimeMinutes ?? null, d.ingredients ?? null, d.isVegan ?? false]);
  clearFeedCache();
  res.status(201).json(presentDish(row));
});

chefRouter.put('/chef/dishes/:id', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const cur = await one<any>('SELECT * FROM dishes WHERE id = $1 AND chef_id = $2 AND NOT is_deleted', [id, uid(req)]);
  if (!cur) throw notFound('Блюдо не найдено');
  const merged = parse(dishSchema, {
    ...presentDish(cur), description: cur.description ?? undefined, weight: cur.weight ?? undefined, ingredients: cur.ingredients ?? undefined, ...req.body,
  });
  const row = await one<any>(
    `UPDATE dishes SET name=$3, description=$4, category=$5, price=$6, weight=$7, photo_urls=$8, portions_left=$9, type=$10,
       prep_time_minutes=$11, ingredients=$12, is_vegan=$13 WHERE id = $1 AND chef_id = $2 RETURNING *`,
    [id, uid(req), merged.name, merged.description ?? null, merged.category, merged.price, merged.weight ?? null, merged.photoUrls,
      merged.portionsLeft ?? null, merged.type, merged.prepTimeMinutes ?? null, merged.ingredients ?? null, merged.isVegan ?? false]);
  clearFeedCache();
  res.json(presentDish(row));
});

chefRouter.put('/chef/dishes/:id/toggle', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const b = parse(z.object({ active: z.boolean().optional() }), req.body ?? {});
  const row = await one<any>('UPDATE dishes SET is_active = COALESCE($3, NOT is_active) WHERE id = $1 AND chef_id = $2 AND NOT is_deleted RETURNING *',
    [id, uid(req), b.active ?? null]);
  if (!row) throw notFound('Блюдо не найдено');
  clearFeedCache();
  res.json(presentDish(row));
});

chefRouter.delete('/chef/dishes/:id', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const row = await one('UPDATE dishes SET is_deleted = TRUE, is_active = FALSE WHERE id = $1 AND chef_id = $2 RETURNING id', [id, uid(req)]);
  if (!row) throw notFound('Блюдо не найдено');
  clearFeedCache();
  res.json({ ok: true });
});

// ----- Заказы повара (ТЗ 4.2) -----
chefRouter.get('/chef/orders', async (req, res) => {
  const { scope } = parse(z.object({ scope: z.enum(['new', 'active', 'today', 'all']).default('all') }), req.query);
  const where = {
    new: `status = 'PENDING'`,
    active: `status IN ('CONFIRMED','COOKING','READY','COURIER_ASSIGNED','IN_DELIVERY')`,
    today: `status IN ('DELIVERED','RATED') AND delivered_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Tashkent') AT TIME ZONE 'Asia/Tashkent'`,
    all: 'TRUE',
  }[scope];
  const rows = await query<any>(
    `SELECT o.*, r.rating AS review_rating FROM orders o LEFT JOIN reviews r ON r.order_id = o.id
      WHERE o.chef_id = $1 AND ${where} ORDER BY o.created_at DESC LIMIT 100`, [uid(req)]);
  res.json(rows.map((o) => ({ ...presentOrder(o, 'chef'), reviewRating: o.review_rating ?? null })));
});

chefRouter.get('/chef/orders/summary', async (req, res) => {
  const r = await one<any>(
    `SELECT count(*) FILTER (WHERE status = 'PENDING')::int AS new,
            count(*) FILTER (WHERE status = ANY($2))::int AS active,
            count(*) FILTER (WHERE status IN ('DELIVERED','RATED') AND delivered_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Tashkent') AT TIME ZONE 'Asia/Tashkent')::int AS done_today,
            COALESCE(sum(chef_net_amount) FILTER (WHERE status IN ('DELIVERED','RATED') AND delivered_at >= date_trunc('day', now() AT TIME ZONE 'Asia/Tashkent') AT TIME ZONE 'Asia/Tashkent'),0)::int AS earned_today
       FROM orders WHERE chef_id = $1`, [uid(req), ACTIVE_STATUSES.filter((s) => s !== 'PENDING')]);
  res.json(r);
});

chefRouter.post('/chef/orders/:id/accept', async (req, res) => {
  await requireApproved(req);
  res.json(presentOrder(await acceptOrder(uid(req), parse(idParam, req.params.id)), 'chef'));
});

chefRouter.post('/chef/orders/:id/decline', async (req, res) => {
  const b = parse(z.object({ reason: z.string().trim().max(200).optional() }), req.body ?? {});
  res.json(presentOrder(await declineOrder(uid(req), parse(idParam, req.params.id), b.reason), 'chef'));
});

chefRouter.post('/chef/orders/:id/ready', async (req, res) => {
  res.json(presentOrder(await markReady(uid(req), parse(idParam, req.params.id)), 'chef'));
});

// ----- Баланс и выплаты (ТЗ 4.4, 6) -----
chefRouter.get('/chef/balance', async (req, res) => { res.json(await chefBalance(uid(req))); });

chefRouter.get('/chef/transactions', async (req, res) => {
  const { page } = parse(z.object({ page: z.coerce.number().int().min(1).default(1) }), req.query);
  res.json(await chefTransactions(uid(req), 50, (page - 1) * 50));
});

chefRouter.post('/chef/payout/request', async (req, res) => {
  await requireApproved(req);
  const { confirm } = parse(z.object({ confirm: z.boolean().default(false) }), req.body ?? {});
  res.json(await requestEarlyWithdrawal(uid(req), confirm));
});

chefRouter.get('/chef/reviews', async (req, res) => {
  const { page } = parse(z.object({ page: z.coerce.number().int().min(1).default(1) }), req.query);
  res.json(await query(`SELECT id, order_id, rating, tags, comment, created_at FROM reviews WHERE chef_id = $1 ORDER BY id DESC LIMIT 20 OFFSET $2`,
    [uid(req), (page - 1) * 20]));
});

chefRouter.get('/chef/stats', async (req, res) => {
  const p = await myProfile(req);
  res.json({ totalOrders: p.total_orders, rating: p.rating, reviewsCount: p.reviews_count, onPlatformSince: p.created_at });
});

chefRouter.get('/chef/notifications', async (req, res) => {
  res.json(await query('SELECT id, type, title, body, data, read_at, created_at FROM notifications WHERE user_id = $1 ORDER BY id DESC LIMIT 100', [uid(req)]));
});
