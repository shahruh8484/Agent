import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import React, { type ReactNode } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { colors, spacing } from '../theme';

/** Зелёная шапка экрана (как в макетах ТЗ) */
export function GreenHeader(props: { title?: string; back?: boolean; right?: ReactNode; children?: ReactNode; onBack?: () => void }) {
  const insets = useSafeAreaInsets();
  const nav = useNavigation();
  return (
    <View style={[s.header, { paddingTop: insets.top + 10 }]}>
      <View style={s.headerRow}>
        {props.back ? (
          <Pressable onPress={props.onBack ?? (() => nav.goBack())} hitSlop={10} style={{ marginRight: 8 }} accessibilityLabel="Назад">
            <Ionicons name="chevron-back" size={26} color={colors.white} />
          </Pressable>
        ) : null}
        {props.title ? (
          <Text style={s.title} numberOfLines={1}>
            {props.title}
          </Text>
        ) : (
          <View style={{ flex: 1 }} />
        )}
        {props.right}
      </View>
      {props.children}
    </View>
  );
}

/** Экран с прокруткой на светло-сером фоне */
export function Screen(props: {
  title?: string;
  back?: boolean;
  right?: ReactNode;
  children: ReactNode;
  scroll?: boolean;
  footer?: ReactNode;
  contentStyle?: StyleProp<ViewStyle>;
  header?: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  const scroll = props.scroll !== false;
  const body = scroll ? (
    <ScrollView contentContainerStyle={[{ padding: spacing.lg, paddingBottom: spacing.xl * 2 }, props.contentStyle]} keyboardShouldPersistTaps="handled">
      {props.children}
    </ScrollView>
  ) : (
    <View style={[{ flex: 1 }, props.contentStyle]}>{props.children}</View>
  );
  return (
    <View style={s.root}>
      {props.header ?? (props.title !== undefined || props.back ? <GreenHeader title={props.title} back={props.back} right={props.right} /> : null)}
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        {body}
        {props.footer ? <View style={[s.footer, { paddingBottom: Math.max(insets.bottom, 12) }]}>{props.footer}</View> : null}
      </KeyboardAvoidingView>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  header: { backgroundColor: colors.primary, paddingHorizontal: spacing.lg, paddingBottom: 14 },
  headerRow: { flexDirection: 'row', alignItems: 'center', minHeight: 32 },
  title: { flex: 1, color: colors.white, fontSize: 20, fontWeight: '700' },
  footer: { backgroundColor: colors.card, paddingHorizontal: spacing.lg, paddingTop: 12, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.border },
});
