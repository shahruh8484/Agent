// Сквозная проверка на живом сервере: заказ → повар принимает → готово → курьер,
// при этом покупатель получает события по WebSocket.
// Запуск: API_URL=http://localhost:4000 npx tsx scripts/smoke-realtime.ts
import { io } from 'socket.io-client';

const BASE = process.env.API_URL ?? 'http://localhost:4000';

async function call(method: string, path: string, body?: unknown, token?: string) {
  const r = await fetch(`${BASE}/api${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const j: any = await r.json();
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status} ${j.message}`);
  return j;
}

async function login(phone: string, role: 'buyer' | 'chef') {
  const s = await call('POST', '/auth/send-otp', { phone });
  return (await call('POST', '/auth/verify-otp', { phone, code: s.devCode, role })).accessToken as string;
}

const buyer = await login('+998901234567', 'buyer');
const chef = await login('+998901110001', 'chef');
const events: string[] = [];

const sock = io(BASE, { path: '/socket.io', auth: { token: buyer }, transports: ['websocket'] });
await new Promise<void>((res, rej) => { sock.on('connect', () => res()); sock.on('connect_error', rej); });
sock.on('order:update', (e) => events.push(`update:${e.status}`));
sock.on('order:tracking', (e) => events.push(`tracking:${e.status}:${e.courier?.position ? 'pos' : '-'}`));
sock.on('notification', (e) => events.push(`push:${e.title}`));

const menu = await call('GET', `/chef/${(await call('GET', '/feed?lat=41.31&lon=69.28')).items.find((i: any) => i.chef.nick === 'Нилуфар').chef.id}/menu`);
const addr = (await call('GET', '/buyer/addresses', undefined, buyer))[0];
const order = await call('POST', '/orders', { items: [{ dishId: menu.dishes[0].id, qty: 1 }], addressId: addr.id }, buyer);
sock.emit('order:subscribe', order.id);
console.log(`Заказ #${order.id}: ${order.dishTotal} + доставка ${order.deliveryFee} = ${order.total} сум`);

await call('POST', `/chef/orders/${order.id}/accept`, {}, chef);
await call('POST', `/chef/orders/${order.id}/ready`, {}, chef);

// Ждём, пока симуляция курьера (DELIVERY_MOCK_SPEED) довезёт заказ
const deadline = Date.now() + 90_000;
let status = '';
while (Date.now() < deadline) {
  status = (await call('GET', `/orders/${order.id}/tracking`, undefined, buyer)).status;
  if (status === 'DELIVERED') break;
  await new Promise((r) => setTimeout(r, 2000));
}
await new Promise((r) => setTimeout(r, 500));
sock.close();
console.log('Финальный статус:', status);
console.log('События WebSocket:', [...new Set(events)].join(' | '));
if (status !== 'DELIVERED' || !events.some((e) => e.startsWith('tracking:IN_DELIVERY:pos'))) process.exit(1);
