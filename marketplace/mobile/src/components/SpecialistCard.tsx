import { router } from 'expo-router';
import { Text, View } from 'react-native';

import { useCities } from '../lib/cities';
import { formatPrice, useT } from '../lib/i18n';
import type { Specialist } from '../lib/types';
import { Card, Stars, styles } from './ui';

export function SpecialistCard({ s }: { s: Specialist }) {
  const { t } = useT();
  const { cityName } = useCities();
  return (
    <Card onPress={() => router.push({ pathname: '/specialist/[id]', params: { id: String(s.id) } })}>
      <Text style={styles.h2}>{s.name}</Text>
      <Stars rating={s.rating} count={s.reviews_count} />
      <Text style={[styles.muted, { marginTop: 4 }]} numberOfLines={1}>
        {s.categories.map((c) => c.name).join(', ')}
      </Text>
      {s.bio ? <Text style={[styles.text, { marginTop: 6 }]} numberOfLines={2}>{s.bio}</Text> : null}
      <View style={[styles.row, { marginTop: 6, justifyContent: 'space-between' }]}>
        <Text style={styles.muted}>{cityName(s.city)}{s.remote ? ` · ${t('online')}` : ''}</Text>
        <Text style={styles.text}>{s.price_from != null ? t('priceFrom', { price: formatPrice(t, s.price_from) }) : formatPrice(t, null)}</Text>
      </View>
    </Card>
  );
}
