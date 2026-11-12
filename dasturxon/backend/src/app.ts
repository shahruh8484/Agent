import cors from 'cors';
import express, { type NextFunction, type Request, type Response } from 'express';
import { config } from './config.js';
import { HttpError } from './lib/errors.js';
import { adminRouter } from './routes/admin.js';
import { authRouter } from './routes/auth.js';
import { buyerRouter } from './routes/buyer.js';
import { chefRouter } from './routes/chef.js';
import { ordersRouter } from './routes/orders.js';
import { publicRouter } from './routes/public.js';
import { UPLOAD_DIR, uploadsRouter } from './routes/uploads.js';

export function createApp() {
  const app = express();
  app.set('trust proxy', true);
  app.use(cors({ origin: config.corsOrigin === '*' ? true : config.corsOrigin.split(',') }));
  app.use(express.json({ limit: '1mb' }));

  app.get('/health', (_req, res) => { res.json({ ok: true }); });
  app.use('/uploads', express.static(UPLOAD_DIR, { maxAge: '30d', immutable: true }));

  const api = express.Router();
  for (const r of [authRouter, publicRouter, buyerRouter, ordersRouter, chefRouter, adminRouter, uploadsRouter]) api.use(r);
  app.use('/api', api);

  app.use((_req, _res, next) => next(new HttpError(404, 'not_found', 'Маршрут не найден')));
  app.use((err: any, _req: Request, res: Response, _next: NextFunction) => {
    if (err instanceof HttpError) return res.status(err.status).json({ error: err.code, message: err.message, details: err.details });
    if (err?.type === 'entity.parse.failed') return res.status(400).json({ error: 'bad_request', message: 'Некорректный JSON' });
    if (err?.type === 'entity.too.large') return res.status(413).json({ error: 'too_large', message: 'Файл слишком большой' });
    console.error(err);
    res.status(500).json({ error: 'internal', message: 'Внутренняя ошибка сервера' });
  });
  return app;
}
