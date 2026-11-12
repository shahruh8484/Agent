// Yandex Geocoder HTTP API (если задан EXPO_PUBLIC_YANDEX_GEOCODER_KEY)
import { TASHKENT, YANDEX_GEOCODER_KEY } from '../config';
import type { Point } from './types';

export interface GeoResult extends Point {
  title: string;
  subtitle: string;
  text: string;
}

export const geocoderEnabled = !!YANDEX_GEOCODER_KEY;

async function geocode(geocodeParam: string, results = 7): Promise<GeoResult[]> {
  if (!YANDEX_GEOCODER_KEY) return [];
  const url =
    `https://geocode-maps.yandex.ru/1.x/?apikey=${encodeURIComponent(YANDEX_GEOCODER_KEY)}` +
    `&format=json&lang=ru_RU&results=${results}&ll=${TASHKENT.lon},${TASHKENT.lat}&spn=0.6,0.6&rspn=1` +
    `&geocode=${encodeURIComponent(geocodeParam)}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error('Геокодер недоступен');
  const j: any = await r.json();
  const members: any[] = j?.response?.GeoObjectCollection?.featureMember ?? [];
  return members.map((m) => {
    const g = m.GeoObject;
    const [lon, lat] = String(g.Point.pos).split(' ').map(Number);
    const text: string = g.metaDataProperty?.GeocoderMetaData?.text ?? `${g.description}, ${g.name}`;
    return { lat, lon, title: g.name, subtitle: g.description ?? '', text };
  });
}

export function searchAddress(q: string) {
  return geocode(q.includes('Ташкент') ? q : `Ташкент, ${q}`);
}

export async function reverseGeocode(p: Point): Promise<string | null> {
  const r = await geocode(`${p.lon},${p.lat}`, 1);
  return r[0]?.text ?? null;
}
