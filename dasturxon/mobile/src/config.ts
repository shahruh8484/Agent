// Конфигурация из переменных окружения (EXPO_PUBLIC_* встраиваются в бандл при сборке)
const trimSlash = (s: string) => s.replace(/\/+$/, '');

/** Адрес сервера по умолчанию (задаётся при сборке) */
export const DEFAULT_API_URL = trimSlash(process.env.EXPO_PUBLIC_API_URL || 'http://localhost:4000');
/** Разрешить менять адрес сервера на экране входа (для тестовых сборок; в магазинной сборке выключить) */
export const ALLOW_SERVER_CHANGE = process.env.EXPO_PUBLIC_ALLOW_SERVER_CHANGE !== 'false';

let apiUrl = DEFAULT_API_URL;
export const getApiUrl = () => apiUrl;
export const getApiBase = () => `${apiUrl}/api`;
export function setApiUrl(url: string) {
  apiUrl = trimSlash(url.trim()) || DEFAULT_API_URL;
}
export function normalizeServerUrl(input: string): string {
  const v = input.trim();
  if (!v) return DEFAULT_API_URL;
  return trimSlash(/^https?:\/\//i.test(v) ? v : `http://${v}`);
}

export const YANDEX_MAPS_KEY = process.env.EXPO_PUBLIC_YANDEX_MAPS_KEY || '';
export const YANDEX_GEOCODER_KEY = process.env.EXPO_PUBLIC_YANDEX_GEOCODER_KEY || '';

export type PaymentMode = 'mock' | 'payme';
export const PAYMENT_MODE: PaymentMode = process.env.EXPO_PUBLIC_PAYMENT_MODE === 'payme' ? 'payme' : 'mock';
export const PAYME_MERCHANT_ID = process.env.EXPO_PUBLIC_PAYME_MERCHANT_ID || '';
export const PAYME_API_URL = process.env.EXPO_PUBLIC_PAYME_API_URL || 'https://checkout.paycom.uz/api';

export const SUPPORT_PHONE = process.env.EXPO_PUBLIC_SUPPORT_PHONE || '+998712000000';
export const SUPPORT_TELEGRAM = process.env.EXPO_PUBLIC_SUPPORT_TELEGRAM || 'https://t.me/dasturxon_support';

/** Центр Ташкента — точка по умолчанию, если адрес/геолокация неизвестны */
export const TASHKENT = { lat: 41.311081, lon: 69.279737 };
