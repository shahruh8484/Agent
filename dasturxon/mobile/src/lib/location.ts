import * as Location from 'expo-location';
import { Platform } from 'react-native';
import { geocoderEnabled, reverseGeocode } from '../api/geocoder';
import type { Point } from '../api/types';

/** Текущая геопозиция (с запросом разрешения). null — если недоступна. */
export async function currentPosition(): Promise<Point | null> {
  try {
    const perm = await Location.requestForegroundPermissionsAsync();
    if (perm.status !== 'granted') return null;
    const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
    return { lat: pos.coords.latitude, lon: pos.coords.longitude };
  } catch {
    return null;
  }
}

/** Адрес по координатам: Яндекс-геокодер, иначе системный (только iOS/Android) */
export async function addressFromPoint(p: Point): Promise<string | null> {
  try {
    if (geocoderEnabled) return await reverseGeocode(p);
    if (Platform.OS === 'web') return null;
    const r = await Location.reverseGeocodeAsync({ latitude: p.lat, longitude: p.lon });
    const a = r[0];
    if (!a) return null;
    return [a.city ?? 'Ташкент', a.district, a.street, a.streetNumber].filter(Boolean).join(', ');
  } catch {
    return null;
  }
}

/** Координаты по тексту адреса системным геокодером (без ключа Яндекса) */
export async function pointFromAddress(text: string): Promise<Point | null> {
  if (Platform.OS === 'web') return null;
  try {
    const r = await Location.geocodeAsync(text.includes('Ташкент') ? text : `Ташкент, ${text}`);
    return r[0] ? { lat: r[0].latitude, lon: r[0].longitude } : null;
  } catch {
    return null;
  }
}

/** Примерные центры районов Ташкента — запасной вариант координат */
export const DISTRICT_CENTERS: Record<string, Point> = {
  Алмазар: { lat: 41.348, lon: 69.205 },
  Бектемир: { lat: 41.209, lon: 69.334 },
  Мирабад: { lat: 41.292, lon: 69.283 },
  'Мирзо-Улугбек': { lat: 41.335, lon: 69.335 },
  Сергели: { lat: 41.226, lon: 69.22 },
  Учтепа: { lat: 41.3, lon: 69.18 },
  Чиланзар: { lat: 41.276, lon: 69.204 },
  Шайхантахур: { lat: 41.326, lon: 69.24 },
  Юнусабад: { lat: 41.365, lon: 69.285 },
  Яккасарай: { lat: 41.283, lon: 69.255 },
  Яшнабад: { lat: 41.3, lon: 69.315 },
  Янгихаёт: { lat: 41.2, lon: 69.21 },
};
