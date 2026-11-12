import { NavigationContainer, DefaultTheme } from '@react-navigation/native';
import { StatusBar } from 'expo-status-bar';
import React, { useEffect } from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { ToastHost } from './src/components/Toast';
import { RootNavigator } from './src/navigation/RootNavigator';
import { useAuth } from './src/store/auth';
import { colors } from './src/theme';

const theme = {
  ...DefaultTheme,
  colors: { ...DefaultTheme.colors, primary: colors.primary, background: colors.bg, card: colors.card },
};

export default function App() {
  const bootstrap = useAuth((s) => s.bootstrap);
  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  return (
    <SafeAreaProvider>
      <NavigationContainer theme={theme}>
        <StatusBar style="light" />
        <RootNavigator />
      </NavigationContainer>
      <ToastHost />
    </SafeAreaProvider>
  );
}
