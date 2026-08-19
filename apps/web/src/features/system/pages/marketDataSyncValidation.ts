export interface MarketDataSyncValidationInput {
  startDate: string;
  endDate: string;
  targetMode: 'holdings' | 'sectors' | 'stocks';
  stockCount: number;
  periods: string[];
  skipDownload: boolean;
  computeDailySignals: boolean;
}

export function inclusiveDateRangeDays(startDate: string, endDate: string) {
  const start = Date.parse(`${startDate}T00:00:00`);
  const end = Date.parse(`${endDate}T00:00:00`);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return 0;
  return Math.floor((end - start) / 86_400_000) + 1;
}

export function validateMarketDataSync({
  startDate,
  endDate,
  targetMode,
  stockCount,
  periods,
  skipDownload,
  computeDailySignals,
}: MarketDataSyncValidationInput): string | null {
  if (!startDate || !endDate) return '请选择完整的开始和结束日期。';
  const rangeDays = inclusiveDateRangeDays(startDate, endDate);
  if (rangeDays <= 0) return '结束日期不能早于开始日期。';
  if (targetMode === 'stocks' && stockCount === 0) {
    return '标的模式下至少输入一个股票或 ETF 代码。';
  }
  if (targetMode === 'holdings' && stockCount === 0) {
    return '当前账户没有可同步的持仓。';
  }
  if (computeDailySignals && !periods.includes('1d')) {
    return '计算日级指标依赖 1d K 线，请先选择日线周期。';
  }
  if (skipDownload && (!computeDailySignals || !periods.includes('1d'))) {
    return '仅补算指标必须同时启用“计算日级指标”并选择 1d。';
  }
  if (computeDailySignals && rangeDays > 30) {
    return '指标补算范围最多 30 天；更长 K 线同步请关闭指标计算。';
  }
  return null;
}
