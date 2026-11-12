import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import type { FeedItem } from '../api/types';
import { money } from '../lib/format';
import { colors, font, radius, shadow, spacing } from '../theme';
import { Stars } from './Stars';
import { Button, DishTypeBadge, Img } from './ui';

/** Карточка блюда в ленте (ТЗ 3.2) */
export function DishCard({ item, onOpen, onOrder }: { item: FeedItem; onOpen: () => void; onOrder: () => void }) {
  return (
    <Pressable onPress={onOpen} style={({ pressed }) => [s.card, pressed && { opacity: 0.95 }]} testID={`dish-${item.id}`}>
      <View>
        <Img uri={item.photoUrl} style={s.photo} />
        <DishTypeBadge readyNow={item.readyNow} style={s.badge} />
        {item.isVegan ? (
          <View style={s.vegan}>
            <Ionicons name="leaf" size={14} color={colors.primary} />
          </View>
        ) : null}
      </View>
      <View style={s.body}>
        <Text style={font.h3} numberOfLines={1}>
          {item.name}
        </Text>
        <Text style={[font.small, { marginTop: 2 }]} numberOfLines={1}>
          {item.chef.nick} · {item.chef.district} · {item.distanceKm} км
        </Text>
        <View style={[s.rowBetween, { marginTop: spacing.sm }]}>
          <View style={{ flex: 1 }}>
            <Text style={s.price}>{money(item.price)}</Text>
            <Text style={font.tiny}>
              + доставка {item.deliveryFee != null ? money(item.deliveryFee) : '—'}
              {item.etaMinutes ? ` · ~${item.etaMinutes} мин` : ''}
            </Text>
            <View style={{ marginTop: 4 }}>
              <Stars value={item.chef.rating} count={item.chef.reviewsCount} size={13} />
            </View>
          </View>
          <Button title="Заказать" small onPress={onOrder} testID={`order-${item.id}`} />
        </View>
      </View>
    </Pressable>
  );
}

const s = StyleSheet.create({
  card: { backgroundColor: colors.card, borderRadius: radius.lg, marginBottom: spacing.lg, overflow: 'hidden', ...shadow },
  photo: { width: '100%', aspectRatio: 16 / 9 },
  badge: { position: 'absolute', top: 10, left: 10 },
  vegan: { position: 'absolute', top: 10, right: 10, backgroundColor: colors.white, borderRadius: 12, padding: 5 },
  body: { padding: spacing.md },
  rowBetween: { flexDirection: 'row', alignItems: 'flex-end' },
  price: { fontSize: 18, fontWeight: '700', color: colors.text },
});
