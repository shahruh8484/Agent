import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Alert, ScrollView, Text, View } from 'react-native';

import { Button, Card, Chip, ErrorText, Field, Loading, Stars, styles } from '../../components/ui';
import { api, ApiError } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { useCities } from '../../lib/cities';
import { formatDate, formatPrice, useT } from '../../lib/i18n';
import { colors } from '../../lib/theme';
import type { Order, OrderResponse } from '../../lib/types';
import { useApi } from '../../lib/useApi';

const openChat = (id: number) => router.push({ pathname: '/chat/[id]', params: { id: String(id) } });

export default function OrderScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { t, lang } = useT();
  const { cityName } = useCities();
  const { data: order, error, reload } = useApi<Order>(`/orders/${id}`);
  if (!order) return error ? <ErrorText text={error} /> : <Loading />;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.muted}>{order.category_name}</Text>
      <Text style={styles.h1}>{order.title}</Text>
      <Text style={{ color: colors.primary, marginBottom: 8 }}>{t(`status_${order.status}`)}</Text>
      <Card>
        {order.description ? <Text style={[styles.text, { marginBottom: 8 }]}>{order.description}</Text> : null}
        <Text style={styles.muted}>{t('budgetLabel', { v: formatPrice(t, order.budget) })}</Text>
        {order.when_text ? <Text style={styles.muted}>{t('whenLabel', { v: order.when_text })}</Text> : null}
        <Text style={styles.muted}>{t('whereLabel', { v: cityName(order.city) + (order.remote ? t('remoteSuffix') : '') })}</Text>
        <Text style={styles.muted}>{t('clientLabel', { v: order.client_name })} · {formatDate(order.created_at, lang)}</Text>
      </Card>
      {order.is_mine ? <ClientView order={order} reload={reload} /> : <SpecialistView order={order} reload={reload} />}
    </ScrollView>
  );
}

function ClientView({ order, reload }: { order: Order; reload: () => Promise<void> }) {
  const { t } = useT();
  const { data: responses } = useApi<OrderResponse[]>(`/orders/${order.id}/responses`);
  const [rating, setRating] = useState(5);
  const [reviewText, setReviewText] = useState('');
  const [error, setError] = useState<string | null>(null);

  async function act(path: string, body: unknown = {}) {
    setError(null);
    try {
      await api(`/orders/${order.id}/${path}`, { body });
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const confirm = (text: string, path: string) =>
    Alert.alert(text, undefined, [
      { text: t('no'), style: 'cancel' },
      { text: t('yes'), onPress: () => act(path) },
    ]);

  return (
    <View>
      <ErrorText text={error} />
      {order.status === 'in_progress' ? (
        <Button title={t('markDone')} onPress={() => confirm(t('markDoneConfirm'), 'complete')} style={{ marginBottom: 12 }} />
      ) : null}
      {order.status === 'completed' && !order.has_review ? (
        <Card>
          <Text style={styles.h2}>{t('rateSpecialist')}</Text>
          <View style={styles.wrap}>
            {[1, 2, 3, 4, 5].map((n) => <Chip key={n} label={'★'.repeat(n)} selected={rating === n} onPress={() => setRating(n)} />)}
          </View>
          <Field value={reviewText} onChangeText={setReviewText} multiline placeholder={t('reviewPlaceholder')} />
          <Button title={t('leaveReview')} onPress={() => act('review', { rating, text: reviewText })} />
        </Card>
      ) : null}

      <Text style={[styles.h2, { marginTop: 8 }]}>{t('responses', { n: responses?.length ?? 0 })}</Text>
      {responses?.length === 0 ? <Text style={styles.muted}>{t('responsesSoon')}</Text> : null}
      {responses?.map((r) => {
        const chosen = order.specialist_id === r.specialist.id;
        return (
          <Card key={r.id} style={chosen && { borderColor: colors.success, borderWidth: 2 }}>
            <Text
              style={[styles.h2, { color: colors.primary }]}
              onPress={() => router.push({ pathname: '/specialist/[id]', params: { id: String(r.specialist.id) } })}
            >
              {r.specialist.name}
            </Text>
            <Stars rating={r.specialist.rating} count={r.specialist.reviews_count} />
            <Text style={[styles.text, { marginVertical: 8 }]}>{r.message}</Text>
            <Text style={styles.muted}>{t('price', { v: formatPrice(t, r.price) })}</Text>
            {chosen ? <Text style={{ color: colors.success, marginTop: 4 }}>{t('chosen')}</Text> : null}
            <View style={[styles.row, { marginTop: 12, gap: 8 }]}>
              <Button title={t('write')} variant="secondary" onPress={() => openChat(r.chat_id)} style={{ flex: 1 }} />
              {order.status === 'open' ? (
                <Button title={t('choose')} onPress={() => act('choose', { response_id: r.id })} style={{ flex: 1 }} />
              ) : null}
            </View>
          </Card>
        );
      })}
      {order.status === 'open' || order.status === 'in_progress' ? (
        <Button title={t('cancelOrder')} variant="secondary" onPress={() => confirm(t('cancelOrderConfirm'), 'close')} style={{ marginTop: 8 }} />
      ) : null}
    </View>
  );
}

function SpecialistView({ order, reload }: { order: Order; reload: () => Promise<void> }) {
  const { user } = useAuth();
  const { t } = useT();
  const [message, setMessage] = useState('');
  const [price, setPrice] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (order.my_response) {
    return (
      <Card>
        <Text style={styles.h2}>{t('yourResponse')}</Text>
        <Text style={styles.text}>{order.my_response.message}</Text>
        <Text style={[styles.muted, { marginVertical: 6 }]}>{t('price', { v: formatPrice(t, order.my_response.price) })}</Text>
        {order.specialist_id === user?.id ? <Text style={{ color: colors.success, marginBottom: 8 }}>{t('clientChoseYou')}</Text> : null}
        {order.chat_id ? <Button title={t('openChat')} onPress={() => openChat(order.chat_id!)} /> : null}
      </Card>
    );
  }
  if (order.status !== 'open') return <Text style={styles.muted}>{t('noMoreResponses')}</Text>;
  if (!user?.has_specialist_profile) {
    return <Button title={t('fillProfileToRespond')} onPress={() => router.push('/specialist-profile')} />;
  }

  async function respond() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ chat_id: number }>(`/orders/${order.id}/responses`, {
        body: { message: message.trim(), price: price ? Number(price.replace(/\D/g, '')) : null },
      });
      await reload();
      openChat(res.chat_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) {
        router.push('/subscription');
      } else {
        setError((e as Error).message);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <Text style={styles.h2}>{t('respond')}</Text>
      <Field value={message} onChangeText={setMessage} multiline placeholder={t('respondPlaceholder')} />
      <Field label={t('yourPrice')} value={price} onChangeText={setPrice} keyboardType="number-pad" />
      <ErrorText text={error} />
      <Button title={t('sendResponse')} onPress={respond} loading={busy} disabled={!message.trim()} />
      {!user.subscription_until ? (
        <Text style={[styles.muted, { marginTop: 8 }]}>{t('needSubscription')}</Text>
      ) : null}
    </Card>
  );
}
