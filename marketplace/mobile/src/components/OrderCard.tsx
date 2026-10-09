import { router } from 'expo-router';
import { Text, View } from 'react-native';

import { useCities } from '../lib/cities';
import { formatDate, formatPrice, useT } from '../lib/i18n';
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
  const { t, lang } = useT();
  const { cityName } = useCities();
  return (
    <Card onPress={() => router.push({ pathname: '/order/[id]', params: { id: String(o.id) } })}>
      <Text style={styles.muted}>{o.category_name}</Text>
      <Text style={styles.h2}>{o.title}</Text>
      {o.description ? <Text style={styles.text} numberOfLines={2}>{o.description}</Text> : null}
      <View style={[styles.row, { justifyContent: 'space-between', marginTop: 8 }]}>
        <Text style={styles.muted}>{cityName(o.city)}{o.remote ? ` · ${t('online')}` : ''} · {formatDate(o.created_at, lang)}</Text>
        <Text style={styles.text}>{formatPrice(t, o.budget)}</Text>
      </View>
      <View style={[styles.row, { justifyContent: 'space-between', marginTop: 6 }]}>
        {showStatus ? <Text style={{ color: statusColor[o.status] }}>{t(`status_${o.status}`)}</Text> : <View />}
        <Text style={styles.muted}>
          {o.responded ? t('youResponded') : ''}{t('responsesCount', { n: o.responses_count })}
        </Text>
      </View>
    </Card>
  );
}
