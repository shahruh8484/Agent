import { Ionicons } from '@expo/vector-icons';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { useNavigation } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect, useState } from 'react';
import { Platform, Text, View } from 'react-native';
import { errorMessage } from '../api/client';
import { buyerApi } from '../api/endpoints';
import { onSocket } from '../api/socket';
import { Button, Empty, Loader } from '../components/ui';
import { registerPush } from '../lib/push';
import { storage } from '../lib/storage';
import { PhoneScreen } from '../screens/auth/PhoneScreen';
import { CodeScreen } from '../screens/auth/CodeScreen';
import { AddAddressScreen, AddressesScreen } from '../screens/buyer/AddressesScreen';
import { AddCardScreen, CardsScreen } from '../screens/buyer/CardsScreen';
import { ChefScreen } from '../screens/buyer/ChefScreen';
import { CheckoutScreen } from '../screens/buyer/CheckoutScreen';
import { FeedScreen } from '../screens/buyer/FeedScreen';
import { MapScreen } from '../screens/buyer/MapScreen';
import { OnboardingScreen } from '../screens/buyer/OnboardingScreen';
import { OrdersScreen } from '../screens/buyer/OrdersScreen';
import { ProfileScreen } from '../screens/buyer/ProfileScreen';
import { RatingScreen } from '../screens/buyer/RatingScreen';
import { TrackingScreen } from '../screens/buyer/TrackingScreen';
import { BalanceScreen } from '../screens/chef/BalanceScreen';
import { DishesScreen } from '../screens/chef/DishesScreen';
import { DishFormScreen } from '../screens/chef/DishFormScreen';
import { ChefOrdersScreen } from '../screens/chef/OrdersScreen';
import { ChefEditProfileScreen, ChefProfileScreen, PayoutCardScreen } from '../screens/chef/ProfileScreen';
import { RegistrationScreen } from '../screens/chef/RegistrationScreen';
import { NotificationsScreen } from '../screens/common/NotificationsScreen';
import { ReviewsScreen } from '../screens/common/ReviewsScreen';
import { SupportScreen } from '../screens/common/SupportScreen';
import { useAuth } from '../store/auth';
import { getSkippedRatings, useBuyer } from '../store/buyer';
import { useCart } from '../store/cart';
import { chefStep, useChef } from '../store/chef';
import { toast } from '../store/toast';
import { colors } from '../theme';
import type { BuyerTabParamList, ChefTabParamList, Nav, RootStackParamList } from './types';

const Stack = createNativeStackNavigator<RootStackParamList>();
const BuyerTab = createBottomTabNavigator<BuyerTabParamList>();
const ChefTab = createBottomTabNavigator<ChefTabParamList>();

const tabOptions = {
  headerShown: false,
  tabBarActiveTintColor: colors.primary,
  tabBarInactiveTintColor: colors.muted,
  tabBarLabelStyle: { fontSize: 11, lineHeight: 14 },
  tabBarStyle: Platform.OS === 'web' ? { height: 60, paddingBottom: 6 } : undefined,
};

type IconName = React.ComponentProps<typeof Ionicons>['name'];
const icon =
  (name: IconName) =>
  ({ color, size }: { color: string; size: number }) => <Ionicons name={name} color={color} size={size} />;

function BuyerTabs() {
  useBuyerSideEffects();
  return (
    <BuyerTab.Navigator screenOptions={tabOptions}>
      <BuyerTab.Screen name="Feed" component={FeedScreen} options={{ title: 'Лента', tabBarIcon: icon('restaurant-outline') }} />
      <BuyerTab.Screen name="Orders" component={OrdersScreen} options={{ title: 'Заказы', tabBarIcon: icon('receipt-outline') }} />
      <BuyerTab.Screen name="Map" component={MapScreen} options={{ title: 'Карта', tabBarIcon: icon('map-outline') }} />
      <BuyerTab.Screen name="Profile" component={ProfileScreen} options={{ title: 'Профиль', tabBarIcon: icon('person-outline') }} />
    </BuyerTab.Navigator>
  );
}

function ChefTabs() {
  useEffect(() => {
    registerPushIfEnabled();
  }, []);
  return (
    <ChefTab.Navigator screenOptions={tabOptions}>
      <ChefTab.Screen name="ChefOrders" component={ChefOrdersScreen} options={{ title: 'Заказы', tabBarIcon: icon('receipt-outline') }} />
      <ChefTab.Screen name="Dishes" component={DishesScreen} options={{ title: 'Меню', tabBarIcon: icon('fast-food-outline') }} />
      <ChefTab.Screen name="Balance" component={BalanceScreen} options={{ title: 'Баланс', tabBarIcon: icon('wallet-outline') }} />
      <ChefTab.Screen name="ChefProfile" component={ChefProfileScreen} options={{ title: 'Профиль', tabBarIcon: icon('person-outline') }} />
    </ChefTab.Navigator>
  );
}

async function registerPushIfEnabled() {
  if ((await storage.get('dx.notifEnabled')) !== '0') registerPush();
}

/** Покупатель: push, неоценённый заказ при открытии (ТЗ 3.6), уведомления по сокету */
function useBuyerSideEffects() {
  const nav = useNavigation<Nav>();
  const refreshUnread = useBuyer((s) => s.refreshUnread);
  useEffect(() => {
    registerPushIfEnabled();
    (async () => {
      try {
        const o = await buyerApi.pendingRating();
        if (o && !(await getSkippedRatings()).includes(o.id)) {
          nav.navigate('Rating', { orderId: o.id, chefId: o.chefId, chefNick: o.chef?.nick ?? 'повар', chefPhoto: o.chef?.photoUrl ?? null });
        }
      } catch {
        /* не критично */
      }
    })();
    const off = onSocket<{ title: string; body: string }>('notification', (n) => {
      toast(`${n.title}: ${n.body}`);
      refreshUnread();
    });
    return off;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}

const stackOptions = { headerShown: false, contentStyle: { backgroundColor: colors.bg } };

export function RootNavigator() {
  const status = useAuth((s) => s.status);
  const role = useAuth((s) => s.user?.role);

  if (status === 'loading') return <Splash />;
  if (status === 'signedOut')
    return (
      <Stack.Navigator screenOptions={stackOptions}>
        <Stack.Screen name="Phone" component={PhoneScreen} />
        <Stack.Screen name="Code" component={CodeScreen} />
      </Stack.Navigator>
    );
  return role === 'chef' ? <ChefRoot /> : <BuyerRoot />;
}

function useGate(loader: () => Promise<unknown>, reset: () => void) {
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const run = () => {
    setError(null);
    loader()
      .then(() => setReady(true))
      .catch((e) => setError(errorMessage(e)));
  };
  useEffect(() => {
    run();
    return reset;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return { error, ready, retry: run };
}

function GateError({ error, retry }: { error: string; retry: () => void }) {
  const signOut = useAuth((s) => s.signOut);
  return (
    <View style={{ flex: 1, justifyContent: 'center', backgroundColor: colors.bg }}>
      <Empty icon="cloud-offline-outline" title="Не удалось загрузить профиль" text={error} action={<Button title="Повторить" onPress={retry} />} />
      <Text onPress={signOut} style={{ textAlign: 'center', color: colors.textSecondary }}>
        Выйти
      </Text>
    </View>
  );
}

function BuyerRoot() {
  const profile = useBuyer((s) => s.profile);
  const gate = useGate(useBuyer.getState().load, () => {
    useBuyer.getState().reset();
    useCart.getState().clear();
  });
  if (gate.error && !profile) return <GateError error={gate.error} retry={gate.retry} />;
  if (!gate.ready || !profile) return <Loader />;
  if (!profile.profileComplete)
    return (
      <Stack.Navigator screenOptions={stackOptions}>
        <Stack.Screen name="BuyerOnboarding" component={OnboardingScreen} />
      </Stack.Navigator>
    );
  return (
    <Stack.Navigator screenOptions={stackOptions}>
      <Stack.Screen name="BuyerTabs" component={BuyerTabs} />
      <Stack.Screen name="Chef" component={ChefScreen} />
      <Stack.Screen name="ChefReviews" component={ReviewsScreen} />
      <Stack.Screen name="Checkout" component={CheckoutScreen} />
      <Stack.Screen name="Tracking" component={TrackingScreen} />
      <Stack.Screen name="Rating" component={RatingScreen} options={{ presentation: 'modal' }} />
      <Stack.Screen name="Notifications" component={NotificationsScreen} />
      <Stack.Screen name="Addresses" component={AddressesScreen} />
      <Stack.Screen name="AddAddress" component={AddAddressScreen} />
      <Stack.Screen name="Cards" component={CardsScreen} />
      <Stack.Screen name="AddCard" component={AddCardScreen} />
      <Stack.Screen name="Support" component={SupportScreen} />
    </Stack.Navigator>
  );
}

function ChefRoot() {
  const profile = useChef((s) => s.profile);
  const gate = useGate(useChef.getState().load, useChef.getState().reset);
  if (gate.error && !profile) return <GateError error={gate.error} retry={gate.retry} />;
  if (!gate.ready || !profile) return <Loader />;
  if (chefStep(profile) !== 'done')
    return (
      <Stack.Navigator screenOptions={stackOptions}>
        <Stack.Screen name="ChefRegistration" component={RegistrationScreen} />
      </Stack.Navigator>
    );
  return (
    <Stack.Navigator screenOptions={stackOptions}>
      <Stack.Screen name="ChefTabs" component={ChefTabs} />
      <Stack.Screen name="DishForm" component={DishFormScreen} />
      <Stack.Screen name="ChefReviews" component={ReviewsScreen} />
      <Stack.Screen name="Notifications" component={NotificationsScreen} />
      <Stack.Screen name="ChefEditProfile" component={ChefEditProfileScreen} />
      <Stack.Screen name="PayoutCard" component={PayoutCardScreen} />
      <Stack.Screen name="Support" component={SupportScreen} />
    </Stack.Navigator>
  );
}

function Splash() {
  return (
    <View style={{ flex: 1, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' }}>
      <Text style={{ color: colors.white, fontSize: 36, fontWeight: '800' }}>Dasturxon</Text>
    </View>
  );
}
