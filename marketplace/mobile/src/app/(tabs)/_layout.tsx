import Tabs from 'expo-router/js-tabs';
import { ColorValue, Text } from 'react-native';

import { useAuth } from '../../lib/auth';
import { colors } from '../../lib/theme';

const icon = (glyph: string) => ({ color }: { color: ColorValue }) => (
  <Text style={{ fontSize: 20, color }}>{glyph}</Text>
);

export default function TabsLayout() {
  const { user } = useAuth();
  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: colors.primary,
        headerTitleStyle: { color: colors.text },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Услуги', tabBarIcon: icon('⌕') }} />
      <Tabs.Screen
        name="orders"
        options={{ title: user?.role === 'specialist' ? 'Заказы' : 'Мои заказы', tabBarIcon: icon('☰') }}
      />
      <Tabs.Screen name="chats" options={{ title: 'Чаты', tabBarIcon: icon('✉') }} />
      <Tabs.Screen name="profile" options={{ title: 'Профиль', tabBarIcon: icon('☺') }} />
    </Tabs>
  );
}
