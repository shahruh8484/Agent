import { z } from 'zod';
import { badRequest } from './errors.js';

export function parse<T extends z.ZodType>(schema: T, data: unknown): z.infer<T> {
  const r = schema.safeParse(data);
  if (!r.success) throw badRequest('Некорректные данные', r.error.issues.map((i) => ({ path: i.path.join('.'), message: i.message })));
  return r.data;
}

export const phoneSchema = z.string().regex(/^\+998\d{9}$/, 'Номер в формате +998XXXXXXXXX');
export const idParam = z.coerce.number().int().positive();
