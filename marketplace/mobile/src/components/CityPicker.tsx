import { Text, View } from 'react-native';

import { useCities } from '../lib/cities';
import { useT } from '../lib/i18n';
import { Chip, styles } from './ui';

export function CityPicker({ value, onChange }: { value: string; onChange: (cityId: string) => void }) {
  const { t } = useT();
  const { cities } = useCities();
  return (
    <View style={{ marginBottom: 8 }}>
      <Text style={styles.label}>{t('city')}</Text>
      <View style={styles.wrap}>
        {cities.map((c) => (
          <Chip key={c.id} label={c.name} selected={c.id === value} onPress={() => onChange(c.id)} />
        ))}
      </View>
    </View>
  );
}
