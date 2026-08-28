const DELETABLE_REPLAY_STATUSES = new Set([
  'COMPLETED',
  'ERROR',
  'FAILED',
  'CANCELLED',
  'STOPPED',
]);

export function canDeleteReplay(status: string | null | undefined) {
  return DELETABLE_REPLAY_STATUSES.has(String(status || '').toUpperCase());
}

export function replayStatusAfterDelete(
  historyRunIds: readonly string[],
  deletedRunId: string,
  activeRunId: string
) {
  if (activeRunId !== deletedRunId) return activeRunId;
  return historyRunIds.find(runId => runId !== deletedRunId) || '';
}
