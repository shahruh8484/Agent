import { Card, Input, Table, Tag } from 'antd';
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useApi } from '../api';
import { ErrorBox, PageHeader } from '../components/common';
import { date, money } from '../format';

interface BuyerRow { id: number; phone: string; name: string | null; is_active: boolean; created_at: string; orders: number; spent: number }

export default function BuyersPage() {
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const q = sp.get('q') ?? '';
  const page = Number(sp.get('page') ?? 1);
  const [search, setSearch] = useState(q);
  const { data, loading, error, reload } = useApi<{ total: number; items: BuyerRow[] }>('/admin/buyers', { q, page });

  return (
    <>
      <PageHeader title="Покупатели" sub={data ? `Всего: ${data.total}` : undefined} />
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Input.Search
          allowClear
          placeholder="Телефон или имя"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onSearch={(v) => setSp(v.trim() ? { q: v.trim() } : {}, { replace: true })}
          style={{ width: 300, maxWidth: '100%' }}
        />
      </Card>
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} styles={{ body: { padding: 0 } }}>
        <Table<BuyerRow>
          rowKey="id"
          loading={loading}
          dataSource={data?.items ?? []}
          scroll={{ x: 700 }}
          onRow={(r) => ({ onClick: () => nav(`/buyers/${r.id}`), className: 'clickable-row' })}
          pagination={{
            current: page, pageSize: 50, total: data?.total ?? 0, showSizeChanger: false,
            onChange: (p) => { const n = new URLSearchParams(sp); n.set('page', String(p)); setSp(n, { replace: true }); },
          }}
          columns={[
            { title: 'ID', dataIndex: 'id', width: 70 },
            { title: 'Имя', dataIndex: 'name', render: (v) => v || '—' },
            { title: 'Телефон', dataIndex: 'phone' },
            { title: 'Заказов', dataIndex: 'orders', align: 'right' },
            { title: 'Потрачено', dataIndex: 'spent', align: 'right', render: (v) => money(v) },
            { title: 'Статус', dataIndex: 'is_active', render: (v) => (v ? <Tag color="green">Активен</Tag> : <Tag color="red">Заблокирован</Tag>) },
            { title: 'Регистрация', dataIndex: 'created_at', render: date },
          ]}
        />
      </Card>
    </>
  );
}
