import { LockOutlined, MailOutlined } from '@ant-design/icons';
import { Alert, Button, Card, Form, Input, Typography } from 'antd';
import { useState } from 'react';
import { useAuth } from '../auth';

export default function LoginPage() {
  const { login } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onFinish = async (v: { email: string; password: string }) => {
    setBusy(true);
    setError(null);
    try {
      await login(v.email.trim(), v.password);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-wrap">
      <Card className="login-card" bordered={false} style={{ boxShadow: '0 10px 40px rgba(15,23,42,.08)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 24 }}>
          <div className="brand-logo" style={{ width: 44, height: 44, fontSize: 22 }}>D</div>
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>Dasturxon</Typography.Title>
            <Typography.Text type="secondary">Вход в админ-панель</Typography.Text>
          </div>
        </div>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />}
        <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
          <Form.Item name="email" label="Email" rules={[{ required: true, type: 'email', message: 'Введите email' }]}>
            <Input prefix={<MailOutlined />} placeholder="name@dasturxon.uz" autoComplete="username" size="large" />
          </Form.Item>
          <Form.Item name="password" label="Пароль" rules={[{ required: true, message: 'Введите пароль' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" size="large" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large" loading={busy}>Войти</Button>
        </Form>
      </Card>
    </div>
  );
}
