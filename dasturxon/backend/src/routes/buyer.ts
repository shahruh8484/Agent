import { Router } from 'express';
import { z } from 'zod';
import { requireUser, uid } from '../auth/middleware.js';
import { one, query, tx } from '../db.js';
import { payments } from '../integrations/payments.js';
import { emit, rooms } from '../integrations/realtime.js';
import { badRequest, conflict, notFound } from '../lib/errors.js';
import { idParam, parse } from '../lib/validate.js';
import { ACTIVE_STATUSES, presentOrder } from '../services/orders.js';

export const buyerRouter = Router();
buyerRouter.use('/buyer', requireUser('buyer'));

const presentAddress = (a: any) => ({ id: a.id, label: a.label, lat: a.lat, lon: a.lon, addressText: a.address_text, isDefault: a.is_default });
const presentCard = (c: any) => ({ id: c.id, maskedPan: c.masked_pan, cardType: c.card_type, provider: c.provider, isDefault: c.is_default });

buyerRouter.get('/buyer/profile', async (req, res) => {
  const u = await one<any>('SELECT id, phone, name, created_at FROM users WHERE id = $1', [uid(req)]);
  const addresses = await query<any>('SELECT * FROM addresses WHERE user_id = $1 AND NOT is_deleted ORDER BY is_default DESC, id', [uid(req)]);
  const cards = await query<any>('SELECT * FROM payment_cards WHERE user_id = $1 AND NOT is_deleted ORDER BY is_default DESC, id', [uid(req)]);
  res.json({
    id: u.id, phone: u.phone, name: u.name, createdAt: u.created_at,
    addresses: addresses.map(presentAddress), cards: cards.map(presentCard),
    // Профиль заполнен, когда есть имя, адрес и карта (ТЗ 3.1)
    profileComplete: !!u.name && addresses.length > 0 && cards.length > 0,
  });
});

buyerRouter.put('/buyer/profile', async (req, res) => {
  const { name } = parse(z.object({ name: z.string().trim().min(1).max(80) }), req.body);
  await query('UPDATE users SET name = $2 WHERE id = $1', [uid(req), name]);
  res.json({ ok: true });
});

// ----- Адреса -----
buyerRouter.get('/buyer/addresses', async (req, res) => {
  const rows = await query<any>('SELECT * FROM addresses WHERE user_id = $1 AND NOT is_deleted ORDER BY is_default DESC, id', [uid(req)]);
  res.json(rows.map(presentAddress));
});

buyerRouter.post('/buyer/addresses', async (req, res) => {
  const b = parse(z.object({
    label: z.string().max(40).optional(), lat: z.number().min(-90).max(90), lon: z.number().min(-180).max(180),
    addressText: z.string().trim().min(3).max(300), isDefault: z.boolean().optional(),
  }), req.body);
  const a = await tx(async (c) => {
    const count = (await one<{ n: number }>('SELECT count(*)::int AS n FROM addresses WHERE user_id = $1 AND NOT is_deleted', [uid(req)], c))!.n;
    const makeDefault = b.isDefault || count === 0;
    if (makeDefault) await query('UPDATE addresses SET is_default = FALSE WHERE user_id = $1', [uid(req)], c);
    return one<any>('INSERT INTO addresses (user_id, label, lat, lon, address_text, is_default) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *',
      [uid(req), b.label ?? null, b.lat, b.lon, b.addressText, makeDefault], c);
  });
  res.status(201).json(presentAddress(a));
});

buyerRouter.put('/buyer/addresses/:id/default', async (req, res) => {
  const id = parse(idParam, req.params.id);
  await tx(async (c) => {
    const a = await one('SELECT id FROM addresses WHERE id = $1 AND user_id = $2 AND NOT is_deleted', [id, uid(req)], c);
    if (!a) throw notFound('Адрес не найден');
    await query('UPDATE addresses SET is_default = (id = $2) WHERE user_id = $1', [uid(req), id], c);
  });
  res.json({ ok: true });
});

buyerRouter.delete('/buyer/addresses/:id', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const a = await one<any>('UPDATE addresses SET is_deleted = TRUE, is_default = FALSE WHERE id = $1 AND user_id = $2 RETURNING *', [id, uid(req)]);
  if (!a) throw notFound('Адрес не найден');
  // Если удалили основной адрес — основным становится самый старый из оставшихся
  await query(`UPDATE addresses SET is_default = TRUE WHERE id = (SELECT id FROM addresses WHERE user_id = $1 AND NOT is_deleted ORDER BY id LIMIT 1)
                 AND NOT EXISTS (SELECT 1 FROM addresses WHERE user_id = $1 AND NOT is_deleted AND is_default)`, [uid(req)]);
  res.json({ ok: true });
});

// ----- Карты (только токены Payme/Click) -----
buyerRouter.get('/buyer/cards', async (req, res) => {
  const rows = await query<any>('SELECT * FROM payment_cards WHERE user_id = $1 AND NOT is_deleted ORDER BY is_default DESC, id', [uid(req)]);
  res.json(rows.map(presentCard));
});

buyerRouter.post('/buyer/cards', async (req, res) => {
  // cardToken — одноразовый токен из формы Payme (cards.create + cards.verify на клиенте)
  const b = parse(z.object({ cardToken: z.string().min(4), provider: z.enum(['payme', 'click']).default('payme') }), req.body);
  const bound = await payments.bindCard(b.cardToken);
  const card = await tx(async (c) => {
    const count = (await one<{ n: number }>('SELECT count(*)::int AS n FROM payment_cards WHERE user_id = $1 AND NOT is_deleted', [uid(req)], c))!.n;
    return one<any>('INSERT INTO payment_cards (user_id, provider, token, masked_pan, card_type, is_default) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *',
      [uid(req), b.provider, bound.token, bound.maskedPan, bound.cardType, count === 0], c);
  });
  res.status(201).json(presentCard(card));
});

buyerRouter.put('/buyer/cards/:id/default', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const c = await one('SELECT id FROM payment_cards WHERE id = $1 AND user_id = $2 AND NOT is_deleted', [id, uid(req)]);
  if (!c) throw notFound('Карта не найдена');
  await query('UPDATE payment_cards SET is_default = (id = $2) WHERE user_id = $1', [uid(req), id]);
  res.json({ ok: true });
});

buyerRouter.delete('/buyer/cards/:id', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const active = await one(`SELECT 1 FROM orders WHERE card_id = $1 AND status = ANY($2)`, [id, ACTIVE_STATUSES]);
  if (active) throw conflict('Карта используется в активном заказе');
  const c = await one('UPDATE payment_cards SET is_deleted = TRUE, is_default = FALSE WHERE id = $1 AND user_id = $2 RETURNING id', [id, uid(req)]);
  if (!c) throw notFound('Карта не найдена');
  await query(`UPDATE payment_cards SET is_default = TRUE WHERE id = (SELECT id FROM payment_cards WHERE user_id = $1 AND NOT is_deleted ORDER BY id LIMIT 1)
                 AND NOT EXISTS (SELECT 1 FROM payment_cards WHERE user_id = $1 AND NOT is_deleted AND is_default)`, [uid(req)]);
  res.json({ ok: true });
});

// ----- История заказов (ТЗ 3.7) -----
buyerRouter.get('/buyer/orders', async (req, res) => {
  const q = parse(z.object({ status: z.enum(['all', 'active', 'done', 'cancelled']).default('all'), page: z.coerce.number().int().min(1).default(1) }), req.query);
  const cond = { all: 'TRUE', active: `o.status = ANY($2)`, done: `o.status IN ('DELIVERED','RATED')`, cancelled: `o.status = 'CANCELLED'` }[q.status];
  const params: unknown[] = [uid(req)];
  if (q.status === 'active') params.push(ACTIVE_STATUSES);
  params.push((q.page - 1) * 20);
  const rows = await query<any>(
    `SELECT o.*, cp.nick AS chef_nick, cp.photo_url AS chef_photo FROM orders o JOIN chef_profiles cp ON cp.user_id = o.chef_id
      WHERE o.buyer_id = $1 AND ${cond} ORDER BY o.created_at DESC LIMIT 20 OFFSET $${params.length}`, params);
  res.json(rows.map((o) => ({ ...presentOrder(o, 'buyer'), chef: { id: o.chef_id, nick: o.chef_nick, photoUrl: o.chef_photo } })));
});

/** Неоценённый доставленный заказ — показать экран оценки при следующем открытии (ТЗ 3.6). */
buyerRouter.get('/buyer/pending-rating', async (req, res) => {
  const o = await one<any>(
    `SELECT o.*, cp.nick AS chef_nick, cp.photo_url AS chef_photo FROM orders o JOIN chef_profiles cp ON cp.user_id = o.chef_id
      WHERE o.buyer_id = $1 AND o.status = 'DELIVERED' AND o.delivered_at > now() - interval '3 days'
      ORDER BY o.delivered_at DESC LIMIT 1`, [uid(req)]);
  res.json(o ? { ...presentOrder(o, 'buyer'), chef: { id: o.chef_id, nick: o.chef_nick, photoUrl: o.chef_photo } } : null);
});

buyerRouter.get('/buyer/notifications', async (req, res) => {
  res.json(await query('SELECT id, type, title, body, data, read_at, created_at FROM notifications WHERE user_id = $1 ORDER BY id DESC LIMIT 100', [uid(req)]));
});

// ----- Жалобы -----
buyerRouter.post('/buyer/complaints', async (req, res) => {
  const b = parse(z.object({
    orderId: z.number().int(), type: z.enum(['quality', 'missing_items', 'late', 'wrong_order', 'hygiene', 'other']),
    description: z.string().trim().min(5).max(2000), photoUrls: z.array(z.string().url()).max(5).default([]),
  }), req.body);
  const o = await one<any>('SELECT * FROM orders WHERE id = $1 AND buyer_id = $2', [b.orderId, uid(req)]);
  if (!o) throw notFound('Заказ не найден');
  if (o.status === 'PENDING') throw badRequest('Заказ ещё не принят — его можно просто отменить');
  const c = await one<any>('INSERT INTO complaints (order_id, buyer_id, chef_id, type, description, photo_urls) VALUES ($1,$2,$3,$4,$5,$6) RETURNING *',
    [o.id, uid(req), o.chef_id, b.type, b.description, b.photoUrls]);
  emit(rooms.admin, 'alert', { type: 'complaint', complaintId: c.id });
  res.status(201).json({ id: c.id, status: c.status });
});

// Удаление аккаунта по запросу (ТЗ 13, GDPR): обезличиваем, история заказов сохраняется для бухгалтерии
buyerRouter.delete('/buyer/account', async (req, res) => {
  const active = await one(`SELECT 1 FROM orders WHERE buyer_id = $1 AND status = ANY($2)`, [uid(req), ACTIVE_STATUSES]);
  if (active) throw conflict('Нельзя удалить аккаунт, пока есть активный заказ');
  await tx(async (c) => {
    await query(`UPDATE users SET phone = 'deleted:' || id, name = NULL, is_active = FALSE, deleted_at = now() WHERE id = $1`, [uid(req)], c);
    await query('UPDATE addresses SET is_deleted = TRUE WHERE user_id = $1', [uid(req)], c);
    await query('UPDATE payment_cards SET is_deleted = TRUE, token = $2 WHERE user_id = $1', [uid(req), 'deleted'], c);
    await query(`UPDATE refresh_tokens SET revoked = TRUE WHERE subject = $1`, [`user:${uid(req)}`], c);
    await query('DELETE FROM device_tokens WHERE user_id = $1', [uid(req)], c);
  });
  res.json({ ok: true });
});
