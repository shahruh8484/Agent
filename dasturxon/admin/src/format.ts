import type { OrderStatus } from './types';

export const TZ = 'Asia/Tashkent';
const TZ_OFFSET_MS = 5 * 3600e3; // Ташкент: UTC+5 без перехода на летнее время

/** 1247500 → «1 247 500 сум» */
export function money(n: number | string | null | undefined, suffix = ' сум'): string {
  if (n === null || n === undefined || n === '') return '—';
  const v = Math.round(Number(n));
  if (!Number.isFinite(v)) return '—';
  const s = Math.abs(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return `${v < 0 ? '−' : ''}${s}${suffix}`;
}

export function num(n: number | string | null | undefined): string {
  return money(n, '');
}

const fmtDateTime = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' });
const fmtDate = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit', year: 'numeric' });
const fmtTime = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, hour: '2-digit', minute: '2-digit' });
const fmtDayMonth = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: 'numeric', month: 'long', weekday: 'long', hour: '2-digit', minute: '2-digit' });

const valid = (s: string | Date | null | undefined) => {
  if (!s) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
};
export const dateTime = (s: string | Date | null | undefined) => { const d = valid(s); return d ? fmtDateTime.format(d) : '—'; };
export const date = (s: string | Date | null | undefined) => { const d = valid(s); return d ? fmtDate.format(d) : '—'; };
export const time = (s: string | Date | null | undefined) => { const d = valid(s); return d ? fmtTime.format(d) : '—'; };
export const longDateTime = (s: string | Date | null | undefined) => { const d = valid(s); return d ? fmtDayMonth.format(d) : '—'; };

/** Дата в Ташкенте в виде YYYY-MM-DD. */
export function tashkentYmd(d: Date = new Date()): string {
  return new Date(d.getTime() + TZ_OFFSET_MS).toISOString().slice(0, 10);
}

/** Начало / конец дня YYYY-MM-DD по Ташкенту в ISO (для фильтров API). */
export const dayStartIso = (ymd: string) => new Date(`${ymd}T00:00:00+05:00`).toISOString();
export const dayEndIso = (ymd: string) => new Date(`${ymd}T23:59:59.999+05:00`).toISOString();

/** Дни текущей недели (Пн–Вс) по Ташкенту. */
export function currentWeekDays(): { ymd: string; label: string; isToday: boolean }[] {
  const now = new Date(Date.now() + TZ_OFFSET_MS);
  const dow = (now.getUTCDay() + 6) % 7;
  const names = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const today = now.toISOString().slice(0, 10);
  return names.map((n, i) => {
    const d = new Date(now.getTime() + (i - dow) * 86400e3);
    const ymd = d.toISOString().slice(0, 10);
    return { ymd, label: `${n} ${ymd.slice(8, 10)}.${ymd.slice(5, 7)}`, isToday: ymd === today };
  });
}

export const statusLabels: Record<OrderStatus, string> = {
  PENDING: 'Новый',
  CONFIRMED: 'Принят',
  COOKING: 'Готовится',
  READY: 'Готов',
  COURIER_ASSIGNED: 'Курьер назначен',
  IN_DELIVERY: 'В пути',
  DELIVERED: 'Доставлен',
  RATED: 'Оценён',
  CANCELLED: 'Отменён',
};

export const statusColors: Record<OrderStatus, string> = {
  PENDING: 'gold',
  CONFIRMED: 'blue',
  COOKING: 'geekblue',
  READY: 'cyan',
  COURIER_ASSIGNED: 'purple',
  IN_DELIVERY: 'processing',
  DELIVERED: 'green',
  RATED: 'green',
  CANCELLED: 'red',
};

export const paymentLabels: Record<string, string> = {
  none: 'Нет оплаты',
  held: 'Захолдировано',
  charged: 'Списано',
  cancelled: 'Холд снят',
  refunded: 'Возвращено',
  partially_refunded: 'Частичный возврат',
};

export const complaintTypes: Record<string, string> = {
  quality: 'Качество еды',
  missing_items: 'Не хватает позиций',
  late: 'Опоздание',
  wrong_order: 'Не тот заказ',
  hygiene: 'Гигиена',
  other: 'Другое',
};

export const complaintStatus: Record<string, { label: string; color: string }> = {
  open: { label: 'Открыта', color: 'red' },
  resolved: { label: 'Решена', color: 'green' },
  closed: { label: 'Закрыта', color: 'default' },
};

export const payoutStatus: Record<string, { label: string; color: string }> = {
  pending: { label: 'Ожидает', color: 'gold' },
  processing: { label: 'В обработке', color: 'blue' },
  done: { label: 'Выплачено', color: 'green' },
  failed: { label: 'Ошибка', color: 'red' },
};

export const payoutKind: Record<string, string> = { weekly: 'Еженедельная', early: 'Досрочная' };

export const reviewTags: Record<string, string> = {
  very_tasty: 'Очень вкусно',
  hot: 'Горячее',
  big_portion: 'Большая порция',
  fast: 'Быстро доставили',
  as_ordered: 'Точно как заказал',
  nice_packaging: 'Красивая упаковка',
};

export const roleLabels: Record<string, string> = { owner: 'Владелец', accountant: 'Бухгалтер', support: 'Поддержка' };

export const verificationLabels: Record<string, { label: string; color: string }> = {
  draft: { label: 'Черновик', color: 'default' },
  pending: { label: 'На проверке', color: 'blue' },
  approved: { label: 'Подтверждён', color: 'green' },
  rejected: { label: 'Отклонён', color: 'red' },
};

export const rating = (r: number | string | null | undefined, count?: number) =>
  r === null || r === undefined || (Number(r) === 0 && !count) ? '—' : Number(r).toFixed(1);

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${v > 0 ? '+' : ''}${v}%`;
}
