import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  bootstrapWebSession,
  clearWebSession,
  getAccessToken,
  loginDevelopmentWebSession,
  loginWebSession,
  logoutWebSession,
  refreshWebSession,
} from '@/core/auth/session-store';
import type { WebSessionGrant } from '@/core/auth/types';

function grant(token: string): WebSessionGrant {
  return {
    accessToken: token,
    accessTokenExpiresAt: new Date(Date.now() + 30 * 60_000).toISOString(),
    tokenType: 'Bearer',
    deviceSessionId: 'web-session-1',
    user: {
      id: 'user-1',
      username: 'owner',
      displayName: 'QuantX Owner',
      permissions: ['portfolio:read', 'portfolio:write'],
      authorizedAccountIds: ['ACCOUNT-1'],
    },
  };
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('web session store', () => {
  beforeEach(() => {
    clearWebSession();
    vi.mocked(window.localStorage.setItem).mockClear();
    vi.mocked(window.sessionStorage.setItem).mockClear();
  });

  afterEach(() => {
    clearWebSession();
    vi.unstubAllGlobals();
  });

  it('keeps the access token in memory only after login', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(grant('access-1')));
    vi.stubGlobal('fetch', fetchMock);

    await loginWebSession({ username: 'owner', password: 'local-password' });

    expect(getAccessToken()).toBe('access-1');
    expect(window.localStorage.setItem).not.toHaveBeenCalled();
    expect(window.sessionStorage.setItem).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      '/auth/web/session',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' })
    );
  });

  it('deduplicates concurrent refresh requests', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(grant('access-2')));
    vi.stubGlobal('fetch', fetchMock);

    const results = await Promise.all([
      refreshWebSession(),
      refreshWebSession(),
      refreshWebSession(),
    ]);

    expect(results).toEqual([true, true, true]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBe('access-2');
  });

  it('creates a database-backed development session after refresh misses', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse(
          { detail: { code: 'UNAUTHENTICATED', message: 'missing' } },
          401
        )
      )
      .mockResolvedValueOnce(jsonResponse(grant('development-access')));
    vi.stubGlobal('fetch', fetchMock);

    await expect(bootstrapWebSession(true)).resolves.toBe(true);

    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/auth/web/session/development',
      expect.objectContaining({ method: 'POST', credentials: 'same-origin' })
    );
    expect(getAccessToken()).toBe('development-access');
  });

  it('deduplicates concurrent development login requests', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(grant('development-access-2')));
    vi.stubGlobal('fetch', fetchMock);

    const sessions = await Promise.all([
      loginDevelopmentWebSession(),
      loginDevelopmentWebSession(),
    ]);

    expect(sessions).toHaveLength(2);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBe('development-access-2');
  });

  it('clears the in-memory session when refresh is rejected', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(grant('access-3')))
      .mockResolvedValueOnce(
        jsonResponse(
          { detail: { code: 'UNAUTHENTICATED', message: 'expired' } },
          401
        )
      );
    vi.stubGlobal('fetch', fetchMock);
    await loginWebSession({ username: 'owner', password: 'local-password' });

    await expect(refreshWebSession()).resolves.toBe(false);
    expect(getAccessToken()).toBeNull();
  });

  it('clears memory after server-side logout succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(grant('access-4')))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    await loginWebSession({ username: 'owner', password: 'local-password' });

    await logoutWebSession();

    expect(getAccessToken()).toBeNull();
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/auth/web/session',
      expect.objectContaining({ method: 'DELETE', credentials: 'same-origin' })
    );
  });

  it('keeps the session when server-side logout cannot be confirmed', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(grant('access-5')))
      .mockRejectedValueOnce(new TypeError('network unavailable'));
    vi.stubGlobal('fetch', fetchMock);
    await loginWebSession({ username: 'owner', password: 'local-password' });

    await expect(logoutWebSession()).rejects.toThrow('network unavailable');
    expect(getAccessToken()).toBe('access-5');
  });
});
