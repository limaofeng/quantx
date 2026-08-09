import { createContext, useContext } from 'react';

import type { LoginCredentials, SessionUser } from './types';

export type BootstrapStatus = 'initializing' | 'ready' | 'error';

export interface AuthContextValue {
  bootstrapStatus: BootstrapStatus;
  bootstrapError: Error | null;
  isAuthenticated: boolean;
  user: SessionUser | null;
  login: (credentials: LoginCredentials) => Promise<void>;
  logout: () => Promise<void>;
  retryBootstrap: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used within AuthProvider');
  return value;
}
