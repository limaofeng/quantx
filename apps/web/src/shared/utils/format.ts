// 格式化工具函数

/**
 * 格式化货币金额
 */
export function formatCurrency(
  amount: number,
  currency = 'CNY',
  options: Intl.NumberFormatOptions = {}
): string {
  const maximumFractionDigits = options.maximumFractionDigits ?? 2;
  const minimumFractionDigits =
    options.minimumFractionDigits ?? Math.min(2, maximumFractionDigits);
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency,
    ...options,
    minimumFractionDigits,
    maximumFractionDigits,
  }).format(amount);
}

/**
 * 格式化百分比
 */
export function formatPercent(value: number, fractionDigits = 2): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'percent',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(value / 100);
}

/**
 * 格式化数字
 */
export function formatNumber(
  value: number,
  options: Intl.NumberFormatOptions = {}
): string {
  const maximumFractionDigits = options.maximumFractionDigits ?? 2;
  const minimumFractionDigits =
    options.minimumFractionDigits ?? Math.min(2, maximumFractionDigits);
  return new Intl.NumberFormat('zh-CN', {
    ...options,
    minimumFractionDigits,
    maximumFractionDigits,
  }).format(value);
}

/**
 * 格式化股票代码（添加交易所前缀）
 */
export function formatStockCode(code: string, exchange?: string): string {
  if (!exchange) return code;

  const exchangePrefix = exchange.toUpperCase();
  return code.startsWith(exchangePrefix) ? code : `${exchangePrefix}:${code}`;
}

/**
 * 格式化交易量（K, M, B 单位）
 */
export function formatVolume(volume: number): string {
  if (volume >= 1e9) {
    return `${(volume / 1e9).toFixed(1)}B`;
  }
  if (volume >= 1e6) {
    return `${(volume / 1e6).toFixed(1)}M`;
  }
  if (volume >= 1e3) {
    return `${(volume / 1e3).toFixed(1)}K`;
  }
  return volume.toString();
}

/**
 * 格式化市值
 */
export function formatMarketCap(marketCap: number): string {
  if (marketCap >= 1e12) {
    return `${(marketCap / 1e12).toFixed(2)}万亿`;
  }
  if (marketCap >= 1e8) {
    return `${(marketCap / 1e8).toFixed(2)}亿`;
  }
  if (marketCap >= 1e4) {
    return `${(marketCap / 1e4).toFixed(2)}万`;
  }
  return formatCurrency(marketCap);
}

/**
 * 获取股票图标文本
 */
export function getStockIconText(name: string): string {
  return name ? name.slice(0, 2) : '';
}
