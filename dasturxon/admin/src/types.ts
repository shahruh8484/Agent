export type Role = 'owner' | 'accountant' | 'support';

export type Permission =
  | 'dashboard' | 'finance' | 'tax_reports' | 'payouts_run' | 'chefs_view' | 'chefs_verify'
  | 'chefs_block_permanent' | 'chefs_block_temporary' | 'buyers_view' | 'complaints' | 'orders_view'
  | 'refund_small' | 'refund_large' | 'settings' | 'staff_manage';

export interface Staff {
  id: number;
  name: string;
  email: string;
  role: Role;
  permissions: Permission[];
}

export type OrderStatus =
  | 'PENDING' | 'CONFIRMED' | 'COOKING' | 'READY' | 'COURIER_ASSIGNED' | 'IN_DELIVERY' | 'DELIVERED' | 'RATED' | 'CANCELLED';

export interface OrderItem { dishId: number; name: string; price: number; qty: number; photoUrl?: string }

export interface AdminOrder {
  id: number;
  status: OrderStatus;
  items: OrderItem[];
  dishTotal: number;
  deliveryFee: number;
  total: number;
  comment: string | null;
  createdAt: string;
  confirmedAt: string | null;
  readyAt: string | null;
  deliveredAt: string | null;
  cancelledAt: string | null;
  cancelReason: string | null;
  paymentStatus: string;
  refundedAmount: number;
  courier: { name: string; phone: string; position: { lat: number; lon: number } | null; etaSeconds: number | null } | null;
  chefId: number;
  buyerId: number;
  deliveryAddress: { text: string; lat: number; lon: number };
  deliveryFeeReal: number | null;
  deliveryBuffer: number | null;
  platformCommission: number;
  chefTax: number;
  chefNetAmount: number;
  paymentId: string | null;
  yandexRequestId: string | null;
  chefNick?: string;
  chefPhone?: string;
  buyerPhone?: string;
  buyerName?: string | null;
}

export interface AdminOrderDetail extends AdminOrder {
  history: { status: OrderStatus; note: string | null; created_at: string }[];
  refunds: { amount: number; reason: string; created_at: string; staff: string | null }[];
  review: { rating: number; tags: string[]; comment: string | null; created_at: string } | null;
}

export interface AiVerdict { approved: boolean; reason: string; source?: 'ai' | 'skipped' }

export interface Payout {
  id: number;
  chef_id: number;
  nick?: string;
  payout_card_masked?: string | null;
  kind: 'weekly' | 'early';
  amount: number;
  commission_1pct: number;
  net_amount: number;
  week_start: string | null;
  week_end: string | null;
  status: 'pending' | 'processing' | 'done' | 'failed';
  bank_transaction_id?: string | null;
  error: string | null;
  created_at: string;
  paid_at: string | null;
}

export interface Complaint {
  id: number;
  order_id: number;
  buyer_id: number;
  chef_id: number;
  type: string;
  description: string;
  photo_urls: string[];
  status: 'open' | 'resolved' | 'closed';
  refund_amount: number | null;
  resolution_note: string | null;
  created_at: string;
  buyer_phone: string;
  buyer_name: string | null;
  chef_nick: string;
}
