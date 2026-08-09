// 计算工具函数

/**
 * 计算收益率
 */
export function calculateReturn(
  currentValue: number,
  initialValue: number
): number {
  if (initialValue === 0) return 0;
  return currentValue - initialValue;
}

/**
 * 计算收益率百分比
 */
export function calculateReturnPercent(
  currentValue: number,
  initialValue: number
): number {
  if (initialValue === 0) return 0;
  return ((currentValue - initialValue) / initialValue) * 100;
}

/**
 * 计算年化收益率
 */
export function calculateAnnualizedReturn(
  totalReturn: number,
  days: number
): number {
  if (days <= 0) return 0;
  return (Math.pow(1 + totalReturn / 100, 365 / days) - 1) * 100;
}

/**
 * 计算夏普比率
 */
export function calculateSharpeRatio(
  returns: number[],
  riskFreeRate = 0.03
): number {
  if (returns.length < 2) return 0;

  const avgReturn = returns.reduce((sum, r) => sum + r, 0) / returns.length;
  const variance =
    returns.reduce((sum, r) => sum + Math.pow(r - avgReturn, 2), 0) /
    (returns.length - 1);
  const volatility = Math.sqrt(variance);

  if (volatility === 0) return 0;
  return (avgReturn - riskFreeRate) / volatility;
}

/**
 * 计算最大回撤
 */
export function calculateMaxDrawdown(equityCurve: number[]): {
  maxDrawdown: number;
  maxDrawdownPercent: number;
  peak: number;
  trough: number;
} {
  if (equityCurve.length === 0) {
    return { maxDrawdown: 0, maxDrawdownPercent: 0, peak: 0, trough: 0 };
  }

  let peak = equityCurve[0];
  let maxDrawdown = 0;
  let maxDrawdownPercent = 0;
  let peakValue = peak;
  let troughValue = peak;

  for (const value of equityCurve) {
    if (value > peak) {
      peak = value;
    }

    const drawdown = peak - value;
    const drawdownPercent = peak === 0 ? 0 : (drawdown / peak) * 100;

    if (drawdown > maxDrawdown) {
      maxDrawdown = drawdown;
      maxDrawdownPercent = drawdownPercent;
      peakValue = peak;
      troughValue = value;
    }
  }

  return {
    maxDrawdown,
    maxDrawdownPercent,
    peak: peakValue,
    trough: troughValue,
  };
}

/**
 * 计算波动率（年化）
 */
export function calculateVolatility(returns: number[]): number {
  if (returns.length < 2) return 0;

  const avgReturn = returns.reduce((sum, r) => sum + r, 0) / returns.length;
  const variance =
    returns.reduce((sum, r) => sum + Math.pow(r - avgReturn, 2), 0) /
    (returns.length - 1);

  // 年化波动率（假设252个交易日）
  return Math.sqrt(variance * 252);
}

/**
 * 计算Value at Risk (VaR)
 */
export function calculateVaR(returns: number[], confidence = 0.95): number {
  if (returns.length === 0) return 0;

  const sortedReturns = [...returns].sort((a, b) => a - b);
  const index = Math.floor((1 - confidence) * sortedReturns.length);

  return sortedReturns[index] || 0;
}

/**
 * 计算贝塔系数
 */
export function calculateBeta(
  assetReturns: number[],
  marketReturns: number[]
): number {
  if (assetReturns.length !== marketReturns.length || assetReturns.length < 2) {
    return 1; // 默认贝塔为1
  }

  const n = assetReturns.length;
  const assetMean = assetReturns.reduce((sum, r) => sum + r, 0) / n;
  const marketMean = marketReturns.reduce((sum, r) => sum + r, 0) / n;

  let covariance = 0;
  let marketVariance = 0;

  for (let i = 0; i < n; i++) {
    const assetDiff = assetReturns[i] - assetMean;
    const marketDiff = marketReturns[i] - marketMean;

    covariance += assetDiff * marketDiff;
    marketVariance += marketDiff * marketDiff;
  }

  if (marketVariance === 0) return 1;

  return covariance / marketVariance;
}

/**
 * 计算相关系数
 */
export function calculateCorrelation(x: number[], y: number[]): number {
  if (x.length !== y.length || x.length < 2) return 0;

  const n = x.length;
  const xMean = x.reduce((sum, val) => sum + val, 0) / n;
  const yMean = y.reduce((sum, val) => sum + val, 0) / n;

  let numerator = 0;
  let xVariance = 0;
  let yVariance = 0;

  for (let i = 0; i < n; i++) {
    const xDiff = x[i] - xMean;
    const yDiff = y[i] - yMean;

    numerator += xDiff * yDiff;
    xVariance += xDiff * xDiff;
    yVariance += yDiff * yDiff;
  }

  const denominator = Math.sqrt(xVariance * yVariance);
  return denominator === 0 ? 0 : numerator / denominator;
}

/**
 * 计算复合年增长率 (CAGR)
 */
export function calculateCAGR(
  endValue: number,
  startValue: number,
  years: number
): number {
  if (startValue <= 0 || years <= 0) return 0;
  return (Math.pow(endValue / startValue, 1 / years) - 1) * 100;
}
