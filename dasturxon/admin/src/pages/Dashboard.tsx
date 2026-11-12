import { ArrowDownOutlined, ArrowUpOutlined, ExclamationCircleOutlined, InfoCircleOutlined, WarningOutlined } from '@ant-design/icons';
import { App, Avatar, Button, Card, Col, Empty, Row, Table, Tag, Typography } from 'antd';
import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api, useApi } from '../api';
import { useAuth } from '../auth';
import { ErrorBox, Loading, PageHeader } from '../components/common';
import { useCounts } from '../components/AppLayout';
import { currentWeekDays, longDateTime, money, num, rating } from '../format';
import { useLive } from '../realtime';

interface Dashboard {
  today: { orders: number; ordersChangePct: number | null; activeChefs: number; totalChefs: number; revenue?: number; revenueChangePct?: number | null; income?: number; incomeChangePct?: number | null; chefTax?: number };
  week: { day: string; orders: number; revenue: number }[];
  alerts: { complaints: number; lowRatingChefs: { id: number; nick: string; rating: number }[]; pendingVerifications: number; failedPayouts: number; pendingEarlyWithdrawals: number };
  topChefs: { id: number; nick: string; district: string; rating: number; is_online: boolean; low_rating_flag: boolean; orders: number; revenue: number }[];
  payouts?: { amount: number; chefs: number; nextPayoutAt: string };
}

function Delta({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="metric-delta flat">нет данных за вчера</span>;
  const cls = v > 0 ? 'up' : v < 0 ? 'down' : 'flat';
  return (
    <span className={`metric-delta ${cls}`}>
      {v > 0 ? <ArrowUpOutlined /> : v < 0 ? <ArrowDownOutlined /> : null} {Math.abs(v)}% <span style={{ color: '#8A94A6' }}>vs вчера</span>
    </span>
  );
}

function Metric({ label, value, delta, hint }: { label: string; value: string; delta?: number | null; hint?: string }) {
  return (
    <Card className="metric-card" bordered={false}>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      {hint ? <span className="metric-delta flat">{hint}</span> : <Delta v={delta} />}
    </Card>
  );
}

export default function DashboardPage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { modal, message } = App.useApp();
  const counts = useCounts();
  const { data, loading, error, reload } = useApi<Dashboard>('/admin/dashboard');
  const [running, setRunning] = useState(false);
  const showMoney = can('finance');

  useLive(['order:update', 'alert', 'payouts:done'], () => void reload(true));

  const week = useMemo(() => {
    const byDay = new Map((data?.week ?? []).map((w) => [w.day, w]));
    return currentWeekDays().map((d) => ({ ...d, orders: byDay.get(d.ymd)?.orders ?? 0, revenue: byDay.get(d.ymd)?.revenue ?? 0 }));
  }, [data]);

  if (loading && !data) return <Loading />;
  if (!data) return <ErrorBox error={error} retry={reload} />;
  const t = data.today;
  const a = data.alerts;

  type AlertItem = { color: 'red' | 'yellow' | 'blue'; icon: JSX.Element; text: string; to: string; show: boolean };
  const alerts = ([
    { color: 'red', icon: <ExclamationCircleOutlined style={{ color: '#E5484D' }} />, text: `Открытых жалоб: ${a.complaints}`, to: '/complaints', show: a.complaints > 0 && can('complaints') },
    ...a.lowRatingChefs.map((c) => ({
      color: 'yellow' as const, icon: <WarningOutlined style={{ color: '#F5A524' }} />, text: `Низкий рейтинг: ${c.nick} (${rating(c.rating, 1)})`, to: `/chefs/${c.id}`, show: can('chefs_view'),
    })),
    { color: 'blue', icon: <InfoCircleOutlined style={{ color: '#3B82F6' }} />, text: `Заявок на верификацию: ${a.pendingVerifications}`, to: '/verification', show: a.pendingVerifications > 0 && can('chefs_verify') },
    { color: 'red', icon: <ExclamationCircleOutlined style={{ color: '#E5484D' }} />, text: `Неудачных выплат за 14 дней: ${a.failedPayouts}`, to: '/payouts?status=failed', show: a.failedPayouts > 0 && can('finance') },
    { color: 'blue', icon: <InfoCircleOutlined style={{ color: '#3B82F6' }} />, text: `Запросов на досрочный вывод: ${a.pendingEarlyWithdrawals}`, to: '/payouts?status=pending&kind=early', show: a.pendingEarlyWithdrawals > 0 && can('finance') },
  ] as AlertItem[]).filter((x) => x.show);

  const runPayouts = () => {
    modal.confirm({
      title: 'Запустить выплаты?',
      content: data.payouts ? <>Будет выплачено <b>{money(data.payouts.amount)}</b> поварам ({data.payouts.chefs}). Действие нельзя отменить.</> : null,
      okText: 'Запустить',
      cancelText: 'Отмена',
      onOk: async () => {
        setRunning(true);
        try {
          const r = await api('/admin/payouts/run', { method: 'POST', body: { confirm: true } });
          message.success(`Выплаты запущены${r?.paid !== undefined ? `: успешно ${r.paid}` : ''}${r?.failed ? `, ошибок ${r.failed}` : ''}`);
          void reload(true);
          counts.refresh();
        } catch (e) {
          message.error((e as Error).message);
        } finally {
          setRunning(false);
        }
      },
    });
  };

  return (
    <>
      <PageHeader title="Дашборд" sub={`Сегодня, ${new Intl.DateTimeFormat('ru-RU', { timeZone: 'Asia/Tashkent', day: 'numeric', month: 'long', weekday: 'long' }).format(new Date())}`} />
      <ErrorBox error={error} retry={reload} />
      <Row gutter={[16, 16]}>
        {showMoney && <Col xs={24} sm={12} xl={6}><Metric label="Выручка сегодня" value={money(t.revenue)} delta={t.revenueChangePct} /></Col>}
        <Col xs={24} sm={12} xl={6}><Metric label="Заказов сегодня" value={num(t.orders)} delta={t.ordersChangePct} /></Col>
        <Col xs={24} sm={12} xl={6}><Metric label="Активных поваров" value={`${t.activeChefs}`} hint={`онлайн из ${t.totalChefs} подтверждённых`} /></Col>
        {showMoney && <Col xs={24} sm={12} xl={6}><Metric label="Доход платформы" value={money(t.income)} delta={t.incomeChangePct} /></Col>}
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={16}>
          <Card bordered={false} title="Заказы за неделю">
            <div style={{ height: 280 }}>
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={week} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
                  <CartesianGrid vertical={false} stroke="#EEF0F3" />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: '#6B7280' }} />
                  <YAxis allowDecimals={false} tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: '#6B7280' }} />
                  <Tooltip
                    cursor={{ fill: 'rgba(31,163,122,.08)' }}
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const p = payload[0].payload as (typeof week)[number];
                      return (
                        <div className="chart-tooltip">
                          <div className="t">{p.label}{p.isToday ? ' · сегодня' : ''}</div>
                          <div>Заказов: <b>{p.orders}</b></div>
                          {showMoney && <div>Выручка: <b>{money(p.revenue)}</b></div>}
                          {showMoney && p.orders > 0 && <div>Средний чек: <b>{money(p.revenue / p.orders)}</b></div>}
                        </div>
                      );
                    }}
                  />
                  <Bar dataKey="orders" radius={[6, 6, 0, 0]} maxBarSize={44}>
                    {week.map((d) => <Cell key={d.ymd} fill={d.isToday ? '#1FA37A' : '#A7DFC9'} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card bordered={false} title="Требует внимания" style={{ height: '100%' }}>
            {alerts.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Всё спокойно" /> : alerts.map((x, i) => (
              <div key={i} className={`alert-row ${x.color}`} onClick={() => nav(x.to)}>
                {x.icon}<span className="alert-text">{x.text}</span>
              </div>
            ))}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={data.payouts ? 16 : 24}>
          <Card bordered={false} title="Топ поваров сегодня" styles={{ body: { padding: 0 } }}>
            <Table
              rowKey="id"
              size="middle"
              pagination={false}
              dataSource={data.topChefs}
              scroll={{ x: 560 }}
              locale={{ emptyText: 'Сегодня заказов ещё не было' }}
              onRow={(r) => ({ onClick: () => nav(`/chefs/${r.id}`), className: 'clickable-row' })}
              columns={[
                { title: '#', width: 48, render: (_v, _r, i) => i + 1 },
                { title: 'Повар', dataIndex: 'nick', render: (v, r) => <span><Avatar size="small" style={{ background: '#E8F6F0', color: '#1FA37A', marginRight: 8 }}>{v?.[0]}</Avatar>{v}{r.low_rating_flag && <Tag color="warning" style={{ marginLeft: 6 }}>низкий рейтинг</Tag>}</span> },
                { title: 'Район', dataIndex: 'district' },
                { title: 'Статус', dataIndex: 'is_online', render: (v) => (v ? <Tag color="green">Онлайн</Tag> : <Tag>Офлайн</Tag>) },
                { title: 'Рейтинг', dataIndex: 'rating', render: (v) => <span>★ {rating(v)}</span> },
                { title: 'Заказов', dataIndex: 'orders', align: 'right' },
                ...(showMoney ? [{ title: 'Выручка', dataIndex: 'revenue', align: 'right' as const, render: (v: number) => money(v) }] : []),
              ]}
            />
          </Card>
        </Col>
        {data.payouts && (
          <Col xs={24} xl={8}>
            <Card bordered={false} title="Выплаты поварам" style={{ height: '100%' }}>
              <Typography.Text type="secondary">К выплате</Typography.Text>
              <div className="metric-value" style={{ color: '#138A64' }}>{money(data.payouts.amount)}</div>
              <div style={{ marginBottom: 4 }}>Поваров: <b>{data.payouts.chefs}</b></div>
              <div style={{ marginBottom: 16 }}>Следующая автоматическая выплата: <b>{longDateTime(data.payouts.nextPayoutAt)}</b></div>
              <Button type="primary" block size="large" loading={running} disabled={!can('payouts_run') || data.payouts.amount <= 0} onClick={runPayouts}>
                Запустить выплаты
              </Button>
              <Button type="link" block onClick={() => nav('/payouts')}>История выплат</Button>
            </Card>
          </Col>
        )}
      </Row>
    </>
  );
}
