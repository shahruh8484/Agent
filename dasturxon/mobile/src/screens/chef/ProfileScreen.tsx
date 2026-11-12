import { useFocusEffect } from '@react-navigation/native';
import React, { useCallback, useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { chefApi } from '../../api/endpoints';
import type { ChefStats } from '../../api/types';
import { CardForm } from '../../components/CardForm';
import { SinglePhoto } from '../../components/PhotoSlots';
import { Screen } from '../../components/Screen';
import { Stars } from '../../components/Stars';
import { Avatar, Button, Card, Checkbox, Chip, Divider, Field, ListRow, SectionTitle, Toggle } from '../../components/ui';
import { confirmDialog } from '../../lib/dialog';
import { dateShort, formatPhone } from '../../lib/format';
import { storage } from '../../lib/storage';
import { useNav } from '../../navigation/types';
import { useAuth } from '../../store/auth';
import { useChef } from '../../store/chef';
import { toast } from '../../store/toast';
import { colors, font } from '../../theme';

/** Профиль повара (ТЗ 4.5) */
export function ChefProfileScreen() {
  const nav = useNav();
  const profile = useChef((s) => s.profile);
  const load = useChef((s) => s.load);
  const signOut = useAuth((s) => s.signOut);
  const [stats, setStats] = useState<ChefStats | null>(null);
  const [notif, setNotif] = useState(true);

  useFocusEffect(
    useCallback(() => {
      load().catch(() => undefined);
      chefApi.stats().then(setStats).catch(() => undefined);
    }, [load]),
  );

  useEffect(() => {
    storage.get('dx.notifEnabled').then((v) => setNotif(v !== '0'));
  }, []);

  if (!profile) return null;

  return (
    <Screen title="Профиль">
      <Card>
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Avatar uri={profile.photoUrl} size={64} />
          <View style={{ flex: 1, marginLeft: 12 }}>
            <Text style={font.h2}>{profile.nick}</Text>
            <Text style={font.small}>
              {profile.firstName} {profile.lastName} · {profile.district}
            </Text>
            <Text style={font.small}>{formatPhone(profile.phone)}</Text>
          </View>
        </View>
        <Button title="Редактировать профиль" variant="secondary" small onPress={() => nav.navigate('ChefEditProfile')} style={{ marginTop: 12 }} />
      </Card>

      <SectionTitle>Статистика</SectionTitle>
      <Card>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
          <View>
            <Stars value={stats?.rating ?? profile.rating ?? 0} count={stats?.reviewsCount ?? profile.reviewsCount} />
            <Text style={font.tiny}>рейтинг</Text>
          </View>
          <View style={{ alignItems: 'flex-end' }}>
            <Text style={font.h3}>{stats?.totalOrders ?? profile.totalOrders ?? 0}</Text>
            <Text style={font.tiny}>заказов всего</Text>
          </View>
        </View>
        {stats?.onPlatformSince ? <Text style={[font.small, { marginTop: 8 }]}>На платформе с {dateShort(stats.onPlatformSince)}</Text> : null}
      </Card>

      <SectionTitle>Выплаты и отзывы</SectionTitle>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <ListRow
          icon="card-outline"
          title="Карта для выплат"
          subtitle={profile.payoutCard ? `${profile.payoutCard.maskedPan} · ${profile.payoutCard.cardType.toUpperCase()}` : 'Не привязана'}
          onPress={() => nav.navigate('PayoutCard')}
        />
        <Divider />
        <ListRow icon="chatbubbles-outline" title="Отзывы" subtitle={`${profile.reviewsCount ?? 0}`} onPress={() => nav.navigate('ChefReviews', {})} />
        <Divider />
        <ListRow icon="mail-unread-outline" title="Уведомления" onPress={() => nav.navigate('Notifications')} />
      </Card>

      <SectionTitle>Настройки</SectionTitle>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <ListRow
          icon="notifications-outline"
          title="Push о новых заказах"
          right={
            <Toggle
              value={notif}
              onChange={(v) => {
                setNotif(v);
                storage.set('dx.notifEnabled', v ? '1' : '0');
              }}
            />
          }
        />
        <Divider />
        <ListRow icon="help-circle-outline" title="Поддержка" onPress={() => nav.navigate('Support')} />
        <Divider />
        <ListRow
          icon="log-out-outline"
          title="Выйти"
          danger
          onPress={async () => {
            if (await confirmDialog('Выйти из аккаунта?', 'Перед выходом переключитесь в офлайн, чтобы не пропустить заказы.', 'Выйти')) signOut();
          }}
        />
      </Card>
    </Screen>
  );
}

export function ChefEditProfileScreen() {
  const nav = useNav();
  const profile = useChef((s) => s.profile);
  const setProfile = useChef((s) => s.setProfile);
  const [nick, setNick] = useState(profile?.nick ?? '');
  const [bio, setBio] = useState(profile?.bio ?? '');
  const [photoUrl, setPhotoUrl] = useState(profile?.photoUrl ?? null);
  const [coverUrl, setCoverUrl] = useState(profile?.coverUrl ?? null);
  const [district, setDistrict] = useState(profile?.district ?? '');
  const [addressText, setAddressText] = useState(profile?.addressText ?? '');
  const [districts, setDistricts] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    chefApi.districts().then(setDistricts).catch(() => undefined);
  }, []);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await chefApi.updateProfile({
        nick: nick.trim(),
        bio: bio.trim(),
        photoUrl: photoUrl ?? undefined,
        coverUrl: coverUrl ?? undefined,
        district,
        addressText: addressText.trim(),
      });
      setProfile(p);
      toast('Профиль сохранён', 'success');
      nav.goBack();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Screen title="Редактирование" back footer={<Button title="Сохранить" onPress={save} loading={busy} />}>
      <SinglePhoto value={photoUrl} onChange={setPhotoUrl} label="Изменить фото" round aspect={[1, 1]} />
      <SinglePhoto value={coverUrl} onChange={setCoverUrl} label="Изменить обложку" aspect={[16, 9]} />
      <Field label="Ник" value={nick} onChangeText={setNick} maxLength={30} autoCapitalize="none" />
      <Field label="О себе" value={bio} onChangeText={setBio} multiline maxLength={500} counter={bio.length} />
      <Text style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 6 }}>Район</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 12 }}>
        {districts.map((d) => (
          <Chip key={d} label={d} active={district === d} onPress={() => setDistrict(d)} style={{ marginBottom: 8 }} />
        ))}
      </View>
      <Field label="Точный адрес" value={addressText} onChangeText={setAddressText} hint="Не показывается покупателям" />
      {error ? <Text style={{ color: colors.red }}>{error}</Text> : null}
    </Screen>
  );
}

export function PayoutCardScreen() {
  const nav = useNav();
  const load = useChef((s) => s.load);
  const profile = useChef((s) => s.profile);
  const [terms, setTerms] = useState(!!profile?.termsAccepted);
  return (
    <Screen title="Карта для выплат" back>
      {profile?.payoutCard ? (
        <Card style={{ marginBottom: 16 }}>
          <Text style={font.small}>Текущая карта</Text>
          <Text style={font.h3}>
            {profile.payoutCard.maskedPan} · {profile.payoutCard.cardType.toUpperCase()}
          </Text>
        </Card>
      ) : null}
      <Text style={[font.body, { marginBottom: 12 }]}>Новая карта UZCARD или HUMO</Text>
      {!profile?.termsAccepted ? <Checkbox checked={terms} onChange={setTerms} label="Принимаю условия сервиса и удержание налога 4%" /> : null}
      <CardForm
        localOnly
        notice={null}
        submitTitle="Сохранить карту"
        disabled={!terms}
        onToken={async (token) => {
          await chefApi.payoutCard(token);
          await load();
          toast('Карта для выплат обновлена', 'success');
          nav.goBack();
        }}
      />
    </Screen>
  );
}
