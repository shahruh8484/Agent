// Нативная карта: WebView с Yandex Maps JS API 2.1
import React, { useEffect, useMemo, useRef } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import { WebView as RNWebView } from 'react-native-webview';
import { YANDEX_MAPS_KEY } from '../../config';
import { MapFallback } from './MapFallback';
import { buildMapHtml, type MapData } from './mapHtml';

export type { MapData, MapMarker } from './mapHtml';

// Типы react-native-webview 14 конфликтуют с типами RN 0.88 (props схлопываются в never),
// поэтому описываем используемое подмножество явно.
interface WebViewHandle {
  injectJavaScript: (js: string) => void;
}
type WebViewMessageEvent = { nativeEvent: { data: string } };
const WebView = RNWebView as unknown as React.ComponentType<{
  ref?: React.Ref<WebViewHandle>;
  originWhitelist?: string[];
  source: { html: string; baseUrl?: string };
  onMessage?: (e: WebViewMessageEvent) => void;
  javaScriptEnabled?: boolean;
  domStorageEnabled?: boolean;
  style?: StyleProp<ViewStyle>;
}>;

export function YandexMap({ data, onMarkerPress, style }: { data: MapData; onMarkerPress?: (id: string | number) => void; style?: StyleProp<ViewStyle> }) {
  const ref = useRef<WebViewHandle>(null);
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
