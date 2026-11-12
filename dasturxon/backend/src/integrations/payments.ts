import { randomUUID } from 'node:crypto';
import { config } from '../config.js';

/**
 * Платёжный шлюз (ТЗ 3.4, 11.2). Схема Payme Subscribe API:
 *   cards.create / cards.verify → токен карты
 *   receipts.create + receipts.pay (hold) → холдирование
 *   receipts.confirm_hold → списание, receipts.cancel → снятие холда / возврат
 * Номера карт на сервере не хранятся — только токен.
 */
export interface PaymentGateway {
  /** Обмен одноразового токена из Payme-формы на постоянный токен карты */
  bindCard(cardToken: string): Promise<{ token: string; maskedPan: string; cardType: 'uzcard' | 'humo' | 'payme' }>;
  hold(cardToken: string, amount: number, orderId: number): Promise<{ ok: true; paymentId: string } | { ok: false; reason: string }>;
  confirm(paymentId: string): Promise<void>;
  cancel(paymentId: string): Promise<void>;
  refund(paymentId: string, amount: number): Promise<void>;
}

/** Mock: токен, содержащий "nofunds", имитирует нехватку средств. */
class MockGateway implements PaymentGateway {
  payments = new Map<string, { amount: number; state: 'held' | 'charged' | 'cancelled'; refunded: number }>();

  async bindCard(cardToken: string) {
    const last4 = cardToken.replace(/\D/g, '').slice(-4).padStart(4, '0');
    const cardType = cardToken.includes('humo') ? 'humo' as const : 'uzcard' as const;
    const prefix = cardType === 'humo' ? '9860' : '8600';
    return { token: `mock_${cardToken}`, maskedPan: `${prefix} **** **** ${last4}`, cardType };
  }
  async hold(cardToken: string, amount: number) {
    if (cardToken.includes('nofunds')) return { ok: false as const, reason: 'Недостаточно средств' };
    const paymentId = `pay_${randomUUID()}`;
    this.payments.set(paymentId, { amount, state: 'held', refunded: 0 });
    return { ok: true as const, paymentId };
  }
  async confirm(paymentId: string) {
    const p = this.payments.get(paymentId);
    if (p) p.state = 'charged';
  }
  async cancel(paymentId: string) {
    const p = this.payments.get(paymentId);
    if (p) p.state = 'cancelled';
  }
  async refund(paymentId: string, amount: number) {
    const p = this.payments.get(paymentId);
    if (p) p.refunded += amount;
  }
}

class PaymeGateway implements PaymentGateway {
  private async call(method: string, params: Record<string, unknown>) {
    const r = await fetch(config.payme.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Auth': `${config.payme.merchantId}:${config.payme.key}` },
      body: JSON.stringify({ id: Date.now(), method, params }),
    });
    const j: any = await r.json();
    if (j.error) throw Object.assign(new Error(j.error.message?.ru ?? j.error.message ?? 'Payme error'), { payme: j.error });
    return j.result;
  }
  async bindCard(cardToken: string) {
    const r = await this.call('cards.check', { token: cardToken });
    const number: string = r.card.number; // уже маскированный: 860006******6311
    const cardType = number.startsWith('9860') ? 'humo' as const : 'uzcard' as const;
    return { token: r.card.token, maskedPan: number, cardType };
  }
  async hold(cardToken: string, amount: number, orderId: number) {
    try {
      const created = await this.call('receipts.create', { amount: amount * 100, account: { order_id: String(orderId) }, hold: true });
      const receiptId = created.receipt._id;
      await this.call('receipts.pay', { id: receiptId, token: cardToken, hold: true });
      return { ok: true as const, paymentId: receiptId };
    } catch (e: any) {
      return { ok: false as const, reason: e.message || 'Недостаточно средств' };
    }
  }
  async confirm(paymentId: string) { await this.call('receipts.confirm_hold', { id: paymentId }); }
  async cancel(paymentId: string) { await this.call('receipts.cancel', { id: paymentId }); }
  async refund(paymentId: string, _amount: number) {
    // Payme не поддерживает частичный возврат чека; частичные возвраты проводятся вручную через кабинет.
    await this.call('receipts.cancel', { id: paymentId });
  }
}

export const payments: PaymentGateway = config.payme.provider === 'payme' ? new PaymeGateway() : new MockGateway();
