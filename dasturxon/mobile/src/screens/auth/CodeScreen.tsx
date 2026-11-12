import { Ionicons } from '@expo/vector-icons';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React, { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { ApiError, errorMessage } from '../../api/client';
import { authApi } from '../../api/endpoints';
import type { Role } from '../../api/types';
import { Button } from '../../components/ui';
import { GreenHeader, Screen } from '../../components/Screen';
import { formatPhone } from '../../lib/format';
import { storage } from '../../lib/storage';
import type { RootStackParamList } from '../../navigation/types';
import { useAuth } from '../../store/auth';
import { colors, font, radius } from '../../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Code'>;

const K_KNOWN = 'dx.knownPhones';

async function knownRole(phone: string): Promise<Role | null> {
  try {
    const m = JSON.parse((await storage.get(K_KNOWN)) ?? '{}');
    return m[phone] ?? null;
  } catch {
    return null;
  }
}

async function rememberRole(phone: string, role: Role) {
  let m: Record<string, Role> = {};
  try {
    m = JSON.parse((await storage.get(K_KNOWN)) ?? '{}');
  } catch {
    /* ignore */
  }
  m[phone] = role;
  await storage.set(K_KNOWN, JSON.stringify(m));
}

export function CodeScreen({ route, navigation }: Props) {
  const { phone } = route.params;
  const signIn = useAuth((s) => s.signIn);
  const [code, setCode] = useState('');
  const [left, setLeft] = useState(route.params.resendAfterSec || 60);
  const [devCode, setDevCode] = useState(route.params.devCode);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [askRole, setAskRole] = useState(false);
  const input = useRef<React.ComponentRef<typeof TextInput>>(null);

  useEffect(() => {
    const t = setInterval(() => setLeft((v) => (v > 0 ? v - 1 : 0)), 1000);
    return () => clearInterval(t);
  }, []);

  const verify = async (c: string, role?: Role) => {
    setBusy(true);
    setError(null);
    try {
      const r = await authApi.verifyOtp(phone, c, role);
      if ('needsRole' in r) {
        // Новый номер — спрашиваем роль; код ещё действителен
        setAskRole(true);
        return;
      }
      await rememberRole(phone, r.user.role);
      await signIn(r);
    } catch (e) {
      setCode('');
      setAskRole(false);
      if (e instanceof ApiError && e.status === 409) {
        // Номер уже зарегистрирован с другой ролью; код не израсходован — входим с этой ролью
        const other: Role = role === 'chef' ? 'buyer' : 'chef';
        await rememberRole(phone, other);
        setError(`${e.message}. Введите код ещё раз — войдём как ${other === 'chef' ? 'повар' : 'покупатель'}.`);
      } else setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const onChange = async (v: string) => {
    const c = v.replace(/\D/g, '').slice(0, 4);
    setCode(c);
    if (c.length === 4) {
      // Роль спрашиваем только для нового номера: сервер вернёт needsRole (ТЗ 3.1/4.1)
      verify(c);
    }
  };

  const resend = async () => {
    setError(null);
    try {
      const r = await authApi.sendOtp(phone);
      setDevCode(r.devCode);
      setLeft(r.resendAfterSec ?? 60);
      setCode('');
      input.current?.focus();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  if (askRole)
    return (
      <Screen header={<GreenHeader title="Кто вы?" back onBack={() => { setAskRole(false); setCode(''); }} />}>
        <Text style={[font.body, { marginBottom: 16 }]}>Выберите, как вы будете пользоваться Dasturxon. Роль закрепляется за номером.</Text>
        <RoleCard
          icon="restaurant-outline"
          title="Покупатель"
          text="Заказываю домашнюю еду у поваров рядом"
          onPress={() => verify(code, 'buyer')}
          disabled={busy}
          testID="role-buyer"
        />
        <RoleCard icon="flame-outline" title="Повар" text="Готовлю дома и продаю свою еду" onPress={() => verify(code, 'chef')} disabled={busy} testID="role-chef" />
        {busy ? <Text style={[font.small, { textAlign: 'center' }]}>Входим…</Text> : null}
      </Screen>
    );

  return (
    <Screen title="Код из SMS" back>
      <Text style={font.body}>Мы отправили код на номер</Text>
      <Text style={[font.h3, { marginTop: 4 }]}>{formatPhone(phone)}</Text>
      <Pressable onPress={() => input.current?.focus()} style={s.boxes}>
        {[0, 1, 2, 3].map((i) => (
          <View key={i} style={[s.box, code.length === i && s.boxActive, error ? { borderColor: colors.red } : null]}>
            <Text style={s.digit}>{code[i] ?? ''}</Text>
          </View>
        ))}
      </Pressable>
      <TextInput
        ref={input}
        testID="code-input"
        value={code}
        onChangeText={onChange}
        keyboardType="number-pad"
        maxLength={4}
        autoFocus
        textContentType="oneTimeCode"
        autoComplete="sms-otp"
        style={s.hidden}
      />
      {error ? <Text style={s.error}>{error}</Text> : null}
      {devCode ? <Text style={[font.small, { textAlign: 'center', marginTop: 8 }]}>Тестовый код: {devCode}</Text> : null}
      {busy ? <Text style={[font.small, { textAlign: 'center', marginTop: 12 }]}>Проверяем…</Text> : null}
      <View style={{ marginTop: 24 }}>
        {left > 0 ? (
          <Text style={[font.small, { textAlign: 'center' }]}>Отправить код повторно через 0:{String(left).padStart(2, '0')}</Text>
        ) : (
          <Button title="Отправить код повторно" variant="outline" onPress={resend} />
        )}
        <Button title="Изменить номер" variant="ghost" onPress={() => navigation.goBack()} style={{ marginTop: 8 }} />
      </View>
    </Screen>
  );
}

function RoleCard(p: { icon: React.ComponentProps<typeof Ionicons>['name']; title: string; text: string; onPress: () => void; disabled?: boolean; testID?: string }) {
  return (
    <Pressable onPress={p.disabled ? undefined : p.onPress} style={({ pressed }) => [s.role, pressed && { borderColor: colors.primary }]} testID={p.testID}>
      <View style={s.roleIcon}>
        <Ionicons name={p.icon} size={28} color={colors.primary} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={font.h3}>{p.title}</Text>
        <Text style={font.small}>{p.text}</Text>
      </View>
      <Ionicons name="chevron-forward" size={20} color={colors.muted} />
    </Pressable>
  );
}

const s = StyleSheet.create({
  boxes: { flexDirection: 'row', justifyContent: 'center', marginTop: 28 },
  box: { width: 56, height: 64, borderRadius: radius.md, borderWidth: 1.5, borderColor: colors.border, backgroundColor: colors.white, marginHorizontal: 6, alignItems: 'center', justifyContent: 'center' },
  boxActive: { borderColor: colors.primary },
  digit: { fontSize: 28, fontWeight: '700', color: colors.text },
  hidden: { position: 'absolute', opacity: 0, width: 1, height: 1 },
  error: { color: colors.red, textAlign: 'center', marginTop: 12 },
  role: { flexDirection: 'row', alignItems: 'center', backgroundColor: colors.white, borderRadius: radius.lg, padding: 16, marginBottom: 12, borderWidth: 1.5, borderColor: colors.border },
  roleIcon: { width: 52, height: 52, borderRadius: 26, backgroundColor: colors.primaryLight, alignItems: 'center', justifyContent: 'center', marginRight: 14 },
});
