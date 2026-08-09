export enum GridType {
  GEOMETRIC = 'GEOMETRIC',
  ARITHMETIC = 'ARITHMETIC',
}

export interface GridConfig {
  symbol: string;
  basePrice: number;
  cashTotal: number;
  positionShares: number;
  avgCost: number;
  lockedCoreShares: number;
  coreShares: number;
  swingShares: number;
  gridType: GridType;
  isStepUnified: boolean;
  stepPctUp: number;
  stepPctDown: number;
  nUp: number;
  nDown: number;
  maxPositionValuePct: number;
  buyBudgetPct: number;
  minTradeValue: number;
}

export interface GridLevel {
  id: string;
  levelIndex: number;
  side: 'BUY' | 'SELL';
  price: number;
  shares: number;
  amount: number;
  pctFromBase: number;
  expectedProfit: number;
  role: 'BUY_SLOT' | 'SELL_WATERLINE';
}

export interface GridResult {
  isValid: boolean;
  errors: string[];
  levels: GridLevel[];
  basePrice: number;
  guards: {
    totalInvested: number;
    maxPositionValue: number;
    buyBudget: number;
  };
}
