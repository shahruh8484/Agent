import { create } from 'zustand';
import { buyerApi } from '../api/endpoints';
import type { Address, BuyerProfile, Point } from '../api/types';
import { TASHKENT } from '../config';
import { storage } from '../lib/storage';

const K_SEEN_NOTIF = 'dx.seenNotif';
const K_SKIPPED_RATING = 'dx.skippedRating';

interface BuyerState {
  profile: BuyerProfile | null;
  loading: boolean;
  /** выбранный адрес доставки (по умолчанию — адрес «по умолчанию» из профиля) */
  addressId: number | null;
  unread: number;
  load: () => Promise<BuyerProfile | null>;
  selectAddress: (id: number) => void;
  refreshUnread: () => Promise<void>;
  markNotificationsSeen: (maxId: number) => Promise<void>;
  reset: () => void;
}

export const useBuyer = create<BuyerState>((set, get) => ({
  profile: null,
  loading: false,
  addressId: null,
  unread: 0,

  load: async () => {
    set({ loading: true });
    try {
      const profile = await buyerApi.profile();
      const cur = get().addressId;
      const still = profile.addresses.find((a) => a.id === cur);
      const def = profile.addresses.find((a) => a.isDefault) ?? profile.addresses[0];
      set({ profile, addressId: still?.id ?? def?.id ?? null });
      return profile;
    } finally {
      set({ loading: false });
    }
  },

  selectAddress: (id) => set({ addressId: id }),

  refreshUnread: async () => {
    try {
      const [list, seen] = await Promise.all([buyerApi.notifications(), storage.get(K_SEEN_NOTIF)]);
      const seenId = Number(seen ?? 0);
      set({ unread: list.filter((n) => !n.read_at && n.id > seenId).length });
    } catch {
      /* не критично */
    }
  },

  markNotificationsSeen: async (maxId) => {
    await storage.set(K_SEEN_NOTIF, String(maxId));
    set({ unread: 0 });
  },

  reset: () => set({ profile: null, addressId: null, unread: 0 }),
}));

export function useCurrentAddress(): Address | null {
  return useBuyer((s) => s.profile?.addresses.find((a) => a.id === s.addressId) ?? null);
}

export function useCurrentPoint(): Point {
  const a = useCurrentAddress();
  return a ? { lat: a.lat, lon: a.lon } : TASHKENT;
}

// Заказы, для которых покупатель нажал «Пропустить» на экране оценки
export async function getSkippedRatings(): Promise<number[]> {
  try {
    return JSON.parse((await storage.get(K_SKIPPED_RATING)) ?? '[]');
  } catch {
    return [];
  }
}

export async function skipRating(orderId: number) {
  const list = await getSkippedRatings();
  await storage.set(K_SKIPPED_RATING, JSON.stringify([...list, orderId].slice(-20)));
}
