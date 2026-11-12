import { randomUUID } from 'node:crypto';

// Массовые выплаты поварам на карты UZCARD/HUMO (ТЗ 6.2). Банк выбирается позже
// (Kapitalbank / Ipoteka / Click Business) — здесь интерфейс и mock.
export interface PayoutBank {
  transfer(cardToken: string, amount: number, ref: string): Promise<{ ok: true; transactionId: string } | { ok: false; error: string }>;
}

class MockBank implements PayoutBank {
  async transfer(cardToken: string, _amount: number, _ref: string) {
    if (cardToken.includes('fail')) return { ok: false as const, error: 'Карта заблокирована' };
    return { ok: true as const, transactionId: `bank_${randomUUID()}` };
  }
}

export const bank: PayoutBank = new MockBank();
