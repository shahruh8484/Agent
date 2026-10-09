import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { ScrollView, Switch, Text, View } from 'react-native';

import { Button, Chip, ErrorText, Field, Loading, styles } from '../components/ui';
import { api, ApiError } from '../lib/api';
import { useAuth } from '../lib/auth';
import type { Section, Specialist } from '../lib/types';

export default function SpecialistProfile() {
  const { user, refresh } = useAuth();
  const [sections, setSections] = useState<Section[] | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [bio, setBio] = useState('');
  const [experience, setExperience] = useState('');
  const [price, setPrice] = useState('');
  const [remote, setRemote] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Section[]>('/categories').then(setSections);
    if (!user) return;
    api<Specialist>(`/specialists/${user.id}`)
      .then((s) => {
        setSelected(s.categories.map((c) => c.id));
        setBio(s.bio);
        setExperience(String(s.experience_years || ''));
        setPrice(s.price_from != null ? String(s.price_from) : '');
        setRemote(s.remote);
      })
      .catch((e) => { if (!(e instanceof ApiError && e.status === 404)) setError(e.message); });
  }, [user]);

  if (!sections) return <Loading />;

  const toggle = (id: number) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api('/me/specialist', {
        method: 'PUT',
        body: {
          bio: bio.trim(),
          experience_years: Number(experience) || 0,
          price_from: price ? Number(price.replace(/\D/g, '')) : null,
          remote,
          category_ids: selected,
        },
      });
      await refresh();
      router.back();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.h2}>Мои услуги ({selected.length})</Text>
      <Text style={[styles.muted, { marginBottom: 8 }]}>Вы будете получать заказы по выбранным услугам.</Text>
      {sections.map((s) => {
        const count = s.children.filter((c) => selected.includes(c.id)).length;
        return (
          <View key={s.id} style={{ marginBottom: 8 }}>
            <Chip label={`${s.name}${count ? ` · ${count}` : ''} ${open === s.id ? '▲' : '▼'}`} onPress={() => setOpen(open === s.id ? null : s.id)} />
            {open === s.id ? (
              <View style={[styles.wrap, { paddingLeft: 12 }]}>
                {s.children.map((c) => (
                  <Chip key={c.id} label={c.name} selected={selected.includes(c.id)} onPress={() => toggle(c.id)} />
                ))}
              </View>
            ) : null}
          </View>
        );
      })}
      <Field label="О себе" value={bio} onChangeText={setBio} multiline placeholder="Образование, опыт, чем вы лучше других" />
      <Field label="Опыт, лет" value={experience} onChangeText={setExperience} keyboardType="number-pad" />
      <Field label="Цена от, ₽" value={price} onChangeText={setPrice} keyboardType="number-pad" />
      <View style={[styles.row, { justifyContent: 'space-between', marginBottom: 16 }]}>
        <Text style={styles.text}>Работаю онлайн / по всей стране</Text>
        <Switch value={remote} onValueChange={setRemote} />
      </View>
      <ErrorText text={error} />
      <Button title="Сохранить анкету" onPress={save} loading={busy} disabled={!selected.length} />
    </ScrollView>
  );
}
