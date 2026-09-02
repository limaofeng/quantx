function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function createPendingIdempotencyKeys() {
  const pending = new Map<string, string>();
  return {
    get(scope: string) {
      const existing = pending.get(scope);
      if (existing) return existing;
      const key = newIdempotencyKey();
      pending.set(scope, key);
      return key;
    },
    clear(scope: string) {
      pending.delete(scope);
    },
  };
}
