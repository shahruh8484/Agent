import { Result, Button } from 'antd';
import type { ReactNode } from 'react';
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth';
import AppLayout from './components/AppLayout';
import { RealtimeProvider } from './realtime';
import type { Permission } from './types';
import LoginPage from './pages/Login';
import DashboardPage from './pages/Dashboard';
import OrdersPage from './pages/Orders';
import OrderDetailPage from './pages/OrderDetail';
import ChefsPage from './pages/Chefs';
import ChefDetailPage from './pages/ChefDetail';
import VerificationPage from './pages/Verification';
import BuyersPage from './pages/Buyers';
import BuyerDetailPage from './pages/BuyerDetail';
import ComplaintsPage from './pages/Complaints';
import ComplaintDetailPage from './pages/ComplaintDetail';
import FinancePage from './pages/Finance';
import PayoutsPage from './pages/Payouts';
import StaffPage from './pages/Staff';
import SettingsPage from './pages/Settings';
import AuditPage from './pages/Audit';

function Forbidden() {
  const nav = useNavigate();
  return <Result status="403" title="Нет доступа" subTitle="У вашей роли нет прав на этот раздел." extra={<Button type="primary" onClick={() => nav('/')}>На главную</Button>} />;
}

function Guard({ perm, children }: { perm: Permission; children: ReactNode }) {
  const { can } = useAuth();
  return can(perm) ? <>{children}</> : <Forbidden />;
}

function Protected() {
  const { staff } = useAuth();
  if (!staff) return <Navigate to="/login" replace />;
  return (
    <RealtimeProvider>
      <AppLayout />
    </RealtimeProvider>
  );
}

function LoginRoute() {
  const { staff } = useAuth();
  return staff ? <Navigate to="/" replace /> : <LoginPage />;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginRoute />} />
        <Route element={<Protected />}>
          <Route index element={<Guard perm="dashboard"><DashboardPage /></Guard>} />
          <Route path="orders" element={<Guard perm="orders_view"><OrdersPage /></Guard>} />
          <Route path="orders/:id" element={<Guard perm="orders_view"><OrderDetailPage /></Guard>} />
          <Route path="chefs" element={<Guard perm="chefs_view"><ChefsPage /></Guard>} />
          <Route path="chefs/:id" element={<Guard perm="chefs_view"><ChefDetailPage /></Guard>} />
          <Route path="verification" element={<Guard perm="chefs_verify"><VerificationPage /></Guard>} />
          <Route path="buyers" element={<Guard perm="buyers_view"><BuyersPage /></Guard>} />
          <Route path="buyers/:id" element={<Guard perm="buyers_view"><BuyerDetailPage /></Guard>} />
          <Route path="complaints" element={<Guard perm="complaints"><ComplaintsPage /></Guard>} />
          <Route path="complaints/:id" element={<Guard perm="complaints"><ComplaintDetailPage /></Guard>} />
          <Route path="finance" element={<Guard perm="finance"><FinancePage /></Guard>} />
          <Route path="payouts" element={<Guard perm="finance"><PayoutsPage /></Guard>} />
          <Route path="staff" element={<Guard perm="staff_manage"><StaffPage /></Guard>} />
          <Route path="settings" element={<Guard perm="settings"><SettingsPage /></Guard>} />
          <Route path="audit" element={<Guard perm="staff_manage"><AuditPage /></Guard>} />
          <Route path="*" element={<Result status="404" title="Страница не найдена" />} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}
