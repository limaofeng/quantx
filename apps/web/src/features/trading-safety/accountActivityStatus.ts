export interface AccountActivityStatus {
  detail: '仅减' | '实盘' | '观察' | '待确认';
  label: 'BLOCK' | 'READY' | 'REDUCE' | 'UNKNOWN';
  tone: 'blocked' | 'ready' | 'reduce-only' | 'checking';
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
  hasSnapshot,
}: AccountActivityStatusInput): AccountActivityStatus {
  // Missing evidence is unknown, including while retrying a failed request.
  // Keep the badge mounted so polling never makes the activity rail flicker.
  if (!hasSnapshot)
    return { detail: '待确认', label: 'UNKNOWN', tone: 'checking' };

  return {
    detail:
      executionMode === 'TRADING'
        ? '实盘'
        : executionMode === 'REDUCE_ONLY'
          ? '仅减'
          : '观察',
    label: canIncreaseRisk ? 'READY' : canReduceRisk ? 'REDUCE' : 'BLOCK',
    tone: canIncreaseRisk ? 'ready' : canReduceRisk ? 'reduce-only' : 'blocked',
  };
}
