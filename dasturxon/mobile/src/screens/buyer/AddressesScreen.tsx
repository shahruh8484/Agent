import { Ionicons } from '@expo/vector-icons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { buyerApi } from '../../api/endpoints';
import { AddressForm } from '../../components/AddressForm';
import { Screen } from '../../components/Screen';
import { Button, Card, Empty } from '../../components/ui';
import { confirmDialog, showAlert } from '../../lib/dialog';
import type { RootStackParamList } from '../../navigation/types';
import { useBuyer } from '../../store/buyer';
import { colors, font, spacing } from '../../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Addresses'>;

/** Адреса: выбор текущего (из шапки ленты/чекаута), по умолчанию, удаление */
export function AddressesScreen({ route, navigation }: Props) {
  const select = !!route.params?.select;
  const profile = useBuyer((s) => s.profile);
  const load = useBuyer((s) => s.load);
  const current = useBuyer((s) => s.addressId);
  const selectAddress = useBuyer((s) => s.selectAddress);
  const addresses = profile?.addresses ?? [];

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      showAlert('Ошибка', errorMessage(e));
    }
  };

  return (
    <Screen title={select ? 'Куда доставить?' : 'Адреса доставки'} back>
      {!addresses.length ? <Empty icon="location-outline" title="Нет адресов" /> : null}
      {addresses.map((a) => (
        <Card
          key={a.id}
          style={{ marginBottom: spacing.sm, borderWidth: 1.5, borderColor: select && a.id === current ? colors.primary : 'transparent' }}
          onPress={
            select
              ? () => {
                  selectAddress(a.id);
                  navigation.goBack();
                }
              : undefined
          }
        >
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Ionicons name={a.isDefault ? 'home' : 'location-outline'} size={22} color={colors.primary} />
            <View style={{ flex: 1, marginLeft: 10 }}>
              <Text style={font.h3}>{a.label ?? (a.isDefault ? 'Основной адрес' : 'Адрес')}</Text>
              <Text style={font.small}>{a.addressText}</Text>
            </View>
            {select && a.id === current ? <Ionicons name="checkmark-circle" size={22} color={colors.primary} /> : null}
          </View>
          {!select ? (
            <View style={{ flexDirection: 'row', marginTop: 10 }}>
              {!a.isDefault ? (
                <Pressable onPress={() => act(() => buyerApi.setDefaultAddress(a.id))} style={{ marginRight: 20 }}>
                  <Text style={{ color: colors.primary }}>Сделать основным</Text>
                </Pressable>
              ) : (
                <Text style={[font.small, { marginRight: 20 }]}>Основной</Text>
              )}
              <Pressable
                onPress={async () => {
                  if (await confirmDialog('Удалить адрес?', a.addressText, 'Удалить', 'Отмена', true)) act(() => buyerApi.deleteAddress(a.id));
                }}
              >
                <Text style={{ color: colors.red }}>Удалить</Text>
              </Pressable>
            </View>
          ) : null}
        </Card>
      ))}
      <Button title="Добавить адрес" icon="add" variant="outline" onPress={() => navigation.navigate('AddAddress')} style={{ marginTop: spacing.md }} />
    </Screen>
  );
}

export function AddAddressScreen({ navigation }: NativeStackScreenProps<RootStackParamList, 'AddAddress'>) {
  const load = useBuyer((s) => s.load);
  const selectAddress = useBuyer((s) => s.selectAddress);
  return (
    <Screen title="Новый адрес" back>
      <AddressForm
        withLabel
        onSubmit={async (v) => {
          const a = await buyerApi.addAddress(v);
          await load();
          selectAddress(a.id);
          navigation.goBack();
        }}
      />
    </Screen>
  );
}
