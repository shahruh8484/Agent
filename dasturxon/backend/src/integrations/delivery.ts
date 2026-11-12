import { randomUUID } from 'node:crypto';
import { config } from '../config.js';
import { haversineKm } from '../lib/geo.js';

export type Point = { lat: number; lon: number };

export type CourierStatus = 'searching' | 'driving_to_pickup' | 'delivering' | 'delivered' | 'cancelled' | 'failed';

export interface Tracking {
  status: CourierStatus;
  driver?: { name: string; phone: string };
  position?: Point;
  etaSeconds?: number;
  /** Реальная стоимость поездки (известна после завершения) */
  finalPrice?: number;
}

/** Яндекс Go for Business (ТЗ 5). */
export interface DeliveryProvider {
  estimate(from: Point, to: Point): Promise<{ minPrice: number; maxPrice: number }>;
  request(from: Point, to: Point, orderId: number): Promise<{ requestId: string }>;
  track(requestId: string): Promise<Tracking>;
  cancel(requestId: string): Promise<void>;
}

/**
 * Mock: цена от расстояния, курьер «едет» по прямой. DELIVERY_MOCK_SPEED ускоряет время.
 * Этапы: поиск 30с → едет к повару 4 мин → везёт покупателю (2 мин/км, мин. 3 мин).
 */
export class MockDelivery implements DeliveryProvider {
  rides = new Map<string, { from: Point; to: Point; createdAt: number; cancelled: boolean; price: number }>();
  now: () => number = () => Date.now();

  private price(from: Point, to: Point) {
    const km = haversineKm(from.lat, from.lon, to.lat, to.lon);
    const max = Math.round((8000 + km * 1500) / 100) * 100;
    return { minPrice: Math.round(max * 0.85), maxPrice: max };
  }
  async estimate(from: Point, to: Point) { return this.price(from, to); }
  async request(from: Point, to: Point) {
    const requestId = `ydx_${randomUUID()}`;
    this.rides.set(requestId, { from, to, createdAt: this.now(), cancelled: false, price: Math.round(this.price(from, to).maxPrice * 0.95) });
    return { requestId };
  }
  async cancel(requestId: string) {
    const r = this.rides.get(requestId);
    if (r) r.cancelled = true;
  }
  async track(requestId: string): Promise<Tracking> {
    const r = this.rides.get(requestId);
    if (!r) return { status: 'failed' };
    if (r.cancelled) return { status: 'cancelled' };
    const t = ((this.now() - r.createdAt) / 1000) * config.yandex.mockSpeed;
    const search = 30, toPickup = 240;
    const km = haversineKm(r.from.lat, r.from.lon, r.to.lat, r.to.lon);
    const deliver = Math.max(180, km * 120);
    const driver = { name: 'Бахром', phone: '+998901234567' };
    const lerp = (a: Point, b: Point, k: number): Point => ({ lat: a.lat + (b.lat - a.lat) * k, lon: a.lon + (b.lon - a.lon) * k });
    const start: Point = { lat: r.from.lat + 0.01, lon: r.from.lon - 0.01 };
    if (t < search) return { status: 'searching' };
    if (t < search + toPickup) {
      const k = (t - search) / toPickup;
      return { status: 'driving_to_pickup', driver, position: lerp(start, r.from, k), etaSeconds: Math.round((search + toPickup - t + deliver) / config.yandex.mockSpeed) };
    }
    if (t < search + toPickup + deliver) {
      const k = (t - search - toPickup) / deliver;
      return { status: 'delivering', driver, position: lerp(r.from, r.to, k), etaSeconds: Math.round((search + toPickup + deliver - t) / config.yandex.mockSpeed) };
    }
    return { status: 'delivered', driver, position: r.to, etaSeconds: 0, finalPrice: r.price };
  }
}

/** Реальная интеграция с API Яндекс Доставки (cargo claims). Эндпоинты нужно сверить с договором/документацией. */
class YandexDelivery implements DeliveryProvider {
  private async call(path: string, body?: unknown, method = 'POST') {
    const r = await fetch(`${config.yandex.url}${path}`, {
      method,
      headers: { Authorization: `Bearer ${config.yandex.token}`, 'Content-Type': 'application/json', 'Accept-Language': 'ru' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(`Yandex ${path}: ${r.status} ${await r.text()}`);
    return r.json() as Promise<any>;
  }
  async estimate(from: Point, to: Point) {
    const j = await this.call('/v2/check-price', {
      route_points: [{ coordinates: [from.lon, from.lat] }, { coordinates: [to.lon, to.lat] }],
      requirements: { taxi_class: 'express' },
    });
    const price = Number(j.price);
    return { minPrice: price, maxPrice: price };
  }
  async request(from: Point, to: Point, orderId: number) {
    const j = await this.call(`/v2/claims/create?request_id=order-${orderId}`, {
      items: [{ title: `Заказ #${orderId}`, quantity: 1, pickup_point: 1, droppof_point: 2, cost_value: '0', cost_currency: 'UZS' }],
      route_points: [
        { point_id: 1, visit_order: 1, type: 'source', address: { coordinates: [from.lon, from.lat], comment: `Заберите заказ #${orderId}` } },
        { point_id: 2, visit_order: 2, type: 'destination', address: { coordinates: [to.lon, to.lat], comment: 'Доставьте покупателю' } },
      ],
      client_requirements: { taxi_class: 'express' },
      skip_client_notify: false,
    });
    await this.call(`/v2/claims/accept?claim_id=${j.id}`, { version: 1 });
    return { requestId: j.id };
  }
  async track(requestId: string): Promise<Tracking> {
    const info = await this.call(`/v2/claims/info?claim_id=${requestId}`, {});
    const map: Record<string, CourierStatus> = {
      performer_lookup: 'searching', performer_draft: 'searching', accepted: 'searching', ready_for_approval: 'searching',
      performer_found: 'driving_to_pickup', pickup_arrived: 'driving_to_pickup', ready_for_pickup_confirmation: 'driving_to_pickup',
      pickuped: 'delivering', delivery_arrived: 'delivering', ready_for_delivery_confirmation: 'delivering',
      delivered: 'delivered', delivered_finish: 'delivered',
      cancelled: 'cancelled', cancelled_by_taxi: 'cancelled', failed: 'failed', performer_not_found: 'failed',
    };
    const status = map[info.status] ?? 'searching';
    const res: Tracking = { status };
    if (info.performer_info) res.driver = { name: info.performer_info.courier_name, phone: '' };
    if (status === 'driving_to_pickup' || status === 'delivering') {
      try {
        const pos = await this.call(`/v2/claims/performer-position?claim_id=${requestId}`, undefined, 'GET');
        res.position = { lat: pos.position.lat, lon: pos.position.lon };
      } catch { /* позиция временно недоступна */ }
    }
    if (status === 'delivered' && info.pricing?.final_price) res.finalPrice = Number(info.pricing.final_price);
    return res;
  }
  async cancel(requestId: string) {
    await this.call(`/v2/claims/cancel?claim_id=${requestId}`, { version: 1, cancel_state: 'free' });
  }
}

export const delivery: DeliveryProvider = config.yandex.provider === 'yandex' ? new YandexDelivery() : new MockDelivery();
