// Ташкент: UTC+5, без перехода на летнее время.
export const TASHKENT_OFFSET_MIN = 5 * 60;

/** Понедельник недели (в ташкентском времени) в формате YYYY-MM-DD. */
export function weekStart(d: Date = new Date()): string {
  const local = new Date(d.getTime() + TASHKENT_OFFSET_MIN * 60000);
  const day = (local.getUTCDay() + 6) % 7; // 0 = понедельник
  local.setUTCDate(local.getUTCDate() - day);
  return local.toISOString().slice(0, 10);
}

export function addDays(isoDate: string, days: number): string {
  const d = new Date(isoDate + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** Ближайшая пятница 10:00 по Ташкенту (момент следующей автоматической выплаты). */
export function nextPayoutAt(now: Date = new Date()): Date {
  const local = new Date(now.getTime() + TASHKENT_OFFSET_MIN * 60000);
  const day = local.getUTCDay(); // 5 = пятница
  let add = (5 - day + 7) % 7;
  const candidate = new Date(Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate() + add, 10, 0, 0));
  if (candidate.getTime() <= local.getTime()) candidate.setUTCDate(candidate.getUTCDate() + 7);
  return new Date(candidate.getTime() - TASHKENT_OFFSET_MIN * 60000);
}

/** Начало текущих суток по Ташкенту, в UTC. */
export function startOfTashkentDay(d: Date = new Date(), offsetDays = 0): Date {
  const local = new Date(d.getTime() + TASHKENT_OFFSET_MIN * 60000);
  const start = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate() + offsetDays);
  return new Date(start - TASHKENT_OFFSET_MIN * 60000);
}
