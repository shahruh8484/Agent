import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { ScrollView, Text, View } from 'react-native';

import { Button, Card, ErrorText, Loading, styles } from '../components/ui';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { formatDate, formatPrice, monthsLabel, useT } from '../lib/i18n';
import { colors } from '../lib/theme';
import type { Plan } from '../lib/types';
import { useApi } from '../lib/useApi';

type Provider = 'dev' | 'payme' | 'click';

interface CheckoutResult {
  payment_id: number;
  status: 'succeeded' | 'pending';
  confirmation_url: string | null;
}

const PROVIDER_NAMES: Record<string, string> = { payme: 'Payme', click: 'Click' };
const PROVIDER_COLORS: Record<string, string> = { payme: '#00BAC7', click: '#0B70E8' };

export default function Subscription() {
  const { user, refresh } = useAuth();
  const { t, lang } = useT();
  const { data } = useApi<{ currency: string; plans: Plan[]; providers: Provider[] }>('/plans');
  const [busy, setBusy] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!data || !user) return <Loading />;

  /** Payme/Click confirm the payment to our server; poll it for a short while after returning. */
  async function waitForPayment(paymentId: number) {
    setPending(true);
    for (let i = 0; i < 10; i++) {
      const p = await api<{ status: string }>(`/payments/${paymentId}`).catch(() => null);
      if (p && p.status !== 'pending') break;
      await new Promise((r) => setTimeout(r, 2000));
    }
    setPending(false);
  }

  async function buy(plan: Plan, provider: Provider) {
    setBusy(`${plan.id}:${provider}`);
    setError(null);
    try {
      const res = await api<CheckoutResult>('/subscription/checkout', { body: { plan_id: plan.id, provider } });
      if (res.confirmation_url) {
        await WebBrowser.openAuthSessionAsync(res.confirmation_url, 'servio://subscription');
        await waitForPayment(res.payment_id);
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
      <Text style={styles.h1}>{t('subscriptionTitle')}</Text>
      <Text style={[styles.text, { marginBottom: 8 }]}>{t('subscriptionText')}</Text>
      <Text style={{ marginBottom: 16, color: user.subscription_until ? colors.success : colors.danger }}>
        {user.subscription_until
          ? t('subscriptionActive', { d: formatDate(user.subscription_until, lang) })
          : t('subscriptionInactive')}
      </Text>
      {pending ? <Text style={{ color: colors.warning, marginBottom: 12 }}>{t('paymentPending')}</Text> : null}
      {data.plans.map((p) => {
        const months = Math.round(p.days / 30);
        return (
          <Card key={p.id}>
            <Text style={styles.h2}>{monthsLabel(t, months)}</Text>
            <Text style={[styles.h1, { color: colors.primary }]}>{formatPrice(t, p.price)}</Text>
            <Text style={[styles.muted, { marginBottom: 12 }]}>
              {t('perMonth', { v: formatPrice(t, p.price / months) })}
            </Text>
            <View style={{ gap: 8 }}>
              {data.providers.map((provider) => (
                <Button
                  key={provider}
                  title={provider === 'dev' ? t('activate') : t('payWith', { p: PROVIDER_NAMES[provider] })}
                  onPress={() => buy(p, provider)}
                  loading={busy === `${p.id}:${provider}`}
                  disabled={!!busy}
                  style={provider !== 'dev' ? { backgroundColor: PROVIDER_COLORS[provider] } : undefined}
                />
              ))}
            </View>
          </Card>
        );
      })}
      <ErrorText text={error} />
    </ScrollView>
  );
}
