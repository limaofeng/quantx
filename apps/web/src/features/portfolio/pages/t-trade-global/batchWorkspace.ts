import type { TTradePositionBatch } from './TTradePositionsView';

const CURRENT_ONLY_EXCEPTION_STATUSES = new Set([
  'EXIT_REJECTED',
  'RECONCILE_REQUIRED',
  'KILL_SWITCHED',
]);

const HISTORICAL_STATUSES = new Set([
  'CLOSED',
  'ENTRY_EXPIRED',
  'ENTRY_REJECTED',
]);

export function isCurrentTTradeBatch(batch: TTradePositionBatch) {
  if (batch.activeVolume > 0) return true;
  const status = String(batch.status || '').toUpperCase();
  if (CURRENT_ONLY_EXCEPTION_STATUSES.has(status)) return true;
  return !HISTORICAL_STATUSES.has(status);
}
