// Адрес сервера, выбранный пользователем на экране входа (тестовые сборки).
import { ALLOW_SERVER_CHANGE, DEFAULT_API_URL, normalizeServerUrl, setApiUrl } from '../config';
import { storage } from './storage';

const KEY = 'dx_api_url';

export async function loadServerUrl(): Promise<void> {
  if (!ALLOW_SERVER_CHANGE) return;
  const saved = await storage.get(KEY);
  if (saved) setApiUrl(saved);
}

/** Проверяет, что по адресу отвечает API Dasturxon, и сохраняет его. */
export async function saveServerUrl(input: string): Promise<string> {
  const url = normalizeServerUrl(input);
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    const r = await fetch(`${url}/health`, { signal: ctrl.signal });
    const j = await r.json().catch(() => null);
    if (!r.ok || !j?.ok) throw new Error('bad');
  } catch {
    throw new Error(`Сервер ${url} не отвечает. Проверьте адрес и что телефон в той же сети.`);
  } finally {
    clearTimeout(t);
  }
  setApiUrl(url);
  if (url === DEFAULT_API_URL) await storage.remove(KEY);
  else await storage.set(KEY, url);
  return url;
}
