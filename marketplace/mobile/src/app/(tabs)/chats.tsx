import { router } from 'expo-router';
import { FlatList, Text, View } from 'react-native';

import { Card, Empty, ErrorText, Loading, styles } from '../../components/ui';
import { formatDate } from '../../lib/api';
import type { ChatSummary } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function Chats() {
  const { data, error, refreshing, reload } = useApi<ChatSummary[]>('/chats');
  if (!data) return error ? <ErrorText text={error} /> : <Loading />;
  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={data}
      keyExtractor={(c) => String(c.id)}
      refreshing={refreshing}
      onRefresh={reload}
      ListEmptyComponent={<Empty text="Здесь появятся переписки по вашим заказам и откликам." />}
      renderItem={({ item }) => (
        <Card onPress={() => router.push({ pathname: '/chat/[id]', params: { id: String(item.id), title: item.other_name } })}>
          <View style={[styles.row, { justifyContent: 'space-between' }]}>
            <Text style={styles.h2}>{item.other_name || 'Пользователь'}</Text>
            <Text style={styles.muted}>{formatDate(item.last_at)}</Text>
          </View>
          <Text style={styles.muted} numberOfLines={1}>{item.order_title}</Text>
          <Text style={styles.text} numberOfLines={1}>{item.last_text}</Text>
        </Card>
      )}
    />
  );
}
