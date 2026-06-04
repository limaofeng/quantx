import type { Time } from 'lightweight-charts';

import type { TradingRange } from '../types';

const TIMEZONE_SUFFIX_RE = /(?:z|[+-]\d{2}:?\d{2})$/i;
const DATE_TIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;
const SHANGHAI_TIME_ZONE = 'Asia/Shanghai';
const SHANGHAI_OFFSET = '+08:00';
const CALL_AUCTION_START_MINUTES = 9 * 60 + 15;
const CALL_AUCTION_END_MINUTES = 9 * 60 + 25;
const MARKET_OPEN_MINUTES = 9 * 60 + 30;

interface TradingSessionOptions {
  includeCallAuction?: boolean;
  now?: Date;
}

const shanghaiDateTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: SHANGHAI_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

const getShanghaiDateParts = (date: Date) => {
  let year = '';
  let month = '';
  let day = '';
  let hour = '0';
  let minute = '0';

  shanghaiDateTimeFormatter.formatToParts(date).forEach(part => {
    if (part.type === 'year') year = part.value;
    if (part.type === 'month') month = part.value;
    if (part.type === 'day') day = part.value;
    if (part.type === 'hour') hour = part.value;
    if (part.type === 'minute') minute = part.value;
  });

  const parsedHour = Number(hour === '24' ? '0' : hour);

  return {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    dateKey: `${year}-${month}-${day}`,
    minutes: parsedHour * 60 + Number(minute),
  };
};

export const getShanghaiDateKey = (date: Date = new Date()) => {
  return getShanghaiDateParts(date).dateKey;
};

export const addShanghaiDays = (date: Date, days: number) => {
  return new Date(date.getTime() + days * 24 * 60 * 60 * 1000);
};

const shouldShowCallAuction = (dateStr: string, now = new Date()) => {
  const chartDate = parseMarketDate(dateStr) || now;
  const chartParts = getShanghaiDateParts(chartDate);
  const nowParts = getShanghaiDateParts(now);

  return (
    chartParts.dateKey === nowParts.dateKey &&
    nowParts.minutes < MARKET_OPEN_MINUTES
  );
};

const shouldIncludeCallAuction = (
  dateStr: string,
  options: TradingSessionOptions = {}
) =>
  options.includeCallAuction ??
  shouldShowCallAuction(dateStr, options.now || new Date());

export const parseMarketDate = (
  value: string | number | Date | null | undefined
): Date | null => {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return value;
  if (typeof value === 'number') return new Date(value);

  const trimmed = value.trim();
  if (!trimmed) return null;

  const normalized = trimmed.includes(' ')
    ? trimmed.replace(' ', 'T')
    : trimmed;
  const withTimezone =
    DATE_TIME_RE.test(normalized) && !TIMEZONE_SUFFIX_RE.test(normalized)
      ? `${normalized}${SHANGHAI_OFFSET}`
      : normalized;

  const date = new Date(withTimezone);
  return Number.isFinite(date.getTime()) ? date : null;
};

export const toChartTimestamp = (
  value: string | number | Date | null | undefined
): Time | null => {
  const date = parseMarketDate(value);
  if (!date) return null;
  return Math.floor(date.getTime() / 1000) as Time;
};

export const isCallAuctionTimestamp = (
  value: string | number | Date | null | undefined
) => {
  const date = parseMarketDate(value);
  if (!date) return false;
  const { minutes } = getShanghaiDateParts(date);
  return (
    minutes >= CALL_AUCTION_START_MINUTES &&
    minutes <= CALL_AUCTION_END_MINUTES
  );
};

export const getCallAuctionDateRange = (
  value: string | number | Date | null | undefined
) => {
  const d = parseMarketDate(value) || new Date();
  const { dateKey } = getShanghaiDateParts(d);

  return {
    startTime: `${dateKey} 09:15:00`,
    endTime: `${dateKey} 09:25:59`,
  };
};

/**
 * 获取 A 股分时展示范围。开盘前默认展示集合竞价；传入 includeCallAuction
 * 时可用 tick 数据把竞价段保留在分时左侧。
 */
export const getTradingRange = (
  dateStr: string,
  options: TradingSessionOptions = {}
): TradingRange => {
  const d = parseMarketDate(dateStr) || new Date();
  const { year, month, day } = getShanghaiDateParts(d);
  const includeCallAuction = shouldIncludeCallAuction(dateStr, options);

  // 09:30 Beijing = 01:30 UTC. 09:15 Beijing = 01:15 UTC before open.
  // 15:00 Beijing = 07:00 UTC
  const start =
    Date.UTC(year, month - 1, day, 1, includeCallAuction ? 15 : 30, 0) / 1000;
  const end = Date.UTC(year, month - 1, day, 7, 0, 0) / 1000;

  return {
    from: start as Time,
    to: end as Time,
  };
};

export const getTradingSessionAnchors = (
  dateStr: string,
  options: TradingSessionOptions = {}
): Time[] => {
  const d = parseMarketDate(dateStr) || new Date();
  const { year, month, day } = getShanghaiDateParts(d);
  const includeCallAuction = shouldIncludeCallAuction(dateStr, options);

  const anchors = [
    Date.UTC(year, month - 1, day, 1, 30, 0) / 1000,
    Date.UTC(year, month - 1, day, 3, 30, 0) / 1000,
    Date.UTC(year, month - 1, day, 5, 0, 0) / 1000,
    Date.UTC(year, month - 1, day, 7, 0, 0) / 1000,
  ];

  if (includeCallAuction) {
    anchors.unshift(
      Date.UTC(year, month - 1, day, 1, 15, 0) / 1000,
      Date.UTC(year, month - 1, day, 1, 25, 0) / 1000
    );
  }

  return anchors as Time[];
};

export const getTradingSessionMinutes = (
  dateStr: string,
  options: TradingSessionOptions = {}
): Time[] => {
  const d = parseMarketDate(dateStr) || new Date();
  const { year, month, day } = getShanghaiDateParts(d);
  const minutes: Time[] = [];

  const addRange = (
    startHour: number,
    startMinute: number,
    endHour: number,
    endMinute: number
  ) => {
    const start =
      Date.UTC(year, month - 1, day, startHour, startMinute, 0) / 1000;
    const end = Date.UTC(year, month - 1, day, endHour, endMinute, 0) / 1000;
    for (let time = start; time <= end; time += 60) {
      minutes.push(time as Time);
    }
  };

  if (shouldIncludeCallAuction(dateStr, options)) {
    addRange(1, 15, 1, 25);
  }
  addRange(1, 30, 3, 30);
  addRange(5, 0, 7, 0);

  return minutes;
};

export const getTradingSessionTickSlots = (
  dateStr: string,
  intervalSeconds = 3,
  options: TradingSessionOptions = {}
): Time[] => {
  const d = parseMarketDate(dateStr) || new Date();
  const { year, month, day } = getShanghaiDateParts(d);
  const slots: Time[] = [];
  const step = Math.max(1, intervalSeconds);

  const addRange = (
    startHour: number,
    startMinute: number,
    endHour: number,
    endMinute: number
  ) => {
    const start =
      Date.UTC(year, month - 1, day, startHour, startMinute, 0) / 1000;
    const end = Date.UTC(year, month - 1, day, endHour, endMinute, 0) / 1000;
    for (let time = start; time <= end; time += step) {
      slots.push(time as Time);
    }
  };

  if (shouldIncludeCallAuction(dateStr, options)) {
    addRange(1, 15, 1, 25);
  }
  addRange(1, 30, 3, 30);
  addRange(5, 0, 7, 0);

  return slots;
};

export const formatTime = (time: number) => {
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: SHANGHAI_TIME_ZONE,
  }).format(new Date(time * 1000));
};

export const formatIntradayTick = (time: number) => {
  if (isCallAuctionTimestamp(new Date(time * 1000))) return '09:15~25';
  const value = formatTime(time);
  return value;
};

export const formatDate = (time: number) => {
  const { month, day } = getShanghaiDateParts(new Date(time * 1000));
  return `${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
};

export const getTickDateRange = (
  tradingDays: string[] = [],
  mode: '1d' | '5d' = '1d',
  now: Date = new Date()
) => {
  if (tradingDays.length === 0) {
    return {
      startTime: undefined,
      endTime: undefined,
    };
  }

  const nowParts = getShanghaiDateParts(now);
  const hours = Math.floor(nowParts.minutes / 60);
  const todayStr = nowParts.dateKey;

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
