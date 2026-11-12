import request from 'supertest';
import { createApp } from '../src/app.js';
import { pool, query } from '../src/db.js';
import { migrate } from '../src/migrate.js';
import { seed } from '../src/seed.js';
import { clearFeedCache } from '../src/services/feed.js';
import { clearQuoteCache } from '../src/services/orders.js';

export const app = createApp();
export const api = () => request(app);

/** Чистая схема + демо-данные перед каждым файлом тестов. */
export async function resetDb() {
  await pool.query('DROP SCHEMA public CASCADE; CREATE SCHEMA public;');
  await migrate();
  await seed();
  clearFeedCache();
  clearQuoteCache();
}

export async function loginUser(phone: string, role: 'buyer' | 'chef' = 'buyer') {
  const s = await api().post('/api/auth/send-otp').send({ phone });
  if (s.status !== 200) throw new Error(`send-otp ${s.status} ${JSON.stringify(s.body)}`);
  const v = await api().post('/api/auth/verify-otp').send({ phone, code: s.body.devCode, role });
  if (v.status !== 200) throw new Error(`verify-otp ${v.status} ${JSON.stringify(v.body)}`);
  return { token: v.body.accessToken as string, user: v.body.user, refresh: v.body.refreshToken as string };
}

export async function loginStaff(email: string, password: string) {
  const r = await api().post('/api/admin/auth/login').send({ email, password });
  if (r.status !== 200) throw new Error(`staff login ${r.status}`);
  return r.body.accessToken as string;
}

export const auth = (t: string) => ({ Authorization: `Bearer ${t}` });

export async function chefIdByNick(nick: string): Promise<number> {
  return (await query<{ user_id: number }>('SELECT user_id FROM chef_profiles WHERE nick = $1', [nick]))[0].user_id;
}

export async function dishOf(chefId: number, type?: string) {
  return (await query<any>(`SELECT * FROM dishes WHERE chef_id = $1 ${type ? 'AND type = $2' : ''} ORDER BY id LIMIT 1`, type ? [chefId, type] : [chefId]))[0];
}
