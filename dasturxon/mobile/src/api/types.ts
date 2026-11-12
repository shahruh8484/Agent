export type Role = 'buyer' | 'chef';

export type OrderStatus =
  | 'PENDING'
  | 'CONFIRMED'
  | 'COOKING'
  | 'READY'
  | 'COURIER_ASSIGNED'
  | 'IN_DELIVERY'
  | 'DELIVERED'
  | 'RATED'
  | 'CANCELLED';

export type DishType = 'ready' | 'order' | 'both';
export type Category = 'breakfast' | 'lunch' | 'dinner' | 'bakery' | 'drinks' | 'other';
export type FeedFilter = 'all' | 'ready' | 'order' | 'breakfast' | 'lunch' | 'dinner' | 'bakery' | 'drinks' | 'vegan';

export interface Point {
  lat: number;
  lon: number;
}

export interface AuthUser {
  id: number;
  phone: string;
  role: Role;
  name: string | null;
}

export interface Tokens {
  accessToken: string;
  refreshToken: string;
  expiresIn?: number;
}

export interface VerifyOtpResponse extends Tokens {
  isNew: boolean;
  user: AuthUser;
}

export interface FeedItem {
  id: number;
  name: string;
  description: string | null;
  price: number;
  weight: string | null;
  photoUrl: string | null;
  type: DishType;
  readyNow: boolean;
  category: Category;
  portionsLeft: number | null;
  isVegan: boolean;
  chef: { id: number; nick: string; district: string; photoUrl: string | null; rating: number; reviewsCount: number };
  distanceKm: number;
  deliveryFee: number | null;
  etaMinutes: number | null;
}

export interface FeedPage {
  page: number;
  hasMore: boolean;
  items: FeedItem[];
}

export interface MapChef {
  id: number;
  nick: string;
  district: string;
  rating: number;
  photoUrl: string | null;
  readyNow: boolean;
  distanceKm: number;
  point: Point;
}

export interface Review {
  id?: number;
  rating: number;
  tags: string[];
  comment: string | null;
  createdAt?: string;
  created_at?: string;
  buyerName?: string;
  order_id?: number;
}

export interface ChefPublic {
  id: number;
  nick: string;
  bio: string | null;
  photoUrl: string | null;
  coverUrl: string | null;
  district: string;
  status: 'ready' | 'order' | 'offline';
  rating: number;
  reviewsCount: number;
  totalOrders: number;
  avgDeliveryMinutes: number | null;
  distanceKm: number | null;
  deliveryFee: number | null;
  lastReviews: Review[];
}

export interface MenuDish {
  id: number;
  name: string;
  description: string | null;
  price: number;
  weight: string | null;
  photoUrls: string[];
  category: Category;
  type: DishType;
  prepTimeMinutes: number | null;
  portionsLeft: number | null;
  ingredients: string | null;
  isVegan: boolean;
}

export interface ChefMenu {
  available: boolean;
  dishes: MenuDish[];
}

export interface Address {
  id: number;
  label: string | null;
  lat: number;
  lon: number;
  addressText: string;
  isDefault: boolean;
}

export interface PaymentCard {
  id: number;
  maskedPan: string;
  cardType: 'uzcard' | 'humo' | 'payme';
  provider: 'payme' | 'click';
  isDefault: boolean;
}

export interface BuyerProfile {
  id: number;
  phone: string;
  name: string | null;
  addresses: Address[];
  cards: PaymentCard[];
  profileComplete: boolean;
}

export interface OrderItem {
  dishId: number;
  name: string;
  price: number;
  qty: number;
  photoUrl: string | null;
}

export interface Courier {
  name: string;
  phone: string;
  position: Point | null;
  etaSeconds: number | null;
}

export interface Order {
  id: number;
  status: OrderStatus;
  items: OrderItem[];
  dishTotal: number;
  deliveryFee?: number;
  total?: number;
  chefAmount?: number;
  expiresAt?: string | null;
  comment: string | null;
  createdAt: string;
  confirmedAt: string | null;
  readyAt: string | null;
  deliveredAt: string | null;
  cancelledAt: string | null;
  cancelReason: string | null;
  paymentStatus: string;
  refundedAmount: number;
  courier: Courier | null;
  chefId: number;
  buyerId: number;
  deliveryAddress: { text: string; lat?: number; lon?: number };
  chef?: { id: number; nick: string; photoUrl: string | null; district?: string; point?: Point | null };
  history?: { status: OrderStatus; note: string | null; created_at: string }[];
  reviewRating?: number | null;
}

export interface Tracking {
  orderId: number;
  status: OrderStatus;
  courier: Courier | null;
}

export interface AppNotification {
  id: number;
  type: string;
  title: string;
  body: string;
  data: unknown;
  read_at: string | null;
  created_at: string;
}

export type VerificationStatus = 'draft' | 'pending' | 'approved' | 'rejected';

export interface ChefProfile {
  id: number;
  phone: string;
  nick?: string;
  firstName?: string;
  lastName?: string;
  bio?: string | null;
  photoUrl?: string | null;
  coverUrl?: string | null;
  district?: string;
  addressText?: string;
  addressLat?: number;
  addressLon?: number;
  isOnline?: boolean;
  verificationStatus?: VerificationStatus;
  verificationNote?: string | null;
  kitchenPhotoUrls?: string[] | null;
  isVerified?: boolean;
  isBlocked?: boolean;
  blockedUntil?: string | null;
  rating?: number;
  reviewsCount?: number;
  totalOrders?: number;
  payoutCard?: { maskedPan: string; cardType: string } | null;
  termsAccepted?: boolean;
  registrationStep?: number;
}

export interface ChefDish extends MenuDish {
  isActive: boolean;
}

export interface DishInput {
  name: string;
  description?: string;
  category: Category;
  price: number;
  weight?: string;
  photoUrls: string[];
  portionsLeft?: number | null;
  type: DishType;
  prepTimeMinutes?: number | null;
  ingredients?: string;
  isVegan?: boolean;
}

export interface ChefSummary {
  new: number;
  active: number;
  done_today: number;
  earned_today: number;
}

export interface ChefBalance {
  balance: number;
  nextPayoutAt: string | null;
  pendingEarlyWithdrawal: number;
  week: { start: string; orders: number; turnover: number; avgCheck: number; avgRating: number | null };
}

export interface ChefTransaction {
  type: 'earning' | 'payout';
  kind: 'order' | 'refund' | 'weekly' | 'early';
  id: number;
  order_id: number | null;
  gross_amount: number | null;
  commission: number | null;
  tax: number | null;
  amount: number;
  created_at: string;
  title: string;
}

export interface ChefStats {
  totalOrders: number;
  rating: number;
  reviewsCount: number;
  onPlatformSince: string;
}
