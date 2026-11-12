import { Ionicons } from '@expo/vector-icons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Linking, Pressable, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { ordersApi } from '../../api/endpoints';
import { onSocket, subscribeOrder } from '../../api/socket';
import type { Courier, Order, OrderStatus, Point } from '../../api/types';
import { YandexMap, type MapData, type MapMarker } from '../../components/map/YandexMap';
import { Screen } from '../../components/Screen';
import { StatusTimeline } from '../../components/StatusTimeline';
import { Button, Card, Empty, Img, Loader, MoneyRow } from '../../components/ui';
import { confirmDialog, showAlert } from '../../lib/dialog';
import { dateTime, money, STATUS_LABEL } from '../../lib/format';
import type { RootStackParamList } from '../../navigation/types';
import { colors, font, radius, spacing } from '../../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Tracking'>;

const FINAL: OrderStatus[] = ['DELIVERED', 'RATED', 'CANCELLED'];

/** Трекинг заказа (ТЗ 3.5): Socket.io + опрос каждые 5 с как запасной канал */
export function TrackingScreen({ route, navigation }: Props) {
  const { orderId } = route.params;
  const [order, setOrder] = useState<Order | null>(null);
  const [courier, setCourier] = useState<Courier | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const ratingShown = useRef(false);
  const statusRef = useRef<OrderStatus | null>(null);

  const load = useCallback(async () => {
    try {
      const o = await ordersApi.get(orderId);
      statusRef.current = o.status;
      setOrder(o);
      if (o.courier) setCourier(o.courier);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [orderId]);

  useEffect(() => {
    load();
    const unsub = subscribeOrder(orderId);
    const offUpdate = onSocket<{ orderId: number; status: OrderStatus }>('order:update', (p) => {
      if (p.orderId === orderId) load();
    });
    const offTrack = onSocket<{ orderId: number; status: OrderStatus; courier: Courier }>('order:tracking', (p) => {
      if (p.orderId !== orderId) return;
      setCourier(p.courier);
      if (statusRef.current !== p.status) load();
    });
    // запасной опрос: если сокет недоступен
    const poll = setInterval(async () => {
      try {
        const t = await ordersApi.tracking(orderId);
        if (t.courier) setCourier(t.courier);
        if (statusRef.current && statusRef.current !== t.status) load();
      } catch {
        /* следующий опрос */
      }
    }, 5000);
    return () => {
      unsub();
      offUpdate();
      offTrack();
      clearInterval(poll);
    };
  }, [orderId, load]);

  // После доставки автоматически показываем экран оценки (ТЗ 3.6)
  useEffect(() => {
    if (order?.status === 'DELIVERED' && !ratingShown.current) {
      ratingShown.current = true;
      navigation.navigate('Rating', { orderId: order.id, chefId: order.chefId, chefNick: order.chef?.nick ?? 'повар', chefPhoto: order.chef?.photoUrl ?? null });
    }
  }, [order?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const mapData: MapData | null = useMemo(() => {
    if (!order) return null;
    const buyer: Point | null = order.deliveryAddress.lat != null ? { lat: order.deliveryAddress.lat, lon: order.deliveryAddress.lon! } : null;
    const markers: MapMarker[] = [];
    if (order.chef?.point) markers.push({ id: 'chef', kind: 'chef', label: order.chef.nick, ...order.chef.point });
    if (buyer) markers.push({ id: 'buyer', kind: 'buyer', label: 'Вы', subtitle: order.deliveryAddress.text, ...buyer });
    if (courier?.position) markers.push({ id: 'courier', kind: 'courier', label: courier.name, subtitle: 'Курьер', ...courier.position });
    const from = courier?.position ?? order.chef?.point ?? null;
    return { center: buyer ?? from ?? { lat: 41.31, lon: 69.28 }, zoom: 13, markers, route: from && buyer ? [from, buyer] : undefined };
  }, [order, courier]);

  if (error && !order) return <Screen title="Заказ" back><Empty icon="alert-circle-outline" title="Не удалось загрузить заказ" text={error} action={<Button title="Повторить" onPress={load} />} /></Screen>;
  if (!order || !mapData) return <Loader />;

  const eta = courier?.etaSeconds != null && !FINAL.includes(order.status) ? Math.max(1, Math.round(courier.etaSeconds / 60)) : null;

  const cancel = async () => {
    const ok = await confirmDialog('Отменить заказ?', 'Деньги сразу вернутся на карту.', 'Отменить заказ', 'Нет', true);
    if (!ok) return;
    setCancelling(true);
    try {
      setOrder({ ...order, ...(await ordersApi.cancel(order.id)) });
      load();
    } catch (e) {
      showAlert('Не удалось отменить', errorMessage(e));
    } finally {
      setCancelling(false);
    }
  };

  return (
    <Screen title={`Заказ №${order.id}`} back>
      <Card>
        <Text style={[font.h1, order.status === 'CANCELLED' && { color: colors.red }]} testID="order-status">
          {STATUS_LABEL[order.status]}
        </Text>
        {eta ? <Text style={[font.h3, { color: colors.primary, marginTop: 4 }]}>Будет через ~{eta} мин</Text> : null}
        {courier && !FINAL.includes(order.status) ? (
          <Pressable onPress={() => Linking.openURL(`tel:${courier.phone}`)} style={{ flexDirection: 'row', alignItems: 'center', marginTop: 8 }}>
            <Ionicons name="bicycle" size={18} color={colors.textSecondary} />
            <Text style={[font.small, { marginLeft: 6 }]}>
              Курьер: {courier.name} · <Text style={{ color: colors.primary }}>{courier.phone}</Text>
            </Text>
          </Pressable>
        ) : null}
      </Card>

      {!FINAL.includes(order.status) && mapData.markers.length ? <YandexMap data={mapData} style={{ height: 240, borderRadius: radius.lg, marginTop: spacing.md }} /> : null}

      <Card style={{ marginTop: spacing.md }}>
        <StatusTimeline order={order} />
      </Card>

      <Card style={{ marginTop: spacing.md }}>
        <Pressable onPress={() => setExpanded((v) => !v)} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={font.h3}>Детали заказа</Text>
          <Ionicons name={expanded ? 'chevron-up' : 'chevron-down'} size={20} color={colors.textSecondary} />
        </Pressable>
        {expanded ? (
          <View style={{ marginTop: 10 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 10 }}>
              <Img uri={order.chef?.photoUrl} rounded style={{ width: 36, height: 36 }} />
              <Text style={[font.body, { marginLeft: 10 }]}>{order.chef?.nick}</Text>
            </View>
            {order.items.map((i) => (
              <MoneyRow key={i.dishId} label={`${i.name} × ${i.qty}`} value={money(i.price * i.qty)} />
            ))}
            <MoneyRow label="Доставка" value={money(order.deliveryFee)} />
            <MoneyRow label="Итого" value={money(order.total)} bold />
            {order.refundedAmount ? <MoneyRow label="Возвращено" value={money(order.refundedAmount)} color={colors.primary} /> : null}
            <Text style={[font.small, { marginTop: 8 }]}>Адрес: {order.deliveryAddress.text}</Text>
            {order.comment ? <Text style={font.small}>Комментарий: {order.comment}</Text> : null}
            <Text style={font.small}>Создан: {dateTime(order.createdAt)}</Text>
          </View>
        ) : null}
      </Card>

      {order.status === 'PENDING' ? <Button title="Отменить заказ" variant="outline" onPress={cancel} loading={cancelling} style={{ marginTop: spacing.lg, borderColor: colors.red }} /> : null}
      {order.status === 'DELIVERED' ? (
        <Button
          title="Оценить заказ"
          onPress={() => navigation.navigate('Rating', { orderId: order.id, chefId: order.chefId, chefNick: order.chef?.nick ?? 'повар', chefPhoto: order.chef?.photoUrl ?? null })}
          style={{ marginTop: spacing.lg }}
        />
      ) : null}
    </Screen>
  );
}
