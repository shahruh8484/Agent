import { Ionicons } from '@expo/vector-icons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React, { useCallback, useEffect, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { errorMessage } from '../../api/client';
import { publicApi } from '../../api/endpoints';
import type { ChefMenu, ChefPublic, MenuDish } from '../../api/types';
import { Stars } from '../../components/Stars';
import { Avatar, Badge, Button, Card, DishTypeBadge, Empty, Img, Loader, MoneyRow, SectionTitle } from '../../components/ui';
import { addToCart } from '../../lib/cartActions';
import { dateShort, money, plural, tagLabel } from '../../lib/format';
import type { RootStackParamList } from '../../navigation/types';
import { useCurrentPoint } from '../../store/buyer';
import { cartCount, cartTotal, useCart } from '../../store/cart';
import { toast } from '../../store/toast';
import { colors, font, radius, spacing } from '../../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Chef'>;

const STATUS = {
  ready: { label: 'Готово сейчас', color: colors.primary },
  order: { label: 'На заказ', color: colors.blue },
  offline: { label: 'Не принимает заказы', color: '#6B7280' },
};

export function ChefScreen({ route, navigation }: Props) {
  const { chefId } = route.params;
  const insets = useSafeAreaInsets();
  const point = useCurrentPoint();
  const [chef, setChef] = useState<ChefPublic | null>(null);
  const [menu, setMenu] = useState<ChefMenu | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cart = useCart();

  const load = useCallback(async () => {
    try {
      setError(null);
      const [c, m] = await Promise.all([publicApi.chef(chefId, point.lat, point.lon), publicApi.menu(chefId)]);
      setChef(c);
      setMenu(m);
      // уточняем стоимость доставки в корзине, если корзина этого повара
      if (useCart.getState().chefId === chefId) useCart.getState().setChef(chefId, c.nick, c.deliveryFee);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, [chefId, point.lat, point.lon]);

  useEffect(() => {
    load();
  }, [load]);

  if (error)
    return (
      <View style={{ flex: 1, paddingTop: insets.top, backgroundColor: colors.bg }}>
        <Empty icon="alert-circle-outline" title="Не удалось открыть повара" text={error} action={<Button title="Назад" onPress={() => navigation.goBack()} />} />
      </View>
    );
  if (!chef || !menu) return <Loader />;

  const available = menu.available && chef.status !== 'offline';
  const myCart = cart.chefId === chefId ? cart.lines : [];
  const qtyOf = (id: number) => myCart.find((l) => l.dish.id === id)?.qty ?? 0;
  const count = cartCount(myCart);
  const dishTotal = cartTotal(myCart);
  const fee = chef.deliveryFee ?? cart.deliveryFee ?? 0;

  const add = (d: MenuDish) =>
    addToCart({ id: chef.id, nick: chef.nick, deliveryFee: chef.deliveryFee }, { id: d.id, name: d.name, price: d.price, photoUrls: d.photoUrls, portionsLeft: d.portionsLeft });

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <ScrollView contentContainerStyle={{ paddingBottom: count ? 220 : 40 }}>
        {/* Зелёная шапка */}
        <View style={[s.header, { paddingTop: insets.top + 8 }]}>
          {chef.coverUrl ? <Img uri={chef.coverUrl} style={s.cover} /> : null}
          <View style={s.coverShade} />
          <Pressable onPress={() => navigation.goBack()} style={s.back} hitSlop={10} accessibilityLabel="Назад">
            <Ionicons name="chevron-back" size={26} color={colors.white} />
          </Pressable>
          <View style={s.headInfo}>
            <Avatar uri={chef.photoUrl} size={72} />
            <View style={{ flex: 1, marginLeft: 14 }}>
              <Text style={s.nick}>{chef.nick}</Text>
              <Text style={s.sub}>
                {chef.district}
                {chef.distanceKm != null ? ` · ${chef.distanceKm} км` : ''}
              </Text>
              <Badge label={STATUS[chef.status].label} color={colors.white} textColor={STATUS[chef.status].color} style={{ marginTop: 6 }} />
            </View>
          </View>
          <View style={s.stats}>
            <View style={s.stat}>
              <Stars value={chef.rating} size={14} light />
              <Text style={s.statLabel}>
                {chef.reviewsCount} {plural(chef.reviewsCount, 'отзыв', 'отзыва', 'отзывов')}
              </Text>
            </View>
            <View style={s.stat}>
              <Text style={s.statValue}>{chef.totalOrders}</Text>
              <Text style={s.statLabel}>заказов</Text>
            </View>
            <View style={s.stat}>
              <Text style={s.statValue}>{chef.avgDeliveryMinutes ? `~${chef.avgDeliveryMinutes} мин` : '—'}</Text>
              <Text style={s.statLabel}>доставка</Text>
            </View>
          </View>
        </View>

        <View style={{ padding: spacing.lg }}>
          {!available ? (
            <Card style={{ marginBottom: spacing.md, backgroundColor: '#FFF7E6' }}>
              <Text style={font.h3}>Повар сейчас не принимает заказы</Text>
              <Text style={[font.small, { marginVertical: 6 }]}>Подпишитесь — пришлём уведомление, когда повар снова будет онлайн.</Text>
              <Button title="Подписаться" icon="notifications-outline" variant="outline" small onPress={() => toast('Вы подписались на повара ' + chef.nick, 'success')} />
            </Card>
          ) : null}

          <SectionTitle>Меню</SectionTitle>
          {menu.dishes.length === 0 ? <Text style={font.small}>Меню пока пустое</Text> : null}
          {menu.dishes.map((d) => {
            const qty = qtyOf(d.id);
            return (
              <Card key={d.id} style={[s.dish, !available && { opacity: 0.55 }]}>
                <Img uri={d.photoUrls[0]} style={s.dishPhoto} />
                <View style={{ flex: 1, marginLeft: 12 }}>
                  <Text style={font.h3} numberOfLines={2}>
                    {d.name}
                  </Text>
                  {d.description ? (
                    <Text style={font.small} numberOfLines={2}>
                      {d.description}
                    </Text>
                  ) : null}
                  <View style={[s.row, { marginTop: 4, flexWrap: 'wrap' }]}>
                    <DishTypeBadge readyNow={d.type !== 'order'} />
                    {d.type === 'order' && d.prepTimeMinutes ? <Text style={[font.tiny, { marginLeft: 6 }]}>~{d.prepTimeMinutes} мин</Text> : null}
                    {d.weight ? <Text style={[font.tiny, { marginLeft: 6 }]}>{d.weight}</Text> : null}
                    {d.isVegan ? <Ionicons name="leaf" size={14} color={colors.primary} style={{ marginLeft: 6 }} /> : null}
                  </View>
                  <View style={[s.row, { justifyContent: 'space-between', marginTop: 8 }]}>
                    <Text style={s.price}>{money(d.price)}</Text>
                    {available ? (
                      qty ? (
                        <View style={s.counter}>
                          <Pressable onPress={() => cart.remove(d.id)} style={s.cBtn} hitSlop={6} accessibilityLabel="Убрать">
                            <Ionicons name="remove" size={18} color={colors.primary} />
                          </Pressable>
                          <Text style={s.cQty}>{qty}</Text>
                          <Pressable onPress={() => add(d)} style={s.cBtn} hitSlop={6} accessibilityLabel="Добавить" testID={`plus-${d.id}`}>
                            <Ionicons name="add" size={18} color={colors.primary} />
                          </Pressable>
                        </View>
                      ) : (
                        <Pressable onPress={() => add(d)} style={s.plus} testID={`add-${d.id}`} accessibilityLabel="В корзину">
                          <Ionicons name="add" size={22} color={colors.white} />
                        </Pressable>
                      )
                    ) : null}
                  </View>
                  {d.portionsLeft != null ? <Text style={font.tiny}>Осталось порций: {d.portionsLeft}</Text> : null}
                </View>
              </Card>
            );
          })}

          {chef.bio ? (
            <>
              <SectionTitle>О поваре</SectionTitle>
              <Card>
                <Text style={font.body}>{chef.bio}</Text>
              </Card>
            </>
          ) : null}

          <SectionTitle
            right={
              chef.reviewsCount > 0 ? (
                <Text style={{ color: colors.primary }} onPress={() => navigation.navigate('ChefReviews', { chefId: chef.id, nick: chef.nick })}>
                  Посмотреть все
                </Text>
              ) : null
            }
          >
            Отзывы
          </SectionTitle>
          {chef.lastReviews.length === 0 ? <Text style={font.small}>Пока нет отзывов</Text> : null}
          {chef.lastReviews.map((r, i) => (
            <Card key={i} style={{ marginBottom: spacing.sm }}>
              <View style={[s.row, { justifyContent: 'space-between' }]}>
                <Text style={font.h3}>{r.buyerName}</Text>
                <Text style={font.tiny}>{dateShort(r.createdAt)}</Text>
              </View>
              <Stars value={r.rating} size={13} />
              {r.tags?.length ? <Text style={[font.small, { marginTop: 4 }]}>{r.tags.map(tagLabel).join(' · ')}</Text> : null}
              {r.comment ? <Text style={[font.body, { marginTop: 4 }]}>{r.comment}</Text> : null}
            </Card>
          ))}
        </View>
      </ScrollView>

      {count > 0 ? (
        <View style={[s.cartBar, { paddingBottom: Math.max(insets.bottom, 12) }]} testID="chef-cart-bar">
          <MoneyRow label={`Блюда (${count})`} value={money(dishTotal)} />
          <MoneyRow label="Доставка" value={money(fee)} />
          <MoneyRow label="Итого" value={money(dishTotal + fee)} bold />
          <Button title="Оплатить картой" icon="card-outline" onPress={() => navigation.navigate('Checkout')} disabled={!available} style={{ marginTop: 8 }} testID="go-checkout" />
        </View>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  header: { backgroundColor: colors.primary, paddingHorizontal: spacing.lg, paddingBottom: spacing.lg, borderBottomLeftRadius: radius.xl, borderBottomRightRadius: radius.xl, overflow: 'hidden' },
  cover: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, opacity: 0.35 },
  coverShade: { position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, backgroundColor: 'rgba(31,163,122,0.55)' },
  back: { marginBottom: 8, alignSelf: 'flex-start' },
  headInfo: { flexDirection: 'row', alignItems: 'center' },
  nick: { color: colors.white, fontSize: 22, fontWeight: '800' },
  sub: { color: 'rgba(255,255,255,0.9)', fontSize: 14, marginTop: 2 },
  stats: { flexDirection: 'row', marginTop: spacing.lg, backgroundColor: 'rgba(255,255,255,0.15)', borderRadius: radius.md, padding: 10 },
  stat: { flex: 1, alignItems: 'center' },
  statValue: { color: colors.white, fontSize: 15, fontWeight: '700' },
  statLabel: { color: 'rgba(255,255,255,0.85)', fontSize: 12, marginTop: 2 },
  row: { flexDirection: 'row', alignItems: 'center' },
  dish: { flexDirection: 'row', padding: spacing.md, marginBottom: spacing.sm },
  dishPhoto: { width: 96, height: 96, borderRadius: radius.md },
  price: { fontSize: 16, fontWeight: '700', color: colors.text },
  plus: { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  counter: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.primaryLight, borderRadius: radius.pill, paddingHorizontal: 4 },
  cBtn: { width: 30, height: 30, alignItems: 'center', justifyContent: 'center' },
  cQty: { minWidth: 22, textAlign: 'center', fontWeight: '700', color: colors.primaryDark },
  cartBar: { position: 'absolute', left: 0, right: 0, bottom: 0, backgroundColor: colors.card, paddingHorizontal: spacing.lg, paddingTop: 12, borderTopLeftRadius: radius.xl, borderTopRightRadius: radius.xl, shadowColor: '#000', shadowOpacity: 0.1, shadowRadius: 12, elevation: 8 },
});
