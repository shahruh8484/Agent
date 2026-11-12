import { create } from 'zustand';
import { setTokenHandlers } from '../api/client';
import { authApi } from '../api/endpoints';
import { disconnectSocket, setSocketTokenGetter } from '../api/socket';
import type { AuthUser, Role, Tokens, VerifyOtpResponse } from '../api/types';
import { resetPush } from '../lib/push';
import { storage } from '../lib/storage';

const K_ACCESS = 'dx.access';
const K_REFRESH = 'dx.refresh';
const K_USER = 'dx.user';
const K_ROLE = 'dx.role';

interface AuthState {
  status: 'loading' | 'signedOut' | 'signedIn';
  accessToken: string | null;
  refreshToken: string | null;
  user: AuthUser | null;
  /** роль, выбранная на этом устройстве ранее (чтобы не спрашивать снова) */
  lastRole: Role | null;
  bootstrap: () => Promise<void>;
  signIn: (r: VerifyOtpResponse) => Promise<void>;
  setTokens: (t: Tokens) => Promise<void>;
  setUserName: (name: string) => void;
  signOut: () => Promise<void>;
}

export const useAuth = create<AuthState>((set, get) => ({
  status: 'loading',
  accessToken: null,
  refreshToken: null,
  user: null,
  lastRole: null,

  bootstrap: async () => {
    const [accessToken, refreshToken, userJson, lastRole] = await Promise.all([
      storage.get(K_ACCESS),
      storage.get(K_REFRESH),
      storage.get(K_USER),
      storage.get(K_ROLE),
    ]);
    let user: AuthUser | null = null;
    try {
      user = userJson ? (JSON.parse(userJson) as AuthUser) : null;
    } catch {
      user = null;
    }
    set({ lastRole: lastRole === 'chef' || lastRole === 'buyer' ? lastRole : null });
    if (!refreshToken || !user) {
      set({ status: 'signedOut' });
      return;
    }
    set({ accessToken, refreshToken, user });
    // Автовход: обновляем пару токенов заранее (access живёт 15 минут)
    try {
      const t = await authApi.refresh(refreshToken);
      await get().setTokens(t);
      set({ status: 'signedIn' });
    } catch (e: any) {
      if (e?.status === 0) {
        // нет сети — пускаем с сохранённым токеном, запросы обновят его при 401
        set({ status: 'signedIn' });
      } else {
        await get().signOut();
      }
    }
  },

  signIn: async (r) => {
    await Promise.all([
      storage.set(K_ACCESS, r.accessToken),
      storage.set(K_REFRESH, r.refreshToken),
      storage.set(K_USER, JSON.stringify(r.user)),
      storage.set(K_ROLE, r.user.role),
    ]);
    set({ accessToken: r.accessToken, refreshToken: r.refreshToken, user: r.user, lastRole: r.user.role, status: 'signedIn' });
  },

  setTokens: async (t) => {
    set({ accessToken: t.accessToken, refreshToken: t.refreshToken });
    await Promise.all([storage.set(K_ACCESS, t.accessToken), storage.set(K_REFRESH, t.refreshToken)]);
  },

  setUserName: (name) => {
    const u = get().user;
    if (!u) return;
    const user = { ...u, name };
    set({ user });
    storage.set(K_USER, JSON.stringify(user));
  },

  signOut: async () => {
    const rt = get().refreshToken;
    if (rt) authApi.logout(rt).catch(() => undefined);
    disconnectSocket();
    resetPush();
    await Promise.all([storage.remove(K_ACCESS), storage.remove(K_REFRESH), storage.remove(K_USER)]);
    set({ status: 'signedOut', accessToken: null, refreshToken: null, user: null });
  },
}));

// Связываем API-клиент и сокет с хранилищем токенов
setTokenHandlers({
  getAccess: () => useAuth.getState().accessToken,
  getRefresh: () => useAuth.getState().refreshToken,
  onRefreshed: (t) => useAuth.getState().setTokens(t),
  onAuthFailed: () => {
    if (useAuth.getState().status === 'signedIn') useAuth.getState().signOut();
  },
});
setSocketTokenGetter(() => useAuth.getState().accessToken);
