// 策略相关类型定义
import { type BaseEntity } from './common';

export interface Strategy extends BaseEntity {
  name: string;
  description?: string;
  status: 'running' | 'paused' | 'stopped' | 'error';
  type: 'momentum' | 'mean_reversion' | 'arbitrage' | 'grid' | 'custom';
  parameters: StrategyParameters;
  performance?: StrategyPerformance;
  riskMetrics?: RiskMetrics;
}

export interface StrategyParameters {
  [key: string]: unknown;
  stopLoss?: number;
  takeProfit?: number;
  maxPositions?: number;
  maxDrawdown?: number;
  initialCapital?: number;
}

export interface StrategyPerformance {
  totalReturn: number;
  totalReturnPercent: number;
  dailyReturn: number;
  dailyReturnPercent: number;
  maxDrawdown: number;
  maxDrawdownPercent: number;
  sharpeRatio: number;
  winRate: number;
  profitFactor: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  avgWinning: number;
  avgLosing: number;
  startDate: string;
  endDate: string;
}

export interface RiskMetrics {
  var95: number; // Value at Risk 95%
  var99: number; // Value at Risk 99%
  cvar95: number; // Conditional Value at Risk 95%
  beta: number;
  alpha: number;
  volatility: number;
  correlation: number;
}

export interface BacktestResult {
  strategyId: string;
  parameters: StrategyParameters;
  performance: StrategyPerformance;
  trades: BacktestTrade[];
  equityCurve: EquityPoint[];
  riskMetrics: RiskMetrics;
}

export interface BacktestTrade {
  date: string;
  stockCode: string;
  type: 'buy' | 'sell';
  quantity: number;
  price: number;
  value: number;
  pnl?: number;
}

export interface EquityPoint {
  date: string;
  equity: number;
  drawdown: number;
}
