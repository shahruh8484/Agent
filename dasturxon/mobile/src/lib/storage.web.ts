// Веб-версия (только для разработки/смоук-тестов): Keychain нет, используем localStorage
const ls = (): Storage | null => {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
};

export const storage = {
  get: async (key: string) => ls()?.getItem(key) ?? null,
  set: async (key: string, value: string) => {
    ls()?.setItem(key, value);
  },
  remove: async (key: string) => {
    ls()?.removeItem(key);
  },
};
