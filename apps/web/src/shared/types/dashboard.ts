// 仪表板相关类型定义

export interface DashboardSummary {
  // 资产概览
  totalAssets: number;
  totalValue: number;
  availableCash: number;
  frozenFunds: number;

  // 盈亏统计
  totalPnL: number;
  todayPnL: number;
  totalReturn: number;
  totalReturnPercent: number;

  // 持仓统计
  holdingsCount: number;
  profitableHoldings: number;
  losingHoldings: number;

  // 策略统计
  strategiesCount: number;
  runningStrategies: number;
  pausedStrategies: number;

  // 交易统计
  transactionCount: number;
  todayTransactionCount: number;
  totalCommission: number;
}

export interface PortfolioAllocation {
  sector: string;
  value: number;
  percentage: number;
  color: string;
}

export interface PerformanceMetrics {
  period: '1D' | '1W' | '1M' | '3M' | '6M' | '1Y' | 'YTD' | 'ALL';
  return: number;
  returnPercent: number;
  volatility: number;
  sharpeRatio: number;
  maxDrawdown: number;
  benchmark?: {
    return: number;
    returnPercent: number;
  };
}

export interface TopPerformer {
  stockCode: string;
  stockName: string;
  return: number;
  returnPercent: number;
  value: number;
}

export interface AssetDistribution {
  cash: number;
  stocks: number;
  other: number;
}
