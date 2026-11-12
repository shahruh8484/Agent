import { api, request } from './client';
import type {
  Address,
  AppNotification,
  BuyerProfile,
  ChefBalance,
  ChefDish,
  ChefMenu,
  ChefProfile,
  ChefPublic,
  ChefStats,
  ChefSummary,
  ChefTransaction,
  DishInput,
  FeedFilter,
  FeedPage,
  MapChef,
  Order,
  PaymentCard,
  Review,
  Role,
  Tokens,
  Tracking,
  VerifyOtpResponse,
  NeedsRoleResponse,
} from './types';

// ---------- Авторизация ----------
export const authApi = {
  sendOtp: (phone: string) =>
    api.post<{ sent: boolean; resendAfterSec: number; devCode?: string }>('/auth/send-otp', { phone }, false),
  verifyOtp: (phone: string, code: string, role?: Role) =>
    api.post<VerifyOtpResponse | NeedsRoleResponse>('/auth/verify-otp', { phone, code, ...(role ? { role } : {}) }, false),
  refresh: (refreshToken: string) => api.post<Tokens>('/auth/refresh', { refreshToken }, false),
  logout: (refreshToken: string) => request<{ ok: boolean }>('/auth/logout', { method: 'DELETE', body: { refreshToken }, auth: false }),
  registerDevice: (token: string, platform: 'ios' | 'android') => api.post<{ ok: boolean }>('/auth/device', { token, platform }),
};

// ---------- Публичные ----------
export const publicApi = {
  feed: (lat: number, lon: number, filter: FeedFilter, page: number) =>
    api.get<FeedPage>('/feed', { lat, lon, filter, page }, false),
  search: (lat: number, lon: number, q: string, page: number) => api.get<FeedPage>('/search', { lat, lon, q, page }, false),
  map: (lat: number, lon: number) => api.get<MapChef[]>('/feed/map', { lat, lon }, false),
  chef: (id: number, lat?: number, lon?: number) => api.get<ChefPublic>(`/chef/${id}`, { lat, lon }, false),
  menu: (id: number) => api.get<ChefMenu>(`/chef/${id}/menu`, undefined, false),
  reviews: (id: number, page = 1) => api.get<Review[]>(`/chef/${id}/reviews`, { page }, false),
  estimate: (chefId: number, lat: number, lon: number) =>
    api.post<{ price: number; distanceKm: number }>('/delivery/estimate', { chefId, lat, lon }, false),
};

// ---------- Покупатель ----------
export const buyerApi = {
  profile: () => api.get<BuyerProfile>('/buyer/profile'),
  updateProfile: (name: string) => api.put<{ ok: boolean }>('/buyer/profile', { name }),
  addresses: () => api.get<Address[]>('/buyer/addresses'),
  addAddress: (a: { label?: string; lat: number; lon: number; addressText: string; isDefault?: boolean }) =>
    api.post<Address>('/buyer/addresses', a),
  setDefaultAddress: (id: number) => api.put<{ ok: boolean }>(`/buyer/addresses/${id}/default`),
  deleteAddress: (id: number) => api.del<{ ok: boolean }>(`/buyer/addresses/${id}`),
  cards: () => api.get<PaymentCard[]>('/buyer/cards'),
  addCard: (cardToken: string, provider: 'payme' | 'click' = 'payme') =>
    api.post<PaymentCard>('/buyer/cards', { cardToken, provider }),
  setDefaultCard: (id: number) => api.put<{ ok: boolean }>(`/buyer/cards/${id}/default`),
  deleteCard: (id: number) => api.del<{ ok: boolean }>(`/buyer/cards/${id}`),
  orders: (status: 'all' | 'active' | 'done' | 'cancelled', page = 1) => api.get<Order[]>('/buyer/orders', { status, page }),
  pendingRating: () => api.get<Order | null>('/buyer/pending-rating'),
  notifications: () => api.get<AppNotification[]>('/buyer/notifications'),
  complaint: (b: { orderId: number; type: string; description: string; photoUrls?: string[] }) =>
    api.post<{ id: number }>('/buyer/complaints', b),
  deleteAccount: () => api.del<{ ok: boolean }>('/buyer/account'),
};

// ---------- Заказы ----------
export const ordersApi = {
  create: (b: { items: { dishId: number; qty: number }[]; addressId: number; cardId?: number; comment?: string }) =>
    api.post<Order>('/orders', b),
  get: (id: number) => api.get<Order>(`/orders/${id}`),
  tracking: (id: number) => api.get<Tracking>(`/orders/${id}/tracking`),
  cancel: (id: number) => api.post<Order>(`/orders/${id}/cancel`),
  rate: (id: number, b: { rating: number; tags: string[]; comment?: string }) =>
    api.post<{ ok: boolean; chefRating: number }>(`/orders/${id}/rate`, b),
};

// ---------- Повар ----------
export interface ChefProfileInput {
  nick?: string;
  firstName?: string;
  lastName?: string;
  photoUrl?: string;
  coverUrl?: string;
  bio?: string;
  district?: string;
  addressText?: string;
  addressLat?: number;
  addressLon?: number;
}

export const chefApi = {
  districts: () => api.get<string[]>('/chef/districts'),
  profile: () => api.get<ChefProfile>('/chef/profile'),
  updateProfile: (b: ChefProfileInput) => api.put<ChefProfile>('/chef/profile', b),
  verification: (kitchenPhotoUrls: string[]) =>
    api.post<{ status: 'pending' | 'rejected'; reason?: string; retake?: boolean }>('/chef/verification', { kitchenPhotoUrls }),
  payoutCard: (cardToken: string) =>
    api.put<{ maskedPan: string; cardType: string }>('/chef/payout-card', { cardToken, acceptTerms: true }),
  setStatus: (online: boolean) => api.put<{ online: boolean }>('/chef/status', { online }),
  dishes: () => api.get<ChefDish[]>('/chef/dishes'),
  createDish: (d: DishInput) => api.post<ChefDish>('/chef/dishes', d),
  updateDish: (id: number, d: Partial<DishInput>) => api.put<ChefDish>(`/chef/dishes/${id}`, d),
  deleteDish: (id: number) => api.del<{ ok: boolean }>(`/chef/dishes/${id}`),
  toggleDish: (id: number, active?: boolean) => api.put<ChefDish>(`/chef/dishes/${id}/toggle`, active === undefined ? {} : { active }),
  orders: (scope: 'new' | 'active' | 'today' | 'all') => api.get<Order[]>('/chef/orders', { scope }),
  summary: () => api.get<ChefSummary>('/chef/orders/summary'),
  accept: (id: number) => api.post<Order>(`/chef/orders/${id}/accept`),
  decline: (id: number, reason?: string) => api.post<Order>(`/chef/orders/${id}/decline`, reason ? { reason } : {}),
  ready: (id: number) => api.post<Order>(`/chef/orders/${id}/ready`),
  balance: () => api.get<ChefBalance>('/chef/balance'),
  transactions: (page = 1) => api.get<ChefTransaction[]>('/chef/transactions', { page }),
  payoutPreview: () => api.post<{ preview: true; amount: number; fee: number; net: number }>('/chef/payout/request', {}),
  payoutConfirm: () => api.post<any>('/chef/payout/request', { confirm: true }),
  reviews: (page = 1) => api.get<Review[]>('/chef/reviews', { page }),
  stats: () => api.get<ChefStats>('/chef/stats'),
  notifications: () => api.get<AppNotification[]>('/chef/notifications'),
};

// ---------- Загрузка фото ----------
export const uploadsApi = {
  upload: (data: Blob | ArrayBuffer, contentType: string) =>
    request<{ url: string }>('/uploads', { method: 'POST', raw: { data, contentType } }),
};
