import { Card, Segmented, Table, Tag, Typography } from 'antd';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useApi } from '../api';
import { ErrorBox, PageHeader } from '../components/common';
import { complaintStatus, complaintTypes, dateTime, money } from '../format';
import { useLive } from '../realtime';
import type { Complaint } from '../types';

export default function ComplaintsPage() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const status = sp.get('status') ?? 'open';
  const { data, loading, error, reload } = useApi<Complaint[]>('/admin/complaints', { status });
  useLive(['alert'], (a) => { if (a?.type === 'complaint') void reload(true); });

  return (
    <>
      <PageHeader title="Жалобы" sub={data ? `Показано: ${data.length}` : undefined} />
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Segmented
          value={status}
          onChange={(v) => setSp({ status: String(v) }, { replace: true })}
          options={[{ value: 'open', label: 'Открытые' }, { value: 'resolved', label: 'Решённые' }, { value: 'closed', label: 'Закрытые' }, { value: 'all', label: 'Все' }]}
        />
      </Card>
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} styles={{ body: { padding: 0 } }}>
        <Table<Complaint>
          rowKey="id"
          loading={loading}
          dataSource={data ?? []}
          scroll={{ x: 860 }}
          pagination={{ pageSize: 30, hideOnSinglePage: true }}
          locale={{ emptyText: status === 'open' ? 'Открытых жалоб нет' : 'Жалоб нет' }}
          onRow={(r) => ({ onClick: () => nav(`/complaints/${r.id}`), className: 'clickable-row' })}
          rowClassName={(r) => (r.status === 'open' ? 'complaint-open' : '')}
          columns={[
            { title: '№', dataIndex: 'id', width: 70, render: (v) => <b>#{v}</b> },
            { title: 'Дата', dataIndex: 'created_at', width: 150, render: dateTime },
            { title: 'Тип', dataIndex: 'type', render: (v) => <Tag color="volcano">{complaintTypes[v] ?? v}</Tag> },
            { title: 'Описание', dataIndex: 'description', render: (v) => <Typography.Text ellipsis style={{ maxWidth: 280 }}>{v}</Typography.Text> },
            { title: 'Заказ', dataIndex: 'order_id', render: (v) => `#${v}` },
            { title: 'Покупатель', render: (_, r) => r.buyer_name || r.buyer_phone },
            { title: 'Повар', dataIndex: 'chef_nick' },
            { title: 'Статус', dataIndex: 'status', render: (v, r) => <div><Tag color={complaintStatus[v]?.color}>{complaintStatus[v]?.label ?? v}</Tag>{r.refund_amount ? <div style={{ fontSize: 12 }}>возврат {money(r.refund_amount)}</div> : null}</div> },
          ]}
        />
      </Card>
    </>
  );
}
