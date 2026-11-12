import type { NextFunction, Request, Response } from 'express';
import { forbidden, unauthorized } from '../lib/errors.js';
import { verifyAccess, type Principal } from './tokens.js';
import { can, type Permission } from './permissions.js';

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
  return (req: Request, _res: Response, next: NextFunction) => {
    const p = verifyAccess(bearer(req));
    if (p.kind !== 'staff') throw forbidden();
    if (perm && !can(p.role, perm)) throw forbidden();
    req.principal = p;
    next();
  };
}

export const uid = (req: Request) => req.principal!.id;
