import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { pool, query } from '../src/db.js';
import { delivery, MockDelivery } from '../src/integrations/delivery.js';
import { sentSms } from '../src/integrations/sms.js';
import { tickOrders, tickPayouts } from '../src/jobs.js';
import { clearFeedCache } from '../src/services/feed.js';
import { api, auth, chefIdByNick, dishOf, loginStaff, loginUser, resetDb } from './helpers.js';

const mock = delivery as MockDelivery;
let clock = Date.now();
mock.now = () => clock;
const advance = (sec: number) => { clock += sec * 1000; };

let buyer: string;
let owner: string;
let support: string;
let accountant: string;

beforeAll(async () => {
  await resetDb();
  buyer = (await loginUser('+998901234567')).token;
  owner = await loginStaff('owner@dasturxon.uz', 'admin12345');
  support = await loginStaff('support@dasturxon.uz', 'support123');
  accountant = await loginStaff('accountant@dasturxon.uz', 'accountant123');
});
afterAll(async () => { await pool.end(); });
beforeEach(() => { clearFeedCache(); });

async function placeOrder(nick = 'Нилуфар', qty = 2, token = buyer) {
  const chefId = await chefIdByNick(nick);
  const dish = await dishOf(chefId);
  const addr = (await api().get('/api/buyer/addresses').set(auth(token))).body[0];
  const r = await api().post('/api/orders').set(auth(token)).send({ items: [{ dishId: dish.id, qty }], addressId: addr.id, comment: 'Без лука' });
  return { r, chefId, dish };
}

async function chefToken(nick: string) {
  const phone = (await query<any>('SELECT u.phone FROM users u JOIN chef_profiles cp ON cp.user_id = u.id WHERE cp.nick = $1', [nick]))[0].phone;
  return (await loginUser(phone, 'chef')).token;
}

describe('авторизация', () => {
  it('SMS-код, вход, refresh с ротацией, лимит 5 SMS за 10 минут', async () => {
    const phone = '+998935550000';
    const s = await api().post('/api/auth/send-otp').send({ phone });
    expect(s.status).toBe(200);
    expect(sentSms.at(-1)!.text).toContain(s.body.devCode);
    const bad = await api().post('/api/auth/verify-otp').send({ phone, code: s.body.devCode === '0000' ? '1111' : '0000' });
    expect(bad.status).toBe(401);
    const v = await api().post('/api/auth/verify-otp').send({ phone, code: s.body.devCode });
    expect(v.status).toBe(200);
    expect(v.body.isNew).toBe(true);
    const r1 = await api().post('/api/auth/refresh').send({ refreshToken: v.body.refreshToken });
    expect(r1.status).toBe(200);
    const reuse = await api().post('/api/auth/refresh').send({ refreshToken: v.body.refreshToken });
    expect(reuse.status).toBe(401);
    for (let i = 0; i < 4; i++) await api().post('/api/auth/send-otp').send({ phone });
    const limited = await api().post('/api/auth/send-otp').send({ phone });
    expect(limited.status).toBe(429);
  });

  it('номер покупателя нельзя использовать как повара', async () => {
    const s = await api().post('/api/auth/send-otp').send({ phone: '+998901234567' });
    const v = await api().post('/api/auth/verify-otp').send({ phone: '+998901234567', code: s.body.devCode, role: 'chef' });
    expect(v.status).toBe(409);
  });
});

describe('регистрация повара (ТЗ 4.1)', () => {
  it('профиль → фото кухни → карта → одобрение админом → онлайн → блюдо в ленте', async () => {
    const { token } = await loginUser('+998977770000', 'chef');
    const p = await api().put('/api/chef/profile').set(auth(token)).send({
      nick: 'Гульнора', firstName: 'Гульнора', lastName: 'Ахмедова', photoUrl: 'https://x.uz/p.jpg', coverUrl: 'https://x.uz/c.jpg',
      bio: 'Домашняя кухня', district: 'Мирабад', addressText: 'ул. Тестовая 5', addressLat: 41.305, addressLon: 69.28,
    });
    expect(p.status).toBe(200);
    const dup = await api().put('/api/chef/profile').set(auth(token)).send({ nick: 'нилуфар' });
    expect(dup.status).toBe(409);

    const v = await api().post('/api/chef/verification').set(auth(token)).send({ kitchenPhotoUrls: ['https://x.uz/k1.jpg'] });
    expect(v.body.status).toBe('pending'); // без ключа AI → ручная проверка

    expect((await api().put('/api/chef/status').set(auth(token)).send({ online: true })).status).toBe(403);

    expect((await api().put('/api/chef/payout-card').set(auth(token)).send({ cardToken: 'humo-1234', acceptTerms: true })).body.cardType).toBe('humo');

    const list = await api().get('/api/admin/verifications').set(auth(support));
    const me = list.body.find((c: any) => c.nick === 'Гульнора');
    expect(me).toBeTruthy();
    expect((await api().put(`/api/admin/chefs/${me.id}/verify`).set(auth(accountant)).send({ approve: true })).status).toBe(403);
    expect((await api().put(`/api/admin/chefs/${me.id}/verify`).set(auth(support)).send({ approve: true })).status).toBe(200);

    expect((await api().put('/api/chef/status').set(auth(token)).send({ online: true })).status).toBe(200);
    const d = await api().post('/api/chef/dishes').set(auth(token)).send({
      name: 'Нарын', category: 'lunch', price: 30000, photoUrls: ['https://x.uz/n.jpg'], type: 'order', prepTimeMinutes: 50, portionsLeft: 3,
    });
    expect(d.status).toBe(201);
    const noPrep = await api().post('/api/chef/dishes').set(auth(token)).send({ name: 'Х', category: 'lunch', price: 30000, photoUrls: ['https://x.uz/n.jpg'], type: 'order' });
    expect(noPrep.status).toBe(400);

    const feed = await api().get('/api/feed').query({ lat: 41.3111, lon: 69.2797, filter: 'order' });
    const item = feed.body.items.find((i: any) => i.name === 'Нарын');
    expect(item).toBeTruthy();
    expect(item.deliveryFee % 100).toBe(0);
    expect(item.chef.nick).toBe('Гульнора');
  });
});

describe('лента и карточка повара (ТЗ 3.2–3.3)', () => {
  it('«готово сейчас» идёт первым, адрес повара не раскрывается', async () => {
    const feed = await api().get('/api/feed').query({ lat: 41.3111, lon: 69.2797 });
    expect(feed.status).toBe(200);
    const items = feed.body.items;
    const firstOrderOnly = items.findIndex((i: any) => !i.readyNow);
    expect(items.slice(firstOrderOnly).every((i: any) => !i.readyNow)).toBe(true);
    expect(JSON.stringify(feed.body)).not.toContain('address');

    const chefId = await chefIdByNick('Нилуфар');
    const card = await api().get(`/api/chef/${chefId}`).query({ lat: 41.3111, lon: 69.2797 });
    expect(card.body.status).toBe('ready');
    expect(card.body.deliveryFee).toBeGreaterThan(0);
    expect(JSON.stringify(card.body)).not.toMatch(/address|41\.2995/);

    const search = await api().get('/api/search').query({ lat: 41.3111, lon: 69.2797, q: 'самса' });
    expect(search.body.items.map((i: any) => i.chef.nick)).toContain('Малика');
  });

  it('повар офлайн не виден в ленте', async () => {
    const t = await chefToken('Азиза');
    await api().put('/api/chef/status').set(auth(t)).send({ online: false });
    clearFeedCache();
    const feed = await api().get('/api/feed').query({ lat: 41.3111, lon: 69.2797 });
    expect(feed.body.items.some((i: any) => i.chef.nick === 'Азиза')).toBe(false);
    await api().put('/api/chef/status').set(auth(t)).send({ online: true });
  });
});

describe('полный цикл заказа (ТЗ 3.4–3.6, 5, 6)', () => {
  it('холд → принят (списание) → готово → курьер → доставлен → оценка → начисление → пятничная выплата', async () => {
    const { r, chefId, dish } = await placeOrder('Нилуфар', 2);
    expect(r.status).toBe(201);
    const order = r.body;
    expect(order.status).toBe('PENDING');
    expect(order.paymentStatus).toBe('held');
    expect(order.dishTotal).toBe(dish.price * 2);
    expect(order.total).toBe(order.dishTotal + order.deliveryFee);

    const chef = await chefToken('Нилуфар');
    const incoming = await api().get('/api/chef/orders').query({ scope: 'new' }).set(auth(chef));
    const mine = incoming.body.find((o: any) => o.id === order.id);
    expect(mine.chefAmount).toBe(Math.round(order.dishTotal * 0.76));
    expect(mine.total).toBeUndefined(); // повар не видит сумму с комиссией
    expect(mine.expiresAt).toBeTruthy();

    // чужой заказ принять нельзя
    const other = await chefToken('Малика');
    expect((await api().post(`/api/chef/orders/${order.id}/accept`).set(auth(other))).status).toBe(403);

    const acc = await api().post(`/api/chef/orders/${order.id}/accept`).set(auth(chef));
    expect(acc.body.status).toBe('COOKING');
    expect(acc.body.paymentStatus).toBe('charged');
    expect((await api().post(`/api/orders/${order.id}/cancel`).set(auth(buyer))).status).toBe(409);

    const ready = await api().post(`/api/chef/orders/${order.id}/ready`).set(auth(chef));
    expect(ready.body.status).toBe('READY');
    const row = (await query<any>('SELECT yandex_request_id FROM orders WHERE id = $1', [order.id]))[0];
    expect(row.yandex_request_id).toMatch(/^ydx_/);

    advance(60); await tickOrders();
    let t = await api().get(`/api/orders/${order.id}/tracking`).set(auth(buyer));
    expect(t.body.status).toBe('COURIER_ASSIGNED');
    expect(t.body.courier.name).toBe('Бахром');

    advance(300); await tickOrders();
    t = await api().get(`/api/orders/${order.id}/tracking`).set(auth(buyer));
    expect(t.body.status).toBe('IN_DELIVERY');

    advance(10_000); await tickOrders();
    const done = await api().get(`/api/orders/${order.id}`).set(auth(buyer));
    expect(done.body.status).toBe('DELIVERED');
    expect(done.body.history.map((h: any) => h.status)).toEqual(
      expect.arrayContaining(['PENDING', 'CONFIRMED', 'COOKING', 'READY', 'COURIER_ASSIGNED', 'IN_DELIVERY', 'DELIVERED']));

    const fin = (await query<any>('SELECT * FROM orders WHERE id = $1', [order.id]))[0];
    expect(fin.delivery_fee_real).toBeLessThan(fin.delivery_fee_shown);
    expect(fin.delivery_buffer).toBe(fin.delivery_fee_shown - fin.delivery_fee_real);

    const pending = await api().get('/api/buyer/pending-rating').set(auth(buyer));
    expect(pending.body.id).toBe(order.id);

    const rate = await api().post(`/api/orders/${order.id}/rate`).set(auth(buyer)).send({ rating: 5, tags: ['very_tasty', 'hot'], comment: 'Отлично' });
    expect(rate.status).toBe(200);
    expect(rate.body.chefRating).toBe(5);
    expect((await api().post(`/api/orders/${order.id}/rate`).set(auth(buyer)).send({ rating: 4 })).status).toBe(409);

    const bal = await api().get('/api/chef/balance').set(auth(chef));
    expect(bal.body.balance).toBe(fin.chef_net_amount);
    expect(bal.body.week.orders).toBe(1);

    // Пятница 10:00 по Ташкенту → автоматическая выплата
    expect(await tickPayouts(new Date('2026-05-01T05:00:30Z'))).toBe(true);
    const after = await api().get('/api/chef/balance').set(auth(chef));
    expect(after.body.balance).toBe(0);
    const payouts = (await query<any>('SELECT * FROM chef_payouts WHERE chef_id = $1', [chefId]));
    expect(payouts).toHaveLength(1);
    expect(payouts[0].status).toBe('done');
    expect(payouts[0].net_amount).toBe(fin.chef_net_amount);
    const tx = await api().get('/api/chef/transactions').set(auth(chef));
    expect(tx.body.map((x: any) => x.type).sort()).toEqual(['earning', 'payout']);
  });

  it('повар не ответил за 5 минут → автоотмена, холд снят, порции возвращены', async () => {
    const chefId = await chefIdByNick('Малика');
    await query('UPDATE dishes SET portions_left = 5 WHERE chef_id = $1', [chefId]);
    const { r, dish } = await placeOrder('Малика', 2);
    expect(r.status).toBe(201);
    expect((await query<any>('SELECT portions_left FROM dishes WHERE id = $1', [dish.id]))[0].portions_left).toBe(3);

    await query(`UPDATE orders SET created_at = now() - interval '200 seconds' WHERE id = $1`, [r.body.id]);
    await tickOrders();
    const n = await query<any>(`SELECT * FROM notifications WHERE type = 'order_reminder' AND data->>'orderId' = $1`, [String(r.body.id)]);
    expect(n).toHaveLength(1);

    await query(`UPDATE orders SET created_at = now() - interval '301 seconds' WHERE id = $1`, [r.body.id]);
    await tickOrders();
    const o = (await query<any>('SELECT * FROM orders WHERE id = $1', [r.body.id]))[0];
    expect(o.status).toBe('CANCELLED');
    expect(o.payment_status).toBe('cancelled');
    expect((await query<any>('SELECT portions_left FROM dishes WHERE id = $1', [dish.id]))[0].portions_left).toBe(5);
    const bn = await query<any>(`SELECT body FROM notifications WHERE type = 'order_timeout' AND data->>'orderId' = $1`, [String(r.body.id)]);
    expect(bn[0].body).toContain('Деньги возвращены');
  });

  it('повар отказал → холд снимается немедленно', async () => {
    const { r } = await placeOrder('Малика', 1);
    const chef = await chefToken('Малика');
    const d = await api().post(`/api/chef/orders/${r.body.id}/decline`).set(auth(chef)).send({ reason: 'Закончился рис' });
    expect(d.body.status).toBe('CANCELLED');
    expect(d.body.paymentStatus).toBe('cancelled');
  });

  it('недостаточно средств → 402, заказ не создаётся', async () => {
    const { token } = await loginUser('+998944440000');
    await api().post('/api/buyer/addresses').set(auth(token)).send({ lat: 41.31, lon: 69.28, addressText: 'Ташкент, ул. Навои 1' });
    // без карты заказ невозможен
    const chefId = await chefIdByNick('Нилуфар');
    const dish = await dishOf(chefId);
    const addr = (await api().get('/api/buyer/addresses').set(auth(token))).body[0];
    const noCard = await api().post('/api/orders').set(auth(token)).send({ items: [{ dishId: dish.id, qty: 1 }], addressId: addr.id });
    expect(noCard.status).toBe(400);
    await api().post('/api/buyer/cards').set(auth(token)).send({ cardToken: 'nofunds-8600' });
    const before = (await query<any>('SELECT count(*)::int AS n FROM orders'))[0].n;
    const r = await api().post('/api/orders').set(auth(token)).send({ items: [{ dishId: dish.id, qty: 1 }], addressId: addr.id });
    expect(r.status).toBe(402);
    expect(r.body.message).toContain('Недостаточно средств');
    expect((await query<any>('SELECT count(*)::int AS n FROM orders'))[0].n).toBe(before);
  });

  it('нельзя смешивать блюда двух поваров', async () => {
    const a = await dishOf(await chefIdByNick('Нилуфар'));
    const b = await dishOf(await chefIdByNick('Малика'));
    const addr = (await api().get('/api/buyer/addresses').set(auth(buyer))).body[0];
    const r = await api().post('/api/orders').set(auth(buyer)).send({ items: [{ dishId: a.id, qty: 1 }, { dishId: b.id, qty: 1 }], addressId: addr.id });
    expect(r.status).toBe(400);
    expect(r.body.message).toContain('одного повара');
  });
});

describe('админ-панель: возвраты, роли, отчёты', () => {
  it('поддержка: возврат ≤ 50 000; больше — только владелец; возврат удерживается с повара', async () => {
    const { r } = await placeOrder('Нилуфар', 3); // 105 000 сум блюда
    const chef = await chefToken('Нилуфар');
    await api().post(`/api/chef/orders/${r.body.id}/accept`).set(auth(chef));
    await api().post(`/api/chef/orders/${r.body.id}/ready`).set(auth(chef));
    advance(100_000); await tickOrders();
    expect((await query<any>('SELECT status FROM orders WHERE id = $1', [r.body.id]))[0].status).toBe('DELIVERED');

    const big = await api().post(`/api/admin/orders/${r.body.id}/refund`).set(auth(support)).send({ amount: 60000, reason: 'Холодная еда' });
    expect(big.status).toBe(403);
    const small = await api().post(`/api/admin/orders/${r.body.id}/refund`).set(auth(support)).send({ amount: 35000, reason: 'Не доложили порцию' });
    expect(small.status).toBe(200);
    expect(small.body.refundedAmount).toBe(35000);
    const ownerRefund = await api().post(`/api/admin/orders/${r.body.id}/refund`).set(auth(owner)).send({ amount: 60000, reason: 'Компенсация' });
    expect(ownerRefund.body.paymentStatus).toBe('partially_refunded');
    const tooMuch = await api().post(`/api/admin/orders/${r.body.id}/refund`).set(auth(owner)).send({ amount: 999999, reason: 'х' + 'ххх' });
    expect(tooMuch.status).toBe(400);

    const refunds = await query<any>(`SELECT * FROM chef_earnings WHERE order_id = $1 AND kind = 'refund' ORDER BY id`, [r.body.id]);
    expect(refunds.map((x) => x.gross_amount)).toEqual([-35000, -60000]);
    expect(refunds[0].net_amount).toBe(-26600);
  });

  it('поддержка блокирует повара максимум на 24 часа, владелец — навсегда', async () => {
    const id = await chefIdByNick('Азиза');
    expect((await api().put(`/api/admin/chefs/${id}/block`).set(auth(support)).send({ blocked: true, hours: 48, reason: 'Жалобы' })).status).toBe(403);
    expect((await api().put(`/api/admin/chefs/${id}/block`).set(auth(support)).send({ blocked: true, hours: 12, reason: 'Жалобы' })).status).toBe(200);
    expect((await api().put(`/api/admin/chefs/${id}/block`).set(auth(owner)).send({ blocked: false })).status).toBe(200);
  });

  it('дашборд: поддержка не видит деньги, владелец видит', async () => {
    const o = await api().get('/api/admin/dashboard').set(auth(owner));
    expect(o.status).toBe(200);
    expect(o.body.today.revenue).toBeGreaterThan(0);
    expect(o.body.payouts).toBeTruthy();
    const s = await api().get('/api/admin/dashboard').set(auth(support));
    expect(s.body.today.revenue).toBeUndefined();
    expect(s.body.payouts).toBeUndefined();
    expect((await api().get('/api/admin/finance').set(auth(support))).status).toBe(403);
  });

  it('финансы и налоговый отчёт Excel', async () => {
    const f = await api().get('/api/admin/finance').query({ period: 'month' }).set(auth(accountant));
    expect(f.status).toBe(200);
    expect(f.body.turnover).toBeGreaterThan(0);
    expect(f.body.chefTaxWithheld).toBeGreaterThan(0);
    const x = await api().get('/api/admin/reports/tax').set(auth(accountant)).buffer(true).parse((res, cb) => {
      const chunks: Buffer[] = []; res.on('data', (c: Buffer) => chunks.push(c)); res.on('end', () => cb(null, Buffer.concat(chunks)));
    });
    expect(x.status).toBe(200);
    expect(x.headers['content-type']).toContain('spreadsheetml');
    expect((x.body as Buffer).subarray(0, 2).toString()).toBe('PK'); // xlsx = zip
  });

  it('досрочный вывод: превью с комиссией 1%, затем заявка', async () => {
    const chef = await chefToken('Нилуфар');
    const bal = (await api().get('/api/chef/balance').set(auth(chef))).body.balance;
    expect(bal).toBeGreaterThan(0);
    const preview = await api().post('/api/chef/payout/request').set(auth(chef)).send({});
    expect(preview.body).toMatchObject({ preview: true, amount: bal, fee: Math.round(bal * 0.01) });
    const req = await api().post('/api/chef/payout/request').set(auth(chef)).send({ confirm: true });
    expect(req.body.payout.status).toBe('pending');
    expect((await api().get('/api/chef/balance').set(auth(chef))).body.balance).toBe(0);
    const done = await api().post(`/api/admin/payouts/${req.body.payout.id}/complete`).set(auth(accountant)).send({ ok: true, note: 'TX-1' });
    expect(done.body.status).toBe('done');
  });
});
