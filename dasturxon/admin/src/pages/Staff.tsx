import { PlusOutlined } from '@ant-design/icons';
import { App, Button, Card, Form, Input, Modal, Select, Switch, Table, Tag } from 'antd';
import { useState } from 'react';
import { api, useApi } from '../api';
import { useAuth } from '../auth';
import { ErrorBox, PageHeader } from '../components/common';
import { date, roleLabels } from '../format';
import type { Role } from '../types';

interface StaffRow { id: number; name: string; email: string; role: Role; is_active: boolean; created_at: string }

const roleOptions = [
  { value: 'owner', label: 'Владелец — полный доступ' },
  { value: 'accountant', label: 'Бухгалтер — финансы, отчёты, выплаты' },
  { value: 'support', label: 'Поддержка — повара, жалобы, возвраты до 50 000' },
];
const roleColor: Record<Role, string> = { owner: 'gold', accountant: 'blue', support: 'green' };

export default function StaffPage() {
  const { staff: me } = useAuth();
  const { message } = App.useApp();
  const { data, loading, error, reload } = useApi<StaffRow[]>('/admin/staff');
  const [editing, setEditing] = useState<StaffRow | 'new' | null>(null);
  const [busy, setBusy] = useState(false);
  const [form] = Form.useForm();

  const open = (s: StaffRow | 'new') => {
    setEditing(s);
    form.resetFields();
    if (s !== 'new') form.setFieldsValue({ name: s.name, email: s.email, role: s.role, isActive: s.is_active });
    else form.setFieldsValue({ role: 'support', isActive: true });
  };

  const save = async () => {
    const v = await form.validateFields();
    setBusy(true);
    try {
      if (editing === 'new') {
        await api('/admin/staff', { method: 'POST', body: { name: v.name, email: v.email, password: v.password, role: v.role } });
        message.success('Сотрудник добавлен');
      } else if (editing) {
        await api(`/admin/staff/${editing.id}`, { method: 'PUT', body: { name: v.name, role: v.role, isActive: v.isActive, password: v.password || undefined } });
        message.success('Сохранено');
      }
      setEditing(null);
      void reload(true);
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const isSelf = editing !== 'new' && editing?.id === me?.id;

  return (
    <>
      <PageHeader title="Сотрудники" extra={<Button type="primary" icon={<PlusOutlined />} onClick={() => open('new')}>Добавить</Button>} />
      <ErrorBox error={error} retry={reload} />
      <Card bordered={false} styles={{ body: { padding: 0 } }}>
        <Table<StaffRow>
          rowKey="id"
          loading={loading}
          dataSource={data ?? []}
          pagination={false}
          scroll={{ x: 640 }}
          onRow={(r) => ({ onClick: () => open(r), className: 'clickable-row' })}
          columns={[
            { title: 'Имя', dataIndex: 'name', render: (v, r) => <>{v}{r.id === me?.id && <Tag style={{ marginLeft: 6 }}>вы</Tag>}</> },
            { title: 'Email', dataIndex: 'email' },
            { title: 'Роль', dataIndex: 'role', render: (v: Role) => <Tag color={roleColor[v]}>{roleLabels[v]}</Tag> },
            { title: 'Статус', dataIndex: 'is_active', render: (v) => (v ? <Tag color="green">Активен</Tag> : <Tag>Отключён</Tag>) },
            { title: 'Добавлен', dataIndex: 'created_at', render: date },
          ]}
        />
      </Card>
      <Modal
        open={!!editing}
        title={editing === 'new' ? 'Новый сотрудник' : 'Редактировать сотрудника'}
        onCancel={() => setEditing(null)}
        onOk={save}
        okText="Сохранить"
        cancelText="Отмена"
        confirmLoading={busy}
        forceRender
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Имя" rules={[{ required: true, min: 2, message: 'Минимум 2 символа' }]}><Input /></Form.Item>
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email', message: 'Введите email' }]}><Input disabled={editing !== 'new'} /></Form.Item>
          <Form.Item name="role" label="Роль" rules={[{ required: true }]}><Select options={roleOptions} disabled={isSelf} /></Form.Item>
          <Form.Item
            name="password"
            label={editing === 'new' ? 'Пароль' : 'Новый пароль (оставьте пустым, чтобы не менять)'}
            rules={[{ required: editing === 'new', message: 'Введите пароль' }, { min: 8, message: 'Минимум 8 символов' }]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          {editing !== 'new' && (
            <Form.Item name="isActive" label="Доступ" valuePropName="checked" extra={isSelf ? 'Нельзя отключить самого себя' : 'Отключённый сотрудник будет разлогинен'}>
              <Switch checkedChildren="активен" unCheckedChildren="отключён" disabled={isSelf} />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </>
  );
}
