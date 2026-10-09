import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

import { Loading } from '../components/ui';
import { AuthProvider, useAuth } from '../lib/auth';
import { colors } from '../lib/theme';

function RootNavigator() {
  const { user, loading } = useAuth();
  if (loading) return <Loading />;
  const onboarded = !!user && user.name.trim() !== '' && user.city !== '';

  return (
    <Stack
      screenOptions={{
        headerTintColor: colors.primary,
        headerTitleStyle: { color: colors.text },
        headerBackTitle: 'Назад',
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Protected guard={!user}>
        <Stack.Screen name="login" options={{ headerShown: false }} />
      </Stack.Protected>
      <Stack.Protected guard={!!user && !onboarded}>
        <Stack.Screen name="onboarding" options={{ title: 'Знакомство', headerBackVisible: false }} />
      </Stack.Protected>
      <Stack.Protected guard={onboarded}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="category/[id]" options={{ title: '' }} />
        <Stack.Screen name="specialist/[id]" options={{ title: 'Специалист' }} />
        <Stack.Screen name="order/new" options={{ title: 'Новый заказ', presentation: 'modal' }} />
        <Stack.Screen name="order/[id]" options={{ title: 'Заказ' }} />
        <Stack.Screen name="chat/[id]" options={{ title: 'Чат' }} />
        <Stack.Screen name="specialist-profile" options={{ title: 'Анкета специалиста' }} />
        <Stack.Screen name="subscription" options={{ title: 'Подписка' }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <AuthProvider>
      <StatusBar style="dark" />
      <RootNavigator />
    </AuthProvider>
  );
}
