import Constants from 'expo-constants';

const API_URL: string =
  process.env.EXPO_PUBLIC_API_URL ??
  (Constants.expoConfig?.extra?.apiUrl as string | undefined) ??
  'http://localhost:8000';

let authToken: string | null = null;
let apiLang: 'uz' | 'ru' = 'uz';

export function setToken(token: string | null) {
  authToken = token;
}

/** The server answers (catalog, cities, errors) in this language. */
export function setApiLang(lang: 'uz' | 'ru') {
  apiLang = lang;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, options: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'Accept-Language': apiLang };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
  let res: Response;
  try {
    res = await fetch(API_URL + path, {
      method: options.method ?? (options.body !== undefined ? 'POST' : 'GET'),
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    });
  } catch {
    throw new ApiError(0, apiLang === 'uz' ? "Server bilan aloqa yo'q" : 'Нет связи с сервером');
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = data?.detail;
    const fallback = apiLang === 'uz' ? "Kiritilgan ma'lumotlarni tekshiring" : 'Проверьте введённые данные';
    throw new ApiError(res.status, typeof detail === 'string' ? detail : fallback);
  }
  return data as T;
}

/** "+998901234567" → "+998 90 123 45 67" */
export function formatPhone(phone: string): string {
  const d = phone.replace(/\D/g, '');
  if (d.length !== 12) return phone;
  return `+${d.slice(0, 3)} ${d.slice(3, 5)} ${d.slice(5, 8)} ${d.slice(8, 10)} ${d.slice(10)}`;
}
