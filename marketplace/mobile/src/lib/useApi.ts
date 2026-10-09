import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';

import { api } from './api';
import { useT } from './i18n';

/** Loads `path` every time the screen gains focus or the language changes; `reload` refetches on demand. */
export function useApi<T>(path: string | null) {
  const { lang } = useT();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api<T>(path));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [path, lang]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const reload = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  return { data, error, refreshing, reload, setData };
}
