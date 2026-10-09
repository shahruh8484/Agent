import { useState } from 'react';
import { ScrollView, Text } from 'react-native';

import { CityPicker } from '../components/CityPicker';
import { Button, Card, ErrorText, Field, styles } from '../components/ui';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { colors } from '../lib/theme';
import type { Role, User } from '../lib/types';

export default function Onboarding() {
  const { user, setUser } = useAuth();
  const [name, setName] = useState(user?.name ?? '');
  const [city, setCity] = useState(user?.city ?? '');
  const [role, setRole] = useState<Role>(user?.role ?? 'client');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      setUser(await api<User>('/me', { method: 'PATCH', body: { name: name.trim(), city: city.trim(), role } }));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  const roleCard = (r: Role, title: string, text: string) => (
    <Card onPress={() => setRole(r)} style={role === r && { borderColor: colors.primary, borderWidth: 2 }}>
      <Text style={styles.h2}>{title}</Text>
      <Text style={styles.muted}>{text}</Text>
    </Card>
  );

  return (
    <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Field label="Как вас зовут?" value={name} onChangeText={setName} placeholder="Имя" />
      <CityPicker value={city} onChange={setCity} />
      <Text style={[styles.label, { marginTop: 8 }]}>Я хочу</Text>
      {roleCard('client', 'Найти специалиста', 'Разместите заказ — специалисты сами предложат свои услуги.')}
      {roleCard('specialist', 'Находить клиентов', 'Откликайтесь на заказы по подписке. Первые дни — бесплатно.')}
      <ErrorText text={error} />
      <Button title="Продолжить" onPress={save} loading={busy} disabled={!name.trim() || !city.trim()} />
    </ScrollView>
  );
}
