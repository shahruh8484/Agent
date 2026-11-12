import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, forceLogout, staffStore, tokens } from './api';
import type { Permission, Staff } from './types';

interface AuthCtx {
  staff: Staff | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (p: Permission) => boolean;
}

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [staff, setStaff] = useState<Staff | null>(() => (tokens.access ? staffStore.get() : null));

  useEffect(() => {
    const onLogout = () => setStaff(null);
    window.addEventListener('dx:logout', onLogout);
    return () => window.removeEventListener('dx:logout', onLogout);
  }, []);

  // Актуализируем права при открытии (роль могла измениться)
  useEffect(() => {
    if (!tokens.access) return;
    api<Staff>('/admin/me').then((s) => { staffStore.set(s); setStaff(s); }).catch(() => { /* 401 уже обработан */ });
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const r = await api<{ accessToken: string; refreshToken: string; staff: Staff }>('/admin/auth/login', { method: 'POST', body: { email, password } });
    tokens.set(r.accessToken, r.refreshToken);
    staffStore.set(r.staff);
    setStaff(r.staff);
  }, []);

  const logout = useCallback(async () => {
    const rt = tokens.refresh;
    if (rt) await api('/auth/logout', { method: 'DELETE', body: { refreshToken: rt } }).catch(() => undefined);
    forceLogout();
  }, []);

  const value = useMemo<AuthCtx>(() => ({
    staff, login, logout,
    can: (p) => !!staff?.permissions.includes(p),
  }), [staff, login, logout]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
