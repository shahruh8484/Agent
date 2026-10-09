import { useState } from 'react';
import { KeyboardAvoidingView, Platform, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button, ErrorText, Field, styles } from '../components/ui';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { colors } from '../lib/theme';
import type { User } from '../lib/types';

export default function Login() {
  const { signIn } = useAuth();
  const [phone, setPhone] = useState('+7');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<'phone' | 'code'>('phone');
  const [hint, setHint] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function requestCode() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ phone: string; debug_code?: string }>('/auth/request-code', { body: { phone } });
      setPhone(res.phone);
      setHint(res.debug_code ? `Тестовый режим: код ${res.debug_code}` : null);
      setStep('code');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function verify() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ token: string; user: User }>('/auth/verify', { body: { phone, code } });
      await signIn(res.token, res.user);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={styles.screen}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={[styles.content, { flex: 1, justifyContent: 'center' }]}>
        <Text style={[styles.h1, { fontSize: 32, color: colors.primary }]}>Servio</Text>
        <Text style={[styles.text, { marginBottom: 32 }]}>
          Найдите проверенного специалиста для любой задачи — или находите клиентов сами.
        </Text>
        {step === 'phone' ? (
          <View>
            <Field label="Номер телефона" value={phone} onChangeText={setPhone} keyboardType="phone-pad" autoFocus />
            <ErrorText text={error} />
            <Button title="Получить код" onPress={requestCode} loading={busy} disabled={phone.replace(/\D/g, '').length < 10} />
          </View>
        ) : (
          <View>
            <Text style={[styles.muted, { marginBottom: 12 }]}>Код отправлен на {phone}</Text>
            {hint ? <Text style={{ color: colors.warning, marginBottom: 12 }}>{hint}</Text> : null}
            <Field label="Код из SMS" value={code} onChangeText={setCode} keyboardType="number-pad" maxLength={4} autoFocus />
            <ErrorText text={error} />
            <Button title="Войти" onPress={verify} loading={busy} disabled={code.length !== 4} />
            <Button title="Изменить номер" variant="secondary" onPress={() => { setStep('phone'); setCode(''); }} style={{ marginTop: 12 }} />
          </View>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
