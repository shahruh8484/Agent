import { router, useLocalSearchParams } from 'expo-router';
import { ScrollView, Text, View } from 'react-native';

import { Button, Card, Chip, ErrorText, Loading, Stars, styles } from '../../components/ui';
import { formatDate, formatPrice } from '../../lib/api';
import type { Specialist } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function SpecialistScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: s, error } = useApi<Specialist>(`/specialists/${id}`);
  if (!s) return error ? <ErrorText text={error} /> : <Loading />;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.h1}>{s.name}</Text>
      <Stars rating={s.rating} count={s.reviews_count} />
      <Text style={[styles.muted, { marginVertical: 8 }]}>
        {s.city}{s.remote ? ' · работает онлайн' : ''} · опыт {s.experience_years} лет · от {formatPrice(s.price_from)}
      </Text>
      <View style={[styles.wrap, { marginBottom: 8 }]}>
        {s.categories.map((c) => <Chip key={c.id} label={c.name} />)}
      </View>
      {s.bio ? <Card><Text style={styles.text}>{s.bio}</Text></Card> : null}
      <Button
        title="Предложить заказ"
        onPress={() => router.push({ pathname: '/order/new', params: { category_id: String(s.categories[0]?.id ?? '') } })}
        style={{ marginBottom: 20 }}
      />
      <Text style={styles.h2}>Отзывы</Text>
      {s.reviews?.length ? s.reviews.map((r) => (
        <Card key={r.id}>
          <View style={[styles.row, { justifyContent: 'space-between' }]}>
            <Text style={styles.text}>{r.client_name}</Text>
            <Text style={styles.muted}>{'★'.repeat(r.rating)}</Text>
          </View>
          <Text style={styles.muted}>{r.order_title} · {formatDate(r.created_at)}</Text>
          {r.text ? <Text style={[styles.text, { marginTop: 6 }]}>{r.text}</Text> : null}
        </Card>
      )) : <Text style={styles.muted}>Пока нет отзывов</Text>}
    </ScrollView>
  );
}
