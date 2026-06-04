// 日期时间工具函数

import { formatDistance, isValid } from 'date-fns';
import { zhCN } from 'date-fns/locale';

const CHINA_TIME_ZONE = 'Asia/Shanghai';
const CHINA_OFFSET = '+08:00';
const DATE_TIME_RE = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/;
const TIMEZONE_SUFFIX_RE = /(?:z|[+-]\d{2}:?\d{2})$/i;

const chinaDateFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: CHINA_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

function parseDate(date: string | Date): Date {
  if (date instanceof Date) return date;

  const normalized = date.includes(' ') ? date.replace(' ', 'T') : date;
  const withTimezone =
    DATE_TIME_RE.test(normalized) && !TIMEZONE_SUFFIX_RE.test(normalized)
      ? `${normalized}${CHINA_OFFSET}`
      : normalized;

  return new Date(withTimezone);
}

function getChinaDateParts(date: Date) {
  let year = '';
  let month = '';
  let day = '';
  let hour = '0';
  let minute = '0';
  let second = '0';

  chinaDateFormatter.formatToParts(date).forEach(part => {
    if (part.type === 'year') year = part.value;
    if (part.type === 'month') month = part.value;
    if (part.type === 'day') day = part.value;
    if (part.type === 'hour') hour = part.value;
    if (part.type === 'minute') minute = part.value;
    if (part.type === 'second') second = part.value;
  });

  const normalizedHour = hour === '24' ? '00' : hour;

  return {
    year,
    month,
    day,
    hour: normalizedHour,
    minute,
    second,
    dateKey: `${year}-${month}-${day}`,
  };
}

function formatChinaDate(date: Date, formatStr: string): string {
  const parts = getChinaDateParts(date);
  return formatStr
    .replace(/yyyy/g, parts.year)
    .replace(/MM/g, parts.month)
    .replace(/dd/g, parts.day)
    .replace(/HH/g, parts.hour)
    .replace(/mm/g, parts.minute)
    .replace(/ss/g, parts.second);
}

function getChinaDayOfWeek(date: Date): number {
  const parts = getChinaDateParts(date);
  return new Date(
    Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day))
  ).getUTCDay();
}

/**
 * 格式化日期
 */
export function formatDate(
  date: string | Date,
  formatStr = 'yyyy-MM-dd'
): string {
  const dateObj = parseDate(date);

  if (!isValid(dateObj)) {
    return '无效日期';
  }

  return formatChinaDate(dateObj, formatStr);
}

/**
 * 格式化日期时间
 */
export function formatDateTime(
  date: string | Date,
  formatStr = 'yyyy-MM-dd HH:mm:ss'
): string {
  return formatDate(date, formatStr);
}

/**
 * 格式化相对时间
 */
export function formatRelativeTime(date: string | Date): string {
  const dateObj = parseDate(date);

  if (!isValid(dateObj)) {
    return '无效日期';
  }

  return formatDistance(dateObj, new Date(), {
    addSuffix: true,
    locale: zhCN,
  });
}

/**
 * 格式化交易时间（只显示时间部分）
 */
export function formatTradeTime(date: string | Date): string {
  return formatDate(date, 'HH:mm:ss');
}

/**
 * 格式化市场时间（考虑交易日）
 */
export function formatMarketTime(date: string | Date): string {
  const dateObj = parseDate(date);

  if (!isValid(dateObj)) {
    return '无效日期';
  }

  const isToday =
    getChinaDateParts(dateObj).dateKey ===
    getChinaDateParts(new Date()).dateKey;

  if (isToday) {
    return formatChinaDate(dateObj, 'HH:mm:ss');
  }

  return formatChinaDate(dateObj, 'MM-dd HH:mm');
}

/**
 * 判断是否为交易日（简化版，不考虑节假日）
 */
export function isTradingDay(date: Date = new Date()): boolean {
  const dayOfWeek = getChinaDayOfWeek(date);
  return dayOfWeek >= 1 && dayOfWeek <= 5; // 周一到周五
}

/**
 * 判断是否在交易时间内
 */
export function isTradingHours(date: Date = new Date()): boolean {
  if (!isTradingDay(date)) {
    return false;
  }

  const parts = getChinaDateParts(date);
  const hours = Number(parts.hour);
  const minutes = Number(parts.minute);
  const time = hours * 100 + minutes;

  // A股交易时间：9:30-11:30, 13:00-15:00
  return (time >= 930 && time <= 1130) || (time >= 1300 && time <= 1500);
}

/**
 * 获取下一个交易日
 */
export function getNextTradingDay(date: Date = new Date()): Date {
  const nextDay = new Date(date.getTime());

  do {
    nextDay.setTime(nextDay.getTime() + 24 * 60 * 60 * 1000);
  } while (!isTradingDay(nextDay));

  return nextDay;
}

/**
 * 获取交易日期范围
 */
export function getTradingDateRange(days: number): { start: Date; end: Date } {
  const end = new Date();
  const start = new Date(end.getTime());

  let addedDays = 0;
  const currentDate = new Date(start);

  while (addedDays < days) {
    currentDate.setTime(currentDate.getTime() - 24 * 60 * 60 * 1000);
    if (isTradingDay(currentDate)) {
      addedDays++;
    }
  }

  start.setTime(currentDate.getTime());

  return { start, end };
}
