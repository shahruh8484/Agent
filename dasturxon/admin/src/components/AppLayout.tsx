import {
  AlertOutlined, AuditOutlined, BarChartOutlined, DashboardOutlined, LogoutOutlined, MenuOutlined, SafetyCertificateOutlined,
  SettingOutlined, ShoppingOutlined, TeamOutlined, UserOutlined, WalletOutlined, FireOutlined,
} from '@ant-design/icons';
import { Avatar, Badge, Button, Dropdown, Grid, Layout, Menu, Tooltip, Typography } from 'antd';
import type { MenuProps } from 'antd';
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { roleLabels } from '../format';
import { useLive, useRealtime } from '../realtime';
import type { Permission } from '../types';

const { Sider, Header, Content } = Layout;

interface Counts { verifications: number; complaints: number; refresh: () => void }
const CountsCtx = createContext<Counts>({ verifications: 0, complaints: 0, refresh: () => undefined });
/** Счётчики для бейджей меню; страницы вызывают refresh() после действий. */
export const useCounts = () => useContext(CountsCtx);

interface NavItem { key: string; label: string; icon: ReactNode; perm: Permission; badge?: 'verifications' | 'complaints' }
const groups: NavItem[][] = [
  [
    { key: '/', label: 'Дашборд', icon: <DashboardOutlined />, perm: 'dashboard' },
    { key: '/orders', label: 'Заказы', icon: <ShoppingOutlined />, perm: 'orders_view' },
    { key: '/chefs', label: 'Повара', icon: <FireOutlined />, perm: 'chefs_view' },
    { key: '/buyers', label: 'Покупатели', icon: <UserOutlined />, perm: 'buyers_view' },
  ],
  [
    { key: '/verification', label: 'Верификация', icon: <SafetyCertificateOutlined />, perm: 'chefs_verify', badge: 'verifications' },
    { key: '/complaints', label: 'Жалобы', icon: <AlertOutlined />, perm: 'complaints', badge: 'complaints' },
    { key: '/payouts', label: 'Выплаты', icon: <WalletOutlined />, perm: 'finance' },
  ],
  [
    { key: '/finance', label: 'Финансы', icon: <BarChartOutlined />, perm: 'finance' },
    { key: '/staff', label: 'Сотрудники', icon: <TeamOutlined />, perm: 'staff_manage' },
    { key: '/settings', label: 'Настройки', icon: <SettingOutlined />, perm: 'settings' },
    { key: '/audit', label: 'Журнал действий', icon: <AuditOutlined />, perm: 'staff_manage' },
  ],
];

export default function AppLayout() {
  const { staff, can, logout } = useAuth();
  const { connected } = useRealtime();
  const navigate = useNavigate();
  const loc = useLocation();
  const screens = Grid.useBreakpoint();
  const mobile = !screens.lg;
  const [open, setOpen] = useState(false);
  const [counts, setCounts] = useState({ verifications: 0, complaints: 0 });

  const refresh = useCallback(() => {
    if (!can('dashboard')) return;
    api('/admin/dashboard').then((d) => setCounts({
      verifications: d.alerts?.pendingVerifications ?? 0,
      complaints: d.alerts?.complaints ?? 0,
    })).catch(() => undefined);
  }, [can]);

  useEffect(() => { refresh(); const t = setInterval(refresh, 60_000); return () => clearInterval(t); }, [refresh]);
  useLive(['alert'], refresh);
  useEffect(() => { if (mobile) setOpen(false); }, [loc.pathname, mobile]);

  const selected = '/' + (loc.pathname.split('/')[1] ?? '');
  const items: MenuProps['items'] = [];
  groups.forEach((g, gi) => {
    const visible = g.filter((i) => can(i.perm));
    if (!visible.length) return;
    if (items.length && gi > 0) items.push({ type: 'divider' });
    for (const i of visible) {
      const n = i.badge ? counts[i.badge] : 0;
      items.push({
        key: i.key,
        icon: i.icon,
        label: (
          <span className="nav-label">
            {i.label}
            {n > 0 && <Badge count={n} size="small" color={i.badge === 'complaints' ? '#E5484D' : '#1FA37A'} />}
          </span>
        ),
      });
    }
  });

  const sider = (
    <Sider
      width={232}
      collapsedWidth={0}
      collapsed={mobile && !open}
      trigger={null}
      theme="light"
      className={`app-sider ${mobile ? 'mobile' : ''}`}
    >
      <div className="brand" onClick={() => navigate('/')}>
        <div className="brand-logo">D</div>
        <div>
          <div className="brand-name">Dasturxon</div>
          <div className="brand-sub">Админ-панель</div>
        </div>
      </div>
      <Menu mode="inline" selectedKeys={[selected]} items={items} onClick={(e) => navigate(e.key)} style={{ borderInlineEnd: 'none' }} />
    </Sider>
  );

  return (
    <CountsCtx.Provider value={{ ...counts, refresh }}>
      <Layout style={{ minHeight: '100vh' }}>
        {sider}
        {mobile && open && <div className="sider-backdrop" onClick={() => setOpen(false)} />}
        <Layout>
          <Header className="app-header">
            {mobile ? <Button type="text" icon={<MenuOutlined />} onClick={() => setOpen((v) => !v)} aria-label="Меню" /> : <span />}
            <div className="header-right">
              <Tooltip title={connected ? 'Живые обновления подключены' : 'Нет подключения к обновлениям'}>
                <Badge status={connected ? 'success' : 'default'} text={screens.sm ? (connected ? 'Онлайн' : 'Офлайн') : undefined} />
              </Tooltip>
              <Dropdown menu={{ items: [{ key: 'logout', icon: <LogoutOutlined />, label: 'Выйти', onClick: () => void logout() }] }} trigger={['click']}>
                <div className="user-chip">
                  <Avatar style={{ background: '#1FA37A' }}>{staff?.name?.[0] ?? '?'}</Avatar>
                  {screens.sm && (
                    <div style={{ lineHeight: 1.2 }}>
                      <div style={{ fontWeight: 600 }}>{staff?.name}</div>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>{roleLabels[staff?.role ?? ''] ?? staff?.role}</Typography.Text>
                    </div>
                  )}
                </div>
              </Dropdown>
            </div>
          </Header>
          <Content className="app-content">
            <Outlet />
          </Content>
        </Layout>
      </Layout>
    </CountsCtx.Provider>
  );
}
