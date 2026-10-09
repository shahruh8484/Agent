import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

import { Loading } from '../components/ui';
import { AuthProvider, useAuth } from '../lib/auth';
import { LangProvider, useT } from '../lib/i18n';
import { colors } from '../lib/theme';

function RootNavigator() {
  const { user, loading } = useAuth();
  const { t } = useT();
  if (loading) return <Loading />;
  const onboarded = !!user && user.name.trim() !== '' && user.city !== '';

  return (
    <Stack
      screenOptions={{
        headerTintColor: colors.primary,
        headerTitleStyle: { color: colors.text },
        headerBackTitle: t('back'),
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Protected guard={!user}>
        <Stack.Screen name="login" options={{ headerShown: false }} />
      </Stack.Protected>
      <Stack.Protected guard={!!user && !onboarded}>
        <Stack.Screen name="onboarding" options={{ title: t('onboardingTitle'), headerBackVisible: false }} />
      </Stack.Protected>
      <Stack.Protected guard={onboarded}>
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="category/[id]" options={{ title: '' }} />
        <Stack.Screen name="specialist/[id]" options={{ title: t('specialist') }} />
        <Stack.Screen name="order/new" options={{ title: t('newOrder'), presentation: 'modal' }} />
        <Stack.Screen name="order/[id]" options={{ title: t('order') }} />
        <Stack.Screen name="chat/[id]" options={{ title: t('chat') }} />
        <Stack.Screen name="specialist-profile" options={{ title: t('specialistProfile') }} />
        <Stack.Screen name="subscription" options={{ title: t('subscription') }} />
      </Stack.Protected>
    </Stack>
  );
}

export default function RootLayout() {
  return (
    <LangProvider>
      <AuthProvider>
        <StatusBar style="dark" />
        <RootNavigator />
      </AuthProvider>
    </LangProvider>
  );
}
