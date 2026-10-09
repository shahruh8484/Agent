import Tabs from 'expo-router/js-tabs';
import { ColorValue, Text } from 'react-native';

import { useAuth } from '../../lib/auth';
import { useT } from '../../lib/i18n';
import { colors } from '../../lib/theme';

const icon = (glyph: string) => ({ color }: { color: ColorValue }) => (
  <Text style={{ fontSize: 20, color }}>{glyph}</Text>
);

export default function TabsLayout() {
  const { user } = useAuth();
  const { t } = useT();
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: colors.primary,
        headerTitleStyle: { color: colors.text },
      }}
    >
      <Tabs.Screen name="index" options={{ title: t('tabServices'), tabBarIcon: icon('⌕') }} />
      <Tabs.Screen
        name="orders"
        options={{ title: user?.role === 'specialist' ? t('tabOrders') : t('tabMyOrders'), tabBarIcon: icon('☰') }}
      />
      <Tabs.Screen name="chats" options={{ title: t('tabChats'), tabBarIcon: icon('✉') }} />
      <Tabs.Screen name="profile" options={{ title: t('tabProfile'), tabBarIcon: icon('☺') }} />
    </Tabs>
  );
}
