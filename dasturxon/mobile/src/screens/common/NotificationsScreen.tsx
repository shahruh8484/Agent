import { Ionicons } from '@expo/vector-icons';
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { buyerApi, chefApi } from '../../api/endpoints';
import type { AppNotification } from '../../api/types';
import { GreenHeader } from '../../components/Screen';
import { Card, Empty } from '../../components/ui';
import { dateTime } from '../../lib/format';
import { useNav } from '../../navigation/types';
import { useAuth } from '../../store/auth';
import { useBuyer } from '../../store/buyer';
import { colors, font, spacing } from '../../theme';

/** Центр уведомлений (покупатель и повар) */
export function NotificationsScreen() {
  const nav = useNav();
  const role = useAuth((s) => s.user?.role);
  const markSeen = useBuyer((s) => s.markNotificationsSeen);
  const [items, setItems] = useState<AppNotification[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = role === 'chef' ? await chefApi.notifications() : await buyerApi.notifications();
      setItems(r);
      if (role === 'buyer' && r.length) markSeen(Math.max(...r.map((n) => n.id)));
    } catch (e) {
      setError(errorMessage(e));
      setItems([]);
    }
  }, [role, markSeen]);

  useEffect(() => {
    load();
  }, [load]);

  const open = (n: AppNotification) => {
    const orderId = (n.data as { orderId?: number } | null)?.orderId;
    if (orderId && role === 'buyer') nav.navigate('Tracking', { orderId });
  };

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <GreenHeader title="Уведомления" back />
      {items === null ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(n) => String(n.id)}
          contentContainerStyle={{ padding: spacing.lg }}
          refreshControl={<RefreshControl refreshing={false} onRefresh={load} />}
          renderItem={({ item: n }) => (
            <Card style={{ marginBottom: spacing.sm }} onPress={() => open(n)}>
              <View style={{ flexDirection: 'row' }}>
                <Ionicons name="notifications" size={20} color={n.read_at ? colors.muted : colors.primary} />
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={font.h3}>{n.title}</Text>
                  <Text style={font.body}>{n.body}</Text>
                  <Text style={[font.tiny, { marginTop: 4 }]}>{dateTime(n.created_at)}</Text>
                </View>
              </View>
            </Card>
          )}
          ListEmptyComponent={<Empty icon="notifications-off-outline" title={error ?? 'Уведомлений пока нет'} />}
        />
      )}
    </View>
  );
}
