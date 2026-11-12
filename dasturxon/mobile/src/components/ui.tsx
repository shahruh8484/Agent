import { Ionicons } from '@expo/vector-icons';
import React, { type ReactNode } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type ImageStyle,
  type StyleProp,
  type TextInputProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { colors, font, radius, shadow, spacing } from '../theme';

export type IconName = React.ComponentProps<typeof Ionicons>['name'];

// ---------- Кнопка ----------
type ButtonVariant = 'primary' | 'secondary' | 'outline' | 'ghost' | 'danger' | 'gray';

export function Button(props: {
  title: string;
  onPress?: () => void;
  variant?: ButtonVariant;
  loading?: boolean;
  disabled?: boolean;
  icon?: IconName;
  small?: boolean;
  style?: StyleProp<ViewStyle>;
  testID?: string;
}) {
  const { variant = 'primary', loading, disabled, small } = props;
  const bg: Record<ButtonVariant, string> = {
    primary: colors.primary,
    secondary: colors.primaryLight,
    outline: 'transparent',
    ghost: 'transparent',
    danger: colors.red,
    gray: colors.gray,
  };
  const fg: Record<ButtonVariant, string> = {
    primary: colors.white,
    secondary: colors.primaryDark,
    outline: colors.primary,
    ghost: colors.textSecondary,
    danger: colors.white,
    gray: colors.text,
  };
  const off = disabled || loading;
  return (
    <Pressable
      testID={props.testID}
      accessibilityRole="button"
      onPress={off ? undefined : props.onPress}
      style={({ pressed }) => [
        styles.btn,
        small && styles.btnSmall,
        { backgroundColor: bg[variant], opacity: off ? 0.5 : pressed ? 0.85 : 1 },
        variant === 'outline' && { borderWidth: 1.5, borderColor: colors.primary },
        props.style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={fg[variant]} />
      ) : (
        <View style={styles.row}>
          {props.icon ? <Ionicons name={props.icon} size={small ? 16 : 18} color={fg[variant]} style={{ marginRight: 6 }} /> : null}
          <Text style={[styles.btnText, small && { fontSize: 14 }, { color: fg[variant] }]}>{props.title}</Text>
        </View>
      )}
    </Pressable>
  );
}

// ---------- Карточка ----------
export function Card({ children, style, onPress }: { children: ReactNode; style?: StyleProp<ViewStyle>; onPress?: () => void }) {
  if (onPress)
    return (
      <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && { opacity: 0.9 }, style]}>
        {children}
      </Pressable>
    );
  return <View style={[styles.card, style]}>{children}</View>;
}

// ---------- Чип ----------
export function Chip(props: { label: string; active?: boolean; onPress?: () => void; color?: string; style?: StyleProp<ViewStyle> }) {
  const c = props.color ?? colors.primary;
  return (
    <Pressable
      onPress={props.onPress}
      style={[
        styles.chip,
        props.active ? { backgroundColor: c, borderColor: c } : { backgroundColor: colors.card, borderColor: colors.border },
        props.style,
      ]}
    >
      <Text style={[styles.chipText, { color: props.active ? colors.white : colors.text }]}>{props.label}</Text>
    </Pressable>
  );
}

// ---------- Бейдж ----------
export function Badge({ label, color = colors.primary, textColor = colors.white, style }: { label: string; color?: string; textColor?: string; style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[styles.badge, { backgroundColor: color }, style]}>
      <Text style={[styles.badgeText, { color: textColor }]}>{label}</Text>
    </View>
  );
}

export function DishTypeBadge({ readyNow, style }: { readyNow: boolean; style?: StyleProp<ViewStyle> }) {
  return readyNow ? <Badge label="Готово сейчас" color={colors.primary} style={style} /> : <Badge label="На заказ" color={colors.blue} style={style} />;
}

// ---------- Поле ввода ----------
export function Field(props: TextInputProps & { label?: string; error?: string | null; hint?: string; counter?: number; right?: ReactNode }) {
  const { label, error, hint, counter, style, right, ...rest } = props;
  return (
    <View style={{ marginBottom: spacing.md }}>
      {label ? <Text style={styles.label}>{label}</Text> : null}
      <View style={[styles.inputWrap, error ? { borderColor: colors.red } : null]}>
        <TextInput placeholderTextColor={colors.muted} style={[styles.input, rest.multiline && { minHeight: 80, textAlignVertical: 'top' }, style as StyleProp<TextStyle>]} {...rest} />
        {right}
      </View>
      <View style={styles.rowBetween}>
        {error ? <Text style={styles.error}>{error}</Text> : hint ? <Text style={styles.hint}>{hint}</Text> : <View />}
        {counter !== undefined && rest.maxLength ? (
          <Text style={styles.hint}>
            {counter}/{rest.maxLength}
          </Text>
        ) : null}
      </View>
    </View>
  );
}

// ---------- Прочее ----------
export function Loader({ style }: { style?: StyleProp<ViewStyle> }) {
  return (
    <View style={[{ flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 }, style]}>
      <ActivityIndicator size="large" color={colors.primary} />
    </View>
  );
}

export function Empty({ icon = 'restaurant-outline', title, text, action }: { icon?: IconName; title: string; text?: string; action?: ReactNode }) {
  return (
    <View style={{ alignItems: 'center', padding: 32 }}>
      <Ionicons name={icon} size={48} color={colors.muted} />
      <Text style={[font.h3, { marginTop: 12, textAlign: 'center' }]}>{title}</Text>
      {text ? <Text style={[font.small, { textAlign: 'center', marginTop: 6 }]}>{text}</Text> : null}
      {action ? <View style={{ marginTop: 16, alignSelf: 'stretch' }}>{action}</View> : null}
    </View>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <View style={[styles.rowBetween, { marginTop: spacing.lg, marginBottom: spacing.sm }]}>
      <Text style={font.h3}>{children}</Text>
      {right}
    </View>
  );
}

export function ListRow(props: { icon?: IconName; title: string; subtitle?: string; right?: ReactNode; onPress?: () => void; danger?: boolean }) {
  return (
    <Pressable onPress={props.onPress} style={({ pressed }) => [styles.listRow, pressed && props.onPress ? { backgroundColor: colors.bg } : null]}>
      {props.icon ? <Ionicons name={props.icon} size={22} color={props.danger ? colors.red : colors.primary} style={{ marginRight: 12 }} /> : null}
      <View style={{ flex: 1 }}>
        <Text style={[font.body, props.danger && { color: colors.red }]}>{props.title}</Text>
        {props.subtitle ? <Text style={font.small}>{props.subtitle}</Text> : null}
      </View>
      {props.right ?? (props.onPress ? <Ionicons name="chevron-forward" size={18} color={colors.muted} /> : null)}
    </Pressable>
  );
}

export function Divider() {
  return <View style={{ height: StyleSheet.hairlineWidth, backgroundColor: colors.border, marginVertical: spacing.sm }} />;
}

export function Img({ uri, style, rounded }: { uri?: string | null; style?: StyleProp<ImageStyle>; rounded?: boolean }) {
  if (!uri)
    return (
      <View style={[{ backgroundColor: colors.gray, alignItems: 'center', justifyContent: 'center' }, style as StyleProp<ViewStyle>, rounded && { borderRadius: 999 }]}>
        <Ionicons name="image-outline" size={24} color={colors.muted} />
      </View>
    );
  return <Image source={{ uri }} style={[{ backgroundColor: colors.gray }, style, rounded && { borderRadius: 999 }]} resizeMode="cover" />;
}

export function Avatar({ uri, size = 48 }: { uri?: string | null; size?: number }) {
  return <Img uri={uri} rounded style={{ width: size, height: size, borderRadius: size / 2, borderWidth: 2, borderColor: colors.white }} />;
}

export function MoneyRow({ label, value, bold, color }: { label: string; value: string; bold?: boolean; color?: string }) {
  return (
    <View style={[styles.rowBetween, { paddingVertical: 4 }]}>
      <Text style={[bold ? font.h3 : font.body, !bold && { color: colors.textSecondary }]}>{label}</Text>
      <Text style={[bold ? font.h3 : font.body, color ? { color } : null]}>{value}</Text>
    </View>
  );
}

export function Checkbox({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: ReactNode }) {
  return (
    <Pressable onPress={() => onChange(!checked)} style={[styles.row, { alignItems: 'flex-start', marginVertical: spacing.sm }]}>
      <Ionicons name={checked ? 'checkbox' : 'square-outline'} size={24} color={checked ? colors.primary : colors.muted} />
      <View style={{ flex: 1, marginLeft: 10 }}>{typeof label === 'string' ? <Text style={font.body}>{label}</Text> : label}</View>
    </Pressable>
  );
}

export function Toggle({ value, onChange, disabled }: { value: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityState={{ checked: value, disabled }}
      onPress={disabled ? undefined : () => onChange(!value)}
      style={[styles.toggle, { backgroundColor: value ? colors.primary : '#C9CED6', opacity: disabled ? 0.5 : 1 }]}
    >
      <View style={[styles.knob, { alignSelf: value ? 'flex-end' : 'flex-start' }]} />
    </Pressable>
  );
}

export const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center' },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  btn: { height: 50, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.lg },
  btnSmall: { height: 38, paddingHorizontal: spacing.md, borderRadius: radius.sm },
  btnText: { fontSize: 16, fontWeight: '600' },
  card: { backgroundColor: colors.card, borderRadius: radius.lg, padding: spacing.lg, ...shadow },
  chip: { paddingHorizontal: 14, height: 34, borderRadius: radius.pill, borderWidth: 1, alignItems: 'center', justifyContent: 'center', marginRight: spacing.sm },
  chipText: { fontSize: 14, fontWeight: '500' },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: radius.sm, alignSelf: 'flex-start' },
  badgeText: { fontSize: 12, fontWeight: '600' },
  label: { fontSize: 13, color: colors.textSecondary, marginBottom: 6, fontWeight: '500' },
  inputWrap: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border, paddingHorizontal: spacing.md },
  input: { flex: 1, minHeight: 48, fontSize: 16, color: colors.text, paddingVertical: 10 },
  error: { color: colors.red, fontSize: 12, marginTop: 4 },
  hint: { color: colors.muted, fontSize: 12, marginTop: 4 },
  listRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 14, paddingHorizontal: spacing.lg, backgroundColor: colors.card },
  toggle: { width: 50, height: 30, borderRadius: 15, padding: 3, justifyContent: 'center' },
  knob: { width: 24, height: 24, borderRadius: 12, backgroundColor: colors.white },
});
