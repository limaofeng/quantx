import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { authConfig } from '@/shared/utils/env';

import {
  AuthContext,
  type AuthContextValue,
  type BootstrapStatus,
} from './auth-context';
import {
  bootstrapWebSession,
  getSessionSnapshot,
  loginWebSession,
  logoutWebSession,
  subscribeSession,
} from './session-store';
import type { LoginCredentials, SessionSnapshot } from './types';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionSnapshot>(getSessionSnapshot);
  const [bootstrapStatus, setBootstrapStatus] =
    useState<BootstrapStatus>('initializing');
  const [bootstrapError, setBootstrapError] = useState<Error | null>(null);

  useEffect(
    () =>
      subscribeSession(() => {
        setSession(getSessionSnapshot());
      }),
    []
  );

  const retryBootstrap = useCallback(async () => {
    setBootstrapStatus('initializing');
    setBootstrapError(null);
    try {
      await bootstrapWebSession(authConfig.developmentAutoLogin);
      setBootstrapStatus('ready');
    } catch (error) {
      setBootstrapError(
        error instanceof Error ? error : new Error('无法恢复 Web 会话')
      );
      setBootstrapStatus('error');
    }
  }, []);

  useEffect(() => {
    void retryBootstrap();
  }, [retryBootstrap]);

  const login = useCallback(async (credentials: LoginCredentials) => {
    await loginWebSession(credentials);
    setBootstrapStatus('ready');
    setBootstrapError(null);
  }, []);

  const logout = useCallback(async () => {
    await logoutWebSession();
    setBootstrapStatus('ready');
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      bootstrapStatus,
      bootstrapError,
      isAuthenticated: Boolean(session.grant),
      user: session.grant?.user || null,
      login,
      logout,
      retryBootstrap,
    }),
    [bootstrapError, bootstrapStatus, login, logout, retryBootstrap, session]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
