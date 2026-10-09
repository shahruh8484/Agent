import { useEffect, useState } from 'react';
import { Text, View } from 'react-native';

import { api } from '../lib/api';
import { Chip, Field, styles } from './ui';

/** Popular cities as chips plus a free-text field for any other city. */
export function CityPicker({ value, onChange }: { value: string; onChange: (city: string) => void }) {
  const [cities, setCities] = useState<string[]>([]);
  useEffect(() => {
    api<string[]>('/cities').then(setCities).catch(() => undefined);
  }, []);

  return (
    <View>
      <Text style={styles.label}>Город</Text>
      <View style={styles.wrap}>
        {cities.map((c) => (
          <Chip key={c} label={c} selected={c === value} onPress={() => onChange(c)} />
        ))}
      </View>
      <Field placeholder="Другой город" value={cities.includes(value) ? '' : value} onChangeText={onChange} />
    </View>
  );
}
