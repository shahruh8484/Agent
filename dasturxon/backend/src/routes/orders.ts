import { Router } from 'express';
import { z } from 'zod';
import { requireUser, uid } from '../auth/middleware.js';
import { one, query } from '../db.js';
import { forbidden, notFound } from '../lib/errors.js';
import { idParam, parse } from '../lib/validate.js';
import { buyerCancel, createOrder, presentOrder, rateOrder } from '../services/orders.js';

export const ordersRouter = Router();

ordersRouter.post('/orders', requireUser('buyer'), async (req, res) => {
  const b = parse(z.object({
    items: z.array(z.object({ dishId: z.number().int().positive(), qty: z.number().int().min(1).max(50) })).min(1).max(30),
    addressId: z.number().int().positive(),
    cardId: z.number().int().positive().optional(),
    comment: z.string().trim().max(200).optional(),
  }), req.body);
  const o = await createOrder(uid(req), b);
  res.status(201).json(presentOrder(o, 'buyer'));
});

async function loadForViewer(req: any) {
  const id = parse(idParam, req.params.id);
  const o = await one<any>('SELECT * FROM orders WHERE id = $1', [id]);
  if (!o) throw notFound('Заказ не найден');
  const me = uid(req);
  if (o.buyer_id !== me && o.chef_id !== me) throw forbidden();
  return { o, viewer: (o.buyer_id === me ? 'buyer' : 'chef') as 'buyer' | 'chef' };
}

ordersRouter.get('/orders/:id', requireUser(), async (req, res) => {
  const { o, viewer } = await loadForViewer(req);
  const history = await query('SELECT status, note, created_at FROM order_status_history WHERE order_id = $1 ORDER BY id', [o.id]);
  const chef = await one<any>('SELECT nick, photo_url, district, address_lat, address_lon FROM chef_profiles WHERE user_id = $1', [o.chef_id]);
  res.json({
    ...presentOrder(o, viewer), history,
    // Точка повара на карте трекинга (только после того, как заказ принят)
    chef: { id: o.chef_id, nick: chef.nick, photoUrl: chef.photo_url, district: chef.district,
      point: viewer === 'buyer' && o.status !== 'PENDING' ? { lat: chef.address_lat, lon: chef.address_lon } : null },
  });
});

ordersRouter.get('/orders/:id/tracking', requireUser(), async (req, res) => {
  const { o } = await loadForViewer(req);
  res.json({
    orderId: o.id, status: o.status,
    courier: o.courier_name ? { name: o.courier_name, phone: o.courier_phone,
      position: o.courier_lat != null ? { lat: o.courier_lat, lon: o.courier_lon } : null, etaSeconds: o.courier_eta_seconds } : null,
  });
});

ordersRouter.post('/orders/:id/cancel', requireUser('buyer'), async (req, res) => {
  const o = await buyerCancel(uid(req), parse(idParam, req.params.id));
  res.json(presentOrder(o, 'buyer'));
});

ordersRouter.post('/orders/:id/rate', requireUser('buyer'), async (req, res) => {
  const b = parse(z.object({
    rating: z.number().int().min(1).max(5),
    tags: z.array(z.enum(['very_tasty', 'hot', 'big_portion', 'fast', 'as_ordered', 'nice_packaging'])).max(6).default([]),
    comment: z.string().trim().max(1000).optional(),
  }), req.body);
  const chef = await rateOrder(uid(req), parse(idParam, req.params.id), b);
  res.json({ ok: true, chefRating: chef.rating });
});
