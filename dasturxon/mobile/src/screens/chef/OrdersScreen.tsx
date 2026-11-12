import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useEffect, useState } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { errorMessage } from '../../api/client';
import { chefApi } from '../../api/endpoints';
import { onSocket } from '../../api/socket';
import type { ChefSummary, Order } from '../../api/types';
import { Badge, Button, Card, Empty, Toggle } from '../../components/ui';
import { confirmDialog, showAlert } from '../../lib/dialog';
import { countdown, money, STATUS_SHORT, time } from '../../lib/format';
import { useChef } from '../../store/chef';
import { toast } from '../../store/toast';
import { colors, font, radius, spacing } from '../../theme';

/** Заказы повара (ТЗ 4.2): онлайн-переключатель, новые (с таймером), активные, выполненные сегодня */
export function ChefOrdersScreen() {
  const insets = useSafeAreaInsets();
  const profile = useChef((s) => s.profile);
  const setProfile = useChef((s) => s.setProfile);
  const [fresh, setFresh] = useState<Order[]>([]);
  const [active, setActive] = useState<Order[]>([]);
  const [today, setToday] = useState<Order[]>([]);
  const [summary, setSummary] = useState<ChefSummary | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());

  const load = useCallback(async () => {
    try {
      const [n, a, t, s] = await Promise.all([chefApi.orders('new'), chefApi.orders('active'), chefApi.orders('today'), chefApi.summary()]);
      setFresh(n);
      setActive(a);
      setToday(t);
      setSummary(s);
    } catch {
      /* следующий опрос */
    } finally {
      setRefreshing(false);
    }
  }, []);

  // Живые обновления: сокет + опрос каждые 5 с
  useFocusEffect(
    useCallback(() => {
      load();
      const off1 = onSocket('order:update', () => load());
      const off2 = onSocket<{ type: string }>('notification', (n) => {
        load();
        if (n?.type === 'new_order') toast('Новый заказ!', 'success');
      });
      const poll = setInterval(load, 5000);
      return () => {
        off1();
        off2();
        clearInterval(poll);
      };
    }, [load]),
  );

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const setOnline = async (online: boolean) => {
    setToggling(true);
    try {
      await chefApi.setStatus(online);
      if (profile) setProfile({ ...profile, isOnline: online });
    } catch (e) {
      showAlert('Не удалось изменить статус', errorMessage(e));
    } finally {
      setToggling(false);
    }
  };

  const act = async (id: number, fn: () => Promise<unknown>, okText: string) => {
    setBusyId(id);
    try {
      await fn();
      toast(okText, 'success');
      await load();
    } catch (e) {
      showAlert('Ошибка', errorMessage(e));
      load();
    } finally {
      setBusyId(null);
    }
  };

  const decline = async (o: Order) => {
    const ok = await confirmDialog('Отказаться от заказа?', 'Деньги вернутся покупателю. Частые отказы снижают рейтинг.', 'Отказать', 'Назад', true);
    if (ok) act(o.id, () => chefApi.decline(o.id, 'Повар не может принять заказ'), 'Заказ отклонён');
  };

  const online = !!profile?.isOnline;

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <View style={[s.header, { paddingTop: insets.top + 10 }]}>
        <View style={s.rowBetween}>
          <View>
            <Text style={s.title}>Заказы</Text>
            <Text style={s.sub}>{online ? 'Вы онлайн — принимаете заказы' : 'Вы офлайн — заказы не поступают'}</Text>
          </View>
          <View style={{ alignItems: 'center' }}>
            <Toggle value={online} onChange={setOnline} disabled={toggling} onColor={colors.primaryDark} offColor="rgba(0,0,0,0.25)" />
            <Text style={[s.sub, { marginTop: 4 }]}>{online ? 'Онлайн' : 'Офлайн'}</Text>
          </View>
        </View>
        {summary ? (
          <View style={s.summary}>
            <Stat value={String(summary.new)} label="новых" />
            <Stat value={String(summary.active)} label="активных" />
            <Stat value={String(summary.done_today)} label="сегодня" />
            <Stat value={money(summary.earned_today)} label="заработано" />
          </View>
        ) : null}
        {profile?.isBlocked ? <Text style={[s.sub, { marginTop: 8, color: '#FFE3E3' }]}>Аккаунт временно заблокирован. Обратитесь в поддержку.</Text> : null}
      </View>

      <ScrollView contentContainerStyle={{ padding: spacing.lg, paddingBottom: 40 }} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(); }} />}>
        {fresh.map((o) => {
          const left = o.expiresAt ? new Date(o.expiresAt).getTime() - now : 0;
          return (
            <Card key={o.id} style={s.newCard}>
              <View style={s.rowBetween}>
                <Text style={[font.h2, { color: colors.red }]}>Новый заказ</Text>
                <View style={s.timer}>
                  <Ionicons name="timer-outline" size={16} color={colors.red} />
                  <Text style={s.timerText} testID={`countdown-${o.id}`}>
                    {countdown(left)}
                  </Text>
                </View>
              </View>
              <Text style={[font.tiny, { marginBottom: 8 }]}>
                №{o.id} · {time(o.createdAt)}
              </Text>
              {o.items.map((i) => (
                <Text key={i.dishId} style={[font.body, { marginBottom: 2 }]}>
                  • {i.name} × {i.qty}
                </Text>
              ))}
              <View style={[s.row, { marginTop: 8 }]}>
                <Ionicons name="location-outline" size={16} color={colors.textSecondary} />
                <Text style={[font.small, { marginLeft: 4, flex: 1 }]}>{o.deliveryAddress?.text}</Text>
              </View>
              {o.comment ? (
                <View style={s.comment}>
                  <Text style={font.small}>💬 {o.comment}</Text>
                </View>
              ) : null}
              <Text style={[font.h2, { marginTop: 10 }]}>{money(o.chefAmount)}</Text>
              <Text style={font.tiny}>к получению после комиссии и налога</Text>
              <View style={[s.row, { marginTop: 12 }]}>
                <Button title="Отказать" variant="gray" onPress={() => decline(o)} style={{ flex: 1, marginRight: 8 }} disabled={busyId === o.id} />
                <Button title="Принять" onPress={() => act(o.id, () => chefApi.accept(o.id), 'Заказ принят — начинайте готовить')} loading={busyId === o.id} disabled={left <= 0} style={{ flex: 2 }} testID={`accept-${o.id}`} />
              </View>
            </Card>
          );
        })}

        <Text style={[font.h3, { marginTop: fresh.length ? 8 : 0, marginBottom: 8 }]}>Активные</Text>
        {!active.length ? <Text style={[font.small, { marginBottom: 12 }]}>Нет активных заказов</Text> : null}
        {active.map((o) => (
          <Card key={o.id} style={{ marginBottom: spacing.md }}>
            <View style={s.rowBetween}>
              <Text style={font.h3}>№{o.id}</Text>
              <Badge label={STATUS_SHORT[o.status]} color={o.status === 'CONFIRMED' || o.status === 'COOKING' ? colors.orange : colors.blue} />
            </View>
            <Text style={[font.small, { marginTop: 6 }]}>{o.items.map((i) => `${i.name} × ${i.qty}`).join(', ')}</Text>
            {o.comment ? <Text style={[font.small, { marginTop: 4 }]}>💬 {o.comment}</Text> : null}
            {o.courier ? (
              <Text style={[font.small, { marginTop: 4 }]}>
                Курьер: {o.courier.name}, {o.courier.phone}
              </Text>
            ) : null}
            <View style={[s.rowBetween, { marginTop: 10 }]}>
              <Text style={font.h3}>{money(o.chefAmount)}</Text>
              {o.status === 'CONFIRMED' || o.status === 'COOKING' ? (
                <Button title="Готово →" small onPress={() => act(o.id, () => chefApi.ready(o.id), 'Вызываем курьера')} loading={busyId === o.id} testID={`ready-${o.id}`} />
              ) : (
                <Text style={font.small}>{o.status === 'READY' ? 'Ищем курьера…' : 'Передан курьеру'}</Text>
              )}
            </View>
          </Card>
        ))}

        <Text style={[font.h3, { marginTop: 8, marginBottom: 8 }]}>Выполнено сегодня</Text>
        {!today.length ? <Empty icon="checkmark-done-outline" title="Сегодня пока без заказов" /> : null}
        {today.map((o) => (
          <Card key={o.id} style={{ marginBottom: spacing.sm, paddingVertical: 12 }}>
            <View style={s.rowBetween}>
              <View style={{ flex: 1 }}>
                <Text style={font.body}>
                  №{o.id} · {time(o.deliveredAt)}
                </Text>
                <Text style={font.tiny} numberOfLines={1}>
                  {o.items.map((i) => `${i.name} × ${i.qty}`).join(', ')}
                </Text>
              </View>
              <View style={{ alignItems: 'flex-end' }}>
                <Text style={font.h3}>{money(o.chefAmount)}</Text>
                {o.reviewRating ? <Text style={font.tiny}>★ {o.reviewRating}</Text> : null}
              </View>
            </View>
          </Card>
        ))}
      </ScrollView>
    </View>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <View style={{ flex: 1, alignItems: 'center' }}>
      <Text style={{ color: colors.white, fontWeight: '700', fontSize: 15 }}>{value}</Text>
      <Text style={{ color: 'rgba(255,255,255,0.85)', fontSize: 11 }}>{label}</Text>
    </View>
  );
}

const s = StyleSheet.create({
  header: { backgroundColor: colors.primary, paddingHorizontal: spacing.lg, paddingBottom: 14 },
  title: { color: colors.white, fontSize: 22, fontWeight: '800' },
  sub: { color: 'rgba(255,255,255,0.9)', fontSize: 12 },
  row: { flexDirection: 'row', alignItems: 'center' },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  summary: { flexDirection: 'row', marginTop: 12, backgroundColor: 'rgba(255,255,255,0.15)', borderRadius: radius.md, paddingVertical: 8 },
  newCard: { borderWidth: 2, borderColor: colors.red, marginBottom: spacing.lg },
  timer: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.redLight, borderRadius: radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
  timerText: { color: colors.red, fontWeight: '700', fontSize: 16, marginLeft: 4, fontVariant: ['tabular-nums'] },
  comment: { backgroundColor: colors.bg, borderRadius: radius.sm, padding: 8, marginTop: 8 },
});
