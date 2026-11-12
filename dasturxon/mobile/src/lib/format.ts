import type { OrderStatus } from '../api/types';

/** 35000 → «35 000 сум» */
export function money(n: number | null | undefined, withCurrency = true): string {
  const v = Math.round(Number(n ?? 0));
  const s = String(Math.abs(v)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  return `${v < 0 ? '−' : ''}${s}${withCurrency ? ' сум' : ''}`;
}

/** Цифры номера без кода страны: «901234567» */
export function phoneDigits(input: string): string {
  let d = input.replace(/\D/g, '');
  if (d.startsWith('998') && d.length > 9) d = d.slice(3);
  return d.slice(0, 9);
}

/** Маска «90 123 45 67» */
export function maskPhone(digits: string): string {
  const d = digits.slice(0, 9);
  const parts = [d.slice(0, 2), d.slice(2, 5), d.slice(5, 7), d.slice(7, 9)].filter(Boolean);
  return parts.join(' ');
}

export function formatPhone(full: string): string {
  const d = phoneDigits(full);
  return `+998 ${maskPhone(d)}`;
}

export function pad2(n: number) {
  return String(n).padStart(2, '0');
}

export function time(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

const MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

export function dateShort(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

export function dateTime(iso?: string | null): string {
  if (!iso) return '';
  return `${dateShort(iso)}, ${time(iso)}`;
}

export function countdown(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
}

export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

export const STATUS_LABEL: Record<OrderStatus, string> = {
  PENDING: 'Ждём подтверждения повара',
  CONFIRMED: 'Повар принял заказ',
  COOKING: 'Повар готовит заказ',
  READY: 'Еда готова, ищем курьера',
  COURIER_ASSIGNED: 'Курьер едет к повару',
  IN_DELIVERY: 'Курьер едет к вам',
  DELIVERED: 'Заказ доставлен',
  RATED: 'Заказ доставлен',
  CANCELLED: 'Заказ отменён',
};

export const STATUS_SHORT: Record<OrderStatus, string> = {
  PENDING: 'Ожидает',
  CONFIRMED: 'Принят',
  COOKING: 'Готовится',
  READY: 'Готов',
  COURIER_ASSIGNED: 'Курьер назначен',
  IN_DELIVERY: 'В пути',
  DELIVERED: 'Доставлен',
  RATED: 'Доставлен',
  CANCELLED: 'Отменён',
};

export const ACTIVE_STATUSES: OrderStatus[] = ['PENDING', 'CONFIRMED', 'COOKING', 'READY', 'COURIER_ASSIGNED', 'IN_DELIVERY'];

export const CATEGORY_LABEL: Record<string, string> = {
  breakfast: 'Завтрак',
  lunch: 'Обед',
  dinner: 'Ужин',
  bakery: 'Выпечка',
  drinks: 'Напитки',
  other: 'Другое',
};

export const REVIEW_TAGS: { key: string; label: string }[] = [
  { key: 'very_tasty', label: 'Очень вкусно' },
  { key: 'hot', label: 'Горячее' },
  { key: 'big_portion', label: 'Большая порция' },
  { key: 'fast', label: 'Быстро доставили' },
  { key: 'as_ordered', label: 'Точно как заказал' },
  { key: 'nice_packaging', label: 'Красивая упаковка' },
];

export const tagLabel = (k: string) => REVIEW_TAGS.find((t) => t.key === k)?.label ?? k;

/** «Ташкент, Мирабадский р-н, ул. ...» → короткий вид для шапки */
export function shortAddress(text?: string | null): string {
  if (!text) return 'Укажите адрес';
  const parts = text.split(',').map((s) => s.trim()).filter(Boolean);
  const meaningful = parts.filter((p) => !/^ташкент$/i.test(p) && !/^узбекистан$/i.test(p));
  return (meaningful.slice(-2).join(', ') || text).slice(0, 40);
}
