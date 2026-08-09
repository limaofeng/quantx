export interface SessionUser {
  id: string;
  username: string;
  displayName: string;
  permissions: string[];
  authorizedAccountIds: string[];
}

export interface WebSessionGrant {
  accessToken: string;
  accessTokenExpiresAt: string;
  tokenType: 'Bearer';
  deviceSessionId: string;
  user: SessionUser;
}

export interface LoginCredentials {
  username: string;
  password: string;
  deviceName?: string;
}

export interface SessionSnapshot {
  grant: WebSessionGrant | null;
  version: number;
}
