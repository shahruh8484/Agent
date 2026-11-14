import { io, type Socket } from 'socket.io-client';
import { getApiUrl } from '../config';

let socket: Socket | null = null;
let tokenGetter: () => string | null = () => null;

export function setSocketTokenGetter(fn: () => string | null) {
  tokenGetter = fn;
}

/** Общий сокет на всё приложение; токен берётся при каждом (пере)подключении */
export function getSocket(): Socket | null {
  if (!tokenGetter()) return null;
  if (!socket) {
    socket = io(getApiUrl(), {
      path: '/socket.io',
      transports: ['websocket'],
      auth: (cb) => cb({ token: tokenGetter() }),
      reconnection: true,
      reconnectionDelay: 2000,
    });
    socket.on('connect_error', () => {
      // токен мог истечь — переподключение возьмёт свежий через auth-колбэк
    });
  }
  if (socket.disconnected) socket.connect();
  return socket;
}

export function disconnectSocket() {
  socket?.removeAllListeners();
  socket?.disconnect();
  socket = null;
}

/** Подписка на событие; возвращает функцию отписки */
export function onSocket<T = any>(event: string, handler: (payload: T) => void): () => void {
  const s = getSocket();
  if (!s) return () => undefined;
  s.on(event, handler);
  return () => {
    s.off(event, handler);
  };
}

export function subscribeOrder(orderId: number): () => void {
  const s = getSocket();
  if (!s) return () => undefined;
  const join = () => s.emit('order:subscribe', orderId);
  join();
  s.on('connect', join);
  return () => {
    s.off('connect', join);
    s.emit('order:unsubscribe', orderId);
  };
}
