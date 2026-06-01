import type { Time } from 'lightweight-charts';

import type { TradingRange } from '../types';

/**
 * 获取 A 股交易时间范围 (09:30 - 15:00 Beijing Time)
 * 映射到 UTC 为 01:30 - 07:00
 */
export const getTradingRange = (dateStr: string): TradingRange => {
  const d = new Date(dateStr);
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth();
  const day = d.getUTCDate();

  // 09:30 Beijing = 01:30 UTC
  // 15:00 Beijing = 07:00 UTC
  const start = Date.UTC(y, m, day, 1, 30, 0) / 1000;
  const end = Date.UTC(y, m, day, 7, 0, 0) / 1000;

  return {
    from: start as Time,
    to: end as Time,
  };
};

export const formatTime = (time: number) => {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(new Date(time * 1000));
};

export const formatDate = (time: number) => {
  const date = new Date(time * 1000);
  const m = (date.getMonth() + 1).toString().padStart(2, '0');
  const d = date.getDate().toString().padStart(2, '0');
  return `${m}-${d}`;
};

const formatDateKey = (date: Date) => {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
};

export const getTickDateRange = (
  tradingDays: string[] = [],
  mode: '1d' | '5d' = '1d'
) => {
  if (tradingDays.length === 0) {
    // Fallback if no trading days loaded yet: return today/empty
    const fallback = formatDateKey(new Date());
    return {
      startTime: `${fallback} 00:00:00`,
      endTime: `${fallback} 23:59:59`,
    };
  }

  const now = new Date();
  const hours = now.getHours();
  const todayStr = formatDateKey(now);

  // Determine "current" trading day anchor
  let anchorDateStr = todayStr;

  // If before 9:00 AM, market not open, use last available trading day
  if (hours < 9) {
    // If today is in list, we want previous one; if not, we want last one in list < today
    // Since list is sorted (ascending from API usually), we find the last one < today or <= yesterday
    // But simplified: just take last known tradingDay effectively?
    // Let's assume tradingDays list goes up to today or tomorrow.

    // Filter days strictly before today
    const pastDays = tradingDays.filter(d => d < todayStr);
    anchorDateStr =
      pastDays.length > 0 ? pastDays[pastDays.length - 1] : tradingDays[0];
  } else {
    // After 9:00 AM.
    // If today is a trading day, use today.
    // If today is NOT a trading day (weekend/holiday), use last available trading day.
    if (tradingDays.includes(todayStr)) {
      anchorDateStr = todayStr;
    } else {
      const pastDays = tradingDays.filter(d => d < todayStr);
      anchorDateStr =
        pastDays.length > 0 ? pastDays[pastDays.length - 1] : tradingDays[0];
    }
  }

  // Calculate Start Time
  let startTimeStr = anchorDateStr;
  let endTimeStr = anchorDateStr;

  if (mode === '5d') {
    // Find index of anchor
    const anchorIndex = tradingDays.indexOf(anchorDateStr);
    if (anchorIndex !== -1) {
      // 5 days inclusive: anchor, -1, -2, -3, -4
      const startIndex = Math.max(0, anchorIndex - 4);
      startTimeStr = tradingDays[startIndex];
    } else {
      // Should not happen if logic above is robust, but fallback
      startTimeStr = anchorDateStr;
    }
    // End time for 5d is usually "now" (which covers anchorDate)
    // If anchor is today (trading), end is today.
    // If anchor is yesterday (today is weekend), end is today (ticks won't exist but ok) or anchor 23:59
    // Safest is to set end to Now or End of Anchor Day.
    // Let's us End of Anchor Day to be precise about "5 Trading Days" but if market is live, we need up to now.
    // Since anchorDate logic handles "before 9:00", if it's live trading session, anchor IS today.
    // If it's weekend, anchor IS Friday.
    // Setting endTime to anchorDate is safe.
    endTimeStr = anchorDateStr;
  }

  // For '1d', startTime == endTime == anchorDateStr

  return {
    startTime: `${startTimeStr} 00:00:00`,
    endTime: `${endTimeStr} 23:59:59`,
  };
};
