import type { NextFunction, Request, Response } from 'express';
import { forbidden, unauthorized } from '../lib/errors.js';
import { verifyAccess, type Principal } from './tokens.js';
import { can, type Permission, type StaffRole } from './permissions.js';
import { one } from '../db.js';

declare global {
  namespace Express {
    interface Request { principal?: Principal }
  }
}

function bearer(req: Request): string {
  const h = req.headers.authorization;
  if (!h?.startsWith('Bearer ')) throw unauthorized();
  return h.slice(7);
}

export function requireUser(role?: 'buyer' | 'chef') {
  return (req: Request, _res: Response, next: NextFunction) => {
    const p = verifyAccess(bearer(req));
    if (p.kind !== 'user') throw forbidden();
    if (role && p.role !== role) throw forbidden(role === 'chef' ? 'Только для поваров' : 'Только для покупателей');
    req.principal = p;
    next();
  };
}

export function requireStaff(perm?: Permission) {
  return async (req: Request, _res: Response, next: NextFunction) => {
    const p = verifyAccess(bearer(req));
    if (p.kind !== 'staff') throw forbidden();
    // Отключённый сотрудник теряет доступ сразу, а роль берётся актуальная, а не из токена
    const s = await one<{ role: StaffRole; is_active: boolean }>('SELECT role, is_active FROM admin_staff WHERE id = $1', [p.id]);
    if (!s || !s.is_active) throw unauthorized('Аккаунт отключён');
    const principal = { ...p, role: s.role } as Principal;
    if (perm && !can(s.role, perm)) throw forbidden();
    req.principal = principal;
    next();
  };
}

export const uid = (req: Request) => req.principal!.id;
