import { Alert, Card, Empty, Image, Space, Spin, Tag, Typography } from 'antd';
import type { ReactNode } from 'react';
import type { ApiError } from '../api';
import { payoutStatus, statusColors, statusLabels } from '../format';
import type { OrderStatus } from '../types';

export function PageHeader({ title, extra, sub }: { title: ReactNode; extra?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="page-header">
      <div>
        <Typography.Title level={3} style={{ margin: 0 }}>{title}</Typography.Title>
        {sub && <Typography.Text type="secondary">{sub}</Typography.Text>}
      </div>
      {extra && <Space wrap>{extra}</Space>}
    </div>
  );
}

export function OrderStatusTag({ status }: { status: OrderStatus }) {
  return <Tag color={statusColors[status] ?? 'default'}>{statusLabels[status] ?? status}</Tag>;
}

export function PayoutStatusTag({ status }: { status: string }) {
  const s = payoutStatus[status] ?? { label: status, color: 'default' };
  return <Tag color={s.color}>{s.label}</Tag>;
}

export function ChefStatusTag({ status }: { status: 'online' | 'offline' | 'blocked' | string }) {
  if (status === 'online') return <Tag color="green">Онлайн</Tag>;
  if (status === 'blocked') return <Tag color="red">Заблокирован</Tag>;
  return <Tag>Офлайн</Tag>;
}

export function ErrorBox({ error, retry }: { error: ApiError | null; retry?: () => void }) {
  if (!error) return null;
  return (
    <Alert
      type="error"
      showIcon
      style={{ marginBottom: 16 }}
      message={error.status === 403 ? 'Недостаточно прав' : 'Не удалось загрузить данные'}
      description={error.message}
      action={retry ? <a onClick={retry}>Повторить</a> : undefined}
    />
  );
}

export function Loading() {
  return <div style={{ padding: 48, textAlign: 'center' }}><Spin size="large" /></div>;
}

export function NotFound({ text = 'Не найдено' }: { text?: string }) {
  return <Card><Empty description={text} /></Card>;
}

const PHOTO_FALLBACK = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90" viewBox="0 0 120 90"><rect width="120" height="90" fill="#F1F3F5"/><path d="M44 60l12-14 9 10 7-8 12 12z" fill="#C7CDD4"/><circle cx="74" cy="34" r="6" fill="#C7CDD4"/><text x="60" y="80" font-family="Arial" font-size="9" fill="#8A94A6" text-anchor="middle">нет фото</text></svg>',
);

/** Фото с превью и заглушкой, если картинка не загрузилась. */
export function Photo({ src, width, height }: { src: string; width: number; height: number }) {
  return <Image src={src} width={width} height={height} fallback={PHOTO_FALLBACK} style={{ objectFit: 'cover', borderRadius: 8 }} />;
}
