export type WebSocketCloseDetails = {
  code?: number;
  reason?: string;
};

export function webSocketCloseDetails(event: unknown): WebSocketCloseDetails {
  if (typeof event !== 'object' || event === null) return {};
  const candidate = event as Record<string, unknown>;
  return {
    code: typeof candidate.code === 'number' ? candidate.code : undefined,
    reason: typeof candidate.reason === 'string' ? candidate.reason : undefined,
  };
}

export function isNormalWebSocketClose(event: unknown): boolean {
  return webSocketCloseDetails(event).code === 1000;
}
