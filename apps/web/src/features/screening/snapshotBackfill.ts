export interface SnapshotBackfillRun {
  created?: string | null;
  expectedStartTime?: string | null;
  id: string;
  startedAt?: string | null;
  state?: string | null;
}

const TERMINAL_RUN_STATES = new Set([
  'COMPLETED',
  'FAILED',
  'CRASHED',
  'CANCELLED',
]);

const START_TIME_TOLERANCE_MS = 5_000;

function timestamp(value?: string | null): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function runTimestamp(run: SnapshotBackfillRun): number {
  return (
    timestamp(run.startedAt) ??
    timestamp(run.expectedStartTime) ??
    timestamp(run.created) ??
    0
  );
}

export function findActiveSnapshotBackfillRun(
  runs: SnapshotBackfillRun[],
  nowMs = Date.now()
): SnapshotBackfillRun | null {
  return (
    runs
      .filter(run => {
        const state = (run.state ?? '').toUpperCase();
        if (!state || TERMINAL_RUN_STATES.has(state)) return false;
        if (timestamp(run.startedAt) !== null) return true;
        const expectedStart = timestamp(run.expectedStartTime);
        return (
          expectedStart === null ||
          expectedStart <= nowMs + START_TIME_TOLERANCE_MS
        );
      })
      .sort((left, right) => runTimestamp(right) - runTimestamp(left))[0] ?? null
  );
}

export function buildSnapshotBackfillParameters(
  missingSnapshotDates: string[]
): Record<string, unknown> | null {
  const dates = Array.from(new Set(missingSnapshotDates)).sort();
  if (dates.length === 0) return null;
  const compact = (value: string) => value.replace(/-/g, '');
  return {
    sectors: ['沪深A股', '沪深ETF'],
    start_time: compact(dates[0]),
    end_time: compact(dates[dates.length - 1]),
    periods: ['1d'],
    skip_download: false,
    compute_daily_signals: true,
  };
}
