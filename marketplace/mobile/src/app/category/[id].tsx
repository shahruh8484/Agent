import { router, Stack, useLocalSearchParams } from 'expo-router';
import { FlatList, View } from 'react-native';

import { SpecialistCard } from '../../components/SpecialistCard';
import { Button, Empty, ErrorText, Loading, styles } from '../../components/ui';
import { useAuth } from '../../lib/auth';
import { useT } from '../../lib/i18n';
import type { Specialist } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function CategoryScreen() {
  const { id, name } = useLocalSearchParams<{ id: string; name?: string }>();
  const { user } = useAuth();
  const { t } = useT();
  const { data, error, refreshing, reload } = useApi<Specialist[]>(
    `/specialists?category_id=${id}&city=${encodeURIComponent(user?.city ?? '')}`,
  );

  return (
    <View style={styles.screen}>
      <Stack.Screen options={{ title: name ?? t('specialists') }} />
      {!data ? (error ? <ErrorText text={error} /> : <Loading />) : (
        <FlatList
          contentContainerStyle={styles.content}
          data={data}
          keyExtractor={(s) => String(s.id)}
          refreshing={refreshing}
          onRefresh={reload}
          ListHeaderComponent={
            <Button
              title={t('postOrderHint')}
              onPress={() => router.push({ pathname: '/order/new', params: { category_id: id } })}
              style={{ marginBottom: 16 }}
            />
          }
          ListEmptyComponent={<Empty text={t('noSpecialists')} />}
          renderItem={({ item }) => <SpecialistCard s={item} />}
        />
      )}
    </View>
  );
}
