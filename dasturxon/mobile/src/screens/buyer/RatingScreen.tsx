import type { NativeStackScreenProps } from '@react-navigation/native-stack';
import React, { useState } from 'react';
import { Text, View } from 'react-native';
import { errorMessage } from '../../api/client';
import { ordersApi } from '../../api/endpoints';
import { Screen } from '../../components/Screen';
import { Stars } from '../../components/Stars';
import { Avatar, Button, Chip, Field } from '../../components/ui';
import { REVIEW_TAGS } from '../../lib/format';
import { repeatOrder } from '../../lib/repeatOrder';
import type { RootStackParamList } from '../../navigation/types';
import { skipRating } from '../../store/buyer';
import { toast } from '../../store/toast';
import { colors, font } from '../../theme';

type Props = NativeStackScreenProps<RootStackParamList, 'Rating'>;

/** Оценка заказа (ТЗ 3.6) */
export function RatingScreen({ route, navigation }: Props) {
  const { orderId, chefNick, chefPhoto, chefId } = route.params;
  const [rating, setRating] = useState(0);
  const [tags, setTags] = useState<string[]>([]);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);

  const toggle = (k: string) => setTags((t) => (t.includes(k) ? t.filter((x) => x !== k) : [...t, k]));

  const send = async () => {
    if (!rating) return setError('Поставьте оценку от 1 до 5');
    setBusy(true);
    setError(null);
    try {
      await ordersApi.rate(orderId, { rating, tags, comment: comment.trim() || undefined });
      toast('Спасибо за оценку!', 'success');
      setSent(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const skip = async () => {
    await skipRating(orderId);
    navigation.goBack();
  };

  const again = async () => {
    try {
      const o = await ordersApi.get(orderId);
      navigation.goBack();
      await repeatOrder(navigation, chefId, o.items);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  return (
    <Screen title="Оценка заказа" back>
      <View style={{ alignItems: 'center', marginTop: 8 }}>
        <Avatar uri={chefPhoto} size={88} />
        <Text style={[font.h3, { marginTop: 8 }]}>{chefNick}</Text>
        <Text style={[font.h2, { marginTop: 16, textAlign: 'center' }]}>{sent ? 'Спасибо за отзыв!' : `Как вам еда от ${chefNick}?`}</Text>
        {!sent ? (
          <View style={{ marginVertical: 16 }}>
            <Stars value={rating} onChange={setRating} size={40} />
          </View>
        ) : null}
      </View>
      {!sent ? (
        <>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 12 }}>
            {REVIEW_TAGS.map((t) => (
              <Chip key={t.key} label={t.label} active={tags.includes(t.key)} onPress={() => toggle(t.key)} style={{ marginBottom: 8 }} />
            ))}
          </View>
          <Field placeholder="Комментарий (необязательно)" value={comment} onChangeText={setComment} multiline maxLength={1000} />
          {error ? <Text style={{ color: colors.red, marginBottom: 8 }}>{error}</Text> : null}
          <Button title="Отправить оценку" onPress={send} loading={busy} testID="send-rating" />
          <Button title="Пропустить" variant="ghost" small onPress={skip} style={{ marginTop: 8, alignSelf: 'center' }} />
        </>
      ) : (
        <Button title="Готово" onPress={() => navigation.goBack()} style={{ marginTop: 16 }} />
      )}
      <View style={{ marginTop: 24, alignItems: 'center' }}>
        <Text style={font.small}>Заказать снова?</Text>
        <Button title="Повторить заказ" icon="repeat" variant="outline" small onPress={again} style={{ marginTop: 8 }} />
      </View>
    </Screen>
  );
}
