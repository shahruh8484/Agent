import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';

import { CityPicker } from '../../components/CityPicker';
import { Button, Card, Chip, ErrorText, Field, styles } from '../../components/ui';
import { api, formatDate } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { colors } from '../../lib/theme';
import type { Role, User } from '../../lib/types';

export default function Profile() {
  const { user, setUser, refresh, signOut } = useAuth();
  const [name, setName] = useState(user?.name ?? '');
  const [city, setCity] = useState(user?.city ?? '');
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useFocusEffect(useCallback(() => { refresh().catch(() => undefined); }, [refresh]));

  if (!user) return null;

  async function update(body: Partial<Pick<User, 'name' | 'city'>> & { role?: Role }) {
    setError(null);
    try {
      setUser(await api<User>('/me', { method: 'PATCH', body }));
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const isSpecialist = user.role === 'specialist';

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
      <Text style={styles.h1}>{user.name}</Text>
      <Text style={[styles.muted, { marginBottom: 16 }]}>{user.phone}</Text>

      <Text style={styles.label}>Режим</Text>
      <View style={styles.wrap}>
        <Chip label="Я клиент" selected={!isSpecialist} onPress={() => update({ role: 'client' })} />
        <Chip label="Я специалист" selected={isSpecialist} onPress={() => update({ role: 'specialist' })} />
      </View>

      {isSpecialist ? (
        <Card>
          <Text style={styles.h2}>Для специалиста</Text>
          <Text style={[styles.text, { marginBottom: 4 }]}>
            Подписка:{' '}
            {user.subscription_until
              ? <Text style={{ color: colors.success }}>активна до {formatDate(user.subscription_until)}</Text>
              : <Text style={{ color: colors.danger }}>не активна</Text>}
          </Text>
          <Text style={[styles.muted, { marginBottom: 12 }]}>
            {user.has_specialist_profile ? 'Анкета заполнена' : 'Заполните анкету, чтобы получать заказы'}
          </Text>
          <Button
            title={user.has_specialist_profile ? 'Редактировать анкету' : 'Заполнить анкету'}
            variant={user.has_specialist_profile ? 'secondary' : 'primary'}
            onPress={() => router.push('/specialist-profile')}
            style={{ marginBottom: 8 }}
          />
          <Button title="Тарифы и подписка" variant="secondary" onPress={() => router.push('/subscription')} />
        </Card>
      ) : null}

      <Field label="Имя" value={name} onChangeText={setName} />
      <CityPicker value={city} onChange={setCity} />
      <ErrorText text={error} />
      <Button title={saved ? 'Сохранено ✓' : 'Сохранить'} onPress={() => update({ name: name.trim(), city: city.trim() })} disabled={!name.trim() || !city.trim()} />
      <Button title="Выйти" variant="secondary" onPress={signOut} style={{ marginTop: 24 }} />
    </ScrollView>
  );
}
