import { type GridConfig, GridType } from './types';

export const DEFAULT_CONFIG: GridConfig = {
  symbol: '510300.SH',
  basePrice: 3.82,
  cashTotal: 100000,
  positionShares: 5000,
  avgCost: 3.75,
  lockedCoreShares: 0,
  coreShares: 5000,
  swingShares: 0,
  gridType: GridType.GEOMETRIC,
  isStepUnified: true,
  stepPctUp: 2.5,
  stepPctDown: 2.5,
  nUp: 4,
  nDown: 4,
  maxPositionValuePct: 100,
  buyBudgetPct: 100,
  minTradeValue: 10000,
};
