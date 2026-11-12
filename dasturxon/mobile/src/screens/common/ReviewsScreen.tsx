import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { chefApi, publicApi } from '../../api/endpoints';
import type { Review } from '../../api/types';
import { GreenHeader } from '../../components/Screen';
import { Stars } from '../../components/Stars';
import { Card, Empty } from '../../components/ui';
import { dateShort, tagLabel } from '../../lib/format';
import type { RootStackParamList } from '../../navigation/types';
import { colors, font, spacing } from '../../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'ChefReviews'>;

/** Все отзывы повара: для покупателя — публичные, для повара (без chefId) — свои */
export function ReviewsScreen({ route }: Props) {
  const chefId = route.params?.chefId;
  const [items, setItems] = useState<Review[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    async (p: number) => {
      setLoading(true);
      try {
        const r = chefId ? await publicApi.reviews(chefId, p) : await chefApi.reviews(p);
        setItems((prev) => (p === 1 ? r : [...prev, ...r]));
        setHasMore(r.length === 20);
        setPage(p);
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    },
    [chefId],
  );

  useEffect(() => {
    load(1);
  }, [load]);

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <GreenHeader back title={route.params?.nick ? `Отзывы · ${route.params.nick}` : 'Отзывы'} />
      <FlatList
        data={items}
        keyExtractor={(r, i) => String(r.id ?? i)}
        contentContainerStyle={{ padding: spacing.lg }}
        renderItem={({ item: r }) => (
          <Card style={{ marginBottom: spacing.sm }}>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
              <Text style={font.h3}>{r.buyerName ?? (r.order_id ? `Заказ №${r.order_id}` : 'Покупатель')}</Text>
              <Text style={font.tiny}>{dateShort(r.createdAt ?? r.created_at)}</Text>
            </View>
            <Stars value={r.rating} size={13} />
            {r.tags?.length ? <Text style={[font.small, { marginTop: 4 }]}>{r.tags.map(tagLabel).join(' · ')}</Text> : null}
            {r.comment ? <Text style={[font.body, { marginTop: 4 }]}>{r.comment}</Text> : null}
          </Card>
        )}
        onEndReached={() => !loading && hasMore && items.length && load(page + 1)}
        ListEmptyComponent={loading ? <ActivityIndicator color={colors.primary} /> : <Empty icon="chatbubble-outline" title={error ?? 'Отзывов пока нет'} />}
      />
    </View>
  );
}
