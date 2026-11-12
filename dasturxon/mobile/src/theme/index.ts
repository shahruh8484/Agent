import { Platform, type ViewStyle } from 'react-native';

export const colors = {
  primary: '#1FA37A',
  primaryDark: '#178563',
  primaryLight: '#E6F6F0',
  blue: '#2F80ED',
  blueLight: '#E8F1FD',
  yellow: '#F2C94C',
  red: '#E5484D',
  redLight: '#FDECEC',
  orange: '#F2994A',
  bg: '#F2F4F5',
  card: '#FFFFFF',
  text: '#1B1F23',
  textSecondary: '#6B7280',
  muted: '#9CA3AF',
  border: '#E5E7EB',
  gray: '#E9ECEF',
  star: '#FFB800',
  white: '#FFFFFF',
};

export const radius = { sm: 8, md: 12, lg: 16, xl: 24, pill: 999 };
export const spacing = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };

export const shadow: ViewStyle = Platform.select<ViewStyle>({
  ios: { shadowColor: '#000', shadowOpacity: 0.06, shadowRadius: 8, shadowOffset: { width: 0, height: 2 } },
  android: { elevation: 2 },
  default: { boxShadow: '0 2px 8px rgba(0,0,0,0.06)' } as ViewStyle,
}) as ViewStyle;

export const font = {
  h1: { fontSize: 24, fontWeight: '700' as const, color: colors.text },
  h2: { fontSize: 20, fontWeight: '700' as const, color: colors.text },
  h3: { fontSize: 17, fontWeight: '600' as const, color: colors.text },
  body: { fontSize: 15, color: colors.text },
  small: { fontSize: 13, color: colors.textSecondary },
  tiny: { fontSize: 11, color: colors.textSecondary },
};
