import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View, type StyleProp, type ViewStyle } from 'react-native';
import { colors, font, radius } from '../../theme';
import { distanceKm, MARKER_COLORS, type MapData } from './mapHtml';

const KIND_LABEL = { chef: 'Повар', chefOffline: 'Повар (на заказ)', buyer: 'Вы', courier: 'Курьер' };

/** Без ключа Яндекс.Карт: простой список точек с расстоянием от центра (покупателя) */
export function MapFallback({ data, onMarkerPress, style }: { data: MapData; onMarkerPress?: (id: string | number) => void; style?: StyleProp<ViewStyle> }) {
  const markers = [...data.markers].sort((a, b) => distanceKm(data.center, a) - distanceKm(data.center, b));
  return (
    <View style={[s.root, style]} testID="map-fallback">
      <View style={s.note}>
        <Ionicons name="map-outline" size={16} color={colors.textSecondary} />
        <Text style={[font.tiny, { marginLeft: 6, flex: 1 }]}>Карта недоступна (не задан ключ Яндекс.Карт) — показываем список</Text>
      </View>
      <ScrollView>
        {markers.map((m) => (
          <Pressable key={`${m.kind}-${m.id}`} onPress={onMarkerPress ? () => onMarkerPress(m.id) : undefined} style={s.row}>
            <View style={[s.dot, { backgroundColor: MARKER_COLORS[m.kind] }]} />
            <View style={{ flex: 1 }}>
              <Text style={font.body}>{m.label || KIND_LABEL[m.kind]}</Text>
              {m.subtitle ? <Text style={font.small}>{m.subtitle}</Text> : null}
            </View>
            {m.kind !== 'buyer' ? <Text style={font.small}>{distanceKm(data.center, m).toFixed(1)} км</Text> : null}
          </Pressable>
        ))}
        {data.route && data.route.length > 1 ? (
          <Text style={[font.tiny, { padding: 12 }]}>Маршрут: {distanceKm(data.route[0], data.route[data.route.length - 1]).toFixed(1)} км по прямой</Text>
        ) : null}
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  root: { backgroundColor: colors.card, borderRadius: radius.lg, overflow: 'hidden' },
  note: { flexDirection: 'row', alignItems: 'center', padding: 10, backgroundColor: colors.bg },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, paddingVertical: 12, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.border },
  dot: { width: 14, height: 14, borderRadius: 7, marginRight: 12, borderWidth: 2, borderColor: colors.white },
});
