import { randomInt } from 'node:crypto';
import bcrypt from 'bcryptjs';
import { Router } from 'express';
import { z } from 'zod';
import { issueTokens, hashOtp, revokeRefresh, rotateRefresh } from '../auth/tokens.js';
import { requireUser, uid } from '../auth/middleware.js';
import { permissionsOf } from '../auth/permissions.js';
import { config } from '../config.js';
import { one, query } from '../db.js';
import { sendSms } from '../integrations/sms.js';
import { HttpError, conflict, unauthorized } from '../lib/errors.js';
import { parse, phoneSchema } from '../lib/validate.js';

export const authRouter = Router();

// ТЗ 3.1 / 13: SMS-код, не более 5 запросов с одного номера за 10 минут
authRouter.post('/auth/send-otp', async (req, res) => {
  const { phone } = parse(z.object({ phone: phoneSchema }), req.body);
  const recent = await one<{ n: number }>(`SELECT count(*)::int AS n FROM otp_codes WHERE phone = $1 AND created_at > now() - interval '10 minutes'`, [phone]);
  if ((recent?.n ?? 0) >= 5) throw new HttpError(429, 'too_many_requests', 'Слишком много запросов. Попробуйте через 10 минут');
  const code = String(randomInt(0, 10000)).padStart(4, '0');
  await query(`INSERT INTO otp_codes (phone, code_hash, expires_at) VALUES ($1,$2, now() + interval '5 minutes')`, [phone, hashOtp(phone, code)]);
  await sendSms(phone, `Ваш код Dasturxon: ${code}. Никому не сообщайте.`);
  res.json({ sent: true, resendAfterSec: 60, ...(config.devOtp ? { devCode: code } : {}) });
});

authRouter.post('/auth/verify-otp', async (req, res) => {
  const body = parse(z.object({ phone: phoneSchema, code: z.string().regex(/^\d{4}$/), role: z.enum(['buyer', 'chef']).optional() }), req.body);
  const otp = await one<any>(`SELECT * FROM otp_codes WHERE phone = $1 AND NOT used AND expires_at > now() ORDER BY created_at DESC LIMIT 1`, [body.phone]);
  if (!otp) throw unauthorized('Код истёк, запросите новый');
  if (otp.attempts >= 5) throw unauthorized('Слишком много попыток, запросите новый код');
  if (otp.code_hash !== hashOtp(body.phone, body.code)) {
    await query('UPDATE otp_codes SET attempts = attempts + 1 WHERE id = $1', [otp.id]);
    throw unauthorized('Неверный код');
  }
  await query('UPDATE otp_codes SET used = TRUE WHERE id = $1', [otp.id]);

  let user = await one<any>('SELECT * FROM users WHERE phone = $1 AND deleted_at IS NULL', [body.phone]);
  let isNew = false;
  if (!user) {
    user = await one<any>('INSERT INTO users (phone, role) VALUES ($1,$2) RETURNING *', [body.phone, body.role ?? 'buyer']);
    isNew = true;
  } else if (body.role && body.role !== user.role) {
    throw conflict(user.role === 'chef' ? 'Этот номер зарегистрирован как повар' : 'Этот номер зарегистрирован как покупатель');
  }
  if (!user.is_active) throw new HttpError(403, 'blocked', 'Аккаунт заблокирован. Обратитесь в поддержку');
  const tokens = await issueTokens({ kind: 'user', id: user.id, role: user.role });
  res.json({ ...tokens, isNew, user: { id: user.id, phone: user.phone, role: user.role, name: user.name } });
});

authRouter.post('/auth/refresh', async (req, res) => {
  const { refreshToken } = parse(z.object({ refreshToken: z.string().min(10) }), req.body);
  res.json(await rotateRefresh(refreshToken));
});

authRouter.delete('/auth/logout', async (req, res) => {
  const { refreshToken } = parse(z.object({ refreshToken: z.string() }), req.body ?? {});
  await revokeRefresh(refreshToken);
  res.json({ ok: true });
});

authRouter.post('/auth/device', requireUser(), async (req, res) => {
  const { token, platform } = parse(z.object({ token: z.string().min(10), platform: z.enum(['ios', 'android']).optional() }), req.body);
  await query('INSERT INTO device_tokens (user_id, token, platform) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING', [uid(req), token, platform ?? null]);
  res.json({ ok: true });
});

// Вход сотрудников админ-панели (email + пароль, bcrypt)
authRouter.post('/admin/auth/login', async (req, res) => {
  const { email, password } = parse(z.object({ email: z.string().email(), password: z.string().min(1) }), req.body);
  const s = await one<any>('SELECT * FROM admin_staff WHERE lower(email) = lower($1)', [email]);
  if (!s || !s.is_active || !(await bcrypt.compare(password, s.password_hash))) throw unauthorized('Неверный email или пароль');
  const tokens = await issueTokens({ kind: 'staff', id: s.id, role: s.role });
  res.json({ ...tokens, staff: { id: s.id, name: s.name, email: s.email, role: s.role, permissions: permissionsOf(s.role) } });
});
