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
const REFRESH_LOCK_NAME = 'quantx-web-session-refresh';
const REFRESH_LEASE_KEY = 'quantx:web-session-refresh-lease';
const REFRESH_LEASE_MS = 5_000;
const SESSION_CHANNEL_NAME = 'quantx-web-session';
const sessionInstanceId =
  typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `session-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

type SessionBroadcastMessage =
  | { grant: WebSessionGrant; source: string; type: 'grant' }
  | { source: string; type: 'clear' };
type SessionBroadcastPayload =
  | { grant: WebSessionGrant; type: 'grant' }
  | { type: 'clear' };

interface RefreshLease {
  expiresAt: number;
  owner: string;
}

let snapshot: SessionSnapshot = { grant: null, version: 0 };
let refreshPromise: Promise<boolean> | null = null;
let developmentLoginPromise: Promise<WebSessionGrant> | null = null;
let refreshTimer: ReturnType<typeof setTimeout> | null = null;
let sessionChannel: BroadcastChannel | null = null;
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

function ensureSessionChannel(): BroadcastChannel | null {
  if (
    sessionChannel ||
    typeof window === 'undefined' ||
    typeof window.BroadcastChannel === 'undefined'
  ) {
    return sessionChannel;
  }
  sessionChannel = new window.BroadcastChannel(SESSION_CHANNEL_NAME);
  sessionChannel.addEventListener('message', event => {
    const message = event.data as SessionBroadcastMessage | undefined;
    if (!message || message.source === sessionInstanceId) return;
    if (message.type === 'grant' && message.grant) {
      setGrant(message.grant);
    } else if (message.type === 'clear') {
      setGrant(null);
    }
  });
  return sessionChannel;
}

function broadcastSession(message: SessionBroadcastPayload): void {
  ensureSessionChannel()?.postMessage({ ...message, source: sessionInstanceId });
}

function setGrant(grant: WebSessionGrant | null, broadcast = false): void {
  snapshot = { grant, version: snapshot.version + 1 };
  if (grant) scheduleRefresh(grant);
  else clearRefreshTimer();
  emit();
  if (broadcast) {
    if (grant) broadcastSession({ type: 'grant', grant });
    else broadcastSession({ type: 'clear' });
  }
}

function hasFreshGrant(): boolean {
  const expiresAt = snapshot.grant
    ? Date.parse(snapshot.grant.accessTokenExpiresAt)
    : 0;
  return Boolean(expiresAt && expiresAt > Date.now() + REFRESH_LEAD_TIME_MS);
}

function waitForSessionChange(version: number, timeoutMs: number): Promise<void> {
  if (snapshot.version !== version) return Promise.resolve();
  return new Promise(resolve => {
    const timer = setTimeout(finish, timeoutMs);
    const unsubscribe = subscribeSession(finish);
    function finish() {
      clearTimeout(timer);
      unsubscribe();
      resolve();
    }
  });
}

async function performRefresh(observedVersion: number): Promise<boolean> {
  try {
    const grant = await refreshWebSessionRequest();
    setGrant(grant, true);
    return true;
  } catch (error) {
    if (error instanceof AuthApiError && error.status === 401) {
      await waitForSessionChange(observedVersion, 100);
      if (snapshot.version !== observedVersion && hasFreshGrant()) return true;
      setGrant(null, true);
      return false;
    }
    throw error;
  }
}

function readRefreshLease(): RefreshLease | null {
  try {
    const value = window.localStorage.getItem(REFRESH_LEASE_KEY);
    if (!value) return null;
    const parsed = JSON.parse(value) as RefreshLease;
    return typeof parsed.owner === 'string' && Number.isFinite(parsed.expiresAt)
      ? parsed
      : null;
  } catch {
    return null;
  }
}

function tryAcquireRefreshLease(): RefreshLease | null {
  if (typeof window === 'undefined') return null;
  try {
    const current = readRefreshLease();
    if (current && current.expiresAt > Date.now()) return null;
    const lease = {
      owner: sessionInstanceId,
      expiresAt: Date.now() + REFRESH_LEASE_MS,
    };
    window.localStorage.setItem(REFRESH_LEASE_KEY, JSON.stringify(lease));
    return readRefreshLease()?.owner === sessionInstanceId ? lease : null;
  } catch {
    return null;
  }
}

function releaseRefreshLease(): void {
  try {
    if (readRefreshLease()?.owner === sessionInstanceId) {
      window.localStorage.removeItem(REFRESH_LEASE_KEY);
    }
  } catch {
    // Storage may be unavailable in hardened browser contexts.
  }
}

async function refreshWithLease(): Promise<boolean> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const observedVersion = snapshot.version;
    const lease = tryAcquireRefreshLease();
    if (lease) {
      try {
        return await performRefresh(observedVersion);
      } finally {
        releaseRefreshLease();
      }
    }
    const activeLease = readRefreshLease();
    if (!activeLease) return performRefresh(observedVersion);
    const waitMs = Math.max(
      50,
      Math.min(REFRESH_LEASE_MS, activeLease.expiresAt - Date.now() + 50)
    );
    await waitForSessionChange(observedVersion, waitMs);
    if (snapshot.version !== observedVersion && hasFreshGrant()) return true;
  }
  return performRefresh(snapshot.version);
}

async function refreshAcrossTabs(): Promise<boolean> {
  ensureSessionChannel();
  const lockManager =
    typeof navigator === 'undefined' ? undefined : navigator.locks;
  if (!lockManager) return refreshWithLease();
  const observedVersion = snapshot.version;
  return lockManager.request(REFRESH_LOCK_NAME, async () => {
    if (snapshot.version !== observedVersion && hasFreshGrant()) return true;
    return performRefresh(observedVersion);
  });
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
  setGrant(grant, true);
  return grant;
}

export async function loginDevelopmentWebSession(): Promise<WebSessionGrant> {
  if (developmentLoginPromise) return developmentLoginPromise;
  developmentLoginPromise = (async () => {
    try {
      const grant = await createDevelopmentWebSession();
      setGrant(grant, true);
      return grant;
    } finally {
      developmentLoginPromise = null;
    }
  })();
  return developmentLoginPromise;
}

export async function refreshWebSession(): Promise<boolean> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = refreshAcrossTabs().finally(() => {
    refreshPromise = null;
  });
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
  setGrant(null, true);
}

export function clearWebSession(): void {
  setGrant(null);
}

ensureSessionChannel();
