export interface AccountActivityStatus {
  detail: '仅减' | '实盘' | '观察';
  label: 'BLOCK' | 'READY' | 'REDUCE';
  tone: 'blocked' | 'ready' | 'reduce-only';
}

interface AccountActivityStatusInput {
  canIncreaseRisk: boolean;
  canReduceRisk: boolean;
  executionMode: string;
  fetching: boolean;
  hasSnapshot: boolean;
}

export function accountActivityStatus({
  canIncreaseRisk,
  canReduceRisk,
  executionMode,
  fetching,
  hasSnapshot,
}: AccountActivityStatusInput): AccountActivityStatus | undefined {
  // Network activity is not an execution state. Hide the badge until the first
  // authoritative snapshot arrives, then retain that state during refreshes.
  if (fetching && !hasSnapshot) return undefined;

  return {
    detail:
      executionMode === 'TRADING'
        ? '实盘'
        : executionMode === 'REDUCE_ONLY'
          ? '仅减'
          : '观察',
    label: canIncreaseRisk ? 'READY' : canReduceRisk ? 'REDUCE' : 'BLOCK',
    tone: canIncreaseRisk
      ? 'ready'
      : canReduceRisk
        ? 'reduce-only'
        : 'blocked',
  };
}
