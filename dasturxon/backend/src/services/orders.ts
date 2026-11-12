import type { PoolClient } from 'pg';
import { config } from '../config.js';
import { one, pool, query, tx, type Db } from '../db.js';
import { delivery, type Tracking } from '../integrations/delivery.js';
import { payments } from '../integrations/payments.js';
import { notify } from '../integrations/push.js';
import { emit, rooms } from '../integrations/realtime.js';
import { badRequest, conflict, forbidden, HttpError, notFound } from '../lib/errors.js';
import { haversineKm } from '../lib/geo.js';
import { nearestDistrict } from '../lib/districts.js';
import { deliveryPriceForBuyer, splitChefAmount } from '../lib/money.js';
import { weekStart } from '../lib/time.js';

export type OrderStatus =
  | 'PENDING' | 'CONFIRMED' | 'COOKING' | 'READY' | 'COURIER_ASSIGNED'
  | 'IN_DELIVERY' | 'DELIVERED' | 'RATED' | 'CANCELLED';

export const ACTIVE_STATUSES: OrderStatus[] = ['PENDING', 'CONFIRMED', 'COOKING', 'READY', 'COURIER_ASSIGNED', 'IN_DELIVERY'];

export interface OrderItem { dishId: number; name: string; price: number; qty: number; photoUrl: string | null }

// ---------- Расчёт доставки ----------

const quoteCache = new Map<string, { at: number; value: DeliveryQuote }>();
export interface DeliveryQuote { yandexPrice: number; price: number; distanceKm: number }

/** Стоимость доставки от повара до точки покупателя (ТЗ 5.1). Кэш 60 секунд. */
export async function quoteDelivery(chefId: number, lat: number, lon: number): Promise<DeliveryQuote> {
  const chef = await one<{ address_lat: number; address_lon: number }>(
    'SELECT address_lat, address_lon FROM chef_profiles WHERE user_id = $1', [chefId]);
  if (!chef || chef.address_lat == null) throw notFound('Повар не найден');
  const key = `${chefId}:${lat.toFixed(3)}:${lon.toFixed(3)}`;
  const hit = quoteCache.get(key);
  if (hit && Date.now() - hit.at < 60_000) return hit.value;
  const est = await delivery.estimate({ lat: chef.address_lat, lon: chef.address_lon }, { lat, lon });
  const value = {
    yandexPrice: est.maxPrice,
    price: deliveryPriceForBuyer(est.maxPrice),
    distanceKm: Math.round(haversineKm(chef.address_lat, chef.address_lon, lat, lon) * 10) / 10,
  };
  quoteCache.set(key, { at: Date.now(), value });
  return value;
}

export function clearQuoteCache() { quoteCache.clear(); }

// ---------- Вспомогательное ----------

async function setStatus(db: Db, orderId: number, status: OrderStatus, extra: Record<string, unknown> = {}, note?: string) {
  const cols = Object.keys(extra);
  const sets = ['status = $2', ...cols.map((c, i) => `${c} = $${i + 3}`)].join(', ');
  await query(`UPDATE orders SET ${sets} WHERE id = $1`, [orderId, status, ...cols.map((c) => extra[c])], db);
  await query('INSERT INTO order_status_history (order_id, status, note) VALUES ($1,$2,$3)', [orderId, status, note ?? null], db);
}

export async function getOrder(id: number, db: Db = pool) {
  return one<any>('SELECT * FROM orders WHERE id = $1', [id], db);
}

async function lockOrder(c: PoolClient, id: number) {
  const o = await one<any>('SELECT * FROM orders WHERE id = $1 FOR UPDATE', [id], c);
  if (!o) throw notFound('Заказ не найден');
  return o;
}

/** Публичное представление заказа (без внутренних финансов платформы). */
export function presentOrder(o: any, viewer: 'buyer' | 'chef' | 'admin') {
  const base: any = {
    id: o.id,
    status: o.status,
    items: o.items,
    dishTotal: o.dish_total,
    deliveryFee: o.delivery_fee_shown,
    total: o.total_amount,
    comment: o.comment,
    createdAt: o.created_at,
    confirmedAt: o.confirmed_at,
    readyAt: o.ready_at,
    deliveredAt: o.delivered_at,
    cancelledAt: o.cancelled_at,
    cancelReason: o.cancel_reason,
    paymentStatus: o.payment_status,
    refundedAmount: o.refunded_amount,
    courier: o.courier_name ? {
      name: o.courier_name, phone: o.courier_phone,
      position: o.courier_lat != null ? { lat: o.courier_lat, lon: o.courier_lon } : null,
      etaSeconds: o.courier_eta_seconds,
    } : null,
    chefId: o.chef_id,
    buyerId: o.buyer_id,
  };
  if (viewer === 'buyer') {
    base.deliveryAddress = { text: o.delivery_address_text, lat: o.delivery_lat, lon: o.delivery_lon };
  }
  if (viewer === 'chef') {
    // Повар видит сумму без комиссии платформы и только район покупателя (ТЗ 4.2)
    delete base.deliveryFee; delete base.total;
    base.chefAmount = o.chef_net_amount;
    base.deliveryAddress = { text: `${nearestDistrict(o.delivery_lat, o.delivery_lon)} р-н` };
    base.expiresAt = o.status === 'PENDING' ? new Date(new Date(o.created_at).getTime() + config.business.chefAcceptTimeoutSec * 1000) : null;
  }
  if (viewer === 'admin') {
    Object.assign(base, {
      deliveryAddress: { text: o.delivery_address_text, lat: o.delivery_lat, lon: o.delivery_lon },
      deliveryFeeReal: o.delivery_fee_real, deliveryBuffer: o.delivery_buffer,
      platformCommission: o.platform_commission, chefTax: o.chef_tax, chefNetAmount: o.chef_net_amount,
      paymentId: o.payment_id, yandexRequestId: o.yandex_request_id,
    });
  }
  return base;
}

function broadcast(o: any) {
  const payload = { orderId: o.id, status: o.status };
  emit(rooms.order(o.id), 'order:update', payload);
  emit(rooms.user(o.buyer_id), 'order:update', payload);
  emit(rooms.user(o.chef_id), 'order:update', payload);
  emit(rooms.admin, 'order:update', payload);
}

async function restorePortions(db: Db, items: OrderItem[]) {
  for (const it of items) {
    await query('UPDATE dishes SET portions_left = portions_left + $2 WHERE id = $1 AND portions_left IS NOT NULL', [it.dishId, it.qty], db);
  }
}

async function chefNick(chefId: number) {
  return (await one<{ nick: string }>('SELECT nick FROM chef_profiles WHERE user_id = $1', [chefId]))?.nick ?? 'Повар';
}

// ---------- Создание заказа (ТЗ 3.4) ----------

export interface CreateOrderInput {
  items: { dishId: number; qty: number }[];
  addressId: number;
  cardId?: number;
  comment?: string;
}

export async function createOrder(buyerId: number, input: CreateOrderInput) {
  const address = await one<any>('SELECT * FROM addresses WHERE id = $1 AND user_id = $2 AND NOT is_deleted', [input.addressId, buyerId]);
  if (!address) throw badRequest('Адрес доставки не найден');
  const card = input.cardId
    ? await one<any>('SELECT * FROM payment_cards WHERE id = $1 AND user_id = $2 AND NOT is_deleted', [input.cardId, buyerId])
    : await one<any>('SELECT * FROM payment_cards WHERE user_id = $1 AND NOT is_deleted ORDER BY is_default DESC, id LIMIT 1', [buyerId]);
  if (!card) throw badRequest('Привяжите карту UZCARD или HUMO, чтобы оформить заказ');

  const dishIds = [...new Set(input.items.map((i) => i.dishId))];
  if (dishIds.length !== input.items.length) throw badRequest('Блюда в заказе повторяются');

  // 1. Резервируем порции и создаём заказ в транзакции
  const order = await tx(async (c) => {
    const dishes = await query<any>('SELECT * FROM dishes WHERE id = ANY($1::bigint[]) FOR UPDATE', [dishIds], c);
    if (dishes.length !== dishIds.length) throw badRequest('Некоторые блюда не найдены');
    const chefIds = new Set(dishes.map((d) => d.chef_id));
    if (chefIds.size !== 1) throw badRequest('В одном заказе могут быть блюда только одного повара');
    const chefId: number = dishes[0].chef_id;
    const chef = await one<any>('SELECT * FROM chef_profiles WHERE user_id = $1', [chefId], c);
    if (!chef || !chef.is_verified || chef.is_blocked) throw badRequest('Повар недоступен');
    if (!chef.is_online) throw badRequest('Повар сейчас офлайн');

    const items: OrderItem[] = [];
    for (const req of input.items) {
      const d = dishes.find((x) => x.id === req.dishId)!;
      if (!d.is_active || d.is_deleted) throw badRequest(`Блюдо «${d.name}» недоступно`);
      if (d.portions_left != null && d.portions_left < req.qty) throw badRequest(`Осталось только ${d.portions_left} порц. «${d.name}»`);
      items.push({ dishId: d.id, name: d.name, price: d.price, qty: req.qty, photoUrl: d.photo_urls[0] ?? null });
    }
    for (const it of items) {
      await query('UPDATE dishes SET portions_left = portions_left - $2 WHERE id = $1 AND portions_left IS NOT NULL', [it.dishId, it.qty], c);
    }

    const dishTotal = items.reduce((s, i) => s + i.price * i.qty, 0);
    const quote = await quoteDelivery(chefId, address.lat, address.lon);
    const split = splitChefAmount(dishTotal);
    const total = dishTotal + quote.price;

    const o = await one<any>(
      `INSERT INTO orders (buyer_id, chef_id, status, items, dish_total, delivery_fee_shown, platform_commission, chef_tax,
         chef_net_amount, total_amount, address_id, delivery_lat, delivery_lon, delivery_address_text, comment, card_id)
       VALUES ($1,$2,'PENDING',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) RETURNING *`,
      [buyerId, chefId, JSON.stringify(items), dishTotal, quote.price, split.commission, split.tax, split.net, total,
        address.id, address.lat, address.lon, address.address_text, input.comment ?? null, card.id], c);
    return o;
  });

  // 2. Холдирование суммы на карте
  const hold = await payments.hold(card.token, order.total_amount, order.id);
  if (!hold.ok) {
    await tx(async (c) => {
      await restorePortions(c, order.items);
      await query('DELETE FROM orders WHERE id = $1', [order.id], c);
    });
    throw new HttpError(402, 'payment_failed', hold.reason || 'Недостаточно средств');
  }
  await query(`UPDATE orders SET payment_id = $2, payment_status = 'held' WHERE id = $1`, [order.id, hold.paymentId]);
  await query(`INSERT INTO order_status_history (order_id, status, note) VALUES ($1,'PENDING','Оплата прошла (холд)')`, [order.id]);

  const fresh = (await getOrder(order.id))!;
  const summary = (fresh.items as OrderItem[]).map((i) => `${i.name} × ${i.qty}`).join(', ');
  await notify(fresh.chef_id, 'new_order', 'Новый заказ!', `${summary}. У вас 5 минут.`, { orderId: fresh.id });
  broadcast(fresh);
  return fresh;
}

// ---------- Действия повара (ТЗ 4.2) ----------

export async function acceptOrder(chefId: number, orderId: number) {
  const o = await tx(async (c) => {
    const o = await lockOrder(c, orderId);
    if (o.chef_id !== chefId) throw forbidden();
    if (o.status !== 'PENDING') throw conflict('Заказ уже обработан');
    await payments.confirm(o.payment_id); // деньги списываются при подтверждении
    await query(`UPDATE orders SET payment_status = 'charged', confirmed_at = now() WHERE id = $1`, [orderId], c);
    await setStatus(c, orderId, 'CONFIRMED');
    await setStatus(c, orderId, 'COOKING');
    return lockOrder(c, orderId);
  });
  const nick = await chefNick(chefId);
  await notify(o.buyer_id, 'order_accepted', 'Заказ принят', `${nick} принял(а) ваш заказ и начал(а) готовить!`, { orderId });
  broadcast(o);
  return o;
}

async function cancelHeld(orderId: number, reason: string, guard: (o: any) => void) {
  return tx(async (c) => {
    const o = await lockOrder(c, orderId);
    guard(o);
    if (o.status !== 'PENDING') throw conflict('Заказ уже обработан');
    if (o.payment_status === 'held') await payments.cancel(o.payment_id); // снимаем холд немедленно
    await query(`UPDATE orders SET payment_status = 'cancelled', cancelled_at = now(), cancel_reason = $2 WHERE id = $1`, [orderId, reason], c);
    await setStatus(c, orderId, 'CANCELLED', {}, reason);
    await restorePortions(c, o.items);
    return lockOrder(c, orderId);
  });
}

export async function declineOrder(chefId: number, orderId: number, reason?: string) {
  const o = await cancelHeld(orderId, reason ? `Повар отказал: ${reason}` : 'Повар отказал', (o) => {
    if (o.chef_id !== chefId) throw forbidden();
  });
  await notify(o.buyer_id, 'order_declined', 'Заказ отменён', 'Повар не смог принять заказ. Деньги возвращены.', { orderId });
  broadcast(o);
  return o;
}

export async function buyerCancel(buyerId: number, orderId: number) {
  const o = await cancelHeld(orderId, 'Отменён покупателем', (o) => {
    if (o.buyer_id !== buyerId) throw forbidden();
    if (o.status !== 'PENDING') throw conflict('Повар уже принял заказ — отменить можно только через поддержку');
  });
  await notify(o.chef_id, 'order_cancelled', 'Заказ отменён', `Покупатель отменил заказ #${o.id}.`, { orderId });
  broadcast(o);
  return o;
}

/** Автоотмена, если повар не ответил за 5 минут. */
export async function autoCancel(orderId: number) {
  const o = await cancelHeld(orderId, 'Повар не ответил за 5 минут', () => {});
  await notify(o.buyer_id, 'order_timeout', 'Заказ отменён', 'Повар не ответил. Деньги возвращены на карту.', { orderId });
  // Частые автоотмены → предупреждение повару (ТЗ 4.2)
  const cnt = await one<{ n: number }>(
    `SELECT count(*)::int AS n FROM orders WHERE chef_id = $1 AND status = 'CANCELLED'
       AND cancel_reason = 'Повар не ответил за 5 минут' AND cancelled_at > now() - interval '7 days'`, [o.chef_id]);
  if ((cnt?.n ?? 0) >= 3) {
    await notify(o.chef_id, 'autocancel_warning', 'Предупреждение',
      `За неделю ${cnt!.n} заказов отменены, потому что вы не ответили. Переключитесь в офлайн, если не можете принимать заказы.`, {});
    emit(rooms.admin, 'alert', { type: 'autocancel', chefId: o.chef_id, count: cnt!.n });
  }
  broadcast(o);
  return o;
}

/** Повар нажал «Готово» → вызываем курьера через Яндекс Go (ТЗ 5.2). */
export async function markReady(chefId: number, orderId: number) {
  const o = await tx(async (c) => {
    const o = await lockOrder(c, orderId);
    if (o.chef_id !== chefId) throw forbidden();
    if (o.status !== 'COOKING' && o.status !== 'CONFIRMED') throw conflict('Заказ нельзя отметить готовым');
    await setStatus(c, orderId, 'READY', { ready_at: new Date() });
    return lockOrder(c, orderId);
  });
  await requestCourier(o);
  await notify(o.buyer_id, 'order_ready', 'Еда готова', 'Ваш заказ готов! Курьер уже едет к вам.', { orderId });
  const fresh = (await getOrder(orderId))!;
  broadcast(fresh);
  return fresh;
}

/** Вызов курьера; при ошибке заказ остаётся READY, фоновая задача повторит попытку. */
export async function requestCourier(o: any) {
  if (o.yandex_request_id) return;
  const chef = await one<any>('SELECT address_lat, address_lon FROM chef_profiles WHERE user_id = $1', [o.chef_id]);
  try {
    const r = await delivery.request({ lat: chef.address_lat, lon: chef.address_lon }, { lat: o.delivery_lat, lon: o.delivery_lon }, o.id);
    await query('UPDATE orders SET yandex_request_id = $2 WHERE id = $1', [o.id, r.requestId]);
  } catch (e) {
    console.error(`[delivery] request failed for order ${o.id}`, e);
    emit(rooms.admin, 'alert', { type: 'courier_request_failed', orderId: o.id });
  }
}

// ---------- Трекинг курьера (ТЗ 3.5, 5.3) ----------

const NEAR_METERS = 300;

export async function applyTracking(orderId: number, t: Tracking) {
  const o = await getOrder(orderId);
  if (!o || !['READY', 'COURIER_ASSIGNED', 'IN_DELIVERY'].includes(o.status)) return o;

  if (t.status === 'cancelled' || t.status === 'failed') {
    // Курьер отменил — сбрасываем заявку, задача вызовет нового курьера
    await query('UPDATE orders SET yandex_request_id = NULL, courier_name = NULL, courier_phone = NULL, courier_lat = NULL, courier_lon = NULL WHERE id = $1', [orderId]);
    await query(`INSERT INTO order_status_history (order_id, status, note) VALUES ($1,$2,'Курьер отменил поездку, ищем нового')`, [orderId, o.status]);
    emit(rooms.admin, 'alert', { type: 'courier_cancelled', orderId });
    return getOrder(orderId);
  }

  await query(`UPDATE orders SET courier_name = $2, courier_phone = $3, courier_lat = $4, courier_lon = $5, courier_eta_seconds = $6 WHERE id = $1`,
    [orderId, t.driver?.name ?? o.courier_name, t.driver?.phone ?? o.courier_phone, t.position?.lat ?? null, t.position?.lon ?? null, t.etaSeconds ?? null]);

  if (t.status === 'driving_to_pickup' && o.status === 'READY') {
    await setStatus(pool, orderId, 'COURIER_ASSIGNED');
  } else if (t.status === 'delivering' && (o.status === 'READY' || o.status === 'COURIER_ASSIGNED')) {
    await setStatus(pool, orderId, 'IN_DELIVERY');
  } else if (t.status === 'delivered') {
    await completeDelivered(orderId, t.finalPrice);
    return getOrder(orderId);
  }

  if (t.status === 'delivering' && t.position && !o.courier_near_notified) {
    const meters = haversineKm(t.position.lat, t.position.lon, o.delivery_lat, o.delivery_lon) * 1000;
    if (meters < NEAR_METERS) {
      await query('UPDATE orders SET courier_near_notified = TRUE WHERE id = $1', [orderId]);
      await notify(o.buyer_id, 'courier_near', 'Курьер рядом', 'Курьер будет у вас через 2 минуты!', { orderId });
    }
  }

  const fresh = (await getOrder(orderId))!;
  emit(rooms.order(orderId), 'order:tracking', {
    orderId, status: fresh.status,
    courier: { name: fresh.courier_name, phone: fresh.courier_phone, position: t.position ?? null, etaSeconds: t.etaSeconds ?? null },
  });
  if (fresh.status !== o.status) broadcast(fresh);
  return fresh;
}

/** Заказ доставлен: фиксируем реальную стоимость доставки и начисление повару (ТЗ 5.4, 6.1). */
export async function completeDelivered(orderId: number, finalPrice?: number) {
  const o = await tx(async (c) => {
    const o = await lockOrder(c, orderId);
    if (o.status === 'DELIVERED' || o.status === 'RATED' || o.status === 'CANCELLED') return null;
    const real = finalPrice ?? (await quoteDelivery(o.chef_id, o.delivery_lat, o.delivery_lon).catch(() => null))?.yandexPrice ?? o.delivery_fee_shown;
    await setStatus(c, orderId, 'DELIVERED', {
      delivered_at: new Date(), delivery_fee_real: real, delivery_buffer: o.delivery_fee_shown - real,
    });
    await query(
      `INSERT INTO chef_earnings (chef_id, order_id, kind, gross_amount, commission, tax, net_amount, week_start)
       VALUES ($1,$2,'order',$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING`,
      [o.chef_id, o.id, o.dish_total, o.platform_commission, o.chef_tax, o.chef_net_amount, weekStart()], c);
    await query('UPDATE chef_profiles SET total_orders = total_orders + 1 WHERE user_id = $1', [o.chef_id], c);
    return lockOrder(c, orderId);
  });
  if (!o) return;
  await notify(o.buyer_id, 'order_delivered', 'Заказ доставлен', 'Заказ доставлен. Как вам понравилась еда?', { orderId, rate: true });
  broadcast(o);
}

// ---------- Оценка (ТЗ 3.6) ----------

export async function rateOrder(buyerId: number, orderId: number, input: { rating: number; tags: string[]; comment?: string }) {
  const o = await tx(async (c) => {
    const o = await lockOrder(c, orderId);
    if (o.buyer_id !== buyerId) throw forbidden();
    if (o.status !== 'DELIVERED') throw conflict(o.status === 'RATED' ? 'Заказ уже оценён' : 'Оценить можно только доставленный заказ');
    await query('INSERT INTO reviews (order_id, buyer_id, chef_id, rating, tags, comment) VALUES ($1,$2,$3,$4,$5,$6)',
      [orderId, buyerId, o.chef_id, input.rating, input.tags, input.comment ?? null], c);
    await setStatus(c, orderId, 'RATED');
    return o;
  });
  const chef = await recomputeRating(o.chef_id);
  await notify(o.chef_id, 'new_review', 'Новый отзыв', `Новый отзыв: ${'★'.repeat(input.rating)}${'☆'.repeat(5 - input.rating)} от покупателя`, { orderId });
  broadcast({ ...o, status: 'RATED' });
  return chef;
}

export async function recomputeRating(chefId: number) {
  const chef = await one<any>(
    `UPDATE chef_profiles cp SET
        rating = COALESCE((SELECT round(avg(rating)::numeric, 2) FROM reviews WHERE chef_id = $1), 0),
        reviews_count = (SELECT count(*) FROM reviews WHERE chef_id = $1)
      WHERE user_id = $1 RETURNING rating, reviews_count, total_orders, low_rating_flag`, [chefId]);
  const { lowRatingThreshold, lowRatingMinOrders } = config.business;
  const low = chef.total_orders >= lowRatingMinOrders && chef.rating < lowRatingThreshold;
  if (low && !chef.low_rating_flag) {
    await query('UPDATE chef_profiles SET low_rating_flag = TRUE WHERE user_id = $1', [chefId]);
    await notify(chefId, 'rating_warning', 'Рейтинг снизился', `Ваш рейтинг упал до ${chef.rating}. Пожалуйста, улучшите качество.`, {});
    emit(rooms.admin, 'alert', { type: 'low_rating', chefId, rating: chef.rating });
  } else if (!low && chef.low_rating_flag) {
    await query('UPDATE chef_profiles SET low_rating_flag = FALSE WHERE user_id = $1', [chefId]);
  }
  return chef;
}

// ---------- Возврат (ТЗ 7.3, 7.5) ----------

export async function refundOrder(staffId: number, orderId: number, amount: number, reason: string) {
  const o = await tx(async (c) => {
    const o = await lockOrder(c, orderId);
    if (!['charged', 'partially_refunded'].includes(o.payment_status)) throw conflict('Возврат возможен только по оплаченному заказу');
    const left = o.total_amount - o.refunded_amount;
    if (amount <= 0 || amount > left) throw badRequest(`Сумма возврата должна быть от 1 до ${left} сум`);

    await payments.refund(o.payment_id, amount);
    const refunded = o.refunded_amount + amount;
    await query('UPDATE orders SET refunded_amount = $2, payment_status = $3 WHERE id = $1',
      [orderId, refunded, refunded >= o.total_amount ? 'refunded' : 'partially_refunded'], c);
    await query('INSERT INTO refunds (order_id, amount, reason, staff_id) VALUES ($1,$2,$3,$4)', [orderId, amount, reason, staffId], c);
    await query(`INSERT INTO order_status_history (order_id, status, note) VALUES ($1,$2,$3)`, [orderId, o.status, `Возврат ${amount} сум: ${reason}`], c);

    // Удержание с повара: возврат сначала покрывает стоимость блюд (повар теряет свою долю),
    // часть, относящаяся к доставке, ложится на платформу.
    const dishRefundedBefore = Math.min(o.refunded_amount, o.dish_total);
    const dishPart = Math.min(amount, o.dish_total - dishRefundedBefore);
    if (dishPart > 0 && ['DELIVERED', 'RATED'].includes(o.status)) {
      const s = splitChefAmount(dishPart);
      await query(
        `INSERT INTO chef_earnings (chef_id, order_id, kind, gross_amount, commission, tax, net_amount, week_start)
         VALUES ($1,$2,'refund',$3,$4,$5,$6,$7)`,
        [o.chef_id, o.id, -s.gross, -s.commission, -s.tax, -s.net, weekStart()], c);
    }
    return lockOrder(c, orderId);
  });
  await notify(o.buyer_id, 'refund', 'Возврат средств', `Вам возвращено ${amount.toLocaleString('ru-RU')} сум по заказу #${orderId}.`, { orderId });
  return o;
}

/** Отмена заказа администратором после подтверждения (повар не может выполнить и т.п.): полный возврат. */
export async function adminCancelOrder(staffId: number, orderId: number, reason: string) {
  const o = await getOrder(orderId);
  if (!o) throw notFound('Заказ не найден');
  if (o.status === 'PENDING') {
    const r = await cancelHeld(orderId, `Отменён администратором: ${reason}`, () => {});
    await notify(r.buyer_id, 'order_cancelled', 'Заказ отменён', 'Заказ отменён поддержкой. Деньги возвращены.', { orderId });
    broadcast(r);
    return r;
  }
  if (!['CONFIRMED', 'COOKING', 'READY', 'COURIER_ASSIGNED', 'IN_DELIVERY'].includes(o.status)) throw conflict('Этот заказ нельзя отменить');
  if (o.yandex_request_id) await delivery.cancel(o.yandex_request_id).catch((e) => console.error('[delivery] cancel failed', e));
  const left = o.total_amount - o.refunded_amount;
  if (left > 0) await refundOrder(staffId, orderId, left, `Отмена: ${reason}`);
  const r = await tx(async (c) => {
    await query('UPDATE orders SET cancelled_at = now(), cancel_reason = $2 WHERE id = $1', [orderId, reason], c);
    await setStatus(c, orderId, 'CANCELLED', {}, `Отменён администратором: ${reason}`);
    await restorePortions(c, o.items);
    return lockOrder(c, orderId);
  });
  await notify(r.chef_id, 'order_cancelled', 'Заказ отменён', `Заказ #${orderId} отменён поддержкой.`, { orderId });
  broadcast(r);
  return r;
}
