// Модальные окна действий, общие для нескольких страниц.
import { App, Form, Input, InputNumber, Modal, Select, Typography } from 'antd';
import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { money } from '../format';

export const SUPPORT_REFUND_LIMIT = 50_000;

const moneyFormatter = (v: number | string | undefined) => (v === undefined || v === '' ? '' : String(v).replace(/\B(?=(\d{3})+(?!\d))/g, ' '));
const moneyParser = (v: string | undefined) => Number((v ?? '').replace(/\D/g, '')) as unknown as 0;

/** Возврат по заказу: сумма + причина, с подтверждением. */
export function RefundModal({ open, onClose, orderId, maxAmount, complaintId, onDone }: {
  open: boolean; onClose: () => void; orderId: number; maxAmount: number; complaintId?: number; onDone: () => void;
}) {
  const { can } = useAuth();
  const { message, modal } = App.useApp();
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const limit = can('refund_large') ? maxAmount : Math.min(maxAmount, SUPPORT_REFUND_LIMIT);

  const submit = async () => {
    const v = await form.validateFields();
    modal.confirm({
      title: 'Подтвердите возврат',
      content: <>Вернуть покупателю <b>{money(v.amount)}</b> по заказу #{orderId}?</>,
      okText: 'Вернуть',
      cancelText: 'Отмена',
      onOk: async () => {
        setBusy(true);
        try {
          await api(`/admin/orders/${orderId}/refund`, { method: 'POST', body: { amount: v.amount, reason: v.reason, complaintId } });
          message.success('Возврат оформлен');
          form.resetFields();
          onClose();
          onDone();
        } catch (e) {
          message.error((e as Error).message);
        } finally {
          setBusy(false);
        }
      },
    });
  };

  return (
    <Modal open={open} title={`Возврат по заказу #${orderId}`} onCancel={onClose} onOk={submit} okText="Оформить возврат" cancelText="Отмена" confirmLoading={busy} destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{ amount: Math.min(limit, maxAmount) || undefined }}>
        <Form.Item
          name="amount"
          label="Сумма, сум"
          extra={<>Доступно к возврату: {money(maxAmount)}{!can('refund_large') && <> · ваш лимит {money(SUPPORT_REFUND_LIMIT)}</>}</>}
          rules={[{ required: true, message: 'Укажите сумму' }, { type: 'number', min: 1, max: limit, message: `От 1 до ${money(limit)}` }]}
        >
          <InputNumber style={{ width: '100%' }} min={1} max={limit} step={1000} formatter={moneyFormatter} parser={moneyParser} />
        </Form.Item>
        <Form.Item name="reason" label="Причина" rules={[{ required: true, min: 3, message: 'Минимум 3 символа' }]}>
          <Input.TextArea rows={3} maxLength={500} placeholder="Например: не хватало позиции" />
        </Form.Item>
      </Form>
    </Modal>
  );
}

/** Блокировка повара: срок + причина. Поддержка — не более 24 ч. */
export function BlockChefModal({ open, onClose, chefId, nick, onDone }: {
  open: boolean; onClose: () => void; chefId: number; nick?: string; onDone: () => void;
}) {
  const { can } = useAuth();
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const permanentAllowed = can('chefs_block_permanent');
  const options = [
    { value: 1, label: '1 час' }, { value: 3, label: '3 часа' }, { value: 6, label: '6 часов' }, { value: 12, label: '12 часов' }, { value: 24, label: '24 часа' },
    ...(permanentAllowed ? [{ value: 72, label: '3 дня' }, { value: 168, label: '7 дней' }, { value: 720, label: '30 дней' }, { value: 0, label: 'Бессрочно' }] : []),
  ];

  const submit = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      await api(`/admin/chefs/${chefId}/block`, { method: 'PUT', body: { blocked: true, hours: v.hours || undefined, reason: v.reason } });
      message.success('Повар заблокирован');
      form.resetFields();
      onClose();
      onDone();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} title={`Заблокировать повара${nick ? ` «${nick}»` : ''}`} onCancel={onClose} onOk={submit} okText="Заблокировать" okButtonProps={{ danger: true }} cancelText="Отмена" confirmLoading={busy} destroyOnClose>
      <Form form={form} layout="vertical" initialValues={{ hours: 24 }}>
        <Form.Item name="hours" label="Срок" rules={[{ required: true }]} extra={!permanentAllowed ? 'Поддержка может блокировать не более чем на 24 часа' : undefined}>
          <Select options={options} />
        </Form.Item>
        <Form.Item name="reason" label="Причина" rules={[{ required: true, message: 'Укажите причину' }]}>
          <Input.TextArea rows={3} maxLength={500} />
        </Form.Item>
        <Typography.Text type="secondary">Повар получит уведомление, его меню скроется из ленты.</Typography.Text>
      </Form>
    </Modal>
  );
}

/** Модалка с одним текстовым полем (сообщение, причина и т.п.). */
export function TextPromptModal({ open, title, label, okText, required = true, minLength, danger, onClose, onSubmit, placeholder }: {
  open: boolean; title: string; label: string; okText: string; required?: boolean; minLength?: number; danger?: boolean; placeholder?: string;
  onClose: () => void; onSubmit: (text: string) => Promise<void>;
}) {
  const { message } = App.useApp();
  const [form] = Form.useForm();
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      await onSubmit((v.text ?? '').trim());
      form.resetFields();
      onClose();
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} title={title} onCancel={onClose} onOk={submit} okText={okText} okButtonProps={{ danger }} cancelText="Отмена" confirmLoading={busy} destroyOnClose>
      <Form form={form} layout="vertical">
        <Form.Item name="text" label={label} rules={[{ required, message: 'Заполните поле' }, ...(minLength ? [{ min: minLength, message: `Минимум ${minLength} символа` }] : [])]}>
          <Input.TextArea rows={4} maxLength={1000} placeholder={placeholder} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
