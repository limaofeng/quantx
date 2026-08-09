import { z } from 'zod';

import type { LoginCredentials, WebSessionGrant } from './types';

const sessionUserSchema = z.object({
  id: z.string(),
  username: z.string(),
  displayName: z.string(),
  permissions: z.array(z.string()),
  authorizedAccountIds: z.array(z.string()),
});

const webSessionGrantSchema = z.object({
  accessToken: z.string().min(1),
  accessTokenExpiresAt: z.string().min(1),
  tokenType: z.literal('Bearer'),
  deviceSessionId: z.string().min(1),
  user: sessionUserSchema,
});

interface ErrorDetail {
  code?: string;
  message?: string;
  requestId?: string;
  retryable?: boolean;
}

export class AuthApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly retryable: boolean;

  constructor(status: number, detail: ErrorDetail = {}) {
    super(detail.message || '会话请求失败，请稍后重试');
    this.name = 'AuthApiError';
    this.status = status;
    this.code = detail.code || 'AUTH_REQUEST_FAILED';
    this.requestId = detail.requestId;
    this.retryable = Boolean(detail.retryable);
  }
}

async function readError(response: Response): Promise<AuthApiError> {
  try {
    const payload = (await response.json()) as { detail?: ErrorDetail };
    return new AuthApiError(response.status, payload.detail);
  } catch {
    return new AuthApiError(response.status);
  }
}

async function readGrant(response: Response): Promise<WebSessionGrant> {
  if (!response.ok) throw await readError(response);
  return webSessionGrantSchema.parse(await response.json());
}

export async function createWebSession(
  credentials: LoginCredentials
): Promise<WebSessionGrant> {
  const response = await fetch('/auth/web/session', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(credentials),
  });
  return readGrant(response);
}

export async function createDevelopmentWebSession(): Promise<WebSessionGrant> {
  const response = await fetch('/auth/web/session/development', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  return readGrant(response);
}

export async function refreshWebSessionRequest(): Promise<WebSessionGrant> {
  const response = await fetch('/auth/web/session/refresh', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  return readGrant(response);
}

export async function deleteWebSessionRequest(): Promise<void> {
  const response = await fetch('/auth/web/session', {
    method: 'DELETE',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok && response.status !== 401) throw await readError(response);
}
