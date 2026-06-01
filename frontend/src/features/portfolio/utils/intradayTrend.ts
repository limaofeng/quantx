import type { UTCTimestamp } from 'lightweight-charts';

export const A_SHARE_TRADING_MINUTES = 240;

export const COMPRESSED_TRADING_RANGE = {
  from: (Date.UTC(2000, 0, 1, 0, 0, 0) / 1000) as UTCTimestamp,
  to: (Date.UTC(2000, 0, 1, 4, 0, 0) / 1000) as UTCTimestamp,
};

const MORNING_START = 9 * 60 + 30;
const MORNING_END = 11 * 60 + 30;
const AFTERNOON_START = 13 * 60;
const AFTERNOON_END = 15 * 60;

export interface IntradayTrendPoint {
  time: UTCTimestamp;
  value: number;
}

export interface IntradayTrendTick {
  time?: string | Date | null;
  lastPrice?: number | null;
  currentPrice?: number | null;
}

export interface IntradayAnchor {
  date: string;
  isToday: boolean;
}

interface ShanghaiDateTimeParts {
  date: string;
  minutes: number;
}

const shanghaiDateTimeFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function getShanghaiDateTimeParts(date: Date): ShanghaiDateTimeParts {
  const parts = shanghaiDateTimeFormatter.formatToParts(date);
  const byType = new Map(parts.map(part => [part.type, part.value]));
  const year = byType.get('year');
  const month = byType.get('month');
  const day = byType.get('day');
  const hour = Number(byType.get('hour') || 0);
  const minute = Number(byType.get('minute') || 0);

  return {
    date: `${year}-${month}-${day}`,
    minutes: hour * 60 + minute,
  };
}

function formatShanghaiDate(date: Date) {
  return getShanghaiDateTimeParts(date).date;
}

export function parseShanghaiDateTime(
  value: string | Date
): ShanghaiDateTimeParts | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime())
      ? null
      : getShanghaiDateTimeParts(value);
  }

  const match = value.match(
    /^(\d{4})[-/](\d{2})[-/](\d{2})(?:[T\s](\d{2}):(\d{2}))?/
  );

  if (match) {
    return {
      date: `${match[1]}-${match[2]}-${match[3]}`,
      minutes: Number(match[4] || 0) * 60 + Number(match[5] || 0),
    };
  }

  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? null
    : getShanghaiDateTimeParts(parsed);
}

export function getCompressedTradingMinute(minutes: number): number | null {
  if (minutes >= MORNING_START && minutes <= MORNING_END) {
    return minutes - MORNING_START;
  }

  if (minutes >= AFTERNOON_START && minutes <= AFTERNOON_END) {
    return MORNING_END - MORNING_START + minutes - AFTERNOON_START;
  }

  return null;
}

export function getCompressedTradingTimestamp(minutes: number) {
  const compressedMinute = getCompressedTradingMinute(minutes);

  return compressedMinute === null
    ? null
    : (((COMPRESSED_TRADING_RANGE.from as number) +
        compressedMinute * 60) as UTCTimestamp);
}

export function resolveIntradayAnchorDate(
  tradingDays: string[],
  now: Date = new Date()
): IntradayAnchor | null {
  const sortedTradingDays = [...new Set(tradingDays)].sort();
  if (sortedTradingDays.length === 0) return null;

  const today = formatShanghaiDate(now);
  const { minutes } = getShanghaiDateTimeParts(now);
  const isTodayTradingDay = sortedTradingDays.includes(today);

  if (isTodayTradingDay && minutes >= MORNING_START) {
    return { date: today, isToday: true };
  }

  const previousTradingDays = sortedTradingDays.filter(day => day < today);
  const previousTradingDay =
    previousTradingDays[previousTradingDays.length - 1];

  if (previousTradingDay) {
    return { date: previousTradingDay, isToday: false };
  }

  const fallbackTradingDays = sortedTradingDays.filter(day => day <= today);
  const fallbackDay = fallbackTradingDays[fallbackTradingDays.length - 1];
  return fallbackDay
    ? { date: fallbackDay, isToday: fallbackDay === today }
    : null;
}

export function getIntradayQueryRange(date: string) {
  return {
    startTime: `${date} 00:00:00`,
    endTime: `${date} 23:59:59`,
  };
}

export function normalizeTicksToIntradayTrend(
  ticks: IntradayTrendTick[],
  anchorDate: string
): IntradayTrendPoint[] {
  const pointsByTime = new Map<number, number>();

  ticks.forEach(tick => {
    if (!tick.time) return;

    const parsedTime = parseShanghaiDateTime(tick.time);
    if (!parsedTime || parsedTime.date !== anchorDate) return;

    const price = tick.lastPrice ?? tick.currentPrice;
    if (typeof price !== 'number' || !Number.isFinite(price) || price <= 0) {
      return;
    }

    const compressedTime = getCompressedTradingTimestamp(parsedTime.minutes);
    if (compressedTime === null) return;

    pointsByTime.set(compressedTime as number, price);
  });

  return [...pointsByTime.entries()]
    .sort(([left], [right]) => left - right)
    .map(([time, value]) => ({
      time: time as UTCTimestamp,
      value,
    }));
}
