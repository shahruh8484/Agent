import { ArrowLeftOutlined } from '@ant-design/icons';
import { App, Button, Card, Descriptions, Space, Table, Tag } from 'antd';
import { useNavigate, useParams } from 'react-router-dom';
import { api, useApi } from '../api';
import { useAuth } from '../auth';
import { ErrorBox, Loading, OrderStatusTag, PageHeader } from '../components/common';
import { complaintStatus, complaintTypes, dateTime, money } from '../format';
import type { AdminOrder } from '../types';

interface Buyer {
  id: number; phone: string; name: string | null; is_active: boolean; created_at: string;
  orders: AdminOrder[]; complaints: { id: number; order_id: number; type: string; status: string; created_at: string }[];
}

export default function BuyerDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const { message, modal } = App.useApp();
  const { data: b, loading, error, reload } = useApi<Buyer>(`/admin/buyers/${id}`);

  if (loading && !b) return <Loading />;
  if (!b) return <ErrorBox error={error} retry={reload} />;

  const delivered = b.orders.filter((o) => ['DELIVERED', 'RATED'].includes(o.status));
  const spent = delivered.reduce((s, o) => s + o.total - o.refundedAmount, 0);

  const toggleBlock = () => modal.confirm({
    title: b.is_active ? 'Заблокировать покупателя?' : 'Разблокировать покупателя?',
    content: b.is_active ? 'Покупатель будет разлогинен и не сможет войти в приложение.' : undefined,
    okText: b.is_active ? 'Заблокировать' : 'Разблокировать',
    okButtonProps: { danger: b.is_active },
    cancelText: 'Отмена',
    onOk: async () => {
      try {
        await api(`/admin/buyers/${b.id}/block`, { method: 'PUT', body: { blocked: b.is_active } });
        message.success(b.is_active ? 'Покупатель заблокирован' : 'Покупатель разблокирован');
        void reload(true);
      } catch (e) { message.error((e as Error).message); }
    },
  });

  return (
    <>
      <PageHeader
        title={<Space><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => nav(-1)} />{b.name || 'Покупатель'} {b.is_active ? <Tag color="green">Активен</Tag> : <Tag color="red">Заблокирован</Tag>}</Space>}
        extra={can('chefs_block_permanent') && <Button danger={b.is_active} onClick={toggleBlock}>{b.is_active ? 'Заблокировать' : 'Разблокировать'}</Button>}
      />
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Descriptions column={{ xs: 1, sm: 2, lg: 4 }} size="small">
          <Descriptions.Item label="Телефон">{b.phone}</Descriptions.Item>
          <Descriptions.Item label="Регистрация">{dateTime(b.created_at)}</Descriptions.Item>
          <Descriptions.Item label="Заказов">{b.orders.length}</Descriptions.Item>
          <Descriptions.Item label="Потрачено">{money(spent)}</Descriptions.Item>
        </Descriptions>
      </Card>
      <Card bordered={false} title="Заказы" style={{ marginBottom: 16 }} styles={{ body: { padding: 0 } }}>
        <Table<AdminOrder>
          rowKey="id"
          size="small"
          dataSource={b.orders}
          scroll={{ x: 600 }}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          onRow={(r) => ({ onClick: () => nav(`/orders/${r.id}`), className: 'clickable-row' })}
          columns={[
            { title: '№', dataIndex: 'id', render: (v) => `#${v}` },
            { title: 'Создан', dataIndex: 'createdAt', render: dateTime },
            { title: 'Состав', render: (_, r) => r.items.map((i) => `${i.name} × ${i.qty}`).join(', ') },
            { title: 'Сумма', dataIndex: 'total', align: 'right', render: (v) => money(v) },
            { title: 'Статус', dataIndex: 'status', render: (v) => <OrderStatusTag status={v} /> },
          ]}
        />
      </Card>
      <Card bordered={false} title="Жалобы" styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id"
          size="small"
          dataSource={b.complaints}
          pagination={false}
          locale={{ emptyText: 'Жалоб нет' }}
          onRow={(r) => ({ onClick: () => can('complaints') && nav(`/complaints/${r.id}`), className: can('complaints') ? 'clickable-row' : undefined })}
          columns={[
            { title: '№', dataIndex: 'id', render: (v: number) => `#${v}` },
            { title: 'Заказ', dataIndex: 'order_id', render: (v: number) => `#${v}` },
            { title: 'Тип', dataIndex: 'type', render: (v: string) => complaintTypes[v] ?? v },
            { title: 'Статус', dataIndex: 'status', render: (v: string) => <Tag color={complaintStatus[v]?.color}>{complaintStatus[v]?.label ?? v}</Tag> },
            { title: 'Дата', dataIndex: 'created_at', render: dateTime },
          ]}
        />
      </Card>
    </>
  );
}
