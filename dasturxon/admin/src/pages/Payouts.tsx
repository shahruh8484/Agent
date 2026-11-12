import { CheckOutlined, CloseOutlined } from '@ant-design/icons';
import { App, Button, Card, Col, Input, Row, Segmented, Space, Table, Typography } from 'antd';
import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, useApi } from '../api';
import { useAuth } from '../auth';
import { ErrorBox, PageHeader, PayoutStatusTag } from '../components/common';
import { TextPromptModal } from '../components/actions';
import { date, dateTime, longDateTime, money, payoutKind } from '../format';
import { useLive } from '../realtime';
import type { Payout } from '../types';

interface Payouts {
  nextPayoutAt: string;
  upcoming: { chef_id: number; nick: string; payout_card_masked: string | null; amount: number }[];
  items: Payout[];
}

export default function PayoutsPage() {
  const { can } = useAuth();
  const { message, modal } = App.useApp();
  const [sp, setSp] = useSearchParams();
  const status = sp.get('status') ?? 'all';
  const kind = sp.get('kind') ?? 'all';
  const { data, loading, error, reload } = useApi<Payouts>('/admin/payouts', { status, kind });
  const [running, setRunning] = useState(false);
  const [rejecting, setRejecting] = useState<Payout | null>(null);
  useLive(['payouts:done', 'alert'], (p) => { if (!p?.type || ['payout_failed', 'early_withdrawal'].includes(p.type)) void reload(true); });

  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); if (v === 'all') n.delete(k); else n.set(k, v); setSp(n, { replace: true }); };
  const upcomingTotal = (data?.upcoming ?? []).reduce((s, u) => s + Number(u.amount), 0);

  const run = () => modal.confirm({
    title: 'Запустить выплаты сейчас?',
    content: <>Будет выплачено <b>{money(upcomingTotal)}</b> поварам ({data?.upcoming.length ?? 0}). Действие нельзя отменить.</>,
    okText: 'Запустить',
    cancelText: 'Отмена',
    onOk: async () => {
      setRunning(true);
      try {
        const r = await api<{ total: number; paid: number; failed: number; amount: number }>('/admin/payouts/run', { method: 'POST', body: { confirm: true } });
        message.success(`Готово: выплачено ${r.paid} из ${r.total}${r.failed ? `, ошибок: ${r.failed}` : ''}`);
        void reload(true);
      } catch (e) { message.error((e as Error).message); } finally { setRunning(false); }
    },
  });

  const approve = (p: Payout) => {
    let note = '';
    modal.confirm({
      title: `Подтвердить досрочную выплату #${p.id}?`,
      content: (
        <div>
          <p>Повар: <b>{p.nick}</b>, к перечислению <b>{money(p.net_amount)}</b> на карту {p.payout_card_masked ?? '—'}.</p>
          <Input placeholder="ID банковской транзакции (необязательно)" onChange={(e) => { note = e.target.value; }} />
        </div>
      ),
      okText: 'Выплачено',
      cancelText: 'Отмена',
      onOk: async () => {
        try {
          await api(`/admin/payouts/${p.id}/complete`, { method: 'POST', body: { ok: true, note: note.trim() || undefined } });
          message.success('Выплата отмечена выполненной');
          void reload(true);
        } catch (e) { message.error((e as Error).message); }
      },
    });
  };

  return (
    <>
      <PageHeader
        title="Выплаты поварам"
        extra={can('payouts_run') && <Button type="primary" loading={running} disabled={!upcomingTotal} onClick={run}>Запустить выплаты</Button>}
      />
      <ErrorBox error={error} retry={reload} />
      <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
        <Col xs={24} md={8}><div className="stat-tile accent"><div className="label">К выплате</div><div className="value">{money(upcomingTotal)}</div></div></Col>
        <Col xs={12} md={8}><div className="stat-tile"><div className="label">Поваров</div><div className="value">{data?.upcoming.length ?? '—'}</div></div></Col>
        <Col xs={12} md={8}><div className="stat-tile"><div className="label">Следующая автоматическая выплата</div><div className="value" style={{ fontSize: 16 }}>{data ? longDateTime(data.nextPayoutAt) : '—'}</div></div></Col>
      </Row>
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={9}>
          <Card bordered={false} title="Ближайшие выплаты" styles={{ body: { padding: 0 } }}>
            <Table
              rowKey="chef_id"
              size="small"
              loading={loading}
              dataSource={data?.upcoming ?? []}
              pagination={{ pageSize: 10, hideOnSinglePage: true }}
              locale={{ emptyText: 'Нет невыплаченных начислений' }}
              columns={[
                { title: 'Повар', dataIndex: 'nick', render: (v: string, r) => <Link to={`/chefs/${r.chef_id}`}>{v}</Link> },
                { title: 'Карта', dataIndex: 'payout_card_masked', render: (v: string | null) => v ?? <Typography.Text type="danger">нет карты</Typography.Text> },
                { title: 'Сумма', dataIndex: 'amount', align: 'right', render: (v: number) => money(v) },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={15}>
          <Card
            bordered={false}
            title="История выплат"
            styles={{ body: { padding: 0 } }}
            extra={
              <Space wrap>
                <Segmented size="small" value={kind} onChange={(v) => set('kind', String(v))} options={[{ value: 'all', label: 'Все' }, { value: 'weekly', label: 'Еженедельные' }, { value: 'early', label: 'Досрочные' }]} />
                <Segmented size="small" value={status} onChange={(v) => set('status', String(v))} options={[{ value: 'all', label: 'Все' }, { value: 'pending', label: 'Ожидают' }, { value: 'processing', label: 'В обработке' }, { value: 'done', label: 'Выплачено' }, { value: 'failed', label: 'Ошибки' }]} />
              </Space>
            }
          >
            <Table<Payout>
              rowKey="id"
              size="small"
              loading={loading}
              dataSource={data?.items ?? []}
              scroll={{ x: 820 }}
              pagination={{ pageSize: 20, hideOnSinglePage: true }}
              locale={{ emptyText: 'Выплат нет' }}
              columns={[
                { title: '№', dataIndex: 'id', width: 60 },
                { title: 'Повар', dataIndex: 'nick', render: (v, r) => <Link to={`/chefs/${r.chef_id}`}>{v}</Link> },
                { title: 'Тип', dataIndex: 'kind', render: (v) => payoutKind[v] ?? v },
                { title: 'Период', render: (_, r) => (r.kind === 'weekly' && r.week_start ? `${date(r.week_start)} — ${date(r.week_end)}` : dateTime(r.created_at)) },
                { title: 'Сумма', dataIndex: 'net_amount', align: 'right', render: (v, r) => <div>{money(v)}{r.commission_1pct > 0 && <div style={{ fontSize: 12, color: '#8A94A6' }}>комиссия {money(r.commission_1pct)}</div>}</div> },
                { title: 'Статус', dataIndex: 'status', render: (v, r) => <div><PayoutStatusTag status={v} />{r.error && <div style={{ fontSize: 12, color: '#E5484D', maxWidth: 200 }}>{r.error}</div>}{r.paid_at && <div style={{ fontSize: 12, color: '#8A94A6' }}>{dateTime(r.paid_at)}</div>}</div> },
                ...(can('payouts_run') ? [{
                  title: '', key: 'act', width: 120, render: (_: unknown, r: Payout) => (r.kind === 'early' && ['pending', 'processing'].includes(r.status) ? (
                    <Space size={4}>
                      <Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => approve(r)} title="Подтвердить выплату" />
                      <Button size="small" danger icon={<CloseOutlined />} onClick={() => setRejecting(r)} title="Отклонить" />
                    </Space>
                  ) : null),
                }] : []),
              ]}
            />
          </Card>
        </Col>
      </Row>
      <TextPromptModal
        open={!!rejecting}
        title={`Отклонить досрочную выплату #${rejecting?.id ?? ''}`}
        label="Причина (сумма вернётся на баланс повара)"
        okText="Отклонить"
        danger
        onClose={() => setRejecting(null)}
        onSubmit={async (note) => {
          await api(`/admin/payouts/${rejecting!.id}/complete`, { method: 'POST', body: { ok: false, note } });
          message.success('Выплата отклонена');
          void reload(true);
        }}
      />
    </>
  );
}
