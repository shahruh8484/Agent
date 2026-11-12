import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { money, plural } from '../../lib/format';
import { useNav } from '../../navigation/types';
import { cartCount, cartTotal, useCart } from '../../store/cart';
import { colors, radius, shadow } from '../../theme';

/** Плавающая плашка корзины в ленте */
export function CartBar() {
  const nav = useNav();
  const lines = useCart((s) => s.lines);
  const chefId = useCart((s) => s.chefId);
  const chefNick = useCart((s) => s.chefNick);
  const n = cartCount(lines);
  if (!n || !chefId) return null;
  return (
    <Pressable style={s.bar} onPress={() => nav.navigate('Chef', { chefId })} testID="cart-bar">
      <View style={s.count}>
        <Text style={s.countText}>{n}</Text>
      </View>
      <View style={{ flex: 1 }}>
        <Text style={s.title}>
          Корзина · {n} {plural(n, 'блюдо', 'блюда', 'блюд')}
        </Text>
        <Text style={s.sub}>у повара {chefNick}</Text>
      </View>
      <Text style={s.total}>{money(cartTotal(lines))}</Text>
      <Ionicons name="chevron-forward" size={20} color={colors.white} />
    </Pressable>
  );
}

const s = StyleSheet.create({
  bar: { position: 'absolute', left: 16, right: 16, bottom: 12, backgroundColor: colors.primaryDark, borderRadius: radius.lg, padding: 12, flexDirection: 'row', alignItems: 'center', ...shadow },
  count: { width: 32, height: 32, borderRadius: 16, backgroundColor: colors.white, alignItems: 'center', justifyContent: 'center', marginRight: 10 },
  countText: { color: colors.primaryDark, fontWeight: '700' },
  title: { color: colors.white, fontWeight: '600', fontSize: 15 },
  sub: { color: 'rgba(255,255,255,0.8)', fontSize: 12 },
  total: { color: colors.white, fontWeight: '700', fontSize: 16, marginRight: 4 },
});
