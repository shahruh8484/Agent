import { Card, Input, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApi } from '../api';
import { ErrorBox, PageHeader } from '../components/common';
import { dateTime } from '../format';

interface AuditRow { id: number; staff_id: number | null; staff_name: string | null; action: string; entity: string | null; entity_id: string | null; data: unknown; created_at: string }

const actionLabels: Record<string, string> = {
  refund: 'Возврат', cancel: 'Отмена заказа', verify_approve: 'Верификация: одобрено', verify_reject: 'Верификация: отклонено',
  block: 'Блокировка', unblock: 'Разблокировка', message: 'Сообщение повару', complaint_warn_chef: 'Жалоба: предупреждение',
  complaint_close: 'Жалоба закрыта', complaint_resolve: 'Жалоба решена', payouts_run: 'Запуск выплат', payout_done: 'Выплата подтверждена',
  payout_reject: 'Выплата отклонена', report_tax: 'Налоговый отчёт', report_transactions: 'Отчёт по транзакциям',
  staff_create: 'Новый сотрудник', staff_update: 'Изменение сотрудника', settings_update: 'Изменение настроек',
};
const entityLink: Record<string, string> = { order: '/orders/', chef: '/chefs/', buyer: '/buyers/', complaint: '/complaints/' };

export default function AuditPage() {
  const { data, loading, error, reload } = useApi<AuditRow[]>('/admin/audit');
  const [q, setQ] = useState('');
  const rows = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s) return data ?? [];
    return (data ?? []).filter((r) => [r.staff_name, r.action, actionLabels[r.action], r.entity, r.entity_id, JSON.stringify(r.data)].some((x) => x?.toLowerCase().includes(s)));
  }, [data, q]);

  return (
    <>
      <PageHeader title="Журнал действий" sub="Последние 300 действий сотрудников" />
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Input.Search allowClear placeholder="Поиск по сотруднику, действию, объекту" value={q} onChange={(e) => setQ(e.target.value)} style={{ width: 340, maxWidth: '100%' }} />
      </Card>
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} styles={{ body: { padding: 0 } }}>
        <Table<AuditRow>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          scroll={{ x: 820 }}
          pagination={{ pageSize: 50, hideOnSinglePage: true, showSizeChanger: false }}
          columns={[
            { title: 'Время', dataIndex: 'created_at', width: 150, render: dateTime },
            { title: 'Сотрудник', dataIndex: 'staff_name', render: (v) => v ?? '—' },
            { title: 'Действие', dataIndex: 'action', render: (v) => <Tag>{actionLabels[v] ?? v}</Tag> },
            {
              title: 'Объект', render: (_, r) => {
                const label = `${r.entity ?? ''}${r.entity_id && r.entity_id !== '-' ? ` #${r.entity_id}` : ''}`;
                const base = r.entity ? entityLink[r.entity] : undefined;
                return base && r.entity_id ? <Link to={`${base}${r.entity_id}`}>{label}</Link> : label || '—';
              },
            },
            {
              title: 'Данные', dataIndex: 'data', render: (v) => (v ? <Typography.Text code style={{ fontSize: 12, wordBreak: 'break-all' }}>{JSON.stringify(v)}</Typography.Text> : '—'),
            },
          ]}
        />
      </Card>
    </>
  );
}
