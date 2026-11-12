// Socket.io: живые обновления для админов (комната `admin`).
import { App } from 'antd';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { io, type Socket } from 'socket.io-client';
import { API_BASE, refreshOnce, tokens } from './api';
import { useAuth } from './auth';
import { money } from './format';

type Handler = (payload: any) => void;
const listeners = new Map<string, Set<Handler>>();

function fire(event: string, payload: unknown) {
  listeners.get(event)?.forEach((h) => { try { h(payload); } catch { /* ignore */ } });
}

/** Подписка компонента на события сокета. */
export function useLive(events: string[], handler: Handler) {
  const ref = useRef(handler);
  ref.current = handler;
  const key = events.join('|');
  useEffect(() => {
    const h: Handler = (p) => ref.current(p);
    const evs = key.split('|');
    evs.forEach((e) => { if (!listeners.has(e)) listeners.set(e, new Set()); listeners.get(e)!.add(h); });
    return () => evs.forEach((e) => listeners.get(e)?.delete(h));
  }, [key]);
}

const Ctx = createContext<{ connected: boolean }>({ connected: false });
export const useRealtime = () => useContext(Ctx);

function alertText(a: any): { title: string; body?: string; link?: string; level: 'error' | 'warning' | 'info' } {
  switch (a?.type) {
    case 'complaint': return { title: 'Новая жалоба', body: a.complaintId ? `Жалоба #${a.complaintId}` : undefined, link: a.complaintId ? `/complaints/${a.complaintId}` : '/complaints', level: 'error' };
    case 'verification': return { title: 'Новая заявка на верификацию', link: '/verification', level: 'info' };
    case 'low_rating': return { title: 'Низкий рейтинг повара', body: a.rating ? `Рейтинг ${Number(a.rating).toFixed(1)}` : undefined, link: a.chefId ? `/chefs/${a.chefId}` : undefined, level: 'warning' };
    case 'payout_failed': return { title: 'Ошибка выплаты', body: a.error, link: '/payouts', level: 'error' };
    case 'early_withdrawal': return { title: 'Запрос на досрочный вывод', body: a.amount ? money(a.amount) : undefined, link: '/payouts', level: 'info' };
    case 'courier_request_failed': return { title: 'Не удалось вызвать курьера', body: `Заказ #${a.orderId}`, link: `/orders/${a.orderId}`, level: 'error' };
    case 'courier_cancelled': return { title: 'Курьер отменил доставку', body: `Заказ #${a.orderId}`, link: `/orders/${a.orderId}`, level: 'warning' };
    case 'autocancel': return { title: 'Повар пропускает заказы', body: a.count ? `Автоотмен: ${a.count}` : undefined, link: a.chefId ? `/chefs/${a.chefId}` : undefined, level: 'warning' };
    default: return { title: 'Уведомление', body: a?.type, level: 'info' };
  }
}

export function RealtimeProvider({ children }: { children: ReactNode }) {
  const { staff } = useAuth();
  const { notification } = App.useApp();
  const navigate = useNavigate();
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<Socket | null>(null);

  useEffect(() => {
    if (!staff) return;
    const s = io(API_BASE, {
      path: '/socket.io',
      // функция — чтобы при переподключении брать свежий access-токен
      auth: (cb) => cb({ token: tokens.access }),
      transports: ['websocket', 'polling'],
    });
    socketRef.current = s;
    s.on('connect', () => setConnected(true));
    s.on('disconnect', () => setConnected(false));
    s.on('connect_error', async (err) => {
      setConnected(false);
      // токен истёк — обновляем и пробуем снова
      if (/auth|token|unauthor/i.test(err.message) && (await refreshOnce())) s.connect();
    });
    s.on('order:update', (p) => fire('order:update', p));
    s.on('payouts:done', (p) => {
      fire('payouts:done', p);
      notification.success({ message: 'Выплаты выполнены', placement: 'bottomRight' });
    });
    s.on('alert', (a) => {
      fire('alert', a);
      const t = alertText(a);
      notification[t.level]({
        message: t.title,
        description: t.body,
        placement: 'bottomRight',
        style: t.link ? { cursor: 'pointer' } : undefined,
        onClick: t.link ? () => navigate(t.link!) : undefined,
      });
    });
    return () => {
      s.disconnect();
      socketRef.current = null;
      setConnected(false);
    };
  }, [staff?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return <Ctx.Provider value={{ connected }}>{children}</Ctx.Provider>;
}
