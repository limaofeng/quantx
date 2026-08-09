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
