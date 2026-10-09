import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { ScrollView, Text } from 'react-native';

import { Button, Card, ErrorText, Loading, styles } from '../components/ui';
import { api, formatDate } from '../lib/api';
import { useAuth } from '../lib/auth';
import { colors } from '../lib/theme';
import type { Plan } from '../lib/types';
import { useApi } from '../lib/useApi';

interface CheckoutResult {
  status: 'succeeded' | 'pending';
  confirmation_url: string | null;
}

export default function Subscription() {
  const { user, refresh } = useAuth();
  const { data } = useApi<{ currency: string; plans: Plan[] }>('/plans');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!data || !user) return <Loading />;

  async function buy(plan: Plan) {
    setBusy(plan.id);
    setError(null);
    try {
      const res = await api<CheckoutResult>('/subscription/checkout', { body: { plan_id: plan.id } });
      if (res.confirmation_url) {
        // The payment page redirects back to servio://subscription; the webhook activates the plan.
        await WebBrowser.openAuthSessionAsync(res.confirmation_url, 'servio://subscription');
      }
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Text style={styles.h1}>Подписка специалиста</Text>
      <Text style={[styles.text, { marginBottom: 8 }]}>
        Неограниченные отклики на заказы во всех ваших услугах. Без оплаты за каждый отклик.
      </Text>
      <Text style={{ marginBottom: 16, color: user.subscription_until ? colors.success : colors.danger }}>
        {user.subscription_until ? `Активна до ${formatDate(user.subscription_until)}` : 'Подписка не активна'}
      </Text>
      {data.plans.map((p) => (
        <Card key={p.id}>
          <Text style={styles.h2}>{p.title}</Text>
          <Text style={[styles.h1, { color: colors.primary }]}>{p.price.toLocaleString('ru-RU')} ₽</Text>
          <Text style={[styles.muted, { marginBottom: 12 }]}>
            {Math.round(p.price / (p.days / 30)).toLocaleString('ru-RU')} ₽ в месяц
          </Text>
          <Button title={user.subscription_until ? 'Продлить' : 'Оформить'} onPress={() => buy(p)} loading={busy === p.id} disabled={!!busy} />
        </Card>
      ))}
      <ErrorText text={error} />
    </ScrollView>
  );
}
