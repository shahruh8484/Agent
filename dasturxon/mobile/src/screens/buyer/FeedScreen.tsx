import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, FlatList, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { errorMessage } from '../../api/client';
import { publicApi } from '../../api/endpoints';
import type { FeedFilter, FeedItem } from '../../api/types';
import { ChefsMap } from '../../components/ChefsMap';
import { DishCard } from '../../components/DishCard';
import { Button, Chip, Empty } from '../../components/ui';
import { addToCart } from '../../lib/cartActions';
import { shortAddress } from '../../lib/format';
import { useNav } from '../../navigation/types';
import { useBuyer, useCurrentAddress, useCurrentPoint } from '../../store/buyer';
import { colors, radius, spacing } from '../../theme';
import { CartBar } from './CartBar';

const FILTERS: { key: FeedFilter; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'ready', label: 'Готово сейчас' },
  { key: 'order', label: 'На заказ' },
  { key: 'breakfast', label: 'Завтраки' },
  { key: 'lunch', label: 'Обеды' },
  { key: 'dinner', label: 'Ужины' },
  { key: 'bakery', label: 'Выпечка' },
  { key: 'vegan', label: 'Веганское' },
];

export function FeedScreen() {
  const nav = useNav();
  const insets = useSafeAreaInsets();
  const address = useCurrentAddress();
  const point = useCurrentPoint();
  const unread = useBuyer((s) => s.unread);
  const refreshUnread = useBuyer((s) => s.refreshUnread);

  const [filter, setFilter] = useState<FeedFilter>('all');
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<FeedItem[]>([]);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<'list' | 'map'>('list');
  const searchRef = useRef<TextInput>(null);
  const reqId = useRef(0);

  const load = useCallback(
    async (p: number, replace: boolean) => {
      const id = ++reqId.current;
      setLoading(true);
      setError(null);
      try {
        const r = query ? await publicApi.search(point.lat, point.lon, query, p) : await publicApi.feed(point.lat, point.lon, filter, p);
        if (id !== reqId.current) return;
        setItems((prev) => (replace ? r.items : [...prev, ...r.items.filter((x) => !prev.some((y) => y.id === x.id))]));
        setHasMore(r.hasMore);
        setPage(p);
      } catch (e) {
        if (id === reqId.current) setError(errorMessage(e));
      } finally {
        if (id === reqId.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [filter, query, point.lat, point.lon],
  );

  useEffect(() => {
    load(1, true);
  }, [load]);

  useFocusEffect(
    useCallback(() => {
      refreshUnread();
    }, [refreshUnread]),
  );

  const onEnd = () => {
    if (!loading && hasMore && items.length) load(page + 1, false);
  };

  const order = async (item: FeedItem) => {
    const ok = await addToCart(
      { id: item.chef.id, nick: item.chef.nick, deliveryFee: item.deliveryFee },
      { id: item.id, name: item.name, price: item.price, photoUrls: item.photoUrl ? [item.photoUrl] : [], portionsLeft: item.portionsLeft },
    );
    if (ok) nav.navigate('Chef', { chefId: item.chef.id });
  };

  const header = (
    <View>
      <View style={s.chipsWrap}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: spacing.lg }}>
          {FILTERS.map((f) => (
            <Chip
              key={f.key}
              label={f.label}
              active={!query && filter === f.key}
              onPress={() => {
                setQuery('');
                setQ('');
                setFilter(f.key);
              }}
            />
          ))}
        </ScrollView>
      </View>
      {query ? (
        <View style={[s.rowBetween, { paddingHorizontal: spacing.lg, marginBottom: 8 }]}>
          <Text style={{ color: colors.textSecondary }}>Результаты поиска «{query}»</Text>
          <Text
            style={{ color: colors.primary }}
            onPress={() => {
              setQuery('');
              setQ('');
            }}
          >
            Сбросить
          </Text>
        </View>
      ) : null}
    </View>
  );

  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <View style={[s.header, { paddingTop: insets.top + 8 }]}>
        <View style={s.rowBetween}>
          <View style={{ flex: 1 }}>
            <Text style={s.logo}>Dasturxon</Text>
            <Pressable onPress={() => nav.navigate('Addresses', { select: true })} style={s.row} testID="address-header">
              <Ionicons name="location" size={14} color={colors.white} />
              <Text style={s.address} numberOfLines={1}>
                {shortAddress(address?.addressText)}
              </Text>
              <Ionicons name="chevron-down" size={14} color={colors.white} />
            </Pressable>
          </View>
          <Pressable onPress={() => searchRef.current?.focus()} hitSlop={8} style={s.iconBtn} accessibilityLabel="Поиск">
            <Ionicons name="search" size={22} color={colors.white} />
          </Pressable>
          <Pressable onPress={() => nav.navigate('Notifications')} hitSlop={8} style={s.iconBtn} accessibilityLabel="Уведомления">
            <Ionicons name="notifications-outline" size={22} color={colors.white} />
            {unread > 0 ? (
              <View style={s.badge}>
                <Text style={s.badgeText}>{unread > 9 ? '9+' : unread}</Text>
              </View>
            ) : null}
          </Pressable>
          <Pressable onPress={() => setMode((m) => (m === 'list' ? 'map' : 'list'))} hitSlop={8} style={s.iconBtn} accessibilityLabel="Список или карта" testID="toggle-map">
            <Ionicons name={mode === 'list' ? 'map-outline' : 'list'} size={22} color={colors.white} />
          </Pressable>
        </View>
        {mode === 'list' ? (
          <View style={s.search}>
            <Ionicons name="search" size={18} color={colors.muted} />
            <TextInput
              style={s.searchInput}
              placeholder="Плов, манты, повар…"
              placeholderTextColor={colors.muted}
              value={q}
              onChangeText={setQ}
              returnKeyType="search"
              onSubmitEditing={() => setQuery(q.trim())}
              ref={searchRef}
              testID="search-input"
            />
            {q ? <Ionicons name="close-circle" size={18} color={colors.muted} onPress={() => { setQ(''); setQuery(''); }} /> : null}
          </View>
        ) : null}
      </View>

      {mode === 'map' ? (
        <ChefsMap center={point} onChef={(id) => nav.navigate('Chef', { chefId: id })} style={{ margin: spacing.lg }} />
      ) : (
        <FlatList
          data={items}
          keyExtractor={(i) => String(i.id)}
          ListHeaderComponent={header}
          renderItem={({ item }) => (
            <View style={{ paddingHorizontal: spacing.lg }}>
              <DishCard item={item} onOpen={() => nav.navigate('Chef', { chefId: item.chef.id })} onOrder={() => order(item)} />
            </View>
          )}
          onEndReached={onEnd}
          onEndReachedThreshold={0.5}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => { setRefreshing(true); load(1, true); }} colors={[colors.primary]} />}
          ListFooterComponent={loading && items.length ? <ActivityIndicator color={colors.primary} style={{ margin: 16 }} /> : <View style={{ height: 90 }} />}
          ListEmptyComponent={
            loading ? (
              <ActivityIndicator color={colors.primary} style={{ marginTop: 40 }} />
            ) : error ? (
              <Empty icon="cloud-offline-outline" title="Не удалось загрузить ленту" text={error} action={<Button title="Повторить" onPress={() => load(1, true)} />} />
            ) : (
              <Empty title="Рядом пока нет блюд" text={query ? 'Попробуйте другой запрос' : 'Попробуйте другой фильтр или загляните позже'} />
            )
          }
        />
      )}
      <CartBar />
    </View>
  );
}

const s = StyleSheet.create({
  header: { backgroundColor: colors.primary, paddingHorizontal: spacing.lg, paddingBottom: 12 },
  row: { flexDirection: 'row', alignItems: 'center', marginTop: 2 },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  logo: { color: colors.white, fontSize: 22, fontWeight: '800' },
  address: { color: colors.white, fontSize: 13, marginHorizontal: 4, maxWidth: 200 },
  iconBtn: { marginLeft: 14, padding: 2 },
  badge: { position: 'absolute', top: -4, right: -6, backgroundColor: colors.red, borderRadius: 9, minWidth: 18, height: 18, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 3 },
  badgeText: { color: colors.white, fontSize: 10, fontWeight: '700' },
  search: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.white, borderRadius: radius.md, paddingHorizontal: 12, marginTop: 10, height: 42 },
  searchInput: { flex: 1, marginLeft: 8, fontSize: 15, color: colors.text },
  chipsWrap: { paddingVertical: 12 },
});
