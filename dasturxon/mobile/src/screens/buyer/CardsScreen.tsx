import { Ionicons } from '@expo/vector-icons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { buyerApi } from '../../api/endpoints';
import { CardForm, CARD_NOTICE } from '../../components/CardForm';
import { Screen } from '../../components/Screen';
import { Button, Card, Empty } from '../../components/ui';
import { confirmDialog, showAlert } from '../../lib/dialog';
import type { RootStackParamList } from '../../navigation/types';
import { useBuyer } from '../../store/buyer';
import { toast } from '../../store/toast';
import { colors, font, spacing } from '../../theme';

export function CardsScreen({ navigation }: NativeStackScreenProps<RootStackParamList, 'Cards'>) {
  const profile = useBuyer((s) => s.profile);
  const load = useBuyer((s) => s.load);
  const cards = profile?.cards ?? [];

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await load();
    } catch (e) {
      showAlert('Ошибка', errorMessage(e));
    }
  };

  return (
    <Screen title="Мои карты" back>
      {!cards.length ? <Empty icon="card-outline" title="Нет привязанных карт" /> : null}
      {cards.map((c) => (
        <Card key={c.id} style={{ marginBottom: spacing.sm }}>
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <Ionicons name="card" size={24} color={colors.primary} />
            <View style={{ flex: 1, marginLeft: 10 }}>
              <Text style={font.h3}>{c.maskedPan}</Text>
              <Text style={font.small}>
                {c.cardType.toUpperCase()}
                {c.isDefault ? ' · основная' : ''}
              </Text>
            </View>
          </View>
          <View style={{ flexDirection: 'row', marginTop: 10 }}>
            {!c.isDefault ? (
              <Pressable onPress={() => act(() => buyerApi.setDefaultCard(c.id))} style={{ marginRight: 20 }}>
                <Text style={{ color: colors.primary }}>Сделать основной</Text>
              </Pressable>
            ) : null}
            <Pressable
              onPress={async () => {
                if (cards.length === 1 && !(await confirmDialog('Удалить единственную карту?', 'Без карты нельзя оформить заказ.', 'Удалить', 'Отмена', true))) return;
                if (cards.length > 1 && !(await confirmDialog('Удалить карту?', c.maskedPan, 'Удалить', 'Отмена', true))) return;
                act(() => buyerApi.deleteCard(c.id));
              }}
            >
              <Text style={{ color: colors.red }}>Удалить</Text>
            </Pressable>
          </View>
        </Card>
      ))}
      <Button title="Добавить карту" icon="add" variant="outline" onPress={() => navigation.navigate('AddCard')} style={{ marginTop: spacing.md }} />
      <Text style={[font.tiny, { marginTop: 12, textAlign: 'center' }]}>{CARD_NOTICE}</Text>
    </Screen>
  );
}

export function AddCardScreen({ navigation }: NativeStackScreenProps<RootStackParamList, 'AddCard'>) {
  const load = useBuyer((s) => s.load);
  return (
    <Screen title="Новая карта" back>
      <Text style={[font.body, { marginBottom: 16 }]}>Принимаем карты UZCARD и HUMO</Text>
      <CardForm
        onToken={async (token) => {
          await buyerApi.addCard(token);
          await load();
          toast('Карта привязана', 'success');
          navigation.goBack();
        }}
      />
    </Screen>
  );
}
