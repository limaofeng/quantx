import { BUY_ZONE_ID, SELL_ZONE_ID, type GridZoneId } from './constants';
import type {
  DisplayGridBookLevel,
  GridBookLevel,
  GridInventoryLot,
  InventoryAllocation,
} from './types';

function inventoryTimeValue(value?: string | null) {
  if (!value) return 0;
  const time = Date.parse(value);
  return Number.isFinite(time) ? time : 0;
}

function activeInventoryLots(lots?: GridInventoryLot[]) {
  return (lots || []).filter(
    lot =>
      lot.bucket === 'swing' &&
      ['OPEN', 'RESERVED'].includes(lot.status) &&
      Number(lot.remainingShares || 0) > 0
  );
}

export function buyLevelAllocation(
  level: GridBookLevel,
  lots?: GridInventoryLot[]
): InventoryAllocation {
  const sourceLots = activeInventoryLots(lots).filter(
    lot => lot.sourceLevelId === level.gridId
  );

  if (sourceLots.length > 0) {
    const shares = sourceLots.reduce(
      (sum, lot) => sum + Number(lot.remainingShares || 0),
      0
    );
    const reservedShares = sourceLots.reduce(
      (sum, lot) => sum + Number(lot.reservedShares || 0),
      0
    );
    const cost = sourceLots.reduce(
      (sum, lot) =>
        sum + Number(lot.entryPrice || 0) * Number(lot.remainingShares || 0),
      0
    );

    return {
      shares,
      reservedShares,
      cost,
      avgCost: shares > 0 ? cost / shares : null,
    };
  }

  const fallbackShares =
    level.status === 'FILLED'
      ? Number(level.filledShares || level.plannedShares || 0)
      : Number(level.filledShares || 0);
  const fallbackCost = Number(level.entryPrice || level.price || 0);
  return {
    shares: fallbackShares,
    reservedShares: Number(level.reservedInventoryShares || 0),
    cost: fallbackCost * fallbackShares,
    avgCost: fallbackShares > 0 ? fallbackCost : null,
  };
}

export function sellLevelAllocation(
  level: GridBookLevel,
  lots?: GridInventoryLot[]
): InventoryAllocation {
  const targetShares = Number(level.plannedShares || 0);
  const candidates = activeInventoryLots(lots)
    .filter(lot => {
      if (Number(lot.entryPrice || 0) >= Number(level.price || 0)) return false;
      return !lot.reservedForLevelId || lot.reservedForLevelId === level.gridId;
    })
    .sort((a, b) => {
      const reservedDiff =
        (b.reservedForLevelId === level.gridId ? 1 : 0) -
        (a.reservedForLevelId === level.gridId ? 1 : 0);
      if (reservedDiff !== 0) return reservedDiff;
      const priceDiff = Number(b.entryPrice || 0) - Number(a.entryPrice || 0);
      if (priceDiff !== 0) return priceDiff;
      return inventoryTimeValue(b.createdAt) - inventoryTimeValue(a.createdAt);
    });

  let remaining = targetShares;
  let shares = 0;
  let reservedShares = 0;
  let cost = 0;

  for (const lot of candidates) {
    if (remaining <= 0) break;
    const lotRemaining = Number(lot.remainingShares || 0);
    const lotReserved = Number(lot.reservedShares || 0);
    const available =
      lot.reservedForLevelId === level.gridId
        ? Math.min(lotRemaining, lotReserved || lotRemaining)
        : Math.max(0, lotRemaining - lotReserved);
    const take = Math.min(remaining, available);
    if (take <= 0) continue;
    shares += take;
    if (lot.reservedForLevelId === level.gridId) {
      reservedShares += take;
    }
    cost += Number(lot.entryPrice || 0) * take;
    remaining -= take;
  }

  return {
    shares,
    reservedShares,
    cost,
    avgCost: shares > 0 ? cost / shares : null,
  };
}

export function gridStateDisplay(
  level: DisplayGridBookLevel,
  allocation: InventoryAllocation
) {
  const isSell = level.derivedSide === 'SELL';
  const terminalState = {
    DISABLED: {
      label: '已禁用',
      className: 'border-slate-500/30 bg-slate-500/10 text-slate-400',
    },
    REJECTED: {
      label: '已拒单',
      className: 'border-rose-500/30 bg-rose-500/10 text-rose-400',
    },
    CANCELLED: {
      label: '已撤销',
      className: 'border-rose-500/30 bg-rose-500/10 text-rose-400',
    },
  } as const;

  if (level.status in terminalState) {
    return terminalState[level.status as keyof typeof terminalState];
  }

  if (level.status === 'PENDING' || level.status === 'PARTIAL_FILLED') {
    return {
      label: isSell ? '卖出中' : '买入中',
      className: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    };
  }

  if (level.status === 'WAIT_REARM') {
    return {
      label: '等待重穿',
      className: 'border-violet-500/30 bg-violet-500/10 text-violet-300',
    };
  }

  if (!isSell && allocation.shares > 0) {
    return {
      label: allocation.reservedShares > 0 ? '卖出中' : '等待卖出',
      className: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
    };
  }

  if (isSell) {
    const hasInventory = allocation.shares > 0;
    return {
      label: hasInventory ? '等待卖出' : '等待库存',
      className: hasInventory
        ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
        : 'border-amber-500/30 bg-amber-500/10 text-amber-300',
    };
  }

  return {
    label: '等待买入',
    className: 'border-blue-500/30 bg-blue-500/10 text-blue-300',
  };
}

export function sideForZone(zoneId: GridZoneId) {
  const side = zoneId === SELL_ZONE_ID ? 'SELL' : 'BUY';
  return {
    side,
    role: side === 'SELL' ? 'SELL_WATERLINE' : 'BUY_SLOT',
  };
}

export function zoneForLevel(level: GridBookLevel): GridZoneId {
  if (level.side === 'SELL' || level.role === 'SELL_WATERLINE') {
    return SELL_ZONE_ID;
  }
  return BUY_ZONE_ID;
}

export function levelOrderIndex(level: GridBookLevel) {
  const order = Math.abs(Number(level.levelIndex || 0));
  return order > 0 ? order : Number.MAX_SAFE_INTEGER;
}

export function sortLevelsForZone<T extends GridBookLevel>(
  levels: T[],
  zoneId: GridZoneId
) {
  return levels
    .map((level, originalIndex) => ({ level, originalIndex }))
    .sort((a, b) => {
      const aOrder = levelOrderIndex(a.level);
      const bOrder = levelOrderIndex(b.level);
      if (
        aOrder === Number.MAX_SAFE_INTEGER &&
        bOrder === Number.MAX_SAFE_INTEGER
      ) {
        return a.originalIndex - b.originalIndex;
      }
      if (aOrder === Number.MAX_SAFE_INTEGER) return 1;
      if (bOrder === Number.MAX_SAFE_INTEGER) return -1;
      const orderDiff = aOrder - bOrder;
      if (orderDiff !== 0) {
        return zoneId === SELL_ZONE_ID ? -orderDiff : orderDiff;
      }
      return a.originalIndex - b.originalIndex;
    })
    .map(item => item.level);
}

export function withZoneIntent(
  level: GridBookLevel,
  zoneId: GridZoneId,
  index: number,
  zoneSize: number
) {
  const sidePatch = sideForZone(zoneId);
  const sideChanged = level.side !== sidePatch.side;
  const levelIndex = zoneId === SELL_ZONE_ID ? zoneSize - index : -(index + 1);
  return {
    ...level,
    ...sidePatch,
    levelIndex,
    amount: Number(level.price || 0) * Number(level.plannedShares || 0),
    expectedProfit: sideChanged ? null : level.expectedProfit,
  };
}

export function withDerivedGridIdentity(
  levels: GridBookLevel[]
): DisplayGridBookLevel[] {
  const sellLevels = sortLevelsForZone(
    levels.filter(level => zoneForLevel(level) === SELL_ZONE_ID),
    SELL_ZONE_ID
  );
  const buyLevels = sortLevelsForZone(
    levels.filter(level => zoneForLevel(level) === BUY_ZONE_ID),
    BUY_ZONE_ID
  );
  const identityById = new Map<
    string,
    { derivedSide: string; derivedRole: string; derivedLevelIndex: number }
  >();

  sellLevels.forEach((level, index) => {
    identityById.set(level.gridId, {
      derivedSide: 'SELL',
      derivedRole: 'SELL_WATERLINE',
      derivedLevelIndex: sellLevels.length - index,
    });
  });
  buyLevels.forEach((level, index) => {
    identityById.set(level.gridId, {
      derivedSide: 'BUY',
      derivedRole: 'BUY_SLOT',
      derivedLevelIndex: -(index + 1),
    });
  });

  return levels.map(level => ({
    ...level,
    ...(identityById.get(level.gridId) || {
      derivedSide: zoneForLevel(level) === SELL_ZONE_ID ? 'SELL' : 'BUY',
      derivedRole:
        zoneForLevel(level) === SELL_ZONE_ID ? 'SELL_WATERLINE' : 'BUY_SLOT',
      derivedLevelIndex:
        zoneForLevel(level) === SELL_ZONE_ID
          ? Math.abs(Number(level.levelIndex || 0))
          : -Math.abs(Number(level.levelIndex || 0)),
    }),
  }));
}
