import { API_BASE } from '../config';
import type { Tokens } from './types';

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

/** Связка с хранилищем токенов (store/auth регистрирует обработчики при старте) */
interface TokenHandlers {
  getAccess: () => string | null;
  getRefresh: () => string | null;
  onRefreshed: (t: Tokens) => void | Promise<void>;
  onAuthFailed: () => void;
}

let handlers: TokenHandlers = {
  getAccess: () => null,
  getRefresh: () => null,
  onRefreshed: () => undefined,
  onAuthFailed: () => undefined,
};

export function setTokenHandlers(h: TokenHandlers) {
  handlers = h;
}

let refreshing: Promise<boolean> | null = null;

/** Обновление пары токенов; одновременные 401 ждут один и тот же запрос */
export function refreshTokens(): Promise<boolean> {
  if (refreshing) return refreshing;
  const rt = handlers.getRefresh();
  if (!rt) return Promise.resolve(false);
  refreshing = (async () => {
    try {
      const r = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refreshToken: rt }),
      });
      if (!r.ok) return false;
      const t = (await r.json()) as Tokens;
      await handlers.onRefreshed(t);
      return true;
    } catch {
      return false;
    } finally {
      setTimeout(() => {
        refreshing = null;
      }, 0);
    }
  })();
  return refreshing;
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE';
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  auth?: boolean;
  /** сырое тело (загрузка фото) */
  raw?: { data: Blob | ArrayBuffer; contentType: string };
}

function buildUrl(path: string, query?: RequestOptions['query']) {
  const qs = query
    ? Object.entries(query)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
        .join('&')
    : '';
  return `${API_BASE}${path}${qs ? `?${qs}` : ''}`;
}

export async function request<T>(path: string, opts: RequestOptions = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const auth = opts.auth !== false;
  const token = auth ? handlers.getAccess() : null;
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.raw) {
    headers['Content-Type'] = opts.raw.contentType;
    body = opts.raw.data as BodyInit;
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }

  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), { method: opts.method ?? 'GET', headers, body });
  } catch {
    throw new ApiError(0, 'network', 'Нет соединения с сервером. Проверьте интернет');
  }

  if (res.status === 401 && auth && token && !retried) {
    const ok = await refreshTokens();
    if (ok) return request<T>(path, opts, true);
    handlers.onAuthFailed();
  }

  const text = await res.text();
  let data: any = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const message =
      (data && typeof data === 'object' && data.message) ||
      (res.status === 402 ? 'Недостаточно средств' : `Ошибка сервера (${res.status})`);
    throw new ApiError(res.status, data?.error ?? 'error', message, data?.details);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query'], auth = true) => request<T>(path, { query, auth }),
  post: <T>(path: string, body?: unknown, auth = true) => request<T>(path, { method: 'POST', body: body ?? {}, auth }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body: body ?? {} }),
  del: <T>(path: string, body?: unknown) => request<T>(path, { method: 'DELETE', body }),
};

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return 'Что-то пошло не так';
}
