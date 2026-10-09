import { router } from 'expo-router';
import { Text, View } from 'react-native';

import { formatDate, formatPrice, STATUS_LABELS } from '../lib/api';
import { colors } from '../lib/theme';
import type { Order } from '../lib/types';
import { Card, styles } from './ui';

const statusColor: Record<string, string> = {
  open: colors.primary,
  in_progress: colors.warning,
  completed: colors.success,
  closed: colors.muted,
};

export function OrderCard({ o, showStatus = true }: { o: Order; showStatus?: boolean }) {
  return (
    <Card onPress={() => router.push({ pathname: '/order/[id]', params: { id: String(o.id) } })}>
      <Text style={styles.muted}>{o.category_name}</Text>
      <Text style={styles.h2}>{o.title}</Text>
      {o.description ? <Text style={styles.text} numberOfLines={2}>{o.description}</Text> : null}
      <View style={[styles.row, { justifyContent: 'space-between', marginTop: 8 }]}>
        <Text style={styles.muted}>{o.city}{o.remote ? ' · онлайн' : ''} · {formatDate(o.created_at)}</Text>
        <Text style={styles.text}>{formatPrice(o.budget)}</Text>
      </View>
      <View style={[styles.row, { justifyContent: 'space-between', marginTop: 6 }]}>
        {showStatus ? <Text style={{ color: statusColor[o.status] }}>{STATUS_LABELS[o.status]}</Text> : <View />}
        <Text style={styles.muted}>
          {o.responded ? 'Вы откликнулись · ' : ''}откликов: {o.responses_count}
        </Text>
      </View>
    </Card>
  );
}
