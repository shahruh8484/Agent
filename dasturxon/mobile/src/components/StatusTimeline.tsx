import { Ionicons } from '@expo/vector-icons';
import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import type { Order, OrderStatus } from '../api/types';
import { time } from '../lib/format';
import { colors, font } from '../theme';

const ORDER: OrderStatus[] = ['PENDING', 'CONFIRMED', 'COOKING', 'READY', 'COURIER_ASSIGNED', 'IN_DELIVERY', 'DELIVERED', 'RATED'];

/** Шаги таймлайна трекинга (ТЗ 3.5) и статусы, которыми шаг считается пройденным */
const STEPS: { label: string; statuses: OrderStatus[] }[] = [
  { label: 'Оплата прошла', statuses: ['PENDING'] },
  { label: 'Повар принял', statuses: ['CONFIRMED', 'COOKING'] },
  { label: 'Еда готова, курьер едет', statuses: ['READY', 'COURIER_ASSIGNED'] },
  { label: 'Курьер едет к вам', statuses: ['IN_DELIVERY'] },
  { label: 'Доставлено', statuses: ['DELIVERED', 'RATED'] },
];

function stepTime(order: Order, statuses: OrderStatus[]): string | null {
  const h = order.history?.find((x) => statuses.includes(x.status));
  if (h) return h.created_at;
  if (statuses.includes('PENDING')) return order.createdAt;
  if (statuses.includes('CONFIRMED')) return order.confirmedAt;
  if (statuses.includes('READY')) return order.readyAt;
  if (statuses.includes('DELIVERED')) return order.deliveredAt;
  return null;
}

export function StatusTimeline({ order }: { order: Order }) {
  const cur = ORDER.indexOf(order.status);
  const cancelled = order.status === 'CANCELLED';
  return (
    <View>
      {STEPS.map((step, i) => {
        const stepIdx = ORDER.indexOf(step.statuses[0]);
        const done = !cancelled && cur >= stepIdx;
        const active = !cancelled && done && (i === STEPS.length - 1 || cur < ORDER.indexOf(STEPS[i + 1].statuses[0]));
        const t = done ? stepTime(order, step.statuses) : null;
        return (
          <View key={step.label} style={s.row}>
            <View style={s.rail}>
              <View style={[s.dot, done && s.dotDone, active && s.dotActive]}>
                {done ? <Ionicons name="checkmark" size={12} color={colors.white} /> : null}
              </View>
              {i < STEPS.length - 1 ? <View style={[s.line, done && cur > stepIdx && { backgroundColor: colors.primary }]} /> : null}
            </View>
            <View style={{ flex: 1, flexDirection: 'row', justifyContent: 'space-between', paddingBottom: 18 }}>
              <Text style={[font.body, !done && { color: colors.muted }, active && { fontWeight: '700' }]}>{step.label}</Text>
              <Text style={font.small}>{t ? time(t) : ''}</Text>
            </View>
          </View>
        );
      })}
      {cancelled ? (
        <View style={[s.row, { alignItems: 'center' }]}>
          <Ionicons name="close-circle" size={22} color={colors.red} />
          <Text style={[font.body, { color: colors.red, marginLeft: 8 }]}>
            Отменён {order.cancelledAt ? `в ${time(order.cancelledAt)}` : ''}
            {order.cancelReason ? ` — ${order.cancelReason}` : ''}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

const s = StyleSheet.create({
  row: { flexDirection: 'row' },
  rail: { width: 28, alignItems: 'center' },
  dot: { width: 20, height: 20, borderRadius: 10, backgroundColor: colors.gray, alignItems: 'center', justifyContent: 'center' },
  dotDone: { backgroundColor: colors.primary },
  dotActive: { borderWidth: 3, borderColor: colors.primaryLight },
  line: { width: 2, flex: 1, backgroundColor: colors.gray, marginVertical: 2 },
});
