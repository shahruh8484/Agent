import { randomUUID } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import express, { Router } from 'express';
import { requireUser } from '../auth/middleware.js';
import { badRequest } from '../lib/errors.js';

// Загрузка фото. Сейчас — на локальный диск (UPLOAD_DIR); для продакшена заменить на S3/R2 (ТЗ 2).
export const UPLOAD_DIR = process.env.UPLOAD_DIR ?? join(process.cwd(), 'uploads');
mkdirSync(UPLOAD_DIR, { recursive: true });

const ext: Record<string, string> = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/heic': 'heic' };

export const uploadsRouter = Router();
uploadsRouter.post('/uploads', requireUser(), express.raw({ type: 'image/*', limit: '10mb' }), (req, res) => {
  const type = req.headers['content-type'] ?? '';
  if (!ext[type] || !Buffer.isBuffer(req.body) || req.body.length === 0) throw badRequest('Отправьте изображение JPEG/PNG/WebP/HEIC в теле запроса');
  const name = `${randomUUID()}.${ext[type]}`;
  writeFileSync(join(UPLOAD_DIR, name), req.body);
  const base = process.env.PUBLIC_URL ?? `${req.protocol}://${req.get('host')}`;
  res.status(201).json({ url: `${base}/uploads/${name}` });
});
