import { router } from 'expo-router';
import { Text, View } from 'react-native';

import { formatPrice } from '../lib/api';
import type { Specialist } from '../lib/types';
import { Card, Stars, styles } from './ui';

export function SpecialistCard({ s }: { s: Specialist }) {
  return (
    <Card onPress={() => router.push({ pathname: '/specialist/[id]', params: { id: String(s.id) } })}>
      <Text style={styles.h2}>{s.name}</Text>
      <Stars rating={s.rating} count={s.reviews_count} />
      <Text style={[styles.muted, { marginTop: 4 }]} numberOfLines={1}>
        {s.categories.map((c) => c.name).join(', ')}
      </Text>
      {s.bio ? <Text style={[styles.text, { marginTop: 6 }]} numberOfLines={2}>{s.bio}</Text> : null}
      <View style={[styles.row, { marginTop: 6, justifyContent: 'space-between' }]}>
        <Text style={styles.muted}>{s.city}{s.remote ? ' · онлайн' : ''}</Text>
        <Text style={styles.text}>от {formatPrice(s.price_from)}</Text>
      </View>
    </Card>
  );
}
