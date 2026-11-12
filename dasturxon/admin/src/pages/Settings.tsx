import { App, Button, Card, Col, Form, InputNumber, Row, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { api, useApi } from '../api';
import { ErrorBox, Loading, PageHeader } from '../components/common';

interface Settings { commissionPct: number; deliveryBufferPct: number; earlyWithdrawPct: number; chefAcceptTimeoutSec: number }

export default function SettingsPage() {
  const { message, modal } = App.useApp();
  const { data, loading, error, reload } = useApi<Settings>('/admin/settings');
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (data) form.setFieldsValue({ ...data, chefAcceptTimeoutMin: data.chefAcceptTimeoutSec / 60 });
  }, [data, form]);

  const save = async () => {
    const v = await form.validateFields();
    modal.confirm({
      title: 'Сохранить настройки платформы?',
      content: 'Новые значения применятся к новым заказам и выплатам сразу после сохранения.',
      okText: 'Сохранить',
      cancelText: 'Отмена',
      onOk: async () => {
        setBusy(true);
        try {
          await api('/admin/settings', {
            method: 'PUT',
            body: {
              commissionPct: v.commissionPct,
              deliveryBufferPct: v.deliveryBufferPct,
              earlyWithdrawPct: v.earlyWithdrawPct,
              chefAcceptTimeoutSec: Math.round(v.chefAcceptTimeoutMin * 60),
            },
          });
          message.success('Настройки сохранены');
          void reload(true);
        } catch (e) {
          message.error((e as Error).message);
        } finally {
          setBusy(false);
        }
      },
    });
  };

  if (loading && !data) return <Loading />;

  return (
    <>
      <PageHeader title="Настройки платформы" />
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} style={{ maxWidth: 760 }}>
        <Form form={form} layout="vertical" requiredMark={false}>
          <Row gutter={24}>
            <Col xs={24} md={12}>
              <Form.Item name="commissionPct" label="Комиссия платформы, %" extra="Удерживается с суммы блюд повара" rules={[{ required: true }]}>
                <InputNumber min={0} max={50} step={0.5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="deliveryBufferPct" label="Буфер доставки, %" extra="Наценка к стоимости Яндекс Доставки" rules={[{ required: true }]}>
                <InputNumber min={0} max={100} step={1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="earlyWithdrawPct" label="Комиссия за досрочный вывод, %" rules={[{ required: true }]}>
                <InputNumber min={0} max={10} step={0.5} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item name="chefAcceptTimeoutMin" label="Время на принятие заказа поваром, мин" extra="От 1 до 30 минут" rules={[{ required: true }]}>
                <InputNumber min={1} max={30} step={1} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Typography.Paragraph type="secondary">Изменения записываются в журнал действий.</Typography.Paragraph>
          <Button type="primary" onClick={save} loading={busy}>Сохранить</Button>
        </Form>
      </Card>
    </>
  );
}
