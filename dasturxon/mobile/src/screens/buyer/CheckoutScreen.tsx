import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { ApiError, errorMessage } from '../../api/client';
import { ordersApi, publicApi } from '../../api/endpoints';
import { Screen } from '../../components/Screen';
import { Button, Card, Empty, Field, Img, MoneyRow, SectionTitle } from '../../components/ui';
import { confirmDialog, showAlert } from '../../lib/dialog';
import { money } from '../../lib/format';
import { useNav } from '../../navigation/types';
import { useBuyer, useCurrentAddress } from '../../store/buyer';
import { cartTotal, useCart } from '../../store/cart';
import { colors, font, radius, spacing } from '../../theme';

/** Оформление заказа (ТЗ 3.4) */
export function CheckoutScreen() {
  const nav = useNav();
  const profile = useBuyer((s) => s.profile);
  const reloadProfile = useBuyer((s) => s.load);
  const address = useCurrentAddress();
  const { chefId, chefNick, lines, deliveryFee, clear } = useCart();
  const [fee, setFee] = useState<number | null>(deliveryFee);
  const [comment, setComment] = useState('');
  const [cardId, setCardId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useFocusEffect(
    useCallback(() => {
      reloadProfile().catch(() => undefined);
    }, [reloadProfile]),
  );

  const cards = profile?.cards ?? [];
  useEffect(() => {
    if (!cardId || !cards.some((c) => c.id === cardId)) setCardId((cards.find((c) => c.isDefault) ?? cards[0])?.id ?? null);
  }, [cards, cardId]);

  // Пересчёт доставки под выбранный адрес
  useEffect(() => {
    if (!chefId || !address) return;
    publicApi
      .estimate(chefId, address.lat, address.lon)
      .then((r) => setFee(r.price))
      .catch(() => undefined);
  }, [chefId, address?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!chefId || !lines.length)
    return (
      <Screen title="Оформление" back>
        <Empty icon="cart-outline" title="Корзина пуста" action={<Button title="В ленту" onPress={() => nav.navigate('BuyerTabs', { screen: 'Feed' })} />} />
      </Screen>
    );

  const dishTotal = cartTotal(lines);
  const total = dishTotal + (fee ?? 0);
  const card = cards.find((c) => c.id === cardId);

  const pay = async () => {
    if (!address) return setError('Укажите адрес доставки');
    if (!card) return setError('Привяжите карту для оплаты');
    setBusy(true);
    setError(null);
    try {
      const order = await ordersApi.create({
        items: lines.map((l) => ({ dishId: l.dish.id, qty: l.qty })),
        addressId: address.id,
        cardId: card.id,
        comment: comment.trim() || undefined,
      });
      clear();
      nav.reset({ index: 1, routes: [{ name: 'BuyerTabs' }, { name: 'Tracking', params: { orderId: order.id } }] });
    } catch (e) {
      if (e instanceof ApiError && e.status === 402) {
        const change = await confirmDialog('Недостаточно средств', 'На карте недостаточно средств для оплаты заказа. Выберите другую карту или пополните баланс.', 'Другая карта', 'Закрыть');
        if (change) nav.navigate('Cards');
        setError('Недостаточно средств на карте');
      } else {
        setError(errorMessage(e));
        showAlert('Не удалось оформить заказ', errorMessage(e));
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen
      title="Оформление заказа"
      back
      footer={
        <>
          {error ? <Text style={{ color: colors.red, marginBottom: 8, textAlign: 'center' }}>{error}</Text> : null}
          <Button title={`Оплатить картой — ${money(total)}`} onPress={pay} loading={busy} disabled={!address || !card} testID="pay" />
        </>
      }
    >
      <SectionTitle>Адрес доставки</SectionTitle>
      <Card onPress={() => nav.navigate('Addresses', { select: true })}>
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Ionicons name="location" size={22} color={colors.primary} />
          <View style={{ flex: 1, marginLeft: 10 }}>
            {address?.label ? <Text style={font.h3}>{address.label}</Text> : null}
            <Text style={font.body}>{address?.addressText ?? 'Выберите адрес'}</Text>
          </View>
          <Text style={{ color: colors.primary }}>Изменить</Text>
        </View>
      </Card>

      <SectionTitle>Комментарий повару</SectionTitle>
      <Field placeholder="Например: без лука, позвонить заранее" value={comment} onChangeText={setComment} maxLength={200} counter={comment.length} multiline />

      <SectionTitle>Заказ у {chefNick}</SectionTitle>
      <Card>
        {lines.map((l) => (
          <View key={l.dish.id} style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 10 }}>
            <Img uri={l.dish.photoUrls[0]} style={{ width: 44, height: 44, borderRadius: radius.sm }} />
            <Text style={[font.body, { flex: 1, marginLeft: 10 }]}>
              {l.dish.name} × {l.qty}
            </Text>
            <Text style={font.body}>{money(l.dish.price * l.qty)}</Text>
          </View>
        ))}
        <View style={{ height: 1, backgroundColor: colors.border, marginVertical: 6 }} />
        <MoneyRow label="Блюда" value={money(dishTotal)} />
        <MoneyRow label="Доставка" value={fee != null ? money(fee) : '…'} />
        <MoneyRow label="Итого" value={money(total)} bold />
      </Card>

      <SectionTitle right={<Text style={{ color: colors.primary }} onPress={() => nav.navigate('AddCard')}>+ Новая карта</Text>}>Оплата</SectionTitle>
      {cards.map((c) => (
        <Pressable
          key={c.id}
          onPress={() => setCardId(c.id)}
          style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: colors.card, borderRadius: radius.md, padding: spacing.md, marginBottom: 8, borderWidth: 1.5, borderColor: c.id === cardId ? colors.primary : colors.border }}
        >
          <Ionicons name={c.id === cardId ? 'radio-button-on' : 'radio-button-off'} size={20} color={colors.primary} />
          <Ionicons name="card" size={22} color={colors.textSecondary} style={{ marginHorizontal: 10 }} />
          <Text style={[font.body, { flex: 1 }]}>{c.maskedPan}</Text>
          <Text style={font.small}>{c.cardType.toUpperCase()}</Text>
        </Pressable>
      ))}
      {!cards.length ? <Text style={font.small}>Нет привязанных карт</Text> : null}
      <Text style={[font.tiny, { marginTop: 8 }]}>Сумма будет заблокирована на карте и списана после того, как повар примет заказ.</Text>
    </Screen>
  );
}
