import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';

import { CityPicker } from '../../components/CityPicker';
import { LangSwitch } from '../../components/LangSwitch';
import { Button, Card, Chip, ErrorText, Field, styles } from '../../components/ui';
import { api, formatPhone } from '../../lib/api';
import { useAuth } from '../../lib/auth';
import { formatDate, useT } from '../../lib/i18n';
import { colors } from '../../lib/theme';
import type { Role, User } from '../../lib/types';

export default function Profile() {
  const { user, setUser, refresh, signOut } = useAuth();
  const { t, lang } = useT();
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
      <Text style={[styles.muted, { marginBottom: 16 }]}>{formatPhone(user.phone)}</Text>

      <Text style={styles.label}>{t('language')}</Text>
      <LangSwitch />

      <Text style={[styles.label, { marginTop: 8 }]}>{t('mode')}</Text>
      <View style={styles.wrap}>
        <Chip label={t('iAmClient')} selected={!isSpecialist} onPress={() => update({ role: 'client' })} />
        <Chip label={t('iAmSpecialist')} selected={isSpecialist} onPress={() => update({ role: 'specialist' })} />
      </View>

      {isSpecialist ? (
        <Card>
          <Text style={styles.h2}>{t('forSpecialist')}</Text>
          <Text style={[styles.text, { marginBottom: 4 }]}>
            {t('subscription')}:{' '}
            {user.subscription_until
              ? <Text style={{ color: colors.success }}>{t('activeUntil', { d: formatDate(user.subscription_until, lang) })}</Text>
              : <Text style={{ color: colors.danger }}>{t('notActive')}</Text>}
          </Text>
          <Text style={[styles.muted, { marginBottom: 12 }]}>
            {user.has_specialist_profile ? t('profileFilled') : t('fillProfileHint')}
          </Text>
          <Button
            title={user.has_specialist_profile ? t('editProfile') : t('fillProfile')}
            variant={user.has_specialist_profile ? 'secondary' : 'primary'}
            onPress={() => router.push('/specialist-profile')}
            style={{ marginBottom: 8 }}
          />
          <Button title={t('plansButton')} variant="secondary" onPress={() => router.push('/subscription')} />
        </Card>
      ) : null}

      <Field label={t('name')} value={name} onChangeText={setName} />
      <CityPicker value={city} onChange={setCity} />
      <ErrorText text={error} />
      <Button title={saved ? t('saved') : t('save')} onPress={() => update({ name: name.trim(), city })} disabled={!name.trim() || !city} />
      <Button title={t('signOut')} variant="secondary" onPress={signOut} style={{ marginTop: 24 }} />
    </ScrollView>
  );
}
