import { CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { App, Avatar, Button, Card, Col, Empty, Image, Row, Space, Tag, Typography } from 'antd';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, useApi } from '../api';
import { TextPromptModal } from '../components/actions';
import { ErrorBox, Loading, Photo, PageHeader } from '../components/common';
import { useCounts } from '../components/AppLayout';
import { dateTime } from '../format';
import { useLive } from '../realtime';
import type { AiVerdict } from '../types';
import { AiVerdictBox } from './ChefDetail';

interface Application {
  id: number; nick: string; name: string; phone: string; district: string; photoUrl: string | null;
  kitchenPhotoUrls: string[]; aiVerdict: AiVerdict | null; payoutCardAdded: boolean; createdAt: string;
}

export default function VerificationPage() {
  const { message, modal } = App.useApp();
  const counts = useCounts();
  const { data, loading, error, reload } = useApi<Application[]>('/admin/verifications');
  const [rejecting, setRejecting] = useState<Application | null>(null);
  useLive(['alert'], (a) => { if (a?.type === 'verification') void reload(true); });

  const done = () => { counts.refresh(); void reload(true); };

  const approve = (a: Application) => modal.confirm({
    title: `Одобрить заявку «${a.nick}»?`,
    content: 'Повар получит уведомление и сможет выйти онлайн.',
    okText: 'Одобрить',
    cancelText: 'Отмена',
    onOk: async () => {
      try {
        await api(`/admin/chefs/${a.id}/verify`, { method: 'PUT', body: { approve: true } });
        message.success(`«${a.nick}» одобрен`);
        done();
      } catch (e) { message.error((e as Error).message); }
    },
  });

  if (loading && !data) return <Loading />;

  return (
    <>
      <PageHeader title="Верификация поваров" sub={data ? `Заявок на проверке: ${data.length}` : undefined} />
      <ErrorBox error={error} retry={reload} />
      {data && data.length === 0 && <Card bordered={false}><Empty description="Новых заявок нет" /></Card>}
      <Row gutter={[16, 16]}>
        {data?.map((a) => (
          <Col key={a.id} xs={24} lg={12} xxl={8}>
            <Card
              bordered={false}
              style={{ height: '100%' }}
              actions={[
                <Button key="ok" type="primary" icon={<CheckOutlined />} onClick={() => approve(a)}>Одобрить</Button>,
                <Button key="no" danger icon={<CloseOutlined />} onClick={() => setRejecting(a)}>Отклонить</Button>,
              ]}
            >
              <Space align="start" style={{ marginBottom: 12 }}>
                <Avatar src={a.photoUrl ?? undefined} size={56}>{a.nick?.[0]}</Avatar>
                <div>
                  <Link to={`/chefs/${a.id}`}><Typography.Text strong style={{ fontSize: 16 }}>{a.nick}</Typography.Text></Link>
                  <div><Typography.Text type="secondary">{a.name} · {a.phone}</Typography.Text></div>
                  <Space size={4} wrap style={{ marginTop: 4 }}>
                    <Tag>{a.district}</Tag>
                    {a.payoutCardAdded ? <Tag color="green">Карта добавлена</Tag> : <Tag color="orange">Нет карты</Tag>}
                  </Space>
                </div>
              </Space>
              <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 8 }}>Подана: {dateTime(a.createdAt)}</Typography.Text>
              {a.kitchenPhotoUrls?.length ? (
                <Image.PreviewGroup>
                  <div className="kitchen-photos" style={{ marginBottom: 12 }}>
                    {a.kitchenPhotoUrls.map((u) => <Photo key={u} src={u} width={110} height={82} />)}
                  </div>
                </Image.PreviewGroup>
              ) : <Typography.Paragraph type="secondary">Фото кухни не загружены</Typography.Paragraph>}
              <AiVerdictBox v={a.aiVerdict} />
            </Card>
          </Col>
        ))}
      </Row>
      <TextPromptModal
        open={!!rejecting}
        title={`Отклонить заявку «${rejecting?.nick ?? ''}»`}
        label="Причина (увидит повар)"
        placeholder="Например: на фото не видно рабочей поверхности"
        okText="Отклонить"
        danger
        onClose={() => setRejecting(null)}
        onSubmit={async (reason) => {
          await api(`/admin/chefs/${rejecting!.id}/verify`, { method: 'PUT', body: { approve: false, reason } });
          message.success('Заявка отклонена');
          done();
        }}
      />
    </>
  );
}
