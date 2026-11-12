import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { colors } from '../theme';

/** Звёзды рейтинга: только отображение, либо выбор (onChange) */
export function Stars(props: { value: number; size?: number; count?: number; onChange?: (v: number) => void; light?: boolean }) {
  const { value, size = 14, onChange } = props;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center' }}>
      {[1, 2, 3, 4, 5].map((i) => {
        const name = value >= i ? 'star' : value >= i - 0.5 ? 'star-half' : 'star-outline';
        const icon = <Ionicons name={name} size={size} color={value >= i - 0.5 ? colors.star : props.light ? 'rgba(255,255,255,0.7)' : colors.muted} />;
        return onChange ? (
          <Pressable key={i} onPress={() => onChange(i)} hitSlop={6} style={{ marginHorizontal: 4 }} accessibilityLabel={`${i} звёзд`}>
            {icon}
          </Pressable>
        ) : (
          <View key={i} style={{ marginRight: 1 }}>
            {icon}
          </View>
        );
      })}
      {!onChange ? (
        <Text style={{ marginLeft: 4, fontSize: size - 1, color: props.light ? colors.white : colors.textSecondary }}>
          {Number(value || 0).toFixed(1)}
          {props.count !== undefined ? ` (${props.count})` : ''}
        </Text>
      ) : null}
    </View>
  );
}
