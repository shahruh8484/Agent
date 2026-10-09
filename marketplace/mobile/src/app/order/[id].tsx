import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Alert, ScrollView, Text, View } from 'react-native';

import { Button, Card, Chip, ErrorText, Field, Loading, Stars, styles } from '../../components/ui';
import { api, ApiError, formatDate, formatPrice, STATUS_LABELS } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { colors } from '../../lib/theme';
import type { Order, OrderResponse } from '../../lib/types';
import { useApi } from '../../lib/useApi';

const openChat = (id: number) => router.push({ pathname: '/chat/[id]', params: { id: String(id) } });

export default function OrderScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data: order, error, reload } = useApi<Order>(`/orders/${id}`);
  if (!order) return error ? <ErrorText text={error} /> : <Loading />;

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.muted}>{order.category_name}</Text>
      <Text style={styles.h1}>{order.title}</Text>
      <Text style={{ color: colors.primary, marginBottom: 8 }}>{STATUS_LABELS[order.status]}</Text>
      <Card>
        {order.description ? <Text style={[styles.text, { marginBottom: 8 }]}>{order.description}</Text> : null}
        <Text style={styles.muted}>Бюджет: {formatPrice(order.budget)}</Text>
        {order.when_text ? <Text style={styles.muted}>Когда: {order.when_text}</Text> : null}
        <Text style={styles.muted}>Где: {order.city}{order.remote ? ', можно онлайн' : ''}</Text>
        <Text style={styles.muted}>Клиент: {order.client_name} · {formatDate(order.created_at)}</Text>
      </Card>
      {order.is_mine ? <ClientView order={order} reload={reload} /> : <SpecialistView order={order} reload={reload} />}
    </ScrollView>
  );
}

function ClientView({ order, reload }: { order: Order; reload: () => Promise<void> }) {
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
      { text: 'Нет', style: 'cancel' },
      { text: 'Да', onPress: () => act(path) },
    ]);

  return (
    <View>
      <ErrorText text={error} />
      {order.status === 'in_progress' ? (
        <Button title="Работа выполнена" onPress={() => confirm('Отметить заказ выполненным?', 'complete')} style={{ marginBottom: 12 }} />
      ) : null}
      {order.status === 'completed' && !order.has_review ? (
        <Card>
          <Text style={styles.h2}>Оцените исполнителя</Text>
          <View style={styles.wrap}>
            {[1, 2, 3, 4, 5].map((n) => <Chip key={n} label={'★'.repeat(n)} selected={rating === n} onPress={() => setRating(n)} />)}
          </View>
          <Field value={reviewText} onChangeText={setReviewText} multiline placeholder="Расскажите, как всё прошло" />
          <Button title="Оставить отзыв" onPress={() => act('review', { rating, text: reviewText })} />
        </Card>
      ) : null}

      <Text style={[styles.h2, { marginTop: 8 }]}>Отклики ({responses?.length ?? 0})</Text>
      {responses?.length === 0 ? <Text style={styles.muted}>Специалисты скоро откликнутся — мы пришлём уведомление.</Text> : null}
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
            <Text style={styles.muted}>Цена: {formatPrice(r.price)}</Text>
            {chosen ? <Text style={{ color: colors.success, marginTop: 4 }}>Выбран исполнителем</Text> : null}
            <View style={[styles.row, { marginTop: 12, gap: 8 }]}>
              <Button title="Написать" variant="secondary" onPress={() => openChat(r.chat_id)} style={{ flex: 1 }} />
              {order.status === 'open' ? (
                <Button title="Выбрать" onPress={() => act('choose', { response_id: r.id })} style={{ flex: 1 }} />
              ) : null}
            </View>
          </Card>
        );
      })}
      {order.status === 'open' || order.status === 'in_progress' ? (
        <Button title="Отменить заказ" variant="secondary" onPress={() => confirm('Отменить заказ?', 'close')} style={{ marginTop: 8 }} />
      ) : null}
    </View>
  );
}

function SpecialistView({ order, reload }: { order: Order; reload: () => Promise<void> }) {
  const { user } = useAuth();
  const [message, setMessage] = useState('');
  const [price, setPrice] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (order.my_response) {
    return (
      <Card>
        <Text style={styles.h2}>Ваш отклик</Text>
        <Text style={styles.text}>{order.my_response.message}</Text>
        <Text style={[styles.muted, { marginVertical: 6 }]}>Цена: {formatPrice(order.my_response.price)}</Text>
        {order.specialist_id === user?.id ? <Text style={{ color: colors.success, marginBottom: 8 }}>Клиент выбрал вас!</Text> : null}
        {order.chat_id ? <Button title="Открыть чат с клиентом" onPress={() => openChat(order.chat_id!)} /> : null}
      </Card>
    );
  }
  if (order.status !== 'open') return <Text style={styles.muted}>Заказ больше не принимает отклики.</Text>;
  if (!user?.has_specialist_profile) {
    return <Button title="Заполнить анкету, чтобы откликаться" onPress={() => router.push('/specialist-profile')} />;
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
      <Text style={styles.h2}>Откликнуться</Text>
      <Field value={message} onChangeText={setMessage} multiline placeholder="Расскажите, как вы решите задачу, и задайте уточняющие вопросы" />
      <Field label="Ваша цена, ₽" value={price} onChangeText={setPrice} keyboardType="number-pad" />
      <ErrorText text={error} />
      <Button title="Отправить отклик" onPress={respond} loading={busy} disabled={!message.trim()} />
      {!user.subscription_until ? (
        <Text style={[styles.muted, { marginTop: 8 }]}>Для отклика нужна активная подписка.</Text>
      ) : null}
    </Card>
  );
}
