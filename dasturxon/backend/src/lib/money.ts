import { config } from '../config.js';

/** Округление вверх до 100 сум (ТЗ 3.3: «округление до 100 сум»). */
export function roundUp100(x: number): number {
  return Math.ceil(x / 100) * 100;
}

/** Цена доставки для покупателя = ответ Яндекса × 1.15, округление вверх до 100. */
export function deliveryPriceForBuyer(yandexPrice: number, bufferPct = config.business.deliveryBufferPct): number {
  return roundUp100(yandexPrice * (1 + bufferPct / 100));
}

/** Расчёт выплаты повару (ТЗ 4.4): X − 20% комиссии − 4% налога = X × 0.76. */
export function splitChefAmount(dishTotal: number, commissionPct = config.business.commissionPct, taxPct = config.business.chefTaxPct) {
  const commission = Math.round((dishTotal * commissionPct) / 100);
  const tax = Math.round((dishTotal * taxPct) / 100);
  return { gross: dishTotal, commission, tax, net: dishTotal - commission - tax };
}

/** Досрочный вывод: сумма × 0.99. */
export function earlyWithdrawal(amount: number, pct = config.business.earlyWithdrawPct) {
  const fee = Math.round((amount * pct) / 100);
  return { amount, fee, net: amount - fee };
}
