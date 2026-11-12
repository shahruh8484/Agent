import { config } from '../config.js';
import { query } from '../db.js';

// Настройки платформы, которые владелец меняет в админке. Хранятся в таблице settings
// и накладываются на значения из окружения.
const KEYS = ['commissionPct', 'deliveryBufferPct', 'earlyWithdrawPct', 'chefAcceptTimeoutSec'] as const;
type Key = typeof KEYS[number];

export async function loadSettings() {
  const rows = await query<{ key: string; value: any }>('SELECT key, value FROM settings');
  for (const r of rows) if ((KEYS as readonly string[]).includes(r.key)) (config.business as any)[r.key] = Number(r.value);
}

export async function getSettings() {
  return Object.fromEntries(KEYS.map((k) => [k, config.business[k]])) as Record<Key, number>;
}

export async function saveSettings(patch: Partial<Record<Key, number>>) {
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) continue;
    await query('INSERT INTO settings (key, value) VALUES ($1,$2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value', [k, JSON.stringify(v)]);
    (config.business as any)[k] = v;
  }
  return getSettings();
}
