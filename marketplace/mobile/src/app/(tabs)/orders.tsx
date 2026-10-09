import { router } from 'expo-router';
import { useState } from 'react';
import { FlatList, View } from 'react-native';

import { OrderCard } from '../../components/OrderCard';
import { Button, Chip, Empty, ErrorText, Loading, styles } from '../../components/ui';
import { useAuth } from '../../lib/auth';
import { useT } from '../../lib/i18n';
import type { Order } from '../../lib/types';
import { useApi } from '../../lib/useApi';

type Tab = 'feed' | 'assigned' | 'mine';

export default function Orders() {
  const { user } = useAuth();
  const { t } = useT();
  const isSpecialist = user?.role === 'specialist';
  const [tab, setTab] = useState<Tab>(isSpecialist ? 'feed' : 'mine');
  const { data, error, refreshing, reload } = useApi<Order[]>(`/orders/${tab}`);

  const empty: Record<Tab, string> = {
    feed: user?.has_specialist_profile ? t('emptyFeed') : t('emptyFeedNoProfile'),
    assigned: t('emptyAssigned'),
    mine: t('emptyMine'),
  };

  return (
    <View style={styles.screen}>
      <View style={[styles.wrap, { paddingHorizontal: 16, paddingTop: 12 }]}>
        {isSpecialist ? (
          <>
            <Chip label={t('feed')} selected={tab === 'feed'} onPress={() => setTab('feed')} />
            <Chip label={t('inWork')} selected={tab === 'assigned'} onPress={() => setTab('assigned')} />
          </>
        ) : null}
        <Chip label={t('tabMyOrders')} selected={tab === 'mine'} onPress={() => setTab('mine')} />
      </View>
      {!data ? (error ? <ErrorText text={error} /> : <Loading />) : (
        <FlatList
          contentContainerStyle={styles.content}
          data={data}
          keyExtractor={(o) => String(o.id)}
          refreshing={refreshing}
          onRefresh={reload}
          ListHeaderComponent={tab === 'mine' ? (
            <Button title={t('newOrderButton')} onPress={() => router.push('/order/new')} style={{ marginBottom: 16 }} />
          ) : null}
          ListEmptyComponent={<Empty text={empty[tab]} />}
          renderItem={({ item }) => <OrderCard o={item} showStatus={tab !== 'feed'} />}
        />
      )}
    </View>
  );
}
