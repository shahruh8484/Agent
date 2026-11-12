import { useNavigation, type NavigatorScreenParams } from '@react-navigation/native';
import type { NativeStackNavigationProp } from '@react-navigation/native-stack';

export type BuyerTabParamList = {
  Feed: undefined;
  Orders: undefined;
  Map: undefined;
  Profile: undefined;
};

export type ChefTabParamList = {
  ChefOrders: undefined;
  Dishes: undefined;
  Balance: undefined;
  ChefProfile: undefined;
};

export type RootStackParamList = {
  // Авторизация
  Phone: undefined;
  Code: { phone: string; devCode?: string; resendAfterSec: number };
  // Покупатель
  BuyerOnboarding: undefined;
  BuyerTabs: NavigatorScreenParams<BuyerTabParamList> | undefined;
  Chef: { chefId: number };
  ChefReviews: { chefId?: number; nick?: string };
  Checkout: undefined;
  Tracking: { orderId: number };
  Rating: { orderId: number; chefNick: string; chefPhoto: string | null; chefId: number };
  Notifications: undefined;
  Addresses: { select?: boolean } | undefined;
  AddAddress: undefined;
  Cards: undefined;
  AddCard: undefined;
  Support: undefined;
  // Повар
  ChefRegistration: undefined;
  ChefTabs: NavigatorScreenParams<ChefTabParamList> | undefined;
  DishForm: { dishId?: number } | undefined;
  ChefEditProfile: undefined;
  PayoutCard: undefined;
};

export type Nav = NativeStackNavigationProp<RootStackParamList>;

export const useNav = () => useNavigation<Nav>();
