// 日期时间工具函数

import { format, formatDistance, parseISO, isValid } from 'date-fns';
import { zhCN } from 'date-fns/locale';

/**
 * 格式化日期
 */
export function formatDate(
  date: string | Date,
  formatStr = 'yyyy-MM-dd'
): string {
  const dateObj = typeof date === 'string' ? parseISO(date) : date;

  if (!isValid(dateObj)) {
    return '无效日期';
  }

  return format(dateObj, formatStr, { locale: zhCN });
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
  const dateObj = typeof date === 'string' ? parseISO(date) : date;

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
  const dateObj = typeof date === 'string' ? parseISO(date) : date;

  if (!isValid(dateObj)) {
    return '无效日期';
  }

  const now = new Date();
  const isToday = format(dateObj, 'yyyy-MM-dd') === format(now, 'yyyy-MM-dd');

  if (isToday) {
    return format(dateObj, 'HH:mm:ss');
  }

  return format(dateObj, 'MM-dd HH:mm');
}

/**
 * 判断是否为交易日（简化版，不考虑节假日）
 */
export function isTradingDay(date: Date = new Date()): boolean {
  const dayOfWeek = date.getDay();
  return dayOfWeek >= 1 && dayOfWeek <= 5; // 周一到周五
}

/**
 * 判断是否在交易时间内
 */
export function isTradingHours(date: Date = new Date()): boolean {
  if (!isTradingDay(date)) {
    return false;
  }

  const hours = date.getHours();
  const minutes = date.getMinutes();
  const time = hours * 100 + minutes;

  // A股交易时间：9:30-11:30, 13:00-15:00
  return (time >= 930 && time <= 1130) || (time >= 1300 && time <= 1500);
}

/**
 * 获取下一个交易日
 */
export function getNextTradingDay(date: Date = new Date()): Date {
  const nextDay = new Date(date);

  do {
    nextDay.setDate(nextDay.getDate() + 1);
  } while (!isTradingDay(nextDay));

  return nextDay;
}

/**
 * 获取交易日期范围
 */
export function getTradingDateRange(days: number): { start: Date; end: Date } {
  const end = new Date();
  const start = new Date();

  let addedDays = 0;
  const currentDate = new Date(start);

  while (addedDays < days) {
    currentDate.setDate(currentDate.getDate() - 1);
    if (isTradingDay(currentDate)) {
      addedDays++;
    }
  }

  start.setTime(currentDate.getTime());

  return { start, end };
}
