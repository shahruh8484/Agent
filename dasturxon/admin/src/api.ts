// HTTP-клиент админ-панели: Bearer-токен, однократная попытка refresh при 401, выход при неудаче.
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Staff } from './types';

export const API_BASE = (import.meta.env.VITE_API_URL || 'http://localhost:4000').replace(/\/+$/, '');

const K_ACCESS = 'dx_admin_access';
const K_REFRESH = 'dx_admin_refresh';
const K_STAFF = 'dx_admin_staff';

function ls(): Storage | null {
  try { return window.localStorage; } catch { return null; }
}

export const tokens = {
  get access() { return ls()?.getItem(K_ACCESS) ?? null; },
  get refresh() { return ls()?.getItem(K_REFRESH) ?? null; },
  set(access: string, refresh: string) {
    ls()?.setItem(K_ACCESS, access);
    ls()?.setItem(K_REFRESH, refresh);
    window.dispatchEvent(new Event('dx:tokens'));
  },
  clear() {
    ls()?.removeItem(K_ACCESS);
    ls()?.removeItem(K_REFRESH);
    ls()?.removeItem(K_STAFF);
  },
};

export const staffStore = {
  get(): Staff | null {
    try { return JSON.parse(ls()?.getItem(K_STAFF) ?? 'null'); } catch { return null; }
  },
  set(s: Staff) { ls()?.setItem(K_STAFF, JSON.stringify(s)); },
};

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) {
    super(message);
  }
}

/** Принудительный выход: чистим токены и сообщаем AuthProvider. */
export function forceLogout() {
  tokens.clear();
  window.dispatchEvent(new Event('dx:logout'));
}

let refreshing: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const rt = tokens.refresh;
  if (!rt) return false;
  try {
    const res = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ refreshToken: rt }),
    });
    if (!res.ok) return false;
    const j = await res.json();
    if (!j.accessToken || !j.refreshToken) return false;
    tokens.set(j.accessToken, j.refreshToken);
    return true;
  } catch {
    return false;
  }
}

/** Один refresh на все параллельные запросы, получившие 401. */
export function refreshOnce(): Promise<boolean> {
  refreshing ??= doRefresh().finally(() => { setTimeout(() => { refreshing = null; }, 0); });
  return refreshing;
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function buildUrl(path: string, q?: Query) {
  const url = new URL(`${API_BASE}/api${path}`);
  if (q) for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
  return url.toString();
}

async function send(path: string, init: RequestInit & { query?: Query }, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  const at = tokens.access;
  if (at) headers.set('Authorization', `Bearer ${at}`);
  let res: Response;
  try {
    res = await fetch(buildUrl(path, init.query), { ...init, headers });
  } catch {
    throw new ApiError(0, 'network', 'Нет связи с сервером');
  }
  if (res.status === 401 && path !== '/admin/auth/login') {
    if (retry && tokens.refresh && (await refreshOnce())) return send(path, init, false);
    forceLogout();
    throw new ApiError(401, 'unauthorized', 'Сессия истекла, войдите снова');
  }
  return res;
}

async function toError(res: Response): Promise<ApiError> {
  let j: any = null;
  try { j = await res.json(); } catch { /* not json */ }
  const msg = j?.message || (res.status === 403 ? 'Недостаточно прав' : `Ошибка сервера (${res.status})`);
  return new ApiError(res.status, j?.error ?? 'error', msg, j?.details);
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown; query?: Query } = {}): Promise<T> {
  const res = await send(path, {
    method: opts.method ?? 'GET',
    query: opts.query,
    headers: opts.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) throw await toError(res);
  if (res.status === 204) return undefined as T;
  return res.json();
}

/** Скачивание файла с авторизацией: fetch → blob → <a download>. */
export async function download(path: string, query: Query, fallbackName: string) {
  const res = await send(path, { method: 'GET', query });
  if (!res.ok) throw await toError(res);
  const blob = await res.blob();
  const cd = res.headers.get('Content-Disposition') ?? '';
  const name = /filename="?([^";]+)"?/.exec(cd)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** Загрузка данных с перезагрузкой; `path = null` — не грузить. */
export function useApi<T = any>(path: string | null, query?: Query) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const [error, setError] = useState<ApiError | null>(null);
  const seq = useRef(0);
  const qKey = JSON.stringify(query ?? {});

  const reload = useCallback(async (silent = false) => {
    if (!path) return;
    const my = ++seq.current;
    if (!silent) setLoading(true);
    try {
      const d = await api<T>(path, { query: JSON.parse(qKey) });
      if (my === seq.current) { setData(d); setError(null); }
    } catch (e) {
      if (my === seq.current) setError(e as ApiError);
    } finally {
      if (my === seq.current) setLoading(false);
    }
  }, [path, qKey]);

  useEffect(() => { void reload(); }, [reload]);
  return { data, loading, error, reload, setData };
}
