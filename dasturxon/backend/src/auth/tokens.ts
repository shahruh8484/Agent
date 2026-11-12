import { createHash, randomBytes } from 'node:crypto';
import jwt from 'jsonwebtoken';
import { config } from '../config.js';
import { one, query } from '../db.js';
import { unauthorized } from '../lib/errors.js';

export type Principal =
  | { kind: 'user'; id: number; role: 'buyer' | 'chef' }
  | { kind: 'staff'; id: number; role: 'owner' | 'accountant' | 'support' };

const sha = (s: string) => createHash('sha256').update(s).digest('hex');

export function signAccess(p: Principal): string {
  return jwt.sign({ kind: p.kind, role: p.role }, config.jwtSecret, { subject: String(p.id), expiresIn: config.accessTtlSec });
}

export function verifyAccess(token: string): Principal {
  try {
    const d = jwt.verify(token, config.jwtSecret) as jwt.JwtPayload;
    return { kind: d.kind, id: Number(d.sub), role: d.role } as Principal;
  } catch {
    throw unauthorized('Токен недействителен или истёк');
  }
}

export async function issueTokens(p: Principal) {
  const refresh = randomBytes(32).toString('base64url');
  await query('INSERT INTO refresh_tokens (subject, token_hash, expires_at) VALUES ($1,$2, now() + make_interval(secs => $3))',
    [`${p.kind}:${p.id}`, sha(refresh), config.refreshTtlSec]);
  return { accessToken: signAccess(p), refreshToken: refresh, expiresIn: config.accessTtlSec };
}

/** Ротация refresh-токена: старый отзывается, выдаётся новая пара. */
export async function rotateRefresh(refresh: string) {
  const row = await one<{ id: number; subject: string }>(
    `UPDATE refresh_tokens SET revoked = TRUE
      WHERE token_hash = $1 AND NOT revoked AND expires_at > now() RETURNING id, subject`, [sha(refresh)]);
  if (!row) throw unauthorized('Refresh-токен недействителен');
  const [kind, idStr] = row.subject.split(':');
  const id = Number(idStr);
  let p: Principal;
  if (kind === 'user') {
    const u = await one<{ role: 'buyer' | 'chef'; is_active: boolean }>('SELECT role, is_active FROM users WHERE id = $1', [id]);
    if (!u || !u.is_active) throw unauthorized('Аккаунт отключён');
    p = { kind: 'user', id, role: u.role };
  } else {
    const s = await one<{ role: any; is_active: boolean }>('SELECT role, is_active FROM admin_staff WHERE id = $1', [id]);
    if (!s || !s.is_active) throw unauthorized('Аккаунт отключён');
    p = { kind: 'staff', id, role: s.role };
  }
  return issueTokens(p);
}

export async function revokeRefresh(refresh: string) {
  await query('UPDATE refresh_tokens SET revoked = TRUE WHERE token_hash = $1', [sha(refresh)]);
}

export const hashOtp = (phone: string, code: string) => sha(`${phone}:${code}:${config.jwtSecret}`);
