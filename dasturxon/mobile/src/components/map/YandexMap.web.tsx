// Веб-версия карты: тот же HTML в <iframe> (react-native-webview в вебе не работает)
import React, { useEffect, useMemo, useRef } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import { YANDEX_MAPS_KEY } from '../../config';
import { MapFallback } from './MapFallback';
import { buildMapHtml, type MapData } from './mapHtml';

export type { MapData, MapMarker } from './mapHtml';

export function YandexMap({ data, onMarkerPress, style }: { data: MapData; onMarkerPress?: (id: string | number) => void; style?: StyleProp<ViewStyle> }) {
  const ref = useRef<HTMLIFrameElement | null>(null);
  const html = useMemo(() => (YANDEX_MAPS_KEY ? buildMapHtml(YANDEX_MAPS_KEY, data) : ''), []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    ref.current?.contentWindow?.postMessage(JSON.stringify(data), '*');
  }, [data]);

  useEffect(() => {
    if (!YANDEX_MAPS_KEY) return;
    const handler = (e: MessageEvent) => {
      if (e.source !== ref.current?.contentWindow) return;
      try {
        const msg = JSON.parse(String(e.data));
        if (msg.type === 'marker') onMarkerPress?.(msg.id);
      } catch {
        /* ignore */
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [onMarkerPress]);

  if (!YANDEX_MAPS_KEY) return <MapFallback data={data} onMarkerPress={onMarkerPress} style={style} />;

  return (
    <View style={[{ overflow: 'hidden' }, style]}>
      {React.createElement('iframe', { ref, srcDoc: html, style: { border: 0, width: '100%', height: '100%' }, title: 'map' })}
    </View>
  );
}
