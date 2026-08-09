import {
  AuthApiError,
  createDevelopmentWebSession,
  createWebSession,
  deleteWebSessionRequest,
  refreshWebSessionRequest,
} from './api';
import type {
  LoginCredentials,
  SessionSnapshot,
  WebSessionGrant,
} from './types';

const REFRESH_LEAD_TIME_MS = 60_000;
const REFRESH_RETRY_DELAY_MS = 15_000;

let snapshot: SessionSnapshot = { grant: null, version: 0 };
let refreshPromise: Promise<boolean> | null = null;
let developmentLoginPromise: Promise<WebSessionGrant> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach(listener => listener());
}

function clearRefreshTimer(): void {
  if (refreshTimer !== null) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
}

function scheduleRefresh(grant: WebSessionGrant): void {
  clearRefreshTimer();
  const expiresAt = Date.parse(grant.accessTokenExpiresAt);
  if (!Number.isFinite(expiresAt)) return;
  const delay = Math.max(0, expiresAt - Date.now() - REFRESH_LEAD_TIME_MS);
  refreshTimer = setTimeout(() => {
    void refreshWebSession().catch(() => {
      const currentExpiry = snapshot.grant
        ? Date.parse(snapshot.grant.accessTokenExpiresAt)
        : 0;
      if (currentExpiry > Date.now()) {
        refreshTimer = setTimeout(() => {
          void refreshWebSession().catch(() => undefined);
        }, REFRESH_RETRY_DELAY_MS);
      }
    });
  }, delay);
}

function setGrant(grant: WebSessionGrant | null): void {
  snapshot = { grant, version: snapshot.version + 1 };
  if (grant) scheduleRefresh(grant);
  else clearRefreshTimer();
  emit();
}

export function getSessionSnapshot(): SessionSnapshot {
  return snapshot;
}

export function subscribeSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function getAccessToken(): string | null {
  return snapshot.grant?.accessToken || null;
}

export function willAccessTokenExpireSoon(): boolean {
  const expiresAt = snapshot.grant
    ? Date.parse(snapshot.grant.accessTokenExpiresAt)
    : 0;
  return !expiresAt || expiresAt <= Date.now() + REFRESH_LEAD_TIME_MS;
}

export async function loginWebSession(
  credentials: LoginCredentials
): Promise<WebSessionGrant> {
  const grant = await createWebSession(credentials);
  setGrant(grant);
  return grant;
}

export async function loginDevelopmentWebSession(): Promise<WebSessionGrant> {
  if (developmentLoginPromise) return developmentLoginPromise;
  developmentLoginPromise = (async () => {
    try {
      const grant = await createDevelopmentWebSession();
      setGrant(grant);
      return grant;
    } finally {
      developmentLoginPromise = null;
    }
  })();
  return developmentLoginPromise;
}

export async function refreshWebSession(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    try {
      const grant = await refreshWebSessionRequest();
      setGrant(grant);
      return true;
    } catch (error) {
      if (error instanceof AuthApiError && error.status === 401) {
        setGrant(null);
        return false;
      }
      throw error;
    } finally {
      refreshPromise = null;
    }
  })();
  return refreshPromise;
}

export async function bootstrapWebSession(
  allowDevelopmentAutoLogin: boolean
): Promise<boolean> {
  const restored = await refreshWebSession();
  if (restored || !allowDevelopmentAutoLogin) return restored;
  await loginDevelopmentWebSession();
  return true;
}

export async function logoutWebSession(): Promise<void> {
  await deleteWebSessionRequest();
  setGrant(null);
}

export function clearWebSession(): void {
  setGrant(null);
}
