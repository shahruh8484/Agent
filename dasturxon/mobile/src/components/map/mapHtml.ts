import type { Point } from '../../api/types';
import { colors } from '../../theme';

export interface MapMarker extends Point {
  id: string | number;
  kind: 'chef' | 'buyer' | 'courier' | 'chefOffline';
  label?: string;
  subtitle?: string;
}

export interface MapData {
  center: Point;
  zoom?: number;
  markers: MapMarker[];
  /** линия маршрута (например, курьер → покупатель) */
  route?: Point[];
}

export const MARKER_COLORS: Record<MapMarker['kind'], string> = {
  chef: colors.primary,
  chefOffline: '#9CA3AF',
  buyer: colors.blue,
  courier: colors.yellow,
};

/**
 * HTML-страница с Yandex Maps JS API 2.1. Данные обновляются вызовом window.setData(json),
 * нажатие на метку отправляет { type: 'marker', id } в React Native (или в родительское окно в вебе).
 */
export function buildMapHtml(apiKey: string, data: MapData): string {
  return `<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" />
<style>html,body,#map{margin:0;padding:0;width:100%;height:100%;}</style>
<script src="https://api-maps.yandex.ru/2.1/?apikey=${encodeURIComponent(apiKey)}&lang=ru_RU"></script>
</head><body><div id="map"></div>
<script>
var COLORS = ${JSON.stringify(MARKER_COLORS)};
var map, layer, routeLine, fitted = false;
function send(msg){
  var s = JSON.stringify(msg);
  if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(s);
  else if (window.parent) window.parent.postMessage(s, '*');
}
function setData(d){
  if (!map) { window.__pending = d; return; }
  layer.removeAll();
  (d.markers || []).forEach(function(m){
    var pm = new ymaps.Placemark([m.lat, m.lon], { iconCaption: m.label || '', hintContent: m.subtitle || '' },
      { preset: m.kind === 'courier' ? 'islands#circleDotIcon' : 'islands#circleIcon', iconColor: COLORS[m.kind] || '#1FA37A' });
    pm.events.add('click', function(){ send({ type: 'marker', id: m.id }); });
    layer.add(pm);
  });
  if (routeLine) { map.geoObjects.remove(routeLine); routeLine = null; }
  if (d.route && d.route.length > 1) {
    routeLine = new ymaps.Polyline(d.route.map(function(p){ return [p.lat, p.lon]; }), {},
      { strokeColor: '#1FA37A', strokeWidth: 4, strokeOpacity: 0.8 });
    map.geoObjects.add(routeLine);
  }
  if (!fitted && (d.markers || []).length > 1) {
    var b = layer.getBounds();
    if (b) map.setBounds(b, { checkZoomRange: true, zoomMargin: 40 });
    fitted = true;
  }
}
window.setData = setData;
window.addEventListener('message', function(e){ try { var d = JSON.parse(e.data); if (d && d.markers) setData(d); } catch(_){} });
ymaps.ready(function(){
  var init = ${JSON.stringify(data)};
  map = new ymaps.Map('map', { center: [init.center.lat, init.center.lon], zoom: init.zoom || 12, controls: ['zoomControl', 'geolocationControl'] });
  layer = new ymaps.GeoObjectCollection();
  map.geoObjects.add(layer);
  setData(window.__pending || init);
  send({ type: 'ready' });
});
</script></body></html>`;
}

/** Расстояние между точками, км */
export function distanceKm(a: Point, b: Point): number {
  const R = 6371;
  const dLat = ((b.lat - a.lat) * Math.PI) / 180;
  const dLon = ((b.lon - a.lon) * Math.PI) / 180;
  const x = Math.sin(dLat / 2) ** 2 + Math.cos((a.lat * Math.PI) / 180) * Math.cos((b.lat * Math.PI) / 180) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
