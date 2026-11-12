import { Avatar, Badge, Card, Input, Segmented, Space, Table, Tag, Typography } from 'antd';
import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useApi } from '../api';
import { useAuth } from '../auth';
import { ChefStatusTag, ErrorBox, PageHeader } from '../components/common';
import { useCounts } from '../components/AppLayout';
import { date, rating, verificationLabels } from '../format';

interface ChefRow {
  id: number; nick: string | null; name: string | null; phone?: string; photoUrl: string | null; district: string | null;
  status: 'online' | 'offline' | 'blocked'; verificationStatus: string; rating: number; totalOrders: number; lowRating: boolean; createdAt: string;
}

export default function ChefsPage() {
  const nav = useNavigate();
  const { can } = useAuth();
  const counts = useCounts();
  const [sp, setSp] = useSearchParams();
  const status = sp.get('status') ?? 'all';
  const q = sp.get('q') ?? '';
  const page = Number(sp.get('page') ?? 1);
  const [search, setSearch] = useState(q);
  const set = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp);
    for (const [k, v] of Object.entries(patch)) { if (v) next.set(k, v); else next.delete(k); }
    if (!('page' in patch)) next.delete('page');
    setSp(next, { replace: true });
  };
  const { data, loading, error, reload } = useApi<{ total: number; items: ChefRow[] }>('/admin/chefs', { status, q, page });

  const options = [
    { value: 'all', label: 'Все' },
    { value: 'online', label: 'Онлайн' },
    { value: 'offline', label: 'Офлайн' },
    { value: 'blocked', label: 'Заблокированы' },
    { value: 'pending', label: <span>На проверке {counts.verifications > 0 && <Badge count={counts.verifications} size="small" color="#1FA37A" />}</span> },
    { value: 'low_rating', label: 'Низкий рейтинг' },
  ];

  return (
    <>
      <PageHeader title="Повара" sub={data ? `Всего: ${data.total}` : undefined} />
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Space wrap size={[12, 12]}>
          <Segmented options={options} value={status} onChange={(v) => set({ status: v === 'all' ? undefined : String(v) })} />
          <Input.Search
            allowClear
            placeholder={can('buyers_view') ? 'Ник, имя или телефон' : 'Ник или имя'}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onSearch={(v) => set({ q: v.trim() || undefined })}
            style={{ width: 280, maxWidth: '100%' }}
          />
        </Space>
      </Card>
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} styles={{ body: { padding: 0 } }}>
        <Table<ChefRow>
          rowKey="id"
          loading={loading}
          dataSource={data?.items ?? []}
          scroll={{ x: 860 }}
          onRow={(r) => ({ onClick: () => nav(`/chefs/${r.id}`), className: 'clickable-row' })}
          pagination={{ current: page, pageSize: 50, total: data?.total ?? 0, showSizeChanger: false, onChange: (p) => set({ page: String(p) }) }}
          columns={[
            {
              title: 'Повар', render: (_, r) => (
                <Space>
                  <Avatar src={r.photoUrl ?? undefined} size={40}>{r.nick?.[0]}</Avatar>
                  <div>
                    <div style={{ fontWeight: 600 }}>{r.nick ?? '—'}</div>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.name}{r.phone ? ` · ${r.phone}` : ''}</Typography.Text>
                  </div>
                </Space>
              ),
            },
            { title: 'Район', dataIndex: 'district', render: (v) => v ?? '—' },
            {
              title: 'Статус', render: (_, r) => (
                <Space size={4} wrap>
                  <ChefStatusTag status={r.status} />
                  {r.verificationStatus !== 'approved' && <Tag color={verificationLabels[r.verificationStatus]?.color}>{verificationLabels[r.verificationStatus]?.label ?? r.verificationStatus}</Tag>}
                </Space>
              ),
            },
            { title: 'Рейтинг', dataIndex: 'rating', render: (v, r) => <span style={{ color: r.lowRating ? '#E5484D' : undefined }}>★ {rating(v, r.totalOrders)}</span> },
            { title: 'Заказов', dataIndex: 'totalOrders', align: 'right' },
            { title: 'Регистрация', dataIndex: 'createdAt', render: date },
          ]}
        />
      </Card>
    </>
  );
}
