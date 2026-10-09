export type Role = 'client' | 'specialist';

export interface User {
  id: number;
  phone: string;
  name: string;
  role: Role;
  city: string;
  subscription_until: string | null;
  has_specialist_profile: boolean;
}

export interface Category {
  id: number;
  name: string;
}

export interface Section extends Category {
  children: Category[];
}

export interface Specialist {
  id: number;
  name: string;
  city: string;
  bio: string;
  experience_years: number;
  price_from: number | null;
  remote: boolean;
  rating: number | null;
  reviews_count: number;
  categories: Category[];
  reviews?: Review[];
}

export interface Review {
  id: number;
  rating: number;
  text: string;
  created_at: string;
  client_name: string;
  order_title: string;
}

export type OrderStatus = 'open' | 'in_progress' | 'completed' | 'closed';

export interface Order {
  id: number;
  client_id: number;
  client_name: string;
  category_id: number;
  category_name: string;
  title: string;
  description: string;
  city: string;
  budget: number | null;
  remote: boolean;
  when_text: string;
  status: OrderStatus;
  specialist_id: number | null;
  created_at: string;
  responses_count: number;
  responded?: boolean;
  is_mine?: boolean;
  my_response?: { id: number; message: string; price: number | null } | null;
  chat_id?: number;
  has_review?: boolean;
}

export interface OrderResponse {
  id: number;
  order_id: number;
  message: string;
  price: number | null;
  created_at: string;
  chat_id: number;
  specialist: Specialist;
}

export interface ChatSummary {
  id: number;
  order_id: number;
  order_title: string;
  other_name: string;
  other_id: number;
  last_text: string | null;
  last_at: string | null;
}

export interface Message {
  id: number;
  chat_id: number;
  sender_id: number;
  text: string;
  created_at: string;
}

export interface Plan {
  id: string;
  title: string;
  days: number;
  price: number;
}
