import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { chefApi } from '../../api/endpoints';
import type { ChefBalance, ChefTransaction } from '../../api/types';
import { GreenHeader } from '../../components/Screen';
import { Button, Card, Empty, MoneyRow, SectionTitle } from '../../components/ui';
import { confirmDialog, showAlert } from '../../lib/dialog';
import { dateShort, dateTime, money } from '../../lib/format';
import { toast } from '../../store/toast';
import { colors, font, radius, spacing } from '../../theme';

/** Баланс повара (ТЗ 4.4) */
export function BalanceScreen() {
  const [balance, setBalance] = useState<ChefBalance | null>(null);
  const [tx, setTx] = useState<ChefTransaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [b, t] = await Promise.all([chefApi.balance(), chefApi.transactions()]);
      setBalance(b);
      setTx(t);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const withdraw = async () => {
    setBusy(true);
    try {
      const p = await chefApi.payoutPreview();
      const ok = await confirmDialog(
        'Досрочный вывод',
        `Сумма: ${money(p.amount)}\nКомиссия 1%: ${money(p.fee)}\nНа карту: ${money(p.net)}\n\nВывести сейчас?`,
        'Вывести',
        'Отмена',
      );
      if (ok) {
        await chefApi.payoutConfirm();
        toast('Заявка на вывод создана', 'success');
        load();
      }
    } catch (e) {
      showAlert('Вывод недоступен', errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const w = balance?.week;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <GreenHeader title="Баланс" />
      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 40 }} refreshControl={<RefreshControl refreshing={false} onRefresh={load} />}>
        {error && !balance ? <Empty icon="cloud-offline-outline" title="Не удалось загрузить" text={error} /> : null}
        {balance ? (
          <>
            <View style={{ backgroundColor: colors.primaryDark, borderRadius: radius.lg, padding: spacing.lg }}>
              <Text style={{ color: 'rgba(255,255,255,0.85)' }}>Накоплено за неделю</Text>
              <Text style={{ color: colors.white, fontSize: 32, fontWeight: '800', marginTop: 4 }} testID="balance">
                {money(balance.balance)}
              </Text>
              <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 8 }}>
                <Ionicons name="calendar-outline" size={16} color={colors.white} />
                <Text style={{ color: colors.white, marginLeft: 6 }}>Следующая выплата: {balance.nextPayoutAt ? dateShort(balance.nextPayoutAt) : '—'}</Text>
              </View>
              {Number(balance.pendingEarlyWithdrawal) > 0 ? (
                <Text style={{ color: colors.white, marginTop: 6 }}>В обработке досрочный вывод: {money(Number(balance.pendingEarlyWithdrawal))}</Text>
              ) : null}
              <Button title="Досрочный вывод (комиссия 1%)" variant="secondary" small onPress={withdraw} loading={busy} disabled={balance.balance <= 0} style={{ marginTop: 14 }} />
            </View>

            <SectionTitle>Эта неделя</SectionTitle>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginHorizontal: -4 }}>
              <Tile label="Заказов" value={String(w?.orders ?? 0)} />
              <Tile label="Средний рейтинг" value={w?.avgRating ? `★ ${Number(w.avgRating).toFixed(1)}` : '—'} />
              <Tile label="Средний чек" value={money(w?.avgCheck ?? 0)} />
              <Tile label="Оборот" value={money(w?.turnover ?? 0)} />
            </View>

            <SectionTitle>История операций</SectionTitle>
            {!tx.length ? <Text style={font.small}>Операций пока нет</Text> : null}
            <Card style={{ padding: 0, overflow: 'hidden' }}>
              {tx.map((t, i) => {
                const neg = t.amount < 0;
                const payout = t.type === 'payout';
                return (
                  <View key={`${t.type}-${t.id}`} style={{ flexDirection: 'row', alignItems: 'center', padding: spacing.md, borderTopWidth: i ? 1 : 0, borderTopColor: colors.border, backgroundColor: payout ? colors.blueLight : colors.card }}>
                    <Ionicons
                      name={payout ? 'arrow-up-circle' : neg ? 'return-down-back' : 'add-circle'}
                      size={24}
                      color={payout ? colors.blue : neg ? colors.red : colors.primary}
                    />
                    <View style={{ flex: 1, marginLeft: 10 }}>
                      <Text style={font.body}>{t.title}</Text>
                      <Text style={font.tiny}>
                        {dateTime(t.created_at)}
                        {t.commission || t.tax ? ` · комиссия ${money(t.commission)} · налог ${money(t.tax)}` : ''}
                      </Text>
                    </View>
                    <Text style={[font.h3, { color: neg ? colors.red : payout ? colors.blue : colors.text }]}>{neg ? money(t.amount) : (payout ? '' : '+') + money(t.amount)}</Text>
                  </View>
                );
              })}
            </Card>
            <View style={{ marginTop: 12 }}>
              <MoneyRow label="Комиссия платформы" value="20%" />
              <MoneyRow label="Налог (удерживается)" value="4%" />
            </View>
          </>
        ) : null}
      </ScrollView>
    </View>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <View style={{ width: '50%', padding: 4 }}>
      <Card style={{ padding: spacing.md }}>
        <Text style={font.small}>{label}</Text>
        <Text style={[font.h3, { marginTop: 4 }]}>{value}</Text>
      </Card>
    </View>
  );
}
