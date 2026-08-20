import type { GraphqlWsStatus } from '@/core/graphql/ws-status';

export const ACTIVE_REPLAY_POLL_MS = 2_500;
export const IDLE_REPLAY_POLL_MS = 30_000;

export function stableValueByKey<T>(
  cache: Map<string, T>,
  key: string,
  value: T | undefined,
  valueKey: string | undefined
) {
  if (!key) return undefined;
  if (value !== undefined && valueKey === key) cache.set(key, value);
  return cache.get(key);
}

export function replayFallbackPollInterval(
  wsStatus: GraphqlWsStatus,
  hasActiveReplay: boolean
) {
  if (wsStatus === 'connected') return null;
  return hasActiveReplay ? ACTIVE_REPLAY_POLL_MS : IDLE_REPLAY_POLL_MS;
}

export function isNewerReplayRevision(
  previousRevision: string | undefined,
  nextRevision: string
) {
  if (!/^\d+$/.test(nextRevision)) return false;
  if (!previousRevision || !/^\d+$/.test(previousRevision)) return true;
  return BigInt(nextRevision) > BigInt(previousRevision);
}

export function replayNoticeRefreshTargets(
  kind: string,
  noticeRunId: string,
  activeRunId: string
) {
  return {
    history: true,
    replay: Boolean(activeRunId && noticeRunId === activeRunId),
    cycles: Boolean(
      activeRunId && noticeRunId === activeRunId && kind === 'RESULT_READY'
    ),
  };
}
