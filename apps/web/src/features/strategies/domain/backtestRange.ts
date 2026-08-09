export type BacktestBoundary = 'start' | 'end';

function formatLocalDate(date: Date) {
  if (Number.isNaN(date.getTime())) {
    throw new Error('回测日期无效');
  }
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * Serialize a date-only picker value without converting it through UTC.
 *
 * Backtests are defined by Shanghai trading dates. Using Date#toISOString here
 * would move local midnight to the previous UTC date in UTC+8 browsers.
 */
export function toBacktestBoundaryIso(date: Date, boundary: BacktestBoundary) {
  const day = formatLocalDate(date);
  return boundary === 'start' ? `${day}T00:00:00.000` : `${day}T23:59:59.999`;
}
