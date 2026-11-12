import { Router } from 'express';
import { z } from 'zod';
import { one, query } from '../db.js';
import { notFound } from '../lib/errors.js';
import { idParam, parse } from '../lib/validate.js';
import { getChefMap, getFeed } from '../services/feed.js';
import { quoteDelivery } from '../services/orders.js';

export const publicRouter = Router();

// /chef/:id — только числовые id; /chef/orders, /chef/balance и т.п. уходят в роутер повара
publicRouter.param('id', (_req, _res, next, id) => (/^\d+$/.test(String(id)) ? next() : next('route')));

const coords = { lat: z.coerce.number().min(-90).max(90), lon: z.coerce.number().min(-180).max(180) };

publicRouter.get('/feed', async (req, res) => {
  const q = parse(z.object({
    ...coords,
    filter: z.enum(['all', 'ready', 'order', 'breakfast', 'lunch', 'dinner', 'bakery', 'drinks', 'vegan']).default('all'),
    page: z.coerce.number().int().min(1).default(1),
  }), req.query);
  res.json(await getFeed(q));
});

publicRouter.get('/search', async (req, res) => {
  const q = parse(z.object({ ...coords, q: z.string().trim().min(1).max(100), page: z.coerce.number().int().min(1).default(1) }), req.query);
  res.json(await getFeed(q));
});

publicRouter.get('/feed/map', async (req, res) => {
  const q = parse(z.object(coords), req.query);
  res.json(await getChefMap(q.lat, q.lon));
});

/** Карточка повара (ТЗ 3.3). Точный адрес покупателю не отдаётся — только район и расстояние. */
publicRouter.get('/chef/:id', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const q = parse(z.object({ lat: coords.lat.optional(), lon: coords.lon.optional() }), req.query);
  const c = await one<any>(
    `SELECT cp.*, u.is_active FROM chef_profiles cp JOIN users u ON u.id = cp.user_id
      WHERE cp.user_id = $1 AND cp.is_verified AND NOT cp.is_blocked AND u.is_active`, [id]);
  if (!c) throw notFound('Повар не найден');
  const hasReady = await one(`SELECT 1 FROM dishes WHERE chef_id = $1 AND is_active AND NOT is_deleted AND type IN ('ready','both') AND (portions_left IS NULL OR portions_left > 0)`, [id]);
  const avg = await one<{ m: number | null }>(
    `SELECT round(avg(extract(epoch FROM delivered_at - ready_at) / 60))::int AS m FROM orders
      WHERE chef_id = $1 AND delivered_at IS NOT NULL AND ready_at IS NOT NULL AND delivered_at > now() - interval '30 days'`, [id]);
  const reviews = await query<any>(
    `SELECT r.rating, r.tags, r.comment, r.created_at, u.name AS buyer_name FROM reviews r JOIN users u ON u.id = r.buyer_id
      WHERE r.chef_id = $1 ORDER BY r.id DESC LIMIT 3`, [id]);
  let delivery = null;
  if (q.lat != null && q.lon != null) delivery = await quoteDelivery(id, q.lat, q.lon).catch(() => null);
  res.json({
    id: c.user_id, nick: c.nick, bio: c.bio, photoUrl: c.photo_url, coverUrl: c.cover_url, district: c.district,
    status: !c.is_online ? 'offline' : hasReady ? 'ready' : 'order',
    rating: c.rating, reviewsCount: c.reviews_count, totalOrders: c.total_orders,
    avgDeliveryMinutes: avg?.m ?? null,
    distanceKm: delivery?.distanceKm ?? null, deliveryFee: delivery?.price ?? null,
    lastReviews: reviews.map((r) => ({ rating: r.rating, tags: r.tags, comment: r.comment, createdAt: r.created_at, buyerName: r.buyer_name ?? 'Покупатель' })),
  });
});

publicRouter.get('/chef/:id/menu', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const chef = await one<any>('SELECT is_online FROM chef_profiles WHERE user_id = $1 AND is_verified AND NOT is_blocked', [id]);
  if (!chef) throw notFound('Повар не найден');
  // Если повар офлайн — меню недоступно (ТЗ 3.3), показываем блюда, но без возможности заказать
  const rows = await query<any>(
    `SELECT * FROM dishes WHERE chef_id = $1 AND is_active AND NOT is_deleted AND (portions_left IS NULL OR portions_left > 0) ORDER BY type, id`, [id]);
  res.json({
    available: chef.is_online,
    dishes: rows.map((d) => ({
      id: d.id, name: d.name, description: d.description, price: d.price, weight: d.weight, photoUrls: d.photo_urls,
      category: d.category, type: d.type, prepTimeMinutes: d.prep_time_minutes, portionsLeft: d.portions_left,
      ingredients: d.ingredients, isVegan: d.is_vegan,
    })),
  });
});

publicRouter.get('/chef/:id/reviews', async (req, res) => {
  const id = parse(idParam, req.params.id);
  const { page } = parse(z.object({ page: z.coerce.number().int().min(1).default(1) }), req.query);
  const rows = await query<any>(
    `SELECT r.id, r.rating, r.tags, r.comment, r.created_at, u.name AS buyer_name FROM reviews r JOIN users u ON u.id = r.buyer_id
      WHERE r.chef_id = $1 ORDER BY r.id DESC LIMIT 20 OFFSET $2`, [id, (page - 1) * 20]);
  res.json(rows.map((r) => ({ id: r.id, rating: r.rating, tags: r.tags, comment: r.comment, createdAt: r.created_at, buyerName: r.buyer_name ?? 'Покупатель' })));
});

publicRouter.post('/delivery/estimate', async (req, res) => {
  const b = parse(z.object({ chefId: z.number().int(), lat: z.number(), lon: z.number() }), req.body);
  const q = await quoteDelivery(b.chefId, b.lat, b.lon);
  res.json({ price: q.price, distanceKm: q.distanceKm });
});
