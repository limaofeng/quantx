import type { StrategyInstance } from '../../domain';

export interface GridBookTabProps {
  instance?: StrategyInstance | null;
  runId?: string;
  backtestId?: string | null;
}

export type GridBookLevel = {
  gridId: string;
  levelIndex: number;
  side: string;
  role?: string;
  price: number;
  plannedShares: number;
  amount: number;
  pctFromBase?: number | null;
  expectedProfit?: number | null;
  enabled: boolean;
  status: string;
  monitoring: boolean;
  pendingShares: number;
  filledShares: number;
  availableInventoryShares?: number;
  reservedInventoryShares?: number;
  cycleCount?: number;
  waitingReason?: string | null;
  orderId?: string | null;
  entryPrice?: number | null;
  entryTime?: string | null;
  lastIntentId?: string | null;
  lastTraceId?: string | null;
  reason?: string | null;
  updatedAt?: string | null;
};

export type DisplayGridBookLevel = GridBookLevel & {
  derivedSide: string;
  derivedRole: string;
  derivedLevelIndex: number;
};

export type GridInventoryLot = {
  lotId: string;
  sourceLevelId?: string | null;
  sourceLevelIndex?: number | null;
  source: string;
  bucket: string;
  entryPrice: number;
  originalShares: number;
  remainingShares: number;
  reservedShares: number;
  reservedForLevelId?: string | null;
  reservedOrderId?: string | null;
  status: string;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type GridReleaseEvent = {
  eventId: string;
  sellLevelId?: string | null;
  sellLevelIndex?: number | null;
  releasedLevelId?: string | null;
  releasedLevelIndex?: number | null;
  lotIds: string[];
  orderId?: string | null;
  intentId?: string | null;
  tradeId?: string | null;
  price: number;
  shares: number;
  createdAt?: string | null;
};

export type InventoryAllocation = {
  shares: number;
  reservedShares: number;
  cost: number;
  avgCost: number | null;
};

export type GridBookSummary = {
  totalLevels: number;
  enabledLevels: number;
  pendingLevels: number;
  filledLevels: number;
  disabledLevels: number;
  plannedAmount: number;
  buySlotCount?: number;
  sellWaterlineCount?: number;
  openLotShares?: number;
  reservedLotShares?: number;
  waitingInventoryLevels?: number;
  completedCycles?: number;
  releaseEventCount?: number;
};

export type GridBook = {
  runId: string;
  instrumentCode: string;
  basePrice: number;
  parameterVersion: string;
  version: number;
  modelVersion?: number;
  inventoryModel?: string;
  releaseRule?: string;
  sellEmptyBehavior?: string;
  editable: boolean;
  needsBacktest: boolean;
  updatedAt?: string | null;
  summary: GridBookSummary;
  levels: GridBookLevel[];
  inventoryLots?: GridInventoryLot[];
  releaseEvents?: GridReleaseEvent[];
};

export type UpdateGridLevel = (
  gridId: string,
  patch: Partial<GridBookLevel>
) => void;
