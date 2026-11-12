// Конфигурация из переменных окружения. Все интеграции по умолчанию в режиме mock,
// чтобы платформа работала без ключей (для разработки и демо).
const env = process.env;

function num(name: string, def: number): number {
  const v = env[name];
  return v === undefined || v === '' ? def : Number(v);
}

export const config = {
  port: num('PORT', 4000),
  databaseUrl: env.DATABASE_URL ?? 'postgres://dx:dx@localhost:5432/dasturxon',
  jwtSecret: env.JWT_SECRET ?? 'dev-secret-change-me',
  accessTtlSec: num('JWT_ACCESS_TTL_SEC', 15 * 60),
  refreshTtlSec: num('JWT_REFRESH_TTL_SEC', 30 * 24 * 3600),
  corsOrigin: env.CORS_ORIGIN ?? '*',
  // Включает фоновые задачи (таймауты, трекинг, выплаты). В тестах выключено.
  jobsEnabled: (env.JOBS_ENABLED ?? 'true') === 'true',
  // В dev-режиме OTP-код возвращается в ответе /auth/send-otp и всегда можно ввести 0000.
  devOtp: (env.DEV_OTP ?? (env.NODE_ENV === 'production' ? 'false' : 'true')) === 'true',

  sms: {
    provider: env.SMS_PROVIDER ?? 'mock', // mock | eskiz
    eskizEmail: env.ESKIZ_EMAIL ?? '',
    eskizPassword: env.ESKIZ_PASSWORD ?? '',
    eskizFrom: env.ESKIZ_FROM ?? '4546',
  },
  payme: {
    provider: env.PAYMENT_PROVIDER ?? 'mock', // mock | payme
    merchantId: env.PAYME_MERCHANT_ID ?? '',
    key: env.PAYME_KEY ?? '',
    url: env.PAYME_URL ?? 'https://checkout.test.paycom.uz/api',
  },
  yandex: {
    provider: env.DELIVERY_PROVIDER ?? 'mock', // mock | yandex
    token: env.YANDEX_GO_TOKEN ?? '',
    url: env.YANDEX_GO_URL ?? 'https://b2b.taxi.yandex.net/b2b/cargo/integration',
    // Скорость симуляции курьера в mock-режиме (1 = реальное время, 60 = в 60 раз быстрее)
    mockSpeed: num('DELIVERY_MOCK_SPEED', 10),
  },
  bank: {
    provider: env.PAYOUT_PROVIDER ?? 'mock',
  },
  ai: {
    // Если ключа нет — AI-проверка пропускается, заявка уходит админу на ручную проверку
    enabled: !!(env.ANTHROPIC_API_KEY || env.ANTHROPIC_AUTH_TOKEN),
    model: env.AI_MODEL ?? 'claude-opus-5-5',
  },
  business: {
    commissionPct: num('COMMISSION_PCT', 20),
    chefTaxPct: num('CHEF_TAX_PCT', 4),
    deliveryBufferPct: num('DELIVERY_BUFFER_PCT', 15),
    earlyWithdrawPct: num('EARLY_WITHDRAW_PCT', 1),
    itParkPct: num('IT_PARK_PCT', 1),
    chefAcceptTimeoutSec: num('CHEF_ACCEPT_TIMEOUT_SEC', 300),
    supportRefundLimit: num('SUPPORT_REFUND_LIMIT', 50000),
    lowRatingThreshold: 3.5,
    lowRatingMinOrders: 10,
  },
};
