import { router, Stack, useLocalSearchParams } from 'expo-router';
import { FlatList, View } from 'react-native';

import { SpecialistCard } from '../../components/SpecialistCard';
import { Button, Empty, ErrorText, Loading, styles } from '../../components/ui';
import { useAuth } from '../../lib/auth';
import type { Specialist } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function CategoryScreen() {
  const { id, name } = useLocalSearchParams<{ id: string; name?: string }>();
  const { user } = useAuth();
  const { data, error, refreshing, reload } = useApi<Specialist[]>(
    `/specialists?category_id=${id}&city=${encodeURIComponent(user?.city ?? '')}`,
  );

  return (
    <View style={styles.screen}>
      <Stack.Screen options={{ title: name ?? 'Специалисты' }} />
      {!data ? (error ? <ErrorText text={error} /> : <Loading />) : (
        <FlatList
          contentContainerStyle={styles.content}
          data={data}
          keyExtractor={(s) => String(s.id)}
          refreshing={refreshing}
          onRefresh={reload}
          ListHeaderComponent={
            <Button
              title="Разместить заказ — специалисты откликнутся сами"
              onPress={() => router.push({ pathname: '/order/new', params: { category_id: id } })}
              style={{ marginBottom: 16 }}
            />
          }
          ListEmptyComponent={<Empty text="В вашем городе пока нет специалистов в этом разделе. Разместите заказ — мы сообщим специалистам." />}
          renderItem={({ item }) => <SpecialistCard s={item} />}
        />
      )}
    </View>
  );
}
