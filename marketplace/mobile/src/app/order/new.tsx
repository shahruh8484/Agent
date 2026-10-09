import { router, useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { ScrollView, Switch, Text, View } from 'react-native';

import { CityPicker } from '../../components/CityPicker';
import { Button, Chip, ErrorText, Field, Loading, styles } from '../../components/ui';
import { api } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import type { Order, Section } from '../../lib/types';
import { useApi } from '../../lib/useApi';

export default function NewOrder() {
  const params = useLocalSearchParams<{ category_id?: string }>();
  const { user } = useAuth();
  const { data: sections } = useApi<Section[]>('/categories');
  const [categoryId, setCategoryId] = useState<number | null>(params.category_id ? Number(params.category_id) : null);
  const [sectionId, setSectionId] = useState<number | null>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [city, setCity] = useState(user?.city ?? '');
  const [budget, setBudget] = useState('');
  const [whenText, setWhenText] = useState('');
  const [remote, setRemote] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // A section id passed from the catalog is not a concrete service: let the user pick one inside it.
  const passedSection = sections?.find((s) => s.id === categoryId);
  const activeSection = sections?.find((s) => s.id === (sectionId ?? passedSection?.id));
  const selected = useMemo(
    () => sections?.flatMap((s) => s.children).find((c) => c.id === categoryId) ?? null,
    [sections, categoryId],
  );

  if (!sections) return <Loading />;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const order = await api<Order>('/orders', {
        body: {
          category_id: categoryId,
          title: title.trim(),
          description: description.trim(),
          city,
          budget: budget ? Number(budget.replace(/\D/g, '')) : null,
          when_text: whenText.trim(),
          remote,
        },
      });
      router.replace({ pathname: '/order/[id]', params: { id: String(order.id) } });
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.label}>Услуга</Text>
      {selected ? (
        <View style={styles.wrap}>
          <Chip label={`${selected.name}  ✕`} selected onPress={() => setCategoryId(null)} />
        </View>
      ) : activeSection ? (
        <View style={styles.wrap}>
          <Chip label="← Разделы" onPress={() => { setSectionId(null); setCategoryId(null); }} />
          {activeSection.children.map((c) => (
            <Chip key={c.id} label={c.name} onPress={() => setCategoryId(c.id)} />
          ))}
        </View>
      ) : (
        <View style={styles.wrap}>
          {sections.map((s) => <Chip key={s.id} label={s.name} onPress={() => setSectionId(s.id)} />)}
        </View>
      )}
      <Field label="Что нужно сделать?" value={title} onChangeText={setTitle} placeholder="Например: репетитор по математике для 9 класса" />
      <Field label="Подробности" value={description} onChangeText={setDescription} multiline placeholder="Опишите задачу, пожелания, адрес или район" />
      <Field label="Когда" value={whenText} onChangeText={setWhenText} placeholder="Например: по вечерам, с 1 ноября" />
      <Field label="Бюджет, ₽" value={budget} onChangeText={setBudget} keyboardType="number-pad" placeholder="Можно оставить пустым" />
      <View style={[styles.row, { justifyContent: 'space-between', marginBottom: 12 }]}>
        <Text style={styles.text}>Можно удалённо / онлайн</Text>
        <Switch value={remote} onValueChange={setRemote} />
      </View>
      <CityPicker value={city} onChange={setCity} />
      <ErrorText text={error} />
      <Button title="Опубликовать заказ" onPress={submit} loading={busy} disabled={!selected || title.trim().length < 3} />
    </ScrollView>
  );
}
