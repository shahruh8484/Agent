import { createServer } from 'node:http';
import { Server } from 'socket.io';
import { createApp } from './app.js';
import { verifyAccess } from './auth/tokens.js';
import { config } from './config.js';
import { one } from './db.js';
import { setEmitter, rooms } from './integrations/realtime.js';
import { startJobs } from './jobs.js';
import { migrate } from './migrate.js';
import { loadSettings } from './services/settings.js';

await migrate();
// Демо-стенд: заполнить базу тестовыми поварами и сотрудниками при запуске (повторно безопасно)
if (process.env.SEED_ON_START === 'true') {
  const { seed } = await import('./seed.js');
  await seed();
}
await loadSettings();

const app = createApp();
const http = createServer(app);
const io = new Server(http, { cors: { origin: '*' }, path: '/socket.io' });

// WebSocket: клиент подключается с JWT и автоматически входит в свою комнату;
// для трекинга подписывается на заказ: socket.emit('order:subscribe', orderId)
io.use((socket, next) => {
  try {
    const token = socket.handshake.auth?.token ?? String(socket.handshake.query?.token ?? '');
    socket.data.principal = verifyAccess(token);
    next();
  } catch { next(new Error('unauthorized')); }
});

io.on('connection', (socket) => {
  const p = socket.data.principal;
  if (p.kind === 'staff') socket.join(rooms.admin);
  else socket.join(rooms.user(p.id));
  socket.on('order:subscribe', async (orderId: number) => {
    if (p.kind === 'staff') return socket.join(rooms.order(Number(orderId)));
    const o = await one('SELECT 1 FROM orders WHERE id = $1 AND (buyer_id = $2 OR chef_id = $2)', [Number(orderId), p.id]);
    if (o) socket.join(rooms.order(Number(orderId)));
  });
  socket.on('order:unsubscribe', (orderId: number) => socket.leave(rooms.order(Number(orderId))));
});

setEmitter((room, event, payload) => io.to(room).emit(event, payload));
if (config.jobsEnabled) startJobs();

http.listen(config.port, () => console.log(`Dasturxon API: http://localhost:${config.port}/api`));
