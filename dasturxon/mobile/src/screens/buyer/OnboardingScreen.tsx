import React, { useState } from 'react';
import { Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { buyerApi } from '../../api/endpoints';
import { AddressForm } from '../../components/AddressForm';
import { CardForm, CARD_NOTICE } from '../../components/CardForm';
import { GreenHeader, Screen } from '../../components/Screen';
import { Button, Field } from '../../components/ui';
import { useAuth } from '../../store/auth';
import { useBuyer } from '../../store/buyer';
import { colors, font } from '../../theme';

/** Онбординг покупателя (ТЗ 3.1): имя → адрес → обязательная привязка карты */
export function OnboardingScreen() {
  const profile = useBuyer((s) => s.profile);
  const load = useBuyer((s) => s.load);
  const setUserName = useAuth((s) => s.setUserName);
  const signOut = useAuth((s) => s.signOut);
  const [name, setName] = useState(profile?.name ?? '');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const step = !profile?.name ? 1 : !profile.addresses.length ? 2 : 3;

  const saveName = async () => {
    if (!name.trim()) return setError('Введите имя');
    setBusy(true);
    try {
      await buyerApi.updateProfile(name.trim());
      setUserName(name.trim());
      await load();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const titles = ['Как вас зовут?', 'Куда доставлять?', 'Привяжите карту'];

  return (
    <Screen header={<GreenHeader title="Регистрация" right={<Text onPress={signOut} style={{ color: colors.white }}>Выйти</Text>} />}>
      <View style={{ flexDirection: 'row', marginBottom: 20 }}>
        {[1, 2, 3].map((i) => (
          <View key={i} style={{ flex: 1, height: 4, borderRadius: 2, marginHorizontal: 2, backgroundColor: i <= step ? colors.primary : colors.gray }} />
        ))}
      </View>
      <Text style={[font.h2, { marginBottom: 16 }]}>{titles[step - 1]}</Text>
      {step === 1 ? (
        <>
          <Field label="Имя" placeholder="Например, Азиз" value={name} onChangeText={setName} maxLength={80} error={error} testID="name-input" autoFocus />
          <Button title="Далее" onPress={saveName} loading={busy} testID="name-next" />
        </>
      ) : step === 2 ? (
        <AddressForm
          submitTitle="Далее"
          onSubmit={async (a) => {
            await buyerApi.addAddress({ ...a, isDefault: true });
            await load();
          }}
        />
      ) : (
        <>
          <CardForm
            notice={CARD_NOTICE}
            onToken={async (token) => {
              await buyerApi.addCard(token);
              await load();
            }}
          />
        </>
      )}
    </Screen>
  );
}
