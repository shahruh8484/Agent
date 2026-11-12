import { DownloadOutlined } from '@ant-design/icons';
import { App, Button, Card, Col, DatePicker, Empty, Row, Segmented, Space, Typography } from 'antd';
import dayjs, { type Dayjs } from 'dayjs';
import { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Area, AreaChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { download, useApi } from '../api';
import { useAuth } from '../auth';
import { ErrorBox, Loading, PageHeader } from '../components/common';
import { date, dayEndIso, dayStartIso, money, num, tashkentYmd } from '../format';
import { useLive } from '../realtime';

interface Finance {
  period: string; from: string; turnover: number; orders: number; commission: number; deliveryBuffer: number; deliveryCost: number;
  earlyWithdrawalFees: number; refunds: number; refundsPlatformShare: number; platformIncome: number; itParkFee: number; netIncome: number;
  chefTaxWithheld: number; paidToChefs: number; chart: { bucket: string; turnover: number; income: number }[];
}

const periods = [{ value: 'day', label: 'День' }, { value: 'week', label: 'Неделя' }, { value: 'month', label: 'Месяц' }, { value: 'year', label: 'Год' }];
const months = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

function bucketLabel(b: string, period: string) {
  if (period === 'day') return b;
  if (period === 'year') { const [y, m] = b.split('-'); return `${months[Number(m) - 1]} ${y.slice(2)}`; }
  return `${b.slice(8, 10)}.${b.slice(5, 7)}`;
}

const short = (v: number) => (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1).replace('.0', '')} млн` : Math.abs(v) >= 1e3 ? `${Math.round(v / 1e3)} тыс` : String(v));

function Tile({ label, value, accent, hint }: { label: string; value: number; accent?: boolean; hint?: string }) {
  return (
    <div className={`stat-tile ${accent ? 'accent' : ''}`}>
      <div className="label">{label}</div>
      <div className="value">{money(value)}</div>
      {hint && <Typography.Text type="secondary" style={{ fontSize: 12 }}>{hint}</Typography.Text>}
    </div>
  );
}

export default function FinancePage() {
  const { can } = useAuth();
  const nav = useNavigate();
  const { message } = App.useApp();
  const [sp, setSp] = useSearchParams();
  const period = sp.get('period') ?? 'month';
  const { data: f, loading, error, reload } = useApi<Finance>('/admin/finance', { period });
  useLive(['order:update', 'payouts:done'], () => void reload(true));
  const [range, setRange] = useState<[Dayjs, Dayjs]>([dayjs().subtract(30, 'day'), dayjs()]);
  const [busy, setBusy] = useState<string | null>(null);

  // Пустые корзины тоже показываем, чтобы ось времени была непрерывной
  const chart = useMemo(() => {
    if (!f) return [];
    const by = new Map(f.chart.map((c) => [c.bucket, c]));
    const keys: string[] = [];
    const today = dayjs(tashkentYmd());
    if (period === 'day') for (let h = 0; h <= 23; h++) keys.push(`${String(h).padStart(2, '0')}:00`);
    else if (period === 'year') { let d = today.subtract(11, 'month'); for (let i = 0; i < 12; i++) { keys.push(d.format('YYYY-MM')); d = d.add(1, 'month'); } }
    else { const n = period === 'week' ? 7 : 30; for (let i = n - 1; i >= 0; i--) keys.push(today.subtract(i, 'day').format('YYYY-MM-DD')); }
    for (const k of by.keys()) if (!keys.includes(k)) keys.push(k);
    keys.sort();
    return keys.map((k) => ({ bucket: k, label: bucketLabel(k, period), turnover: Number(by.get(k)?.turnover ?? 0), income: Number(by.get(k)?.income ?? 0) }));
  }, [f, period]);

  const dl = async (kind: 'tax' | 'transactions') => {
    setBusy(kind);
    try {
      const [a, b] = range;
      await download(`/admin/reports/${kind}`, { from: dayStartIso(a.format('YYYY-MM-DD')), to: dayEndIso(b.format('YYYY-MM-DD')) }, `${kind}_${a.format('YYYY-MM-DD')}_${b.format('YYYY-MM-DD')}.xlsx`);
      message.success('Файл скачан');
    } catch (e) {
      message.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <PageHeader
        title="Финансы"
        sub={f ? `С ${date(f.from)} · выполнено заказов: ${num(f.orders)}` : undefined}
        extra={<Segmented options={periods} value={period} onChange={(v) => setSp({ period: String(v) }, { replace: true })} />}
      />
      <ErrorBox error={error} retry={reload} />
      {loading && !f ? <Loading /> : f && (
        <>
          <Card bordered={false} style={{ marginBottom: 16 }}>
            <Row gutter={[12, 12]}>
              <Col xs={24} sm={12} lg={8} xl={4}><Tile label="Оборот" value={f.turnover} hint="с доставкой, за вычетом возвратов" /></Col>
              <Col xs={24} sm={12} lg={8} xl={4}><Tile label="Доход платформы" value={f.platformIncome} /></Col>
              <Col xs={24} sm={12} lg={8} xl={4}><Tile label="Взнос IT Park 1%" value={f.itParkFee} /></Col>
              <Col xs={24} sm={12} lg={8} xl={4}><Tile label="Чистый доход" value={f.netIncome} accent /></Col>
              <Col xs={24} sm={12} lg={8} xl={4}><Tile label="Выплачено поварам" value={f.paidToChefs} /></Col>
              <Col xs={24} sm={12} lg={8} xl={4}><Tile label="Налог поваров удержан" value={f.chefTaxWithheld} /></Col>
            </Row>
          </Card>
          <Row gutter={[16, 16]}>
            <Col xs={24} xl={16}>
              <Card bordered={false} title="Оборот и доход">
                {chart.every((c) => !c.turnover && !c.income) ? <Empty description="За период нет выполненных заказов" style={{ padding: 40 }} /> : (
                  <div style={{ height: 300 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                        <defs>
                          <linearGradient id="gT" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#1FA37A" stopOpacity={0.25} /><stop offset="100%" stopColor="#1FA37A" stopOpacity={0} /></linearGradient>
                          <linearGradient id="gI" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3B82F6" stopOpacity={0.25} /><stop offset="100%" stopColor="#3B82F6" stopOpacity={0} /></linearGradient>
                        </defs>
                        <CartesianGrid vertical={false} stroke="#EEF0F3" />
                        <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: '#6B7280' }} minTickGap={16} />
                        <YAxis tickLine={false} axisLine={false} tick={{ fontSize: 12, fill: '#6B7280' }} tickFormatter={short} width={60} />
                        <Tooltip formatter={(v: number, n: string) => [money(v), n]} labelStyle={{ fontWeight: 600 }} contentStyle={{ borderRadius: 10, border: '1px solid #ECEFF3' }} />
                        <Legend iconType="circle" />
                        <Area type="monotone" dataKey="turnover" name="Оборот" stroke="#1FA37A" strokeWidth={2} fill="url(#gT)" />
                        <Area type="monotone" dataKey="income" name="Доход платформы" stroke="#3B82F6" strokeWidth={2} fill="url(#gI)" />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Card>
            </Col>
            <Col xs={24} xl={8}>
              <Card bordered={false} title="Детализация дохода" style={{ marginBottom: 16 }}>
                <div className="money-list">
                  <div className="row"><span>Комиссия с поваров</span><span>{money(f.commission)}</span></div>
                  <div className="row"><span>Буфер доставки</span><span>{money(f.deliveryBuffer)}</span></div>
                  <div className="row"><span>Комиссия за досрочный вывод</span><span>{money(f.earlyWithdrawalFees)}</span></div>
                  <div className="row"><span>Возвраты за счёт платформы</span><span>{money(-f.refundsPlatformShare)}</span></div>
                  <div className="row total"><span>Доход платформы</span><span>{money(f.platformIncome)}</span></div>
                  <div className="row"><span>Взнос IT Park 1%</span><span>{money(-f.itParkFee)}</span></div>
                  <div className="row total"><span>Чистый доход</span><span>{money(f.netIncome)}</span></div>
                  <div className="row" style={{ color: '#6B7280' }}><span>Оплачено Яндекс Доставке</span><span>{money(f.deliveryCost)}</span></div>
                  <div className="row" style={{ color: '#6B7280' }}><span>Всего возвратов</span><span>{money(f.refunds)}</span></div>
                </div>
              </Card>
            </Col>
          </Row>
        </>
      )}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        {can('tax_reports') && (
          <Col xs={24} xl={16}>
            <Card bordered={false} title="Отчёты (Excel)">
              <Space wrap size={[12, 12]}>
                <DatePicker.RangePicker format="DD.MM.YYYY" value={range} allowClear={false} onChange={(r) => r && r[0] && r[1] && setRange([r[0], r[1]])} />
                <Button icon={<DownloadOutlined />} loading={busy === 'tax'} onClick={() => dl('tax')}>Налоговый отчёт</Button>
                <Button icon={<DownloadOutlined />} loading={busy === 'transactions'} onClick={() => dl('transactions')}>Все транзакции</Button>
              </Space>
              <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
                Налоговый отчёт — оборот и удержанный налог 4% по каждому повару. Транзакции — заказы, выплаты и сводка за период.
              </Typography.Paragraph>
            </Card>
          </Col>
        )}
        <Col xs={24} xl={8}>
          <Card bordered={false} title="Выплаты поварам">
            <Typography.Paragraph type="secondary">История выплат, ближайшие выплаты и подтверждение досрочных выводов.</Typography.Paragraph>
            <Button type="primary" onClick={() => nav('/payouts')}>Перейти к выплатам</Button>
          </Card>
        </Col>
      </Row>
    </>
  );
}
