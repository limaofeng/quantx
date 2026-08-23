import type { GraphqlWsStatus } from '@/core/graphql/ws-status';

/**
 * The subscription is only an invalidation hint. A healthy WebSocket does not
 * prove that Redis delivered every T-trade notification, so a visible realtime
 * workspace periodically asks the HTTP query for the server-owned snapshot.
 */
export const T_TRADE_SERVER_TRUTH_AUDIT_INTERVAL_MS = 30_000;

const MAX_SEEN_SUBSCRIPTION_VERSIONS = 64;

export function createTTradeServerTruthRefreshPolicy(
  options: { auditIntervalMs?: number } = {}
) {
  const auditIntervalMs = Math.max(
    1,
    options.auditIntervalMs ?? T_TRADE_SERVER_TRUTH_AUDIT_INTERVAL_MS
  );
  const seenVersionsByAccount = new Map<string, string[]>();
  const lastSubscriptionErrorByAccount = new Map<string, string>();
  const lastNetworkRequestAtByAccount = new Map<string, number>();

  const hasAccount = (accountId: string) => Boolean(accountId.trim());

  const shouldRefreshForSubscriptionVersion = (
    accountId: string,
    version: string | null | undefined
  ) => {
    if (!hasAccount(accountId) || !version) return false;
    const versions = seenVersionsByAccount.get(accountId) || [];
    if (versions.includes(version)) return false;
    versions.push(version);
    if (versions.length > MAX_SEEN_SUBSCRIPTION_VERSIONS) versions.shift();
    seenVersionsByAccount.set(accountId, versions);
    return true;
  };

  const shouldRefreshForSubscriptionError = (
    accountId: string,
    errorKey: string | null | undefined
  ) => {
    if (!hasAccount(accountId) || !errorKey) return false;
    if (lastSubscriptionErrorByAccount.get(accountId) === errorKey) {
      return false;
    }
    lastSubscriptionErrorByAccount.set(accountId, errorKey);
    return true;
  };

  const clearSubscriptionError = (accountId: string) => {
    lastSubscriptionErrorByAccount.delete(accountId);
  };

  const noteNetworkRequest = (accountId: string, now: number) => {
    if (!hasAccount(accountId) || !Number.isFinite(now)) return;
    lastNetworkRequestAtByAccount.set(accountId, now);
  };

  const shouldRunAudit = (
    accountId: string,
    wsStatus: GraphqlWsStatus,
    now: number
  ) => {
    if (
      !hasAccount(accountId) ||
      wsStatus !== 'connected' ||
      !Number.isFinite(now)
    ) {
      return false;
    }
    const lastNetworkRequestAt = lastNetworkRequestAtByAccount.get(accountId);
    if (
      lastNetworkRequestAt != null &&
      now - lastNetworkRequestAt < auditIntervalMs
    ) {
      return false;
    }
    // Reserve the interval before dispatching. This prevents repeated timer
    // callbacks from queuing duplicate HTTP requests while one is in flight.
    lastNetworkRequestAtByAccount.set(accountId, now);
    return true;
  };

  const resetForReconnect = (accountId: string) => {
    seenVersionsByAccount.delete(accountId);
    lastSubscriptionErrorByAccount.delete(accountId);
    lastNetworkRequestAtByAccount.delete(accountId);
  };

  return {
    clearSubscriptionError,
    noteNetworkRequest,
    resetForReconnect,
    shouldRefreshForSubscriptionError,
    shouldRefreshForSubscriptionVersion,
    shouldRunAudit,
  };
}
