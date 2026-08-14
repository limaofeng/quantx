import { financialToneClass } from '@/shared/utils/financialColors';

export function toFiniteNumber(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function formatCompactCurrency(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';

  const abs = Math.abs(amount);
  const sign = amount < 0 ? '-' : '';
  if (abs >= 100000000) return `${sign}¥${(abs / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${sign}¥${(abs / 10000).toFixed(2)}万`;
  return `${sign}¥${abs.toFixed(2)}`;
}

export function formatSignedCurrency(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return `${amount >= 0 ? '+' : ''}${formatCompactCurrency(amount)}`;
}

export function formatShares(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return Math.round(amount).toLocaleString('zh-CN');
}

export function formatPrice(value: unknown) {
  const price = toFiniteNumber(value);
  if (price === null || price <= 0) return '--';
  return `¥${price.toFixed(price >= 10 ? 2 : 3)}`;
}

export function formatPercent(value: unknown, signed = true) {
  const percent = toFiniteNumber(value);
  if (percent === null) return '--';
  const prefix = signed && percent > 0 ? '+' : '';
  return `${prefix}${percent.toFixed(2)}%`;
}

export function formatDate(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString('zh-CN', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

export function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

export function getToneClass(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null || amount === 0) return 'text-slate-200';
  return financialToneClass(amount);
}

export function getProgressPercent(part: unknown, total: unknown) {
  const partNumber = toFiniteNumber(part);
  const totalNumber = toFiniteNumber(total);
  if (partNumber === null || totalNumber === null || totalNumber <= 0) return 0;
  return Math.max(0, Math.min(100, (partNumber / totalNumber) * 100));
}

export function sourceLabel(source?: string | null) {
  if (!source) return '未知来源';
  if (source.includes('CNINFO')) return '巨潮/AkShare';
  if (source.includes('EASTMONEY')) return '东方财富/AkShare';
  return source;
}
