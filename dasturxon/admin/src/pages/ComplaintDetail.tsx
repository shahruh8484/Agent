import { ArrowLeftOutlined, CheckOutlined, DollarOutlined, StopOutlined, WarningOutlined } from '@ant-design/icons';
import { Alert, App, Avatar, Button, Card, Col, Descriptions, Image, List, Row, Space, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api, useApi } from '../api';
import { useAuth } from '../auth';
import { BlockChefModal, RefundModal, TextPromptModal } from '../components/actions';
import { ErrorBox, Loading, OrderStatusTag, PageHeader } from '../components/common';
import { useCounts } from '../components/AppLayout';
import { complaintStatus, complaintTypes, dateTime, money, paymentLabels } from '../format';
import type { AdminOrder, Complaint } from '../types';

export default function ComplaintDetailPage() {
  const { id } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const counts = useCounts();
  const { message } = App.useApp();
  const { data: c, loading, error, reload } = useApi<Complaint & { order: AdminOrder }>(`/admin/complaints/${id}`);
  const [modal, setModal] = useState<null | 'refund' | 'warn' | 'block' | 'close' | 'resolve'>(null);

  if (loading && !c) return <Loading />;
  if (!c) return <ErrorBox error={error} retry={reload} />;

  const o = c.order;
  const refundLeft = o.total - o.refundedAmount;
  const canRefund = can('refund_small') && ['charged', 'partially_refunded'].includes(o.paymentStatus) && refundLeft > 0;
  const done = () => { counts.refresh(); void reload(true); };
  const st = complaintStatus[c.status];

  const act = (action: 'warn_chef' | 'close' | 'resolve', okMsg: string) => async (note: string) => {
    await api(`/admin/complaints/${c.id}`, { method: 'PUT', body: { action, note: note || undefined } });
    message.success(okMsg);
    done();
  };

  return (
    <>
      <PageHeader
        title={<Space><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => nav(-1)} />Жалоба #{c.id} <Tag color={st?.color}>{st?.label ?? c.status}</Tag></Space>}
        sub={`${complaintTypes[c.type] ?? c.type} · ${dateTime(c.created_at)}`}
        extra={<>
          {canRefund && <Button icon={<DollarOutlined />} onClick={() => setModal('refund')}>Возврат</Button>}
          <Button icon={<WarningOutlined />} onClick={() => setModal('warn')}>Предупредить повара</Button>
          {can('chefs_block_temporary') && <Button danger icon={<StopOutlined />} onClick={() => setModal('block')}>Заблокировать повара</Button>}
          {c.status === 'open' && <Button icon={<CheckOutlined />} onClick={() => setModal('resolve')}>Решена</Button>}
          {c.status !== 'closed' && <Button type="primary" onClick={() => setModal('close')}>Закрыть</Button>}
        </>}
      />
      <ErrorBox error={error} retry={reload} />
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={14}>
          <Card bordered={false} title="Суть жалобы" style={{ marginBottom: 16 }}>
            <Typography.Paragraph style={{ whiteSpace: 'pre-wrap', fontSize: 15 }}>{c.description}</Typography.Paragraph>
            {c.photo_urls?.length > 0 && (
              <Image.PreviewGroup>
                <div className="kitchen-photos">{c.photo_urls.map((u) => <Image key={u} src={u} width={120} height={90} />)}</div>
              </Image.PreviewGroup>
            )}
            <Descriptions column={{ xs: 1, sm: 2 }} size="small" style={{ marginTop: 16 }}>
              <Descriptions.Item label="Покупатель">{can('buyers_view') ? <Link to={`/buyers/${c.buyer_id}`}>{c.buyer_name || c.buyer_phone}</Link> : c.buyer_name}</Descriptions.Item>
              <Descriptions.Item label="Телефон">{c.buyer_phone}</Descriptions.Item>
              <Descriptions.Item label="Повар"><Link to={`/chefs/${c.chef_id}`}>{c.chef_nick}</Link></Descriptions.Item>
              <Descriptions.Item label="Тип">{complaintTypes[c.type] ?? c.type}</Descriptions.Item>
            </Descriptions>
            {(c.resolution_note || c.refund_amount) && (
              <Alert
                style={{ marginTop: 12 }}
                type={c.status === 'open' ? 'info' : 'success'}
                showIcon
                message="Решение"
                description={<>{c.refund_amount ? <div>Возврат: {money(c.refund_amount)}</div> : null}{c.resolution_note && <div>{c.resolution_note}</div>}</>}
              />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card bordered={false} title={<Space>Заказ <Link to={`/orders/${o.id}`}>#{o.id}</Link> <OrderStatusTag status={o.status} /></Space>}>
            <List
              size="small"
              dataSource={o.items}
              renderItem={(i) => (
                <List.Item extra={money(i.price * i.qty)}>
                  <List.Item.Meta avatar={<Avatar shape="square" src={i.photoUrl}>{i.name[0]}</Avatar>} title={i.name} description={`× ${i.qty}`} />
                </List.Item>
              )}
            />
            <div className="money-list" style={{ marginTop: 8 }}>
              <div className="row"><span>Блюда</span><span>{money(o.dishTotal)}</span></div>
              <div className="row"><span>Доставка</span><span>{money(o.deliveryFee)}</span></div>
              <div className="row total"><span>Итого</span><span>{money(o.total)}</span></div>
              <div className="row"><span>Оплата</span><span>{paymentLabels[o.paymentStatus] ?? o.paymentStatus}</span></div>
              {o.refundedAmount > 0 && <div className="row" style={{ color: '#E5484D' }}><span>Уже возвращено</span><span>{money(o.refundedAmount)}</span></div>}
              <div className="row"><span>Создан</span><span>{dateTime(o.createdAt)}</span></div>
              <div className="row"><span>Доставлен</span><span>{dateTime(o.deliveredAt)}</span></div>
            </div>
          </Card>
        </Col>
      </Row>

      <RefundModal open={modal === 'refund'} onClose={() => setModal(null)} orderId={o.id} maxAmount={refundLeft} complaintId={c.id} onDone={done} />
      <BlockChefModal open={modal === 'block'} onClose={() => setModal(null)} chefId={c.chef_id} nick={c.chef_nick} onDone={done} />
      <TextPromptModal
        open={modal === 'warn'} onClose={() => setModal(null)} title={`Предупреждение повару «${c.chef_nick}»`} required={false}
        label="Текст предупреждения (необязательно)" placeholder={`Получена жалоба по заказу #${o.id}. Пожалуйста, будьте внимательнее.`}
        okText="Отправить" onSubmit={act('warn_chef', 'Предупреждение отправлено, жалоба решена')}
      />
      <TextPromptModal
        open={modal === 'resolve'} onClose={() => setModal(null)} title="Отметить жалобу решённой" required={false}
        label="Комментарий" okText="Решена" onSubmit={act('resolve', 'Жалоба решена')}
      />
      <TextPromptModal
        open={modal === 'close'} onClose={() => setModal(null)} title="Закрыть жалобу" required={false}
        label="Комментарий (например, жалоба не подтвердилась)" okText="Закрыть" onSubmit={act('close', 'Жалоба закрыта')}
      />
    </>
  );
}
