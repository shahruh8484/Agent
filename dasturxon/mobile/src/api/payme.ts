/**
 * Привязка карты через Payme Subscribe API (клиентская часть).
 * Используется при EXPO_PUBLIC_PAYMENT_MODE=payme. Схема:
 *   1. cards.create { card: { number, expire: "MMYY" }, save: true } → token (неверифицированный)
 *   2. cards.get_verify_code { token } → SMS на телефон владельца карты
 *   3. cards.verify { token, code } → токен становится постоянным
 * Полученный токен отправляется на бэкенд (`POST /buyer/cards` / `PUT /chef/payout-card`),
 * там он проверяется через cards.check. Заголовок X-Auth на клиенте содержит ТОЛЬКО merchant id
 * (секретный ключ кассы хранится только на сервере).
 */
import { PAYME_API_URL, PAYME_MERCHANT_ID } from '../config';

export class PaymeError extends Error {
  code?: number;
  constructor(message: string, code?: number) {
    super(message);
    this.code = code;
  }
}

async function call<T>(method: string, params: Record<string, unknown>): Promise<T> {
  if (!PAYME_MERCHANT_ID) throw new PaymeError('Не задан EXPO_PUBLIC_PAYME_MERCHANT_ID');
  let res: Response;
  try {
    res = await fetch(PAYME_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Auth': PAYME_MERCHANT_ID },
      body: JSON.stringify({ id: Date.now(), method, params }),
    });
  } catch {
    throw new PaymeError('Нет соединения с Payme');
  }
  const json: any = await res.json().catch(() => ({}));
  if (json.error) {
    const msg = json.error.message?.ru ?? json.error.message ?? 'Ошибка Payme';
    throw new PaymeError(typeof msg === 'string' ? msg : 'Ошибка Payme', json.error.code);
  }
  return json.result as T;
}

export interface PaymeCard {
  number: string; // маскированный: 860006******6311
  expire: string;
  token: string;
  recurrent: boolean;
  verify: boolean;
}

/** Шаг 1: создать токен карты. number — 16 цифр, expire — «MMYY». */
export async function createCard(number: string, expire: string): Promise<PaymeCard> {
  const r = await call<{ card: PaymeCard }>('cards.create', {
    card: { number: number.replace(/\D/g, ''), expire: expire.replace(/\D/g, '') },
    save: true,
  });
  return r.card;
}

/** Шаг 2: отправить SMS-код. Возвращает маскированный телефон и время ожидания (мс). */
export async function sendVerifyCode(token: string): Promise<{ sent: boolean; phone: string; wait: number }> {
  return call('cards.get_verify_code', { token });
}

/** Шаг 3: подтвердить карту кодом из SMS. Возвращает итоговую карту с постоянным токеном. */
export async function verifyCard(token: string, code: string): Promise<PaymeCard> {
  const r = await call<{ card: PaymeCard }>('cards.verify', { token, code });
  return r.card;
}

export async function removeCard(token: string): Promise<void> {
  await call('cards.remove', { token });
}
