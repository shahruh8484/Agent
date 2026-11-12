import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, ScrollView, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { buyerApi } from '../../api/endpoints';
import type { Order } from '../../api/types';
import { GreenHeader } from '../../components/Screen';
import { Badge, Button, Card, Chip, Empty, Img } from '../../components/ui';
import { ACTIVE_STATUSES, dateTime, money, STATUS_SHORT } from '../../lib/format';
import { repeatOrder } from '../../lib/repeatOrder';
import { useNav } from '../../navigation/types';
import { colors, font, spacing } from '../../theme';

type Filter = 'all' | 'active' | 'done' | 'cancelled';
const FILTERS: { key: Filter; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'active', label: 'Активные' },
  { key: 'done', label: 'Выполненные' },
  { key: 'cancelled', label: 'Отменённые' },
];

function statusColor(o: Order) {
  if (o.status === 'CANCELLED') return colors.red;
  if (ACTIVE_STATUSES.includes(o.status)) return colors.blue;
  return colors.primary;
}

/** История заказов (ТЗ 3.7) */
export function OrdersScreen() {
  const nav = useNav();
  const [filter, setFilter] = useState<Filter>('all');
  const [items, setItems] = useState<Order[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (p: number) => {
      setLoading(true);
      setError(null);
      try {
        const r = await buyerApi.orders(filter, p);
        setItems((prev) => (p === 1 ? r : [...prev, ...r]));
        setHasMore(r.length === 20);
        setPage(p);
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    },
    [filter],
  );

  useFocusEffect(
    useCallback(() => {
      load(1);
    }, [load]),
  );

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <GreenHeader title="Мои заказы" />
      <View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ padding: spacing.lg, paddingBottom: 4 }}>
          {FILTERS.map((f) => (
            <Chip key={f.key} label={f.label} active={filter === f.key} onPress={() => setFilter(f.key)} />
          ))}
        </ScrollView>
      </View>
      <FlatList
        data={items}
        keyExtractor={(o) => String(o.id)}
        contentContainerStyle={{ padding: spacing.lg }}
        refreshControl={<RefreshControl refreshing={false} onRefresh={() => load(1)} />}
        onEndReached={() => !loading && hasMore && load(page + 1)}
        renderItem={({ item: o }) => (
          <Card style={{ marginBottom: spacing.md }} onPress={() => nav.navigate('Tracking', { orderId: o.id })}>
            <View style={{ flexDirection: 'row', alignItems: 'center' }}>
              <Img uri={o.chef?.photoUrl} rounded style={{ width: 40, height: 40 }} />
              <View style={{ flex: 1, marginLeft: 10 }}>
                <Text style={font.h3}>{o.chef?.nick}</Text>
                <Text style={font.tiny}>
                  №{o.id} · {dateTime(o.createdAt)}
                </Text>
              </View>
              <Badge label={STATUS_SHORT[o.status]} color={statusColor(o)} />
            </View>
            <Text style={[font.small, { marginTop: 8 }]} numberOfLines={2}>
              {o.items.map((i) => `${i.name} × ${i.qty}`).join(', ')}
            </Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 10 }}>
              <Text style={font.h3}>{money(o.total)}</Text>
              {!ACTIVE_STATUSES.includes(o.status) ? (
                <Button title="Повторить заказ" icon="repeat" small variant="secondary" onPress={() => repeatOrder(nav, o.chefId, o.items)} />
              ) : (
                <Text style={{ color: colors.primary }}>Отследить →</Text>
              )}
            </View>
          </Card>
        )}
        ListEmptyComponent={loading ? <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} /> : <Empty icon="receipt-outline" title={error ?? 'Заказов пока нет'} text={error ? undefined : 'Самое время попробовать домашнюю еду!'} />}
        ListFooterComponent={loading && items.length ? <ActivityIndicator color={colors.primary} /> : undefined}
      />
    </View>
  );
}
