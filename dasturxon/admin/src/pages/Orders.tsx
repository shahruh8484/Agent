import { SearchOutlined } from '@ant-design/icons';
import { Card, DatePicker, Input, Segmented, Space, Table, Typography } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useApi } from '../api';
import { ErrorBox, OrderStatusTag, PageHeader } from '../components/common';
import { dateTime, dayEndIso, dayStartIso, money, paymentLabels } from '../format';
import { useLive } from '../realtime';
import type { AdminOrder } from '../types';

const statusOptions = [
  { value: 'all', label: 'Все' },
  { value: 'active', label: 'Активные' },
  { value: 'done', label: 'Выполненные' },
  { value: 'cancelled', label: 'Отменённые' },
];

export default function OrdersPage() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const status = sp.get('status') ?? 'all';
  const q = sp.get('q') ?? '';
  const from = sp.get('from') ?? '';
  const to = sp.get('to') ?? '';
  const page = Number(sp.get('page') ?? 1);
  const [search, setSearch] = useState(q);

  const set = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) { if (v) next.set(k, v); else next.delete(k); }
    if (!('page' in patch)) next.delete('page');
    setSp(next, { replace: true });
  };

  const { data, loading, error, reload } = useApi<{ total: number; items: AdminOrder[] }>('/admin/orders', {
    status, q, page,
    from: from ? dayStartIso(from) : undefined,
    to: to ? dayEndIso(to) : undefined,
  });
  useLive(['order:update'], () => void reload(true));

  const onRange = (r: null | [Dayjs | null, Dayjs | null]) => {
    set({ from: r?.[0]?.format('YYYY-MM-DD'), to: r?.[1]?.format('YYYY-MM-DD') });
  };

  return (
    <>
      <PageHeader title="Заказы" sub={data ? `Найдено: ${data.total}` : undefined} />
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Space wrap size={[12, 12]}>
          <Segmented options={statusOptions} value={status} onChange={(v) => set({ status: v === 'all' ? undefined : String(v) })} />
          <DatePicker.RangePicker
            format="DD.MM.YYYY"
            allowEmpty={[true, true]}
            value={[from ? dayjs(from) : null, to ? dayjs(to) : null]}
            onChange={(r) => onRange(r as [Dayjs | null, Dayjs | null] | null)}
            placeholder={['С даты', 'По дату']}
          />
          <Input.Search
            allowClear
            placeholder="№ заказа, ник повара, телефон"
            prefix={<SearchOutlined />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onSearch={(v) => set({ q: v.trim() || undefined })}
            style={{ width: 300, maxWidth: '100%' }}
          />
        </Space>
      </Card>
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} styles={{ body: { padding: 0 } }}>
        <Table<AdminOrder>
          rowKey="id"
          loading={loading}
          dataSource={data?.items ?? []}
          scroll={{ x: 900 }}
          onRow={(r) => ({ onClick: () => nav(`/orders/${r.id}`), className: 'clickable-row' })}
          pagination={{ current: page, pageSize: 50, total: data?.total ?? 0, showSizeChanger: false, onChange: (p) => set({ page: String(p) }) }}
          columns={[
            { title: '№', dataIndex: 'id', width: 80, render: (v) => <b>#{v}</b> },
            { title: 'Создан', dataIndex: 'createdAt', width: 150, render: dateTime },
            { title: 'Повар', dataIndex: 'chefNick' },
            { title: 'Покупатель', render: (_, r) => <div>{r.buyerName || '—'}<br /><Typography.Text type="secondary">{r.buyerPhone}</Typography.Text></div> },
            { title: 'Состав', render: (_, r) => <Typography.Text ellipsis style={{ maxWidth: 220 }}>{r.items.map((i) => `${i.name} × ${i.qty}`).join(', ')}</Typography.Text> },
            { title: 'Сумма', dataIndex: 'total', align: 'right', render: (v, r) => <div>{money(v)}{r.refundedAmount > 0 && <div style={{ color: '#E5484D', fontSize: 12 }}>возврат {money(r.refundedAmount)}</div>}</div> },
            { title: 'Оплата', dataIndex: 'paymentStatus', render: (v) => paymentLabels[v] ?? v },
            { title: 'Статус', dataIndex: 'status', render: (v) => <OrderStatusTag status={v} /> },
          ]}
        />
      </Card>
    </>
  );
}
