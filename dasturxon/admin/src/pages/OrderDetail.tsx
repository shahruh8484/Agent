import { ArrowLeftOutlined } from '@ant-design/icons';
import { App, Avatar, Button, Card, Col, Descriptions, Empty, List, Rate, Row, Space, Tag, Timeline, Typography } from 'antd';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, useApi } from '../api';
import { useAuth } from '../auth';
import { RefundModal, TextPromptModal } from '../components/actions';
import { ErrorBox, Loading, OrderStatusTag, PageHeader } from '../components/common';
import { dateTime, money, paymentLabels, reviewTags, statusColors, statusLabels } from '../format';
import { useLive } from '../realtime';
import type { AdminOrderDetail } from '../types';

const CANCELLABLE = ['PENDING', 'CONFIRMED', 'COOKING', 'READY', 'COURIER_ASSIGNED', 'IN_DELIVERY'];

export default function OrderDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const { message } = App.useApp();
  const { data: o, loading, error, reload } = useApi<AdminOrderDetail>(`/admin/orders/${id}`);
  const [refundOpen, setRefundOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);
  useLive(['order:update'], (p) => { if (String(p?.orderId) === id) void reload(true); });

  if (loading && !o) return <Loading />;
  if (!o) return <ErrorBox error={error} retry={reload} />;

  const refundLeft = o.total - o.refundedAmount;
  const canRefund = can('refund_small') && ['charged', 'partially_refunded'].includes(o.paymentStatus) && refundLeft > 0;
  const canCancel = can('refund_small') && CANCELLABLE.includes(o.status);

  return (
    <>
      <PageHeader
        title={<Space><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => nav(-1)} />Заказ #{o.id} <OrderStatusTag status={o.status} /></Space>}
        sub={`Создан ${dateTime(o.createdAt)}`}
        extra={<>
          {canRefund && <Button onClick={() => setRefundOpen(true)}>Возврат</Button>}
          {canCancel && <Button danger onClick={() => setCancelOpen(true)}>Отменить заказ</Button>}
        </>}
      />
      <ErrorBox error={error} retry={reload} />
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={15}>
          <Card bordered={false} title="Состав заказа" style={{ marginBottom: 16 }}>
            <List
              dataSource={o.items}
              renderItem={(i) => (
                <List.Item extra={<b>{money(i.price * i.qty)}</b>}>
                  <List.Item.Meta
                    avatar={<Avatar shape="square" size={48} src={i.photoUrl}>{i.name[0]}</Avatar>}
                    title={i.name}
                    description={`${money(i.price)} × ${i.qty}`}
                  />
                </List.Item>
              )}
            />
            {o.comment && <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>Комментарий: {o.comment}</Typography.Paragraph>}
          </Card>
          <Card bordered={false} title="Участники и доставка" style={{ marginBottom: 16 }}>
            <Descriptions column={{ xs: 1, sm: 2 }} size="small">
              <Descriptions.Item label="Повар">{can('chefs_view') ? <Link to={`/chefs/${o.chefId}`}>{o.chefNick}</Link> : o.chefNick}</Descriptions.Item>
              <Descriptions.Item label="Телефон повара">{o.chefPhone ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Покупатель">{can('buyers_view') ? <Link to={`/buyers/${o.buyerId}`}>{o.buyerName || 'Без имени'}</Link> : (o.buyerName || '—')}</Descriptions.Item>
              <Descriptions.Item label="Телефон покупателя">{o.buyerPhone ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Адрес" span={2}>{o.deliveryAddress?.text ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="Курьер">{o.courier ? `${o.courier.name}, ${o.courier.phone}` : '—'}</Descriptions.Item>
              <Descriptions.Item label="Заявка Яндекс">{o.yandexRequestId ?? '—'}</Descriptions.Item>
              {o.cancelReason && <Descriptions.Item label="Причина отмены" span={2}>{o.cancelReason}</Descriptions.Item>}
            </Descriptions>
          </Card>
          {o.review && (
            <Card bordered={false} title="Отзыв" style={{ marginBottom: 16 }}>
              <Rate disabled value={o.review.rating} />
              <div style={{ margin: '8px 0' }}>{(o.review.tags ?? []).map((t) => <Tag key={t}>{reviewTags[t] ?? t}</Tag>)}</div>
              {o.review.comment && <Typography.Paragraph>{o.review.comment}</Typography.Paragraph>}
              <Typography.Text type="secondary">{dateTime(o.review.created_at)}</Typography.Text>
            </Card>
          )}
        </Col>
        <Col xs={24} lg={9}>
          <Card bordered={false} title="Деньги" style={{ marginBottom: 16 }}>
            <div className="money-list">
              <div className="row"><span>Блюда</span><span>{money(o.dishTotal)}</span></div>
              <div className="row"><span>Доставка (покупатель)</span><span>{money(o.deliveryFee)}</span></div>
              <div className="row total"><span>Итого</span><span>{money(o.total)}</span></div>
              {can('finance') && <>
                <div className="row"><span>Комиссия платформы</span><span>{money(o.platformCommission)}</span></div>
                <div className="row"><span>Налог повара</span><span>{money(o.chefTax)}</span></div>
                <div className="row"><span>Повару</span><span>{money(o.chefNetAmount)}</span></div>
                <div className="row"><span>Доставка (Яндекс)</span><span>{money(o.deliveryFeeReal)}</span></div>
                <div className="row"><span>Буфер доставки</span><span>{money(o.deliveryBuffer)}</span></div>
              </>}
              <div className="row"><span>Оплата</span><span>{paymentLabels[o.paymentStatus] ?? o.paymentStatus}</span></div>
              {o.refundedAmount > 0 && <div className="row" style={{ color: '#E5484D' }}><span>Возвращено</span><span>{money(o.refundedAmount)}</span></div>}
            </div>
          </Card>
          <Card bordered={false} title="История" style={{ marginBottom: 16 }}>
            {o.history.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> : (
              <Timeline
                items={o.history.map((h) => ({
                  color: statusColors[h.status] === 'red' ? 'red' : statusColors[h.status] === 'green' ? 'green' : 'blue',
                  children: (
                    <div>
                      <b>{statusLabels[h.status] ?? h.status}</b> <Typography.Text type="secondary">· {dateTime(h.created_at)}</Typography.Text>
                      {h.note && <div style={{ color: '#6B7280' }}>{h.note}</div>}
                    </div>
                  ),
                }))}
              />
            )}
          </Card>
          {o.refunds.length > 0 && (
            <Card bordered={false} title="Возвраты">
              <List
                size="small"
                dataSource={o.refunds}
                renderItem={(r) => (
                  <List.Item>
                    <div style={{ width: '100%' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between' }}><b>{money(r.amount)}</b><Typography.Text type="secondary">{dateTime(r.created_at)}</Typography.Text></div>
                      <div>{r.reason}</div>
                      {r.staff && <Typography.Text type="secondary">Оформил: {r.staff}</Typography.Text>}
                    </div>
                  </List.Item>
                )}
              />
            </Card>
          )}
        </Col>
      </Row>
      <RefundModal open={refundOpen} onClose={() => setRefundOpen(false)} orderId={o.id} maxAmount={refundLeft} onDone={() => void reload(true)} />
      <TextPromptModal
        open={cancelOpen}
        title={`Отменить заказ #${o.id}`}
        label="Причина отмены"
        okText="Отменить заказ"
        danger
        minLength={3}
        onClose={() => setCancelOpen(false)}
        onSubmit={async (reason) => {
          await api(`/admin/orders/${o.id}/cancel`, { method: 'POST', body: { reason } });
          message.success('Заказ отменён, деньги возвращены покупателю');
          void reload(true);
        }}
      />
    </>
  );
}
