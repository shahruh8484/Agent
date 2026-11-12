import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { buyerApi } from '../../api/endpoints';
import { Screen } from '../../components/Screen';
import { Button, Card, Divider, Field, ListRow, SectionTitle, Toggle } from '../../components/ui';
import { confirmDialog, showAlert } from '../../lib/dialog';
import { formatPhone } from '../../lib/format';
import { storage } from '../../lib/storage';
import { useNav } from '../../navigation/types';
import { useAuth } from '../../store/auth';
import { useBuyer } from '../../store/buyer';
import { toast } from '../../store/toast';
import { colors, font } from '../../theme';

const K_NOTIF = 'dx.notifEnabled';

/** Профиль покупателя (ТЗ 3.8) */
export function ProfileScreen() {
  const nav = useNav();
  const profile = useBuyer((s) => s.profile);
  const load = useBuyer((s) => s.load);
  const signOut = useAuth((s) => s.signOut);
  const setUserName = useAuth((s) => s.setUserName);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(profile?.name ?? '');
  const [notif, setNotif] = useState(true);

  useFocusEffect(
    useCallback(() => {
      load().catch(() => undefined);
    }, [load]),
  );

  useEffect(() => {
    storage.get(K_NOTIF).then((v) => setNotif(v !== '0'));
  }, []);

  const saveName = async () => {
    try {
      await buyerApi.updateProfile(name.trim());
      setUserName(name.trim());
      await load();
      setEditing(false);
      toast('Имя сохранено', 'success');
    } catch (e) {
      showAlert('Ошибка', errorMessage(e));
    }
  };

  const logout = async () => {
    if (await confirmDialog('Выйти из аккаунта?', 'Вы сможете войти снова по номеру телефона.', 'Выйти')) signOut();
  };

  const deleteAccount = async () => {
    if (!(await confirmDialog('Удалить аккаунт?', 'Личные данные, адреса и карты будут удалены. Это действие необратимо.', 'Удалить', 'Отмена', true))) return;
    try {
      await buyerApi.deleteAccount();
      signOut();
    } catch (e) {
      showAlert('Не удалось удалить', errorMessage(e));
    }
  };

  const def = profile?.addresses.find((a) => a.isDefault);
  const card = profile?.cards.find((c) => c.isDefault);

  return (
    <Screen title="Профиль">
      <Card>
        {editing ? (
          <>
            <Field label="Имя" value={name} onChangeText={setName} maxLength={80} autoFocus />
            <View style={{ flexDirection: 'row' }}>
              <Button title="Сохранить" small onPress={saveName} style={{ flex: 1, marginRight: 8 }} />
              <Button title="Отмена" small variant="gray" onPress={() => setEditing(false)} style={{ flex: 1 }} />
            </View>
          </>
        ) : (
          <View style={{ flexDirection: 'row', alignItems: 'center' }}>
            <View style={{ flex: 1 }}>
              <Text style={font.h2}>{profile?.name ?? '—'}</Text>
              <Text style={font.small}>{profile ? formatPhone(profile.phone) : ''}</Text>
            </View>
            <Text
              style={{ color: colors.primary }}
              onPress={() => {
                setName(profile?.name ?? '');
                setEditing(true);
              }}
            >
              Изменить
            </Text>
          </View>
        )}
      </Card>

      <SectionTitle>Доставка и оплата</SectionTitle>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <ListRow icon="location-outline" title="Адреса доставки" subtitle={def?.addressText ?? 'Не указан'} onPress={() => nav.navigate('Addresses')} />
        <Divider />
        <ListRow icon="card-outline" title="Карты" subtitle={card ? `${card.maskedPan} · ${card.cardType.toUpperCase()}` : 'Не привязана'} onPress={() => nav.navigate('Cards')} />
      </Card>

      <SectionTitle>Настройки</SectionTitle>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <ListRow
          icon="notifications-outline"
          title="Уведомления о заказах"
          right={
            <Toggle
              value={notif}
              onChange={(v) => {
                setNotif(v);
                storage.set(K_NOTIF, v ? '1' : '0');
              }}
            />
          }
        />
        <Divider />
        <ListRow icon="mail-unread-outline" title="Центр уведомлений" onPress={() => nav.navigate('Notifications')} />
        <Divider />
        <ListRow icon="help-circle-outline" title="Поддержка" onPress={() => nav.navigate('Support')} />
      </Card>

      <Card style={{ padding: 0, overflow: 'hidden', marginTop: 16 }}>
        <ListRow icon="log-out-outline" title="Выйти" onPress={logout} danger />
      </Card>
      <Button title="Удалить аккаунт" variant="ghost" small onPress={deleteAccount} style={{ marginTop: 12, alignSelf: 'center' }} />
    </Screen>
  );
}
