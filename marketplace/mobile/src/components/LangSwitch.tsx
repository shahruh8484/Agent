import { View } from 'react-native';

import { useT } from '../lib/i18n';
import { Chip, styles } from './ui';

export function LangSwitch() {
  const { lang, setLang } = useT();
  return (
    <View style={styles.wrap}>
      <Chip label="O'zbekcha" selected={lang === 'uz'} onPress={() => setLang('uz')} />
      <Chip label="Русский" selected={lang === 'ru'} onPress={() => setLang('ru')} />
    </View>
  );
}
