import React, { useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import { ALLOW_SERVER_CHANGE, getApiUrl } from '../../config';
import { saveServerUrl } from '../../lib/server';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { errorMessage } from '../../api/client';
import { authApi } from '../../api/endpoints';
import { Button } from '../../components/ui';
import { maskPhone, phoneDigits } from '../../lib/format';
import { useNav } from '../../navigation/types';
import { colors, font, radius } from '../../theme';

export function PhoneScreen() {
  const nav = useNav();
  const insets = useSafeAreaInsets();
  const [digits, setDigits] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const valid = digits.length === 9;

  const submit = async () => {
    if (!valid) return;
    setBusy(true);
    setError(null);
    const phone = `+998${digits}`;
    try {
      const r = await authApi.sendOtp(phone);
      nav.navigate('Code', { phone, devCode: r.devCode, resendAfterSec: r.resendAfterSec ?? 60 });
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: colors.white }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <View style={[s.top, { paddingTop: insets.top + 48 }]}>
        <Text style={s.logo}>Dasturxon</Text>
        <Text style={s.tagline}>Домашняя еда от соседей</Text>
      </View>
      <View style={s.body}>
        <Text style={font.h2}>Вход</Text>
        <Text style={[font.small, { marginTop: 6, marginBottom: 20 }]}>Введите номер телефона — мы отправим SMS с кодом</Text>
        <View style={s.phoneWrap}>
          <Text style={s.prefix}>+998</Text>
          <TextInput
            testID="phone-input"
            style={s.phoneInput}
            value={maskPhone(digits)}
            onChangeText={(v) => setDigits(phoneDigits(v))}
            placeholder="90 123 45 67"
            placeholderTextColor={colors.muted}
            keyboardType="phone-pad"
            autoFocus
            maxLength={12}
            onSubmitEditing={submit}
          />
        </View>
        {error ? <Text style={s.error}>{error}</Text> : null}
        <Button title="Получить код" onPress={submit} disabled={!valid} loading={busy} style={{ marginTop: 20 }} testID="get-code" />
        <Text style={[font.tiny, { textAlign: 'center', marginTop: 16 }]}>Нажимая «Получить код», вы соглашаетесь с условиями сервиса и политикой конфиденциальности</Text>
        {ALLOW_SERVER_CHANGE ? <ServerSetting /> : null}
      </View>
    </KeyboardAvoidingView>
  );
}

/** Тестовые сборки: адрес сервера можно поменять без пересборки приложения */
function ServerSetting() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(getApiUrl());
  const [current, setCurrent] = useState(getApiUrl());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const save = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const url = await saveServerUrl(value);
      setCurrent(url);
      setValue(url);
      setOpen(false);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <Pressable onPress={() => setOpen(true)} style={{ marginTop: 28, alignItems: 'center' }} testID="server-open">
        <Text style={[font.tiny, { color: colors.muted }]}>Сервер: {current} · изменить</Text>
      </Pressable>
    );
  }
  return (
    <View style={{ marginTop: 24 }}>
      <Text style={font.small}>Адрес сервера</Text>
      <TextInput
        testID="server-input"
        style={[s.phoneInput, s.serverInput]}
        value={value}
        onChangeText={setValue}
        placeholder="http://192.168.1.10:4000"
        placeholderTextColor={colors.muted}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
      />
      {msg ? <Text style={s.error}>{msg}</Text> : null}
      <Button title="Сохранить" onPress={save} loading={busy} style={{ marginTop: 10 }} testID="server-save" />
    </View>
  );
}

const s = StyleSheet.create({
  serverInput: { borderWidth: 1, borderColor: colors.border, borderRadius: radius.md, paddingHorizontal: 12, paddingVertical: 10, marginTop: 6, fontSize: 15 },
  top: { backgroundColor: colors.primary, paddingBottom: 40, alignItems: 'center', borderBottomLeftRadius: 32, borderBottomRightRadius: 32 },
  logo: { color: colors.white, fontSize: 36, fontWeight: '800', letterSpacing: 0.5 },
  tagline: { color: 'rgba(255,255,255,0.85)', fontSize: 15, marginTop: 6 },
  body: { padding: 24 },
  phoneWrap: { flexDirection: 'row', alignItems: 'center', borderWidth: 1.5, borderColor: colors.border, borderRadius: radius.md, paddingHorizontal: 14, height: 56 },
  prefix: { fontSize: 18, fontWeight: '600', color: colors.text, marginRight: 8 },
  phoneInput: { flex: 1, fontSize: 18, color: colors.text, height: '100%' },
  error: { color: colors.red, marginTop: 8 },
});
