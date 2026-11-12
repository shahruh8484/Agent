import { ArrowLeftOutlined, MessageOutlined, StopOutlined, UnlockOutlined } from '@ant-design/icons';
import { Alert, App, Avatar, Button, Card, Col, Descriptions, Empty, Image, List, Rate, Row, Space, Table, Tabs, Tag, Typography } from 'antd';
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, useApi } from '../api';
import { useAuth } from '../auth';
import { BlockChefModal, TextPromptModal } from '../components/actions';
import { ErrorBox, Loading, OrderStatusTag, PageHeader, PayoutStatusTag } from '../components/common';
import { useCounts } from '../components/AppLayout';
import { date, dateTime, money, payoutKind, rating, reviewTags, verificationLabels } from '../format';
import type { AdminOrder, AiVerdict, Payout } from '../types';

interface ChefFinance { turnover: number; paid: number; balance: number; tax_withheld: number; commission: number }
interface ChefDetail {
  id: number; nick: string; name: string | null; payoutCard: string | null; finance: ChefFinance; payouts: Payout[];
  firstName?: string; lastName?: string; phone?: string; bio?: string | null; photoUrl?: string | null; coverUrl?: string | null;
  district?: string; addressText?: string; isOnline?: boolean; isVerified?: boolean; isBlocked?: boolean; blockedUntil?: string | null; blockReason?: string | null;
  verificationStatus?: string; verificationNote?: string | null; kitchenPhotoUrls?: string[]; aiVerdict?: AiVerdict | null;
  rating?: number; reviewsCount?: number; totalOrders?: number; lowRating?: boolean; createdAt?: string;
  reviews?: { id: number; order_id: number; rating: number; tags: string[]; comment: string | null; created_at: string }[];
  dishes?: { id: number; name: string; price: number; type: string; is_active: boolean; photo_urls: string[] }[];
  orders?: AdminOrder[];
}

export function AiVerdictBox({ v }: { v: AiVerdict | null | undefined }) {
  if (!v) return <Alert type="info" showIcon message="AI-проверка не проводилась" />;
  if (v.source === 'skipped') return <Alert type="info" showIcon message="AI-проверка пропущена" description={v.reason} />;
  return v.approved
    ? <Alert type="success" showIcon message="AI: кухня выглядит пригодной" description={v.reason !== 'ok' ? v.reason : undefined} />
    : <Alert type="error" showIcon message="AI: есть замечания" description={v.reason} />;
}

function FinanceBlock({ f, payoutCard }: { f: ChefFinance; payoutCard: string | null }) {
  const tiles = [
    { label: 'Оборот', v: f.turnover },
    { label: 'Комиссия платформы', v: f.commission },
    { label: 'Налог удержан', v: f.tax_withheld },
    { label: 'Выплачено', v: f.paid },
    { label: 'Баланс к выплате', v: f.balance, accent: true },
  ];
  return (
    <>
      <Row gutter={[12, 12]}>
        {tiles.map((t) => (
          <Col key={t.label} flex="1 1 160px">
            <div className={`stat-tile ${t.accent ? 'accent' : ''}`}><div className="label">{t.label}</div><div className="value">{money(t.v)}</div></div>
          </Col>
        ))}
      </Row>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>Карта для выплат: {payoutCard ?? 'не указана'}</Typography.Paragraph>
    </>
  );
}

function PayoutsTable({ items }: { items: Payout[] }) {
  return (
    <Table<Payout>
      rowKey="id"
      size="small"
      dataSource={items}
      pagination={{ pageSize: 10, hideOnSinglePage: true }}
      scroll={{ x: 640 }}
      locale={{ emptyText: 'Выплат ещё не было' }}
      columns={[
        { title: '№', dataIndex: 'id', width: 70 },
        { title: 'Тип', dataIndex: 'kind', render: (v) => payoutKind[v] ?? v },
        { title: 'Период', render: (_, r) => (r.week_start ? `${date(r.week_start)} — ${date(r.week_end)}` : '—') },
        { title: 'Сумма', dataIndex: 'net_amount', align: 'right', render: (v, r) => <div>{money(v)}{r.commission_1pct > 0 && <div style={{ fontSize: 12, color: '#8A94A6' }}>комиссия {money(r.commission_1pct)}</div>}</div> },
        { title: 'Статус', dataIndex: 'status', render: (v, r) => <div><PayoutStatusTag status={v} />{r.error && <div style={{ fontSize: 12, color: '#E5484D' }}>{r.error}</div>}</div> },
        { title: 'Дата', render: (_, r) => dateTime(r.paid_at ?? r.created_at) },
      ]}
    />
  );
}

export default function ChefDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const counts = useCounts();
  const { message, modal } = App.useApp();
  const { data: c, loading, error, reload } = useApi<ChefDetail>(`/admin/chefs/${id}`);
  const [blockOpen, setBlockOpen] = useState(false);
  const [msgOpen, setMsgOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);

  if (loading && !c) return <Loading />;
  if (!c) return <ErrorBox error={error} retry={reload} />;

  const financeOnly = c.orders === undefined; // бухгалтер получает только финансы
  const back = <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => nav(-1)} />;

  if (financeOnly) {
    return (
      <>
        <PageHeader title={<Space>{back}{c.nick}</Space>} sub={c.name ?? undefined} />
        <Card bordered={false} title="Финансы" style={{ marginBottom: 16 }}><FinanceBlock f={c.finance} payoutCard={c.payoutCard} /></Card>
        <Card bordered={false} title="Выплаты" styles={{ body: { padding: 0 } }}><PayoutsTable items={c.payouts} /></Card>
      </>
    );
  }

  const unblock = () => modal.confirm({
    title: `Разблокировать «${c.nick}»?`,
    okText: 'Разблокировать',
    cancelText: 'Отмена',
    onOk: async () => {
      try {
        await api(`/admin/chefs/${c.id}/block`, { method: 'PUT', body: { blocked: false } });
        message.success('Повар разблокирован');
        void reload(true);
      } catch (e) { message.error((e as Error).message); }
    },
  });

  const approve = () => modal.confirm({
    title: `Одобрить заявку «${c.nick}»?`,
    content: 'Повар сможет выйти онлайн и принимать заказы.',
    okText: 'Одобрить',
    cancelText: 'Отмена',
    onOk: async () => {
      try {
        await api(`/admin/chefs/${c.id}/verify`, { method: 'PUT', body: { approve: true } });
        message.success('Заявка одобрена');
        counts.refresh();
        void reload(true);
      } catch (e) { message.error((e as Error).message); }
    },
  });

  const v = verificationLabels[c.verificationStatus ?? ''];
  const status = c.isBlocked ? <Tag color="red">Заблокирован</Tag> : c.isOnline ? <Tag color="green">Онлайн</Tag> : <Tag>Офлайн</Tag>;

  return (
    <>
      <PageHeader
        title={<Space>{back}Профиль повара</Space>}
        extra={<>
          {c.verificationStatus === 'pending' && can('chefs_verify') && <>
            <Button type="primary" onClick={approve}>Одобрить</Button>
            <Button danger onClick={() => setRejectOpen(true)}>Отклонить</Button>
          </>}
          {can('complaints') && <Button icon={<MessageOutlined />} onClick={() => setMsgOpen(true)}>Написать</Button>}
          {can('chefs_block_temporary') && (c.isBlocked
            ? <Button icon={<UnlockOutlined />} onClick={unblock}>Разблокировать</Button>
            : <Button danger icon={<StopOutlined />} onClick={() => setBlockOpen(true)}>Заблокировать</Button>)}
        </>}
      />
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} style={{ marginBottom: 16 }}>
        <Space size={20} align="start" wrap>
          <Avatar src={c.photoUrl ?? undefined} size={88}>{c.nick?.[0]}</Avatar>
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>{c.nick}</Typography.Title>
            <Typography.Text type="secondary">{[c.firstName, c.lastName].filter(Boolean).join(' ') || c.name} · {c.phone}</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <Space size={4} wrap>
                {status}
                {v && <Tag color={v.color}>{v.label}</Tag>}
                {c.lowRating && <Tag color="warning">Низкий рейтинг</Tag>}
              </Space>
            </div>
            <div style={{ marginTop: 8 }}>
              <Space size={16} wrap>
                <span>★ <b>{rating(c.rating, c.reviewsCount)}</b> ({c.reviewsCount} отзывов)</span>
                <span>Заказов: <b>{c.totalOrders}</b></span>
                <span>С нами с {date(c.createdAt)}</span>
              </Space>
            </div>
          </div>
        </Space>
        {c.isBlocked && (
          <Alert style={{ marginTop: 16 }} type="error" showIcon
            message={c.blockedUntil ? `Заблокирован до ${dateTime(c.blockedUntil)}` : 'Заблокирован бессрочно'}
            description={c.blockReason ? `Причина: ${c.blockReason}` : undefined} />
        )}
        {c.verificationStatus === 'rejected' && c.verificationNote && <Alert style={{ marginTop: 16 }} type="warning" showIcon message="Заявка отклонена" description={c.verificationNote} />}
      </Card>

      <Card bordered={false}>
        <Tabs
          items={[
            {
              key: 'profile', label: 'Профиль', children: (
                <Row gutter={[24, 24]}>
                  <Col xs={24} lg={14}>
                    <Descriptions column={1} size="small" bordered>
                      <Descriptions.Item label="Ник">{c.nick}</Descriptions.Item>
                      <Descriptions.Item label="Имя">{c.firstName} {c.lastName}</Descriptions.Item>
                      <Descriptions.Item label="Телефон">{c.phone}</Descriptions.Item>
                      <Descriptions.Item label="Район">{c.district ?? '—'}</Descriptions.Item>
                      <Descriptions.Item label="Адрес">{c.addressText ?? '—'}</Descriptions.Item>
                      <Descriptions.Item label="О себе">{c.bio || '—'}</Descriptions.Item>
                      <Descriptions.Item label="Карта для выплат">{c.payoutCard ?? 'не указана'}</Descriptions.Item>
                      <Descriptions.Item label="Регистрация">{dateTime(c.createdAt)}</Descriptions.Item>
                    </Descriptions>
                  </Col>
                  <Col xs={24} lg={10}>
                    <Typography.Title level={5}>Фото кухни</Typography.Title>
                    {c.kitchenPhotoUrls?.length ? (
                      <Image.PreviewGroup>
                        <div className="kitchen-photos">{c.kitchenPhotoUrls.map((u) => <Image key={u} src={u} width={120} height={90} />)}</div>
                      </Image.PreviewGroup>
                    ) : <Typography.Text type="secondary">Не загружены</Typography.Text>}
                    <div style={{ marginTop: 12 }}><AiVerdictBox v={c.aiVerdict} /></div>
                  </Col>
                </Row>
              ),
            },
            {
              key: 'orders', label: `Заказы (${c.orders?.length ?? 0})`, children: (
                <Table<AdminOrder>
                  rowKey="id"
                  size="small"
                  dataSource={c.orders}
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
              ),
            },
            {
              key: 'finance', label: 'Финансы', children: (
                <>
                  <FinanceBlock f={c.finance} payoutCard={c.payoutCard} />
                  <Typography.Title level={5} style={{ marginTop: 8 }}>Выплаты</Typography.Title>
                  <PayoutsTable items={c.payouts} />
                </>
              ),
            },
            {
              key: 'reviews', label: `Отзывы (${c.reviews?.length ?? 0})`, children: c.reviews?.length ? (
                <List
                  dataSource={c.reviews}
                  pagination={{ pageSize: 10, hideOnSinglePage: true }}
                  renderItem={(r) => (
                    <List.Item>
                      <div style={{ width: '100%' }}>
                        <Space wrap><Rate disabled value={r.rating} style={{ fontSize: 14 }} /><Typography.Text type="secondary">{dateTime(r.created_at)} · заказ <a onClick={() => nav(`/orders/${r.order_id}`)}>#{r.order_id}</a></Typography.Text></Space>
                        <div style={{ marginTop: 4 }}>{(r.tags ?? []).map((t) => <Tag key={t}>{reviewTags[t] ?? t}</Tag>)}</div>
                        {r.comment && <div style={{ marginTop: 4 }}>{r.comment}</div>}
                      </div>
                    </List.Item>
                  )}
                />
              ) : <Empty description="Отзывов пока нет" />,
            },
            {
              key: 'dishes', label: `Меню (${c.dishes?.length ?? 0})`, children: (
                <Table
                  rowKey="id"
                  size="small"
                  dataSource={c.dishes}
                  pagination={false}
                  columns={[
                    { title: '', dataIndex: 'photo_urls', width: 60, render: (v: string[]) => <Avatar shape="square" src={v?.[0]} /> },
                    { title: 'Блюдо', dataIndex: 'name' },
                    { title: 'Тип', dataIndex: 'type', render: (v: string) => ({ ready: 'Готовое', order: 'Под заказ', both: 'Готовое и под заказ' }[v] ?? v) },
                    { title: 'Цена', dataIndex: 'price', align: 'right', render: (v: number) => money(v) },
                    { title: 'Статус', dataIndex: 'is_active', render: (v: boolean) => (v ? <Tag color="green">Активно</Tag> : <Tag>Скрыто</Tag>) },
                  ]}
                />
              ),
            },
          ]}
        />
      </Card>

      <BlockChefModal open={blockOpen} onClose={() => setBlockOpen(false)} chefId={c.id} nick={c.nick} onDone={() => void reload(true)} />
      <TextPromptModal
        open={msgOpen} title={`Сообщение повару «${c.nick}»`} label="Текст сообщения (придёт push-уведомлением)" okText="Отправить"
        onClose={() => setMsgOpen(false)}
        onSubmit={async (text) => { await api(`/admin/chefs/${c.id}/message`, { method: 'POST', body: { text } }); message.success('Сообщение отправлено'); }}
      />
      <TextPromptModal
        open={rejectOpen} title={`Отклонить заявку «${c.nick}»`} label="Причина отклонения (увидит повар)" okText="Отклонить" danger
        onClose={() => setRejectOpen(false)}
        onSubmit={async (reason) => {
          await api(`/admin/chefs/${c.id}/verify`, { method: 'PUT', body: { approve: false, reason } });
          message.success('Заявка отклонена');
          counts.refresh();
          void reload(true);
        }}
      />
    </>
  );
}
