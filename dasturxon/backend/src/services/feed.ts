import { query } from '../db.js';
import { sqlDistanceKm } from '../lib/geo.js';
import { quoteDelivery } from './orders.js';

export type FeedFilter = 'all' | 'ready' | 'order' | 'breakfast' | 'lunch' | 'dinner' | 'bakery' | 'drinks' | 'vegan';

const PAGE = 20;
const cache = new Map<string, { at: number; value: any }>();

/**
 * Лента блюд (ТЗ 3.2). Сортировка:
 * 1) «готово сейчас» (повар онлайн, тип ready/both), 2) расстояние, 3) рейтинг.
 * Повара офлайн, на проверке или заблокированные в ленте не показываются.
 */
export async function getFeed(opts: { lat: number; lon: number; filter?: FeedFilter; page?: number; q?: string }) {
  const filter = opts.filter ?? 'all';
  const page = Math.max(1, opts.page ?? 1);
  const key = `${opts.lat.toFixed(3)}:${opts.lon.toFixed(3)}:${filter}:${page}:${opts.q ?? ''}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < 60_000) return hit.value;

  const params: unknown[] = [opts.lat, opts.lon];
  const where: string[] = [
    'd.is_active', 'NOT d.is_deleted', '(d.portions_left IS NULL OR d.portions_left > 0)',
    'cp.is_online', 'cp.is_verified', 'NOT cp.is_blocked', 'u.is_active',
  ];
  if (filter === 'ready') where.push(`d.type IN ('ready','both')`);
  else if (filter === 'order') where.push(`d.type IN ('order','both')`);
  else if (filter === 'vegan') where.push('d.is_vegan');
  else if (filter !== 'all') { params.push(filter); where.push(`d.category = $${params.length}`); }
  if (opts.q) {
    params.push(`%${opts.q.trim()}%`);
    const p = `$${params.length}`;
    where.push(`(d.name ILIKE ${p} OR cp.nick ILIKE ${p} OR d.description ILIKE ${p} OR d.category ILIKE ${p})`);
  }
  const dist = sqlDistanceKm('cp.address_lat', 'cp.address_lon', '$1', '$2');
  params.push(PAGE + 1, (page - 1) * PAGE);
  const rows = await query<any>(
    `SELECT d.id, d.name, d.description, d.price, d.weight, d.photo_urls, d.type, d.category, d.prep_time_minutes,
            d.portions_left, d.is_vegan, cp.user_id AS chef_id, cp.nick, cp.district, cp.photo_url AS chef_photo,
            cp.rating, cp.reviews_count, ${dist} AS distance_km,
            (d.type IN ('ready','both')) AS ready_now
       FROM dishes d JOIN chef_profiles cp ON cp.user_id = d.chef_id JOIN users u ON u.id = cp.user_id
      WHERE ${where.join(' AND ')}
      ORDER BY ready_now DESC, round(${dist}::numeric, 1) ASC, cp.rating DESC, d.id DESC
      LIMIT $${params.length - 1} OFFSET $${params.length}`, params);

  const hasMore = rows.length > PAGE;
  const items = rows.slice(0, PAGE);
  // Стоимость доставки — по одному запросу на повара (кэшируется 60 с)
  const quotes = new Map<number, Awaited<ReturnType<typeof quoteDelivery>> | null>();
  await Promise.all([...new Set(items.map((r) => r.chef_id))].map(async (id) => {
    quotes.set(id, await quoteDelivery(id, opts.lat, opts.lon).catch(() => null));
  }));
  const value = {
    page, hasMore,
    items: items.map((r) => {
      const q = quotes.get(r.chef_id);
      const km = Math.round(r.distance_km * 10) / 10;
      const prep = r.ready_now ? 0 : (r.prep_time_minutes ?? 30);
      return {
        id: r.id, name: r.name, description: r.description, price: r.price, weight: r.weight,
        photoUrl: r.photo_urls[0] ?? null, type: r.type, readyNow: r.ready_now, category: r.category,
        portionsLeft: r.portions_left, isVegan: r.is_vegan,
        chef: { id: r.chef_id, nick: r.nick, district: r.district, photoUrl: r.chef_photo, rating: r.rating, reviewsCount: r.reviews_count },
        distanceKm: km,
        deliveryFee: q?.price ?? null,
        // Примерное время: приготовление + 10 мин подача курьера + ~3 мин/км
        etaMinutes: prep + 10 + Math.round(km * 3),
      };
    }),
  };
  cache.set(key, { at: Date.now(), value });
  return value;
}

export function clearFeedCache() { cache.clear(); }

/** Точки поваров для режима «карта» (без точного адреса — округление до ~500 м). */
export async function getChefMap(lat: number, lon: number, radiusKm = 15) {
  const dist = sqlDistanceKm('cp.address_lat', 'cp.address_lon', '$1', '$2');
  const rows = await query<any>(
    `SELECT cp.user_id AS id, cp.nick, cp.district, cp.rating, cp.photo_url, cp.address_lat, cp.address_lon, ${dist} AS distance_km,
            EXISTS (SELECT 1 FROM dishes d WHERE d.chef_id = cp.user_id AND d.is_active AND NOT d.is_deleted AND d.type IN ('ready','both')
                    AND (d.portions_left IS NULL OR d.portions_left > 0)) AS ready_now
       FROM chef_profiles cp JOIN users u ON u.id = cp.user_id
      WHERE cp.is_online AND cp.is_verified AND NOT cp.is_blocked AND u.is_active AND ${dist} < $3
      ORDER BY distance_km LIMIT 200`, [lat, lon, radiusKm]);
  return rows.map((r) => ({
    id: r.id, nick: r.nick, district: r.district, rating: r.rating, photoUrl: r.photo_url, readyNow: r.ready_now,
    distanceKm: Math.round(r.distance_km * 10) / 10,
    // Адрес повара покупателю не раскрывается (ТЗ 13) — точка смещена до сетки ~500 м
    point: { lat: Math.round(r.address_lat * 200) / 200, lon: Math.round(r.address_lon * 200) / 200 },
  }));
}
