export type ManualCostBasisMode = 'BROKER_BUY_ORDERS' | 'MANUAL_UNIT_COST';

export interface ExitPlanCostBasisCandidate {
  estimatedBuyFeeCny: number;
  orderId: string;
  orderTime?: string | null;
  tradedPrice: number;
  tradedVolume: number;
}

export interface ExitPlanHoldingCapacity {
  capacityError?: string | null;
  capacityStatus: string;
  protectedVolume: number;
  totalVolume: number;
  unallocatedVolume: number;
}

export function readCostBasis(value: unknown) {
  const basis =
    value && typeof value === 'object'
      ? (value as Record<string, unknown>)
      : {};
  return {
    basisVolume:
      typeof basis.basis_volume === 'number' ? basis.basis_volume : 0,
    frozenAt: typeof basis.frozen_at === 'string' ? basis.frozen_at : '',
    mode:
      typeof basis.mode === 'string' ? basis.mode : 'POSITION_AVERAGE_SNAPSHOT',
    unitCost: typeof basis.unit_cost_cny === 'number' ? basis.unit_cost_cny : 0,
  };
}

export function costBasisModeLabel(mode: string) {
  if (mode === 'BROKER_BUY_ORDERS') return '成交委托';
  if (mode === 'MANUAL_UNIT_COST') return '手工全成本';
  if (mode === 'ENTRY_FILLS') return '策略入场成交';
  return '历史持仓均价';
}

export function summarizeSelectedCostBasis(
  candidates: readonly ExitPlanCostBasisCandidate[],
  selectedOrderIds: readonly string[]
) {
  const selected = candidates.filter(item =>
    selectedOrderIds.includes(item.orderId)
  );
  const volume = selected.reduce((total, item) => total + item.tradedVolume, 0);
  const totalCost = selected.reduce(
    (total, item) =>
      total + item.tradedPrice * item.tradedVolume + item.estimatedBuyFeeCny,
    0
  );
  return {
    count: selected.length,
    unitCost: volume > 0 ? totalCost / volume : 0,
    volume,
  };
}
