import * as ImagePicker from 'expo-image-picker';
import { uploadsApi } from '../api/endpoints';
import { showAlert } from './dialog';

export type PickSource = 'library' | 'camera';

const ALLOWED = ['image/jpeg', 'image/png', 'image/webp', 'image/heic'];

/** Выбрать фото (галерея/камера) и загрузить на сервер. Возвращает URL или null, если отменено. */
export async function pickAndUpload(opts: { source?: PickSource; aspect?: [number, number] } = {}): Promise<string | null> {
  const source = opts.source ?? 'library';
  const perm =
    source === 'camera'
      ? await ImagePicker.requestCameraPermissionsAsync()
      : await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!perm.granted) {
    showAlert('Нет доступа', source === 'camera' ? 'Разрешите доступ к камере в настройках' : 'Разрешите доступ к фото в настройках');
    return null;
  }
  const options: ImagePicker.ImagePickerOptions = {
    mediaTypes: ['images'],
    quality: 0.7,
    allowsEditing: !!opts.aspect,
    aspect: opts.aspect,
  };
  const res =
    source === 'camera' ? await ImagePicker.launchCameraAsync(options) : await ImagePicker.launchImageLibraryAsync(options);
  if (res.canceled || !res.assets?.length) return null;
  return uploadUri(res.assets[0].uri, res.assets[0].mimeType);
}

export async function uploadUri(uri: string, mimeType?: string | null): Promise<string> {
  const blob = await (await fetch(uri)).blob();
  let type = (mimeType || blob.type || 'image/jpeg').toLowerCase();
  if (type === 'image/jpg') type = 'image/jpeg';
  if (!ALLOWED.includes(type)) type = 'image/jpeg';
  const { url } = await uploadsApi.upload(blob, type);
  return url;
}
