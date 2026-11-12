/** Расстояние по прямой в км (ТЗ 3.2: «расстояние считается по прямой»). */
export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** SQL-выражение haversine (км) для сортировки ленты в БД. */
export const sqlDistanceKm = (latCol: string, lonCol: string, latParam: string, lonParam: string) =>
  `(6371 * 2 * asin(sqrt(power(sin(radians(${latCol} - ${latParam}) / 2), 2) + cos(radians(${latParam})) * cos(radians(${latCol})) * power(sin(radians(${lonCol} - ${lonParam}) / 2), 2))))`;
