export type WebSocketCloseDetails = {
  code?: number;
  reason?: string;
};

const EXPECTED_CLOSE_CODES = new Set([1000, 4205]);

export function webSocketCloseDetails(event: unknown): WebSocketCloseDetails {
  if (typeof event !== 'object' || event === null) return {};
  const candidate = event as Record<string, unknown>;
  return {
    code: typeof candidate.code === 'number' ? candidate.code : undefined,
    reason: typeof candidate.reason === 'string' ? candidate.reason : undefined,
  };
}

export function isNormalWebSocketClose(event: unknown): boolean {
  const code = webSocketCloseDetails(event).code;
  return code !== undefined && EXPECTED_CLOSE_CODES.has(code);
}
