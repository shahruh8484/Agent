import { router, useLocalSearchParams } from 'expo-router';
import { ScrollView, Text, View } from 'react-native';

import { Button, Card, Chip, ErrorText, Loading, Stars, styles } from '../../components/ui';
import { useCities } from '../../lib/cities';
import { formatDate, formatPrice, useT } from '../../lib/i18n';
import type { Specialist } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function SpecialistScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t, lang } = useT();
  const { cityName } = useCities();
  const { data: s, error } = useApi<Specialist>(`/specialists/${id}`);
  if (!s) return error ? <ErrorText text={error} /> : <Loading />;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.h1}>{s.name}</Text>
      <Stars rating={s.rating} count={s.reviews_count} />
      <Text style={[styles.muted, { marginVertical: 8 }]}>
        {cityName(s.city)}{s.remote ? ` · ${t('worksOnline')}` : ''} · {t('experience', { n: s.experience_years })}
        {s.price_from != null ? ` · ${t('priceFrom', { price: formatPrice(t, s.price_from) })}` : ''}
      </Text>
      <View style={[styles.wrap, { marginBottom: 8 }]}>
        {s.categories.map((c) => <Chip key={c.id} label={c.name} />)}
      </View>
      {s.bio ? <Card><Text style={styles.text}>{s.bio}</Text></Card> : null}
      <Button
        title={t('offerOrder')}
        onPress={() => router.push({ pathname: '/order/new', params: { category_id: String(s.categories[0]?.id ?? '') } })}
        style={{ marginBottom: 20 }}
      />
      <Text style={styles.h2}>{t('reviews')}</Text>
      {s.reviews?.length ? s.reviews.map((r) => (
        <Card key={r.id}>
          <View style={[styles.row, { justifyContent: 'space-between' }]}>
            <Text style={styles.text}>{r.client_name}</Text>
            <Text style={styles.muted}>{'★'.repeat(r.rating)}</Text>
          </View>
          <Text style={styles.muted}>{r.order_title} · {formatDate(r.created_at, lang)}</Text>
          {r.text ? <Text style={[styles.text, { marginTop: 6 }]}>{r.text}</Text> : null}
        </Card>
      )) : <Text style={styles.muted}>{t('noReviewsYet')}</Text>}
    </ScrollView>
  );
}
