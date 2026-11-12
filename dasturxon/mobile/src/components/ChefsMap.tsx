import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import { errorMessage } from '../api/client';
import { publicApi } from '../api/endpoints';
import type { MapChef, Point } from '../api/types';
import { YandexMap, type MapData } from './map/YandexMap';
import { Empty, Loader } from './ui';

/** Карта поваров рядом (зелёные — «готово сейчас», серые — на заказ) */
export function ChefsMap({ center, onChef, style }: { center: Point; onChef: (id: number) => void; style?: StyleProp<ViewStyle> }) {
  const [chefs, setChefs] = useState<MapChef[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    publicApi
      .map(center.lat, center.lon)
      .then((r) => alive && setChefs(r))
      .catch((e) => alive && setError(errorMessage(e)));
    return () => {
      alive = false;
    };
  }, [center.lat, center.lon]);

  const data: MapData = useMemo(
    () => ({
      center,
      zoom: 12,
      markers: [
        { id: 'me', kind: 'buyer', label: 'Вы', ...center },
        ...(chefs ?? []).map((c) => ({
          id: c.id,
          kind: c.readyNow ? ('chef' as const) : ('chefOffline' as const),
          label: c.nick,
          subtitle: `${c.district} · ★ ${Number(c.rating).toFixed(1)}${c.readyNow ? ' · Готово сейчас' : ' · На заказ'}`,
          ...c.point,
        })),
      ],
    }),
    [chefs, center],
  );

  const onMarker = useCallback((id: string | number) => typeof id === 'number' && onChef(id), [onChef]);

  if (error) return <Empty icon="cloud-offline-outline" title="Не удалось загрузить карту" text={error} />;
  if (!chefs) return <Loader />;
  return (
    <View style={[{ flex: 1 }, style]}>
      <YandexMap data={data} onMarkerPress={onMarker} style={{ flex: 1 }} />
    </View>
  );
}
