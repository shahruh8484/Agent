import { describe, expect, it } from 'vitest';
import { can } from '../src/auth/permissions.js';
import { deliveryPriceForBuyer, earlyWithdrawal, roundUp100, splitChefAmount } from '../src/lib/money.js';
import { nextPayoutAt, weekStart } from '../src/lib/time.js';

describe('деньги', () => {
  it('доставка = ответ Яндекса × 1.15, округление вверх до 100 сум', () => {
    expect(deliveryPriceForBuyer(10000)).toBe(11500);
    expect(deliveryPriceForBuyer(12000)).toBe(13800);
    expect(deliveryPriceForBuyer(9999)).toBe(11500); // 11498.85 → 11500
    expect(roundUp100(11501)).toBe(11600);
  });

  it('повару X × 0.76: комиссия 20%, налог 4%', () => {
    expect(splitChefAmount(35000)).toEqual({ gross: 35000, commission: 7000, tax: 1400, net: 26600 });
    const s = splitChefAmount(12345);
    expect(s.commission + s.tax + s.net).toBe(12345);
  });

  it('досрочный вывод: × 0.99', () => {
    expect(earlyWithdrawal(247500)).toEqual({ amount: 247500, fee: 2475, net: 245025 });
  });
});

describe('время (Ташкент UTC+5)', () => {
  it('следующая выплата — пятница 10:00 по Ташкенту', () => {
    // Среда 2026-04-29 12:00 UTC → пятница 2026-05-01 05:00 UTC (10:00 Ташкент)
    expect(nextPayoutAt(new Date('2026-04-29T12:00:00Z')).toISOString()).toBe('2026-05-01T05:00:00.000Z');
    // Пятница 10:30 по Ташкенту → следующая пятница
    expect(nextPayoutAt(new Date('2026-05-01T05:30:00Z')).toISOString()).toBe('2026-05-08T05:00:00.000Z');
  });

  it('неделя начинается в понедельник по Ташкенту', () => {
    expect(weekStart(new Date('2026-05-03T20:00:00Z'))).toBe('2026-05-04'); // вс 20:00 UTC = пн 01:00 Ташкент
    expect(weekStart(new Date('2026-05-03T10:00:00Z'))).toBe('2026-04-27');
  });
});

describe('роли админ-панели (ТЗ 7.1)', () => {
  it('матрица доступа', () => {
    expect(can('owner', 'settings')).toBe(true);
    expect(can('accountant', 'payouts_run')).toBe(true);
    expect(can('accountant', 'chefs_verify')).toBe(false);
    expect(can('support', 'refund_small')).toBe(true);
    expect(can('support', 'refund_large')).toBe(false);
    expect(can('support', 'chefs_block_permanent')).toBe(false);
    expect(can('support', 'finance')).toBe(false);
  });
});
