import React from 'react';
import { Linking, Text } from 'react-native';
import { Screen } from '../../components/Screen';
import { Card, Divider, ListRow } from '../../components/ui';
import { SUPPORT_PHONE, SUPPORT_TELEGRAM } from '../../config';
import { formatPhone } from '../../lib/format';
import { font } from '../../theme';

export function SupportScreen() {
  return (
    <Screen title="Поддержка" back>
      <Text style={[font.body, { marginBottom: 16 }]}>Мы на связи ежедневно с 8:00 до 23:00. Если с заказом что-то не так — напишите нам, разберёмся.</Text>
      <Card style={{ padding: 0, overflow: 'hidden' }}>
        <ListRow icon="paper-plane-outline" title="Написать в Telegram" subtitle={SUPPORT_TELEGRAM.replace('https://', '')} onPress={() => Linking.openURL(SUPPORT_TELEGRAM)} />
        <Divider />
        <ListRow icon="call-outline" title="Позвонить" subtitle={formatPhone(SUPPORT_PHONE)} onPress={() => Linking.openURL(`tel:${SUPPORT_PHONE}`)} />
      </Card>
    </Screen>
  );
}
