import React from 'react';
import { View } from 'react-native';
import { ChefsMap } from '../../components/ChefsMap';
import { GreenHeader } from '../../components/Screen';
import { useNav } from '../../navigation/types';
import { useCurrentPoint } from '../../store/buyer';
import { colors, spacing } from '../../theme';

/** Вкладка «Карта»: повара рядом */
export function MapScreen() {
  const nav = useNav();
  const point = useCurrentPoint();
  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
      <GreenHeader title="Повара рядом" />
      <ChefsMap center={point} onChef={(id) => nav.navigate('Chef', { chefId: id })} style={{ margin: spacing.lg }} />
    </View>
  );
}
