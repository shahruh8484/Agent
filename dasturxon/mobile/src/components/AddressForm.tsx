import { Ionicons } from '@expo/vector-icons';
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';
import { geocoderEnabled, searchAddress, type GeoResult } from '../api/geocoder';
import type { Point } from '../api/types';
import { TASHKENT } from '../config';
import { addressFromPoint, currentPosition, pointFromAddress } from '../lib/location';
import { colors, font, radius } from '../theme';
import { Button, Field } from './ui';

export interface AddressValue {
  addressText: string;
  lat: number;
  lon: number;
  label?: string;
}

/**
 * Ввод адреса доставки: поиск через Яндекс-геокодер (если есть ключ),
 * иначе — ручной ввод + «Моё местоположение» (expo-location).
 */
export function AddressForm(props: { onSubmit: (v: AddressValue) => Promise<void>; submitTitle?: string; withLabel?: boolean; initialText?: string }) {
  const [text, setText] = useState(props.initialText ?? '');
  const [label, setLabel] = useState('');
  const [point, setPoint] = useState<Point | null>(null);
  const [results, setResults] = useState<GeoResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [locating, setLocating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const skipSearch = useRef(false);

  useEffect(() => {
    if (!geocoderEnabled) return;
    if (skipSearch.current) {
      skipSearch.current = false;
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    if (text.trim().length < 3) return setResults([]);
    timer.current = setTimeout(async () => {
      setSearching(true);
      try {
        setResults(await searchAddress(text.trim()));
      } catch {
        setResults([]);
      } finally {
        setSearching(false);
      }
    }, 400);
  }, [text]);

  const pick = (r: GeoResult) => {
    skipSearch.current = true;
    setText(r.text);
    setPoint({ lat: r.lat, lon: r.lon });
    setResults([]);
  };

  const locate = async () => {
    setLocating(true);
    setError(null);
    const p = await currentPosition();
    if (!p) {
      setLocating(false);
      setError('Не удалось определить местоположение. Введите адрес вручную');
      return;
    }
    setPoint(p);
    const t = await addressFromPoint(p);
    skipSearch.current = true;
    setText(t ?? `Моё местоположение (${p.lat.toFixed(5)}, ${p.lon.toFixed(5)})`);
    setNote('Координаты определены по геолокации. Уточните подъезд и квартиру в адресе.');
    setLocating(false);
  };

  const submit = async () => {
    const t = text.trim();
    if (t.length < 3) return setError('Введите адрес доставки');
    setError(null);
    setBusy(true);
    try {
      let p = point;
      if (!p) p = await pointFromAddress(t);
      // без координат считаем доставку от центра Ташкента — повар увидит точный адрес текстом
      if (!p) p = TASHKENT;
      await props.onSubmit({ addressText: t, lat: p.lat, lon: p.lon, label: label.trim() || undefined });
    } catch (e: any) {
      setError(e?.message ?? 'Не удалось сохранить адрес');
    } finally {
      setBusy(false);
    }
  };

  return (
    <View>
      {props.withLabel ? <Field label="Название (необязательно)" placeholder="Дом, Работа…" value={label} onChangeText={setLabel} maxLength={40} /> : null}
      <Field
        label="Адрес доставки"
        placeholder={geocoderEnabled ? 'Улица, дом' : 'Ташкент, район, улица, дом, кв.'}
        value={text}
        onChangeText={(v) => {
          setText(v);
          setPoint(null);
          setNote(null);
        }}
        testID="address-input"
        error={error}
        right={searching ? <ActivityIndicator color={colors.primary} /> : <Ionicons name="search" size={20} color={colors.muted} />}
      />
      {results.length ? (
        <View style={{ backgroundColor: colors.card, borderRadius: radius.md, marginTop: -8, marginBottom: 12, borderWidth: 1, borderColor: colors.border }}>
          {results.map((r, i) => (
            <Pressable key={`${r.lat}-${r.lon}-${i}`} onPress={() => pick(r)} style={{ padding: 12, borderBottomWidth: i < results.length - 1 ? 1 : 0, borderBottomColor: colors.border }}>
              <Text style={font.body}>{r.title}</Text>
              <Text style={font.small}>{r.subtitle}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      <Button title="Моё местоположение" icon="locate" variant="outline" onPress={locate} loading={locating} small style={{ alignSelf: 'flex-start', marginBottom: 12 }} />
      {note ? <Text style={[font.small, { marginBottom: 12 }]}>{note}</Text> : null}
      <Button title={props.submitTitle ?? 'Сохранить адрес'} onPress={submit} loading={busy} testID="address-submit" />
    </View>
  );
}
