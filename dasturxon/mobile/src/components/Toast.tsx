import React from 'react';
import { Pressable, StyleSheet, Text } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useToast } from '../store/toast';
import { colors } from '../theme';

export function ToastHost() {
  const { message, kind, hide } = useToast();
  const insets = useSafeAreaInsets();
  if (!message) return null;
  const bg = kind === 'error' ? colors.red : kind === 'success' ? colors.primary : '#333';
  return (
    <Pressable onPress={hide} style={[s.toast, { bottom: insets.bottom + 72, backgroundColor: bg }]} testID="toast">
      <Text style={s.text}>{message}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  toast: { position: 'absolute', left: 16, right: 16, padding: 14, borderRadius: 12, zIndex: 1000, elevation: 10 },
  text: { color: '#fff', fontSize: 15, textAlign: 'center' },
});
