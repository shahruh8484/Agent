import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { Text, View } from 'react-native';
import { errorMessage } from '../api/client';
import * as payme from '../api/payme';
import { PAYMENT_MODE } from '../config';
import { colors, font, radius } from '../theme';
import { Button, Field } from './ui';

export const CARD_NOTICE = 'Карта нужна только для оплаты заказов. Данные карты надёжно защищены.';

/** Токен карты для mock-режима платёжного шлюза: mock-<last4> / humo-<last4> */
export function mockCardToken(number: string): string {
  const d = number.replace(/\D/g, '');
  return `${d.startsWith('9860') ? 'humo' : 'mock'}-${d.slice(-4)}`;
}

const fmtNumber = (v: string) =>
  v
    .replace(/\D/g, '')
    .slice(0, 16)
    .replace(/(\d{4})(?=\d)/g, '$1 ');

const fmtExpiry = (v: string) => {
  const d = v.replace(/\D/g, '').slice(0, 4);
  return d.length > 2 ? `${d.slice(0, 2)}/${d.slice(2)}` : d;
};

function validate(number: string, expiry: string, localOnly: boolean): string | null {
  const d = number.replace(/\D/g, '');
  if (d.length !== 16) return 'Введите 16 цифр номера карты';
  if (localOnly && !d.startsWith('8600') && !d.startsWith('5614') && !d.startsWith('9860')) return 'Нужна карта UZCARD или HUMO';
  const e = expiry.replace(/\D/g, '');
  if (e.length !== 4) return 'Введите срок действия ММ/ГГ';
  const mm = Number(e.slice(0, 2));
  const yy = Number(e.slice(2));
  if (mm < 1 || mm > 12) return 'Неверный месяц';
  const now = new Date();
  const curYY = now.getFullYear() % 100;
  if (yy < curYY || (yy === curYY && mm < now.getMonth() + 1)) return 'Срок действия карты истёк';
  return null;
}

/**
 * Форма привязки карты. mock: сразу отдаёт токен mock-xxxx.
 * payme: cards.create → SMS-код (cards.get_verify_code) → cards.verify → токен.
 */
export function CardForm(props: {
  onToken: (token: string) => Promise<void>;
  submitTitle?: string;
  localOnly?: boolean;
  notice?: string | null;
  disabled?: boolean;
}) {
  const [number, setNumber] = useState('');
  const [expiry, setExpiry] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ token: string; phone: string } | null>(null);
  const [code, setCode] = useState('');

  const submit = async () => {
    const err = validate(number, expiry, !!props.localOnly);
    setError(err);
    if (err) return;
    setBusy(true);
    try {
      if (PAYMENT_MODE === 'mock') {
        await props.onToken(mockCardToken(number));
      } else {
        const card = await payme.createCard(number, expiry);
        const sent = await payme.sendVerifyCode(card.token);
        setPending({ token: card.token, phone: sent.phone });
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    if (!pending) return;
    if (code.length < 4) return setError('Введите код из SMS');
    setBusy(true);
    try {
      const card = await payme.verifyCard(pending.token, code);
      await props.onToken(card.token);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  if (pending)
    return (
      <View>
        <Text style={[font.body, { marginBottom: 12 }]}>Код подтверждения отправлен на {pending.phone}</Text>
        <Field label="Код из SMS" value={code} onChangeText={(v) => setCode(v.replace(/\D/g, '').slice(0, 6))} keyboardType="number-pad" error={error} />
        <Button title="Подтвердить карту" onPress={verify} loading={busy} />
        <Button title="Изменить карту" variant="ghost" onPress={() => setPending(null)} style={{ marginTop: 8 }} />
      </View>
    );

  return (
    <View>
      <Field
        label="Номер карты"
        placeholder="8600 0000 0000 0000"
        value={number}
        onChangeText={(v) => setNumber(fmtNumber(v))}
        keyboardType="number-pad"
        maxLength={19}
        testID="card-number"
        right={<Ionicons name="card-outline" size={22} color={colors.muted} />}
      />
      <Field label="Срок действия" placeholder="ММ/ГГ" value={expiry} onChangeText={(v) => setExpiry(fmtExpiry(v))} keyboardType="number-pad" maxLength={5} style={{ maxWidth: 120 }} testID="card-expiry" error={error} />
      {props.notice !== null ? (
        <View style={{ flexDirection: 'row', backgroundColor: colors.primaryLight, padding: 12, borderRadius: radius.md, marginBottom: 16 }}>
          <Ionicons name="shield-checkmark" size={20} color={colors.primary} />
          <Text style={[font.small, { marginLeft: 8, flex: 1, color: colors.primaryDark }]}>{props.notice ?? CARD_NOTICE}</Text>
        </View>
      ) : null}
      <Button title={props.submitTitle ?? 'Привязать карту'} onPress={submit} loading={busy} disabled={props.disabled} testID="card-submit" />
      {PAYMENT_MODE === 'mock' ? <Text style={[font.tiny, { textAlign: 'center', marginTop: 8 }]}>Тестовый режим оплаты</Text> : null}
    </View>
  );
}
