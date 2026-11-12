import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { chefApi } from '../../api/endpoints';
import type { ChefDish } from '../../api/types';
import { GreenHeader } from '../../components/Screen';
import { Button, Card, DishTypeBadge, Empty, Img, Toggle } from '../../components/ui';
import { showAlert } from '../../lib/dialog';
import { CATEGORY_LABEL, money } from '../../lib/format';
import { useNav } from '../../navigation/types';
import { colors, font, radius, spacing } from '../../theme';

/** Мои блюда (ТЗ 4.3) */
export function DishesScreen() {
  const nav = useNav();
  const [items, setItems] = useState<ChefDish[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await chefApi.dishes());
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
      setItems((v) => v ?? []);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const toggle = async (d: ChefDish, active: boolean) => {
    setItems((list) => list?.map((x) => (x.id === d.id ? { ...x, isActive: active } : x)) ?? null);
    try {
      await chefApi.toggleDish(d.id, active);
    } catch (e) {
      showAlert('Ошибка', errorMessage(e));
      load();
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <GreenHeader title="Моё меню" right={<Text style={{ color: colors.white, fontWeight: '600' }} onPress={() => nav.navigate('DishForm')}>+ Добавить</Text>} />
      {items === null ? (
        <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(d) => String(d.id)}
          contentContainerStyle={{ padding: spacing.lg }}
          refreshControl={<RefreshControl refreshing={false} onRefresh={load} />}
          renderItem={({ item: d }) => (
            <Card style={{ marginBottom: spacing.md, flexDirection: 'row', padding: spacing.md, opacity: d.isActive ? 1 : 0.65 }} onPress={() => nav.navigate('DishForm', { dishId: d.id })}>
              <Img uri={d.photoUrls[0]} style={{ width: 80, height: 80, borderRadius: radius.md }} />
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Text style={font.h3} numberOfLines={1}>
                  {d.name}
                </Text>
                <Text style={font.small}>
                  {money(d.price)} · {CATEGORY_LABEL[d.category]}
                </Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 4 }}>
                  <DishTypeBadge readyNow={d.type !== 'order'} />
                  {d.type === 'both' ? <Text style={[font.tiny, { marginLeft: 4 }]}>+ на заказ</Text> : null}
                </View>
                <Text style={[font.tiny, { marginTop: 4 }]}>
                  {d.portionsLeft == null ? 'Порции: без ограничений' : d.portionsLeft === 0 ? 'Порции закончились' : `Осталось порций: ${d.portionsLeft}`}
                </Text>
              </View>
              <View style={{ alignItems: 'center', justifyContent: 'space-between' }}>
                <Toggle value={d.isActive} onChange={(v) => toggle(d, v)} />
                <Text style={{ color: colors.primary, fontSize: 13 }}>Изменить</Text>
              </View>
            </Card>
          )}
          ListEmptyComponent={
            <Empty
              icon="fast-food-outline"
              title={error ?? 'В меню пока нет блюд'}
              text="Добавьте первое блюдо — оно появится в ленте у покупателей рядом"
              action={<Button title="Добавить блюдо" icon="add" onPress={() => nav.navigate('DishForm')} />}
            />
          }
        />
      )}
    </View>
  );
}
