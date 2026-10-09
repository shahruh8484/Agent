import * as SecureStore from 'expo-secure-store';
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';
import { Platform } from 'react-native';

import { api, setToken } from './api';
import { registerForPush } from './push';
import type { User } from './types';

const TOKEN_KEY = 'servio_token';

// SecureStore is native-only; on web (dev preview) the session lives in memory.
const storage = {
  get: () => (Platform.OS === 'web' ? Promise.resolve(null) : SecureStore.getItemAsync(TOKEN_KEY)),
  set: (v: string) => (Platform.OS === 'web' ? Promise.resolve() : SecureStore.setItemAsync(TOKEN_KEY, v)),
  clear: () => (Platform.OS === 'web' ? Promise.resolve() : SecureStore.deleteItemAsync(TOKEN_KEY)),
};

interface AuthState {
  user: User | null;
  loading: boolean;
  signIn: (token: string, user: User) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
  setUser: (user: User) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      const token = await storage.get();
      if (token) {
        setToken(token);
        try {
          setUser(await api<User>('/me'));
          registerForPush();
        } catch {
          setToken(null);
          await storage.clear();
        }
      }
      setLoading(false);
    })();
  }, []);

  const signIn = useCallback(async (token: string, u: User) => {
    setToken(token);
    await storage.set(token);
    setUser(u);
    registerForPush();
  }, []);

  const signOut = useCallback(async () => {
    await api('/auth/logout', { body: {} }).catch(() => undefined);
    setToken(null);
    await storage.clear();
    setUser(null);
  }, []);

  const refresh = useCallback(async () => {
    setUser(await api<User>('/me'));
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, signIn, signOut, refresh, setUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth outside AuthProvider');
  return ctx;
}
