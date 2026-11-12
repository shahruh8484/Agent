import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { chefApi } from '../../api/endpoints';
import type { ChefProfile } from '../../api/types';
import { CardForm } from '../../components/CardForm';
import { PhotoSlots, SinglePhoto } from '../../components/PhotoSlots';
import { GreenHeader, Screen } from '../../components/Screen';
import { Button, Card, Checkbox, Chip, Field } from '../../components/ui';
import { confirmDialog } from '../../lib/dialog';
import { currentPosition, DISTRICT_CENTERS, pointFromAddress } from '../../lib/location';
import { useAuth } from '../../store/auth';
import { chefStep, useChef } from '../../store/chef';
import { colors, font, radius } from '../../theme';

/** Регистрация повара (ТЗ 4.1): 1 ник+имя → 2 фото/описание/район/адрес → 3 фото кухни → 4 карта выплат → ожидание проверки */
export function RegistrationScreen() {
  const profile = useChef((s) => s.profile);
  const load = useChef((s) => s.load);
  const signOut = useAuth((s) => s.signOut);
  const serverStep = chefStep(profile);
  // шаги 1 и 2 — одна запись профиля на сервере, но на экране это два шага
  const [localStep, setLocalStep] = useState<1 | 2>(1);
  const step = serverStep === 1 ? localStep : serverStep;

  const logout = async () => {
    if (await confirmDialog('Выйти?', 'Прогресс регистрации сохранён.', 'Выйти')) signOut();
  };

  const stepNo = step === 'waiting' || step === 'done' ? 5 : (step as number);

  return (
    <Screen
      header={
        <GreenHeader title={step === 'waiting' ? 'На проверке' : 'Регистрация повара'} right={<Text onPress={logout} style={{ color: colors.white }}>Выйти</Text>}>
          {step !== 'waiting' ? (
            <View style={{ flexDirection: 'row', marginTop: 12 }}>
              {[1, 2, 3, 4].map((i) => (
                <View key={i} style={{ flex: 1, height: 4, borderRadius: 2, marginHorizontal: 2, backgroundColor: i <= stepNo ? colors.white : 'rgba(255,255,255,0.35)' }} />
              ))}
            </View>
          ) : null}
        </GreenHeader>
      }
    >
      {step === 1 || step === 2 ? <ProfileSteps step={step} setStep={setLocalStep} profile={profile} onSaved={load} /> : null}
      {step === 3 ? <KitchenStep profile={profile!} onDone={load} /> : null}
      {step === 4 ? <PayoutStep onDone={load} /> : null}
      {step === 'waiting' ? <Waiting onRefresh={load} /> : null}
    </Screen>
  );
}

function ProfileSteps({ step, setStep, profile, onSaved }: { step: 1 | 2; setStep: (s: 1 | 2) => void; profile: ChefProfile | null; onSaved: () => Promise<unknown> }) {
  const [nick, setNick] = useState(profile?.nick ?? '');
  const [firstName, setFirstName] = useState(profile?.firstName ?? '');
  const [lastName, setLastName] = useState(profile?.lastName ?? '');
  const [photoUrl, setPhotoUrl] = useState<string | null>(profile?.photoUrl ?? null);
  const [coverUrl, setCoverUrl] = useState<string | null>(profile?.coverUrl ?? null);
  const [bio, setBio] = useState(profile?.bio ?? '');
  const [district, setDistrict] = useState(profile?.district ?? '');
  const [addressText, setAddressText] = useState(profile?.addressText ?? '');
  const [point, setPoint] = useState<{ lat: number; lon: number } | null>(null);
  const [districts, setDistricts] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    chefApi.districts().then(setDistricts).catch(() => setDistricts(Object.keys(DISTRICT_CENTERS)));
  }, []);

  const next = () => {
    if (!/^[A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9_]{3,30}$/.test(nick.trim())) return setError('Ник: 3–30 символов, буквы/цифры/подчёркивание, без пробелов');
    if (!firstName.trim() || !lastName.trim()) return setError('Укажите имя и фамилию');
    setError(null);
    setStep(2);
  };

  const save = async () => {
    if (!photoUrl) return setError('Добавьте фото профиля');
    if (!coverUrl) return setError('Добавьте обложку');
    if (!district) return setError('Выберите район');
    if (addressText.trim().length < 3) return setError('Укажите точный адрес');
    setBusy(true);
    setError(null);
    try {
      const p = point ?? (await pointFromAddress(addressText)) ?? DISTRICT_CENTERS[district] ?? { lat: 41.311, lon: 69.28 };
      await chefApi.updateProfile({
        nick: nick.trim(),
        firstName: firstName.trim(),
        lastName: lastName.trim(),
        photoUrl,
        coverUrl,
        bio: bio.trim() || undefined,
        district,
        addressText: addressText.trim(),
        addressLat: p.lat,
        addressLon: p.lon,
      });
      await onSaved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (step === 1)
    return (
      <>
        <Text style={[font.h2, { marginBottom: 6 }]}>Как вас будут видеть покупатели</Text>
        <Text style={[font.small, { marginBottom: 16 }]}>Ник показывается в ленте и на карточке повара</Text>
        <Field label="Ник" placeholder="Например, Nilufar_oshxona" value={nick} onChangeText={setNick} autoCapitalize="none" maxLength={30} />
        <Field label="Имя" value={firstName} onChangeText={setFirstName} maxLength={50} />
        <Field label="Фамилия" value={lastName} onChangeText={setLastName} maxLength={50} error={error} />
        <Button title="Далее" onPress={next} />
      </>
    );

  return (
    <>
      <Text style={[font.h2, { marginBottom: 12 }]}>Профиль и кухня</Text>
      <SinglePhoto value={photoUrl} onChange={setPhotoUrl} label="Фото профиля" round aspect={[1, 1]} />
      <SinglePhoto value={coverUrl} onChange={setCoverUrl} label="Обложка (ваши блюда)" aspect={[16, 9]} />
      <Field label="О себе" placeholder="Готовлю по семейным рецептам…" value={bio} onChangeText={setBio} multiline maxLength={500} counter={bio.length} />
      <Text style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 6, fontWeight: '500' }}>Район</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 12 }}>
        {districts.map((d) => (
          <Chip key={d} label={d} active={district === d} onPress={() => setDistrict(d)} style={{ marginBottom: 8 }} />
        ))}
      </View>
      <Field label="Точный адрес (для курьера)" placeholder="Улица, дом, подъезд, квартира" value={addressText} onChangeText={(v) => { setAddressText(v); setPoint(null); }} hint="Покупатели не видят ваш точный адрес — только район" />
      <Button
        title={point ? 'Геолокация определена ✓' : 'Определить по геолокации'}
        icon="locate"
        variant="outline"
        small
        style={{ alignSelf: 'flex-start', marginBottom: 16 }}
        onPress={async () => setPoint(await currentPosition())}
      />
      {error ? <Text style={{ color: colors.red, marginBottom: 8 }}>{error}</Text> : null}
      <View style={{ flexDirection: 'row' }}>
        <Button title="Назад" variant="gray" onPress={() => setStep(1)} style={{ flex: 1, marginRight: 8 }} />
        <Button title="Далее" onPress={save} loading={busy} style={{ flex: 2 }} />
      </View>
    </>
  );
}

function KitchenStep({ profile, onDone }: { profile: ChefProfile; onDone: () => Promise<unknown> }) {
  const rejected = profile.verificationStatus === 'rejected';
  const [photos, setPhotos] = useState<string[]>(rejected ? [] : profile.kitchenPhotoUrls ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!photos.length) return setError('Добавьте от 1 до 3 фото кухни');
    setBusy(true);
    setError(null);
    try {
      const r = await chefApi.verification(photos);
      if (r.status === 'rejected') {
        setPhotos([]);
        setError(r.reason ?? 'Фото не прошли проверку');
      }
      await onDone();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Text style={[font.h2, { marginBottom: 6 }]}>Фото вашей кухни</Text>
      <Text style={[font.small, { marginBottom: 16 }]}>1–3 фото: рабочая поверхность, плита, холодильник. Кухня должна быть чистой и хорошо освещённой.</Text>
      {rejected && profile.verificationNote ? (
        <Card style={{ backgroundColor: colors.redLight, marginBottom: 16 }}>
          <Text style={[font.h3, { color: colors.red }]}>Фото не прошли проверку</Text>
          <Text style={font.body}>{profile.verificationNote}</Text>
          <Text style={[font.small, { marginTop: 4 }]}>Переснимите фото с учётом замечаний.</Text>
        </Card>
      ) : null}
      <PhotoSlots value={photos} onChange={setPhotos} max={3} camera />
      {error ? <Text style={{ color: colors.red, marginBottom: 8 }}>{error}</Text> : null}
      <Button title="Отправить на проверку" onPress={submit} loading={busy} />
    </>
  );
}

function PayoutStep({ onDone }: { onDone: () => Promise<unknown> }) {
  const [terms, setTerms] = useState(false);
  return (
    <>
      <Text style={[font.h2, { marginBottom: 6 }]}>Карта для выплат</Text>
      <Text style={[font.small, { marginBottom: 16 }]}>Выплаты раз в неделю на карту UZCARD или HUMO.</Text>
      <View style={{ backgroundColor: colors.primaryLight, borderRadius: radius.md, padding: 12, marginBottom: 16 }}>
        <Text style={[font.small, { color: colors.primaryDark }]}>
          Платформа удерживает комиссию 20% и налог 4% (за вас, как самозанятого) с каждого заказа. Остальное — ваше.
        </Text>
      </View>
      <Checkbox checked={terms} onChange={setTerms} label="Я принимаю условия сервиса и согласен с удержанием налога 4%" />
      <CardForm
        localOnly
        notice={null}
        submitTitle="Сохранить карту"
        disabled={!terms}
        onToken={async (token) => {
          await chefApi.payoutCard(token);
          await onDone();
        }}
      />
    </>
  );
}

function Waiting({ onRefresh }: { onRefresh: () => Promise<unknown> }) {
  const [busy, setBusy] = useState(false);
  return (
    <View style={{ alignItems: 'center', paddingTop: 32 }}>
      <View style={{ width: 96, height: 96, borderRadius: 48, backgroundColor: colors.primaryLight, alignItems: 'center', justifyContent: 'center' }}>
        <Ionicons name="time-outline" size={48} color={colors.primary} />
      </View>
      <Text style={[font.h2, { marginTop: 20, textAlign: 'center' }]}>Заявка на проверке</Text>
      <Text style={[font.body, { marginTop: 8, textAlign: 'center', color: colors.textSecondary }]}>
        Обычно проверка занимает до 24 часов. Мы пришлём уведомление, как только профиль будет одобрен.
      </Text>
      <Button
        title="Проверить статус"
        variant="outline"
        loading={busy}
        onPress={async () => {
          setBusy(true);
          await onRefresh().catch(() => undefined);
          setBusy(false);
        }}
        style={{ marginTop: 24, alignSelf: 'stretch' }}
      />
    </View>
  );
}
