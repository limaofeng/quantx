// 测试模拟数据
import {
  type Stock,
  type Holding,
  type Transaction,
  type Strategy,
  type DashboardSummary,
  type PortfolioAllocation,
} from '@/shared/types';

// 模拟股票数据
export const mockStock: Stock = {
  id: '1',
  stockCode: '000001',
  code: '000001',
  name: '平安银行',
  market: 'SZ',
  type: 'stock',
  currentPrice: 12.5,
  quote: {
    lastPrice: 12.5,
    changePercent: 2.5,
    volume: 1500000,
  },
};

// 模拟持仓数据
export const mockHolding: Holding = {
  id: '1',
  stockCode: '000001',
  stockName: '平安银行',
  volume: 1000,
  canUseVolume: 1000,
  openPrice: 12.0,
  marketValue: 12500,
  frozenVolume: 0,
  onRoadVolume: 0,
  yesterdayVolume: 1000,
  avgPrice: 12.0,
  lastPrice: 12.5,
  profitRate: 4.17,
  profitLoss: 500,
  createdAt: '2024-01-01T00:00:00Z',
};

// 模拟交易记录
export const mockTransaction: Transaction = {
  id: '1',
  type: 'buy',
  stockCode: '000001',
  stockName: '平安银行',
  quantity: 1000,
  price: 12.0,
  totalAmount: 12000,
  status: 'filled',
  orderTime: '2024-01-01T09:30:00Z',
  fillTime: '2024-01-01T09:30:05Z',
  commission: 5,
  createdAt: '2024-01-01T09:30:00Z',
};

// 模拟策略数据
export const mockStrategy: Strategy = {
  id: '1',
  name: '动量策略',
  description: '基于动量指标的交易策略',
  status: 'running',
  type: 'momentum',
  parameters: {
    stopLoss: 5,
    takeProfit: 10,
    maxPositions: 5,
    initialCapital: 100000,
  },
  performance: {
    totalReturn: 15000,
    totalReturnPercent: 15.0,
    dailyReturn: 150,
    dailyReturnPercent: 0.15,
    maxDrawdown: 3000,
    maxDrawdownPercent: 3.0,
    sharpeRatio: 1.5,
    winRate: 65.5,
    profitFactor: 1.8,
    totalTrades: 50,
    winningTrades: 33,
    losingTrades: 17,
    avgWinning: 800,
    avgLosing: -400,
    startDate: '2024-01-01',
    endDate: '2024-12-31',
  },
  createdAt: '2024-01-01T00:00:00Z',
};

// 模拟仪表板摘要
export const mockDashboardSummary: DashboardSummary = {
  totalAssets: 150000,
  totalValue: 145000,
  availableCash: 25000,
  frozenFunds: 5000,
  totalPnL: 15000,
  todayPnL: 500,
  totalReturn: 15000,
  totalReturnPercent: 11.5,
  holdingsCount: 5,
  profitableHoldings: 3,
  losingHoldings: 2,
  strategiesCount: 3,
  runningStrategies: 2,
  pausedStrategies: 1,
  transactionCount: 25,
  todayTransactionCount: 3,
  totalCommission: 125,
};

// 模拟投资组合分配
export const mockPortfolioAllocation: PortfolioAllocation[] = [
  {
    sector: '金融',
    value: 50000,
    percentage: 34.5,
    color: '#8884d8',
  },
  {
    sector: '科技',
    value: 45000,
    percentage: 31.0,
    color: '#82ca9d',
  },
  {
    sector: '消费',
    value: 30000,
    percentage: 20.7,
    color: '#ffc658',
  },
  {
    sector: '医疗',
    value: 20000,
    percentage: 13.8,
    color: '#ff7c7c',
  },
];

// 模拟数组数据
export const mockStocks: Stock[] = [
  mockStock,
  {
    ...mockStock,
    id: '2',
    stockCode: '000002',
    code: '000002',
    name: '万科A',
    currentPrice: 18.5,
    quote: {
      lastPrice: 18.5,
      changePercent: -1.2,
      volume: 1500000,
    },
  },
];

export const mockHoldings: Holding[] = [
  mockHolding,
  {
    ...mockHolding,
    id: '2',
    stockCode: '000002',
    stockName: '万科A',
    volume: 500,
    marketValue: 9250,
    profitLoss: -250,
    profitRate: -2.63,
  },
];

export const mockTransactions: Transaction[] = [
  mockTransaction,
  {
    ...mockTransaction,
    id: '2',
    type: 'sell',
    stockCode: '000002',
    stockName: '万科A',
    quantity: 200,
    price: 18.5,
    totalAmount: 3700,
  },
];

export const mockStrategies: Strategy[] = [
  mockStrategy,
  {
    ...mockStrategy,
    id: '2',
    name: '均值回归策略',
    status: 'paused',
    type: 'mean_reversion',
  },
];
