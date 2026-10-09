import { useState } from 'react';
import { KeyboardAvoidingView, Platform, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { LangSwitch } from '../components/LangSwitch';
import { Button, ErrorText, Field, styles } from '../components/ui';
import { api, formatPhone } from '../lib/api';
import { useAuth } from '../lib/auth';
import { useT } from '../lib/i18n';
import { colors } from '../lib/theme';
import type { User } from '../lib/types';

export default function Login() {
  const { signIn } = useAuth();
  const { t } = useT();
  const [phone, setPhone] = useState('+998 ');
  const [code, setCode] = useState('');
  const [step, setStep] = useState<'phone' | 'code'>('phone');
  const [devCode, setDevCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function requestCode() {
    setBusy(true);
    setError(null);
    try {
      const res = await api<{ phone: string; debug_code?: string }>('/auth/request-code', { body: { phone } });
      setPhone(res.phone);
      setDevCode(res.debug_code ?? null);
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

  const digits = phone.replace(/\D/g, '');
  return (
    <SafeAreaView style={styles.screen}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={[styles.content, { flex: 1, justifyContent: 'center' }]}>
        <LangSwitch />
        <Text style={[styles.h1, { fontSize: 32, color: colors.primary, marginTop: 16 }]}>Servio</Text>
        <Text style={[styles.text, { marginBottom: 32 }]}>{t('tagline')}</Text>
        {step === 'phone' ? (
          <View>
            <Field label={t('phone')} value={phone} onChangeText={setPhone} keyboardType="phone-pad" placeholder="+998 90 123 45 67" autoFocus />
            <ErrorText text={error} />
            <Button title={t('getCode')} onPress={requestCode} loading={busy} disabled={digits.length !== 12 && digits.length !== 9} />
          </View>
        ) : (
          <View>
            <Text style={[styles.muted, { marginBottom: 12 }]}>{t('codeSentTo', { phone: formatPhone(phone) })}</Text>
            {devCode ? <Text style={{ color: colors.warning, marginBottom: 12 }}>{t('devCode', { code: devCode })}</Text> : null}
            <Field label={t('smsCode')} value={code} onChangeText={setCode} keyboardType="number-pad" maxLength={4} autoFocus />
            <ErrorText text={error} />
            <Button title={t('signIn')} onPress={verify} loading={busy} disabled={code.length !== 4} />
            <Button title={t('changePhone')} variant="secondary" onPress={() => { setStep('phone'); setCode(''); }} style={{ marginTop: 12 }} />
          </View>
        )}
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
