import { useEffect, useState } from 'react';

import { api } from './api';
import { useT } from './i18n';

export interface City {
  id: string;
  name: string;
}

const cache: Partial<Record<string, City[]>> = {};

/** Cities are stored by id; this resolves them to names in the current language. */
export function useCities() {
  const { lang } = useT();
  const [cities, setCities] = useState<City[]>(cache[lang] ?? []);

  useEffect(() => {
    if (cache[lang]) {
      setCities(cache[lang]!);
      return;
    }
    api<City[]>('/cities')
      .then((list) => {
        cache[lang] = list;
        setCities(list);
      })
      .catch(() => undefined);
  }, [lang]);

  const cityName = (id: string) => cities.find((c) => c.id === id)?.name ?? id;
  return { cities, cityName };
}
