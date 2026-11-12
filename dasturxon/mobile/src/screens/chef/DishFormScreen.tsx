import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React, { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { chefApi } from '../../api/endpoints';
import type { Category, DishInput, DishType } from '../../api/types';
import { PhotoSlots } from '../../components/PhotoSlots';
import { Screen } from '../../components/Screen';
import { Button, Checkbox, Chip, Field, Loader } from '../../components/ui';
import { confirmDialog } from '../../lib/dialog';
import { CATEGORY_LABEL } from '../../lib/format';
import type { RootStackParamList } from '../../navigation/types';
import { toast } from '../../store/toast';
import { colors } from '../../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'DishForm'>;

const TYPES: { key: DishType; label: string }[] = [
  { key: 'ready', label: 'Готово сейчас' },
  { key: 'order', label: 'На заказ' },
  { key: 'both', label: 'Оба' },
];

const Label = ({ children }: { children: string }) => <Text style={{ fontSize: 13, color: colors.textSecondary, marginBottom: 6, fontWeight: '500' }}>{children}</Text>;

/** Добавление / редактирование блюда (ТЗ 4.3) */
export function DishFormScreen({ route, navigation }: Props) {
  const dishId = route.params?.dishId;
  const [loading, setLoading] = useState(!!dishId);
  const [photos, setPhotos] = useState<string[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState<Category>('lunch');
  const [price, setPrice] = useState('');
  const [weight, setWeight] = useState('');
  const [portions, setPortions] = useState('');
  const [type, setType] = useState<DishType>('ready');
  const [prep, setPrep] = useState('');
  const [ingredients, setIngredients] = useState('');
  const [vegan, setVegan] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!dishId) return;
    chefApi
      .dishes()
      .then((list) => {
        const d = list.find((x) => x.id === dishId);
        if (!d) return;
        setPhotos(d.photoUrls);
        setName(d.name);
        setDescription(d.description ?? '');
        setCategory(d.category);
        setPrice(String(d.price));
        setWeight(d.weight ?? '');
        setPortions(d.portionsLeft == null ? '' : String(d.portionsLeft));
        setType(d.type);
        setPrep(d.prepTimeMinutes ? String(d.prepTimeMinutes) : '');
        setIngredients(d.ingredients ?? '');
        setVegan(d.isVegan);
      })
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setLoading(false));
  }, [dishId]);

  const save = async () => {
    const p = Number(price.replace(/\D/g, ''));
    if (!photos.length) return setError('Добавьте хотя бы одно фото');
    if (name.trim().length < 2) return setError('Введите название блюда');
    if (!p || p < 1000) return setError('Цена — от 1 000 сум');
    if (type !== 'ready' && !Number(prep)) return setError('Укажите время приготовления для блюд «На заказ»');
    const body: DishInput = {
      name: name.trim(),
      description: description.trim() || undefined,
      category,
      price: p,
      weight: weight.trim() || undefined,
      photoUrls: photos,
      portionsLeft: portions.trim() === '' ? null : Number(portions),
      type,
      prepTimeMinutes: type === 'ready' ? null : Number(prep),
      ingredients: ingredients.trim() || undefined,
      isVegan: vegan,
    };
    setBusy(true);
    setError(null);
    try {
      if (dishId) await chefApi.updateDish(dishId, body);
      else await chefApi.createDish(body);
      toast(dishId ? 'Блюдо сохранено' : 'Блюдо добавлено в меню', 'success');
      navigation.goBack();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!dishId || !(await confirmDialog('Удалить блюдо?', name, 'Удалить', 'Отмена', true))) return;
    try {
      await chefApi.deleteDish(dishId);
      navigation.goBack();
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  if (loading) return <Loader />;

  return (
    <Screen title={dishId ? 'Редактирование' : 'Новое блюдо'} back footer={<Button title="Сохранить" onPress={save} loading={busy} />}>
      <Label>Фото (1–5)</Label>
      <PhotoSlots value={photos} onChange={setPhotos} max={5} camera aspect={[16, 9]} />
      <Field label="Название" value={name} onChangeText={setName} maxLength={100} placeholder="Плов по-фергански" />
      <Field label="Описание" value={description} onChangeText={setDescription} maxLength={300} counter={description.length} multiline />
      <Label>Категория</Label>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 12 }}>
        {(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => (
          <Chip key={c} label={CATEGORY_LABEL[c]} active={category === c} onPress={() => setCategory(c)} style={{ marginBottom: 8 }} />
        ))}
      </View>
      <View style={{ flexDirection: 'row' }}>
        <View style={{ flex: 1, marginRight: 8 }}>
          <Field label="Цена, сум" value={price} onChangeText={(v) => setPrice(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="35000" />
        </View>
        <View style={{ flex: 1 }}>
          <Field label="Вес / объём" value={weight} onChangeText={setWeight} placeholder="400 г" maxLength={50} />
        </View>
      </View>
      <Field label="Количество порций" value={portions} onChangeText={(v) => setPortions(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="Без ограничений" hint="Оставьте пустым, если порций сколько угодно" />
      <Label>Тип</Label>
      <View style={{ flexDirection: 'row', marginBottom: 12 }}>
        {TYPES.map((t) => (
          <Chip key={t.key} label={t.label} active={type === t.key} onPress={() => setType(t.key)} color={t.key === 'order' ? colors.blue : colors.primary} />
        ))}
      </View>
      {type !== 'ready' ? <Field label="Время приготовления, мин" value={prep} onChangeText={(v) => setPrep(v.replace(/\D/g, ''))} keyboardType="number-pad" placeholder="60" /> : null}
      <Field label="Состав / аллергены" value={ingredients} onChangeText={setIngredients} maxLength={500} multiline placeholder="Говядина, рис, морковь. Аллергены: кунжут" />
      <Checkbox checked={vegan} onChange={setVegan} label="Веганское блюдо" />
      {error ? <Text style={{ color: colors.red, marginTop: 8 }}>{error}</Text> : null}
      {dishId ? <Button title="Удалить блюдо" variant="ghost" onPress={remove} style={{ marginTop: 16 }} /> : null}
    </Screen>
  );
}
