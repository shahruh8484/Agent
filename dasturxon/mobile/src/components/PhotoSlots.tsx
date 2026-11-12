import { Ionicons } from '@expo/vector-icons';
import React, { useState } from 'react';
import { ActivityIndicator, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { errorMessage } from '../api/client';
import { showAlert } from '../lib/dialog';
import { pickAndUpload, type PickSource } from '../lib/upload';
import { colors, radius } from '../theme';
import { Img } from './ui';

/** Набор фото с загрузкой на сервер (блюда, фото кухни) */
export function PhotoSlots(props: { value: string[]; onChange: (v: string[]) => void; max: number; camera?: boolean; aspect?: [number, number] }) {
  const [busy, setBusy] = useState(false);

  const add = async (source: PickSource) => {
    setBusy(true);
    try {
      const url = await pickAndUpload({ source, aspect: props.aspect });
      if (url) props.onChange([...props.value, url].slice(0, props.max));
    } catch (e) {
      showAlert('Не удалось загрузить фото', errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 12 }}>
      {props.value.map((u, i) => (
        <View key={`${u}-${i}`} style={s.slot}>
          <Img uri={u} style={s.img} />
          <Pressable style={s.remove} onPress={() => props.onChange(props.value.filter((_, j) => j !== i))} hitSlop={6}>
            <Ionicons name="close" size={14} color={colors.white} />
          </Pressable>
        </View>
      ))}
      {props.value.length < props.max ? (
        <>
          {props.camera && Platform.OS !== 'web' ? (
            <Pressable style={[s.slot, s.add]} onPress={() => add('camera')} disabled={busy}>
              <Ionicons name="camera-outline" size={26} color={colors.primary} />
              <Text style={s.addText}>Камера</Text>
            </Pressable>
          ) : null}
          <Pressable style={[s.slot, s.add]} onPress={() => add('library')} disabled={busy}>
            {busy ? <ActivityIndicator color={colors.primary} /> : <Ionicons name="images-outline" size={26} color={colors.primary} />}
            <Text style={s.addText}>Галерея</Text>
          </Pressable>
        </>
      ) : null}
    </ScrollView>
  );
}

/** Одна фотография (аватар/обложка) */
export function SinglePhoto(props: { value: string | null; onChange: (v: string) => void; label: string; round?: boolean; aspect?: [number, number] }) {
  const [busy, setBusy] = useState(false);
  const pick = async () => {
    setBusy(true);
    try {
      const url = await pickAndUpload({ aspect: props.aspect });
      if (url) props.onChange(url);
    } catch (e) {
      showAlert('Не удалось загрузить фото', errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const size = props.round ? { width: 96, height: 96, borderRadius: 48 } : { width: '100%' as const, height: 140, borderRadius: radius.md };
  return (
    <Pressable onPress={pick} style={{ alignItems: props.round ? 'center' : 'stretch', marginBottom: 12 }}>
      {props.value ? (
        <Img uri={props.value} style={size} />
      ) : (
        <View style={[size, s.add, { borderStyle: 'dashed' }]}>
          <Ionicons name="camera-outline" size={28} color={colors.primary} />
        </View>
      )}
      <Text style={[s.addText, { textAlign: 'center', marginTop: 6 }]}>{busy ? 'Загрузка…' : props.label}</Text>
    </Pressable>
  );
}

const s = StyleSheet.create({
  slot: { width: 96, height: 96, borderRadius: radius.md, marginRight: 10, overflow: 'hidden' },
  img: { width: 96, height: 96 },
  add: { borderWidth: 1.5, borderColor: colors.primary, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.primaryLight },
  addText: { fontSize: 12, color: colors.primaryDark, marginTop: 4 },
  remove: { position: 'absolute', top: 4, right: 4, backgroundColor: 'rgba(0,0,0,0.6)', borderRadius: 10, padding: 3 },
});
