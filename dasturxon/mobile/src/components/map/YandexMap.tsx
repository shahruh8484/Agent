// Нативная карта: WebView с Yandex Maps JS API 2.1
import React, { useEffect, useMemo, useRef } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import { WebView, type WebViewMessageEvent } from 'react-native-webview';
import { YANDEX_MAPS_KEY } from '../../config';
import { MapFallback } from './MapFallback';
import { buildMapHtml, type MapData } from './mapHtml';

export type { MapData, MapMarker } from './mapHtml';

export function YandexMap({ data, onMarkerPress, style }: { data: MapData; onMarkerPress?: (id: string | number) => void; style?: StyleProp<ViewStyle> }) {
  const ref = useRef<WebView>(null);
  const ready = useRef(false);
  // HTML строится один раз — дальнейшие обновления идут через setData, без перезагрузки карты
  const html = useMemo(() => (YANDEX_MAPS_KEY ? buildMapHtml(YANDEX_MAPS_KEY, data) : ''), []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (ready.current) ref.current?.injectJavaScript(`window.setData(${JSON.stringify(data)}); true;`);
  }, [data]);

  if (!YANDEX_MAPS_KEY) return <MapFallback data={data} onMarkerPress={onMarkerPress} style={style} />;

  const onMessage = (e: WebViewMessageEvent) => {
    try {
      const msg = JSON.parse(e.nativeEvent.data);
      if (msg.type === 'ready') {
        ready.current = true;
        ref.current?.injectJavaScript(`window.setData(${JSON.stringify(data)}); true;`);
      }
      if (msg.type === 'marker') onMarkerPress?.(msg.id);
    } catch {
      /* ignore */
    }
  };

  return (
    <View style={[{ overflow: 'hidden' }, style]}>
      <WebView
        ref={ref}
        originWhitelist={['*']}
        source={{ html, baseUrl: 'https://localhost' }}
        onMessage={onMessage}
        javaScriptEnabled
        domStorageEnabled
        style={{ flex: 1 }}
      />
    </View>
  );
}
