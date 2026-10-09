import { router } from 'expo-router';
import { useState } from 'react';
import { FlatList, View } from 'react-native';

import { OrderCard } from '../../components/OrderCard';
import { Button, Chip, Empty, ErrorText, Loading, styles } from '../../components/ui';
import { useAuth } from '../../lib/auth';
import type { Order } from '../../lib/types';
import { useApi } from '../../lib/useApi';

type Tab = 'feed' | 'assigned' | 'mine';

export default function Orders() {
  const { user } = useAuth();
  const isSpecialist = user?.role === 'specialist';
  const [tab, setTab] = useState<Tab>(isSpecialist ? 'feed' : 'mine');
  const { data, error, refreshing, reload } = useApi<Order[]>(`/orders/${tab}`);

  const empty: Record<Tab, string> = {
    feed: user?.has_specialist_profile
      ? 'Подходящих заказов пока нет. Мы пришлём уведомление, когда появятся.'
      : 'Заполните анкету специалиста в профиле, чтобы видеть заказы по своим услугам.',
    assigned: 'Здесь будут заказы, где клиент выбрал вас исполнителем.',
    mine: 'У вас пока нет заказов. Разместите первый — специалисты откликнутся сами.',
  };

  return (
    <View style={styles.screen}>
      <View style={[styles.wrap, { paddingHorizontal: 16, paddingTop: 12 }]}>
        {isSpecialist ? (
          <>
            <Chip label="Лента заказов" selected={tab === 'feed'} onPress={() => setTab('feed')} />
            <Chip label="В работе" selected={tab === 'assigned'} onPress={() => setTab('assigned')} />
          </>
        ) : null}
        <Chip label="Мои заказы" selected={tab === 'mine'} onPress={() => setTab('mine')} />
      </View>
      {!data ? (error ? <ErrorText text={error} /> : <Loading />) : (
        <FlatList
          contentContainerStyle={styles.content}
          data={data}
          keyExtractor={(o) => String(o.id)}
          refreshing={refreshing}
          onRefresh={reload}
          ListHeaderComponent={tab === 'mine' ? (
            <Button title="+ Новый заказ" onPress={() => router.push('/order/new')} style={{ marginBottom: 16 }} />
          ) : null}
          ListEmptyComponent={<Empty text={empty[tab]} />}
          renderItem={({ item }) => <OrderCard o={item} showStatus={tab !== 'feed'} />}
        />
      )}
    </View>
  );
}
