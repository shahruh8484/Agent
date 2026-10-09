import { router } from 'expo-router';
import { useMemo, useState } from 'react';
import { FlatList, Pressable, Text, View } from 'react-native';

import { Button, Card, ErrorText, Field, Loading, styles } from '../../components/ui';
import { colors } from '../../lib/theme';
import type { Section } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function Catalog() {
  const { data, error } = useApi<Section[]>('/categories');
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState<number | null>(null);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !data) return null;
    return data.flatMap((s) => s.children).filter((c) => c.name.toLowerCase().includes(q));
  }, [query, data]);

  if (!data) return error ? <ErrorText text={error} /> : <Loading />;

  const goCategory = (id: number, name: string) =>
    router.push({ pathname: '/category/[id]', params: { id: String(id), name } });

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      keyboardShouldPersistTaps="handled"
      ListHeaderComponent={
        <View>
          <Field placeholder="Какая услуга нужна?" value={query} onChangeText={setQuery} />
          <Button title="+ Разместить заказ" onPress={() => router.push('/order/new')} style={{ marginBottom: 16 }} />
          {matches ? (
            matches.length ? matches.map((c) => (
              <Card key={c.id} onPress={() => goCategory(c.id, c.name)}>
                <Text style={styles.text}>{c.name}</Text>
              </Card>
            )) : <Text style={styles.muted}>Ничего не нашлось</Text>
          ) : null}
        </View>
      }
      data={matches ? [] : data}
      keyExtractor={(s) => String(s.id)}
      renderItem={({ item }) => (
        <Card onPress={() => setOpen(open === item.id ? null : item.id)}>
          <View style={[styles.row, { justifyContent: 'space-between' }]}>
            <Text style={styles.h2}>{item.name}</Text>
            <Text style={styles.muted}>{open === item.id ? '▲' : '▼'}</Text>
          </View>
          {open === item.id ? (
            <View style={{ marginTop: 8 }}>
              <Pressable onPress={() => goCategory(item.id, item.name)}>
                <Text style={{ color: colors.primary, paddingVertical: 8 }}>Все специалисты раздела</Text>
              </Pressable>
              {item.children.map((c) => (
                <Pressable key={c.id} onPress={() => goCategory(c.id, c.name)}>
                  <Text style={[styles.text, { paddingVertical: 8 }]}>{c.name}</Text>
                </Pressable>
              ))}
            </View>
          ) : (
            <Text style={styles.muted} numberOfLines={1}>
              {item.children.map((c) => c.name).join(', ')}
            </Text>
          )}
        </Card>
      )}
    />
  );
}
