import { router } from 'expo-router';
import { FlatList, Text, View } from 'react-native';

import { Card, Empty, ErrorText, Loading, styles } from '../../components/ui';
import { formatDate, useT } from '../../lib/i18n';
import type { ChatSummary } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function Chats() {
  const { t, lang } = useT();
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
      ListEmptyComponent={<Empty text={t('emptyChats')} />}
      renderItem={({ item }) => (
        <Card onPress={() => router.push({ pathname: '/chat/[id]', params: { id: String(item.id), title: item.other_name } })}>
          <View style={[styles.row, { justifyContent: 'space-between' }]}>
            <Text style={styles.h2}>{item.other_name || t('user')}</Text>
            <Text style={styles.muted}>{formatDate(item.last_at, lang)}</Text>
          </View>
          <Text style={styles.muted} numberOfLines={1}>{item.order_title}</Text>
          <Text style={styles.text} numberOfLines={1}>{item.last_text}</Text>
        </Card>
      )}
    />
  );
}
