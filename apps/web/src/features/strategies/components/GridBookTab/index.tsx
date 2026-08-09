import {
  KeyboardSensor,
  PointerSensor,
  type DragEndEvent,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import { arrayMove, sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { AlertCircle } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Card } from '@/components/ui/card';

import {
  StrategyGridBookQuery,
  UpdateStrategyGridBookMutation,
} from '../../hooks/strategyInstanceOperations';

import { GridBookSummaryCard } from './components/GridBookSummaryCard';
import { GridLevelsPanel } from './components/GridLevelsPanel';
import { SidePanels } from './components/SidePanels';
import {
  BASE_MARKER_ID,
  BUY_ZONE_ID,
  lockedStatuses,
  SELL_ZONE_ID,
  type GridZoneId,
} from './constants';
import {
  sortLevelsForZone,
  withDerivedGridIdentity,
  withZoneIntent,
} from './gridLogic';
import type { GridBook, GridBookLevel, GridBookTabProps } from './types';

export default function GridBookTab({
  instance,
  runId,
  backtestId,
}: GridBookTabProps) {
  const [{ data, fetching, error }, reexecuteQuery] = useQuery({
    query: StrategyGridBookQuery,
    variables: { instanceId: runId || '', backtestId: backtestId || null },
    pause: !runId,
    requestPolicy: 'cache-and-network',
  });
  const [, updateGridBook] = useMutation(UpdateStrategyGridBookMutation);
  const book = data?.strategyGridBook as GridBook | undefined;
  const [levels, setLevels] = useState<GridBookLevel[]>([]);
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  useEffect(() => {
    setLevels(book?.levels || []);
    setDirty(false);
    setSaveError(null);
  }, [backtestId, book?.levels, book?.runId, book?.version, book?.updatedAt]);

  const editable = !!book?.editable;
  const summary = useMemo(() => {
    if (book?.summary) return book.summary;
    return {
      totalLevels: levels.length,
      enabledLevels: levels.filter(level => level.enabled).length,
      pendingLevels: levels.filter(level => level.status === 'PENDING').length,
      filledLevels: levels.filter(level => level.status === 'FILLED').length,
      disabledLevels: levels.filter(level => !level.enabled).length,
      plannedAmount: levels
        .filter(level => level.enabled)
        .reduce((sum, level) => sum + level.price * level.plannedShares, 0),
      buySlotCount: levels.filter(
        level => level.role === 'BUY_SLOT' || level.side === 'BUY'
      ).length,
      sellWaterlineCount: levels.filter(
        level => level.role === 'SELL_WATERLINE' || level.side === 'SELL'
      ).length,
      openLotShares: 0,
      reservedLotShares: 0,
      waitingInventoryLevels: levels.filter(
        level => level.waitingReason === 'waiting_swing_inventory'
      ).length,
      completedCycles: levels.reduce(
        (sum, level) => sum + (level.cycleCount || 0),
        0
      ),
      releaseEventCount: 0,
    };
  }, [book?.summary, levels]);

  const displayLevels = useMemo(
    () => withDerivedGridIdentity(levels),
    [levels]
  );
  const sellLevels = useMemo(() => {
    const zoneLevels = displayLevels.filter(
      level => level.derivedSide === 'SELL'
    );
    return sortLevelsForZone(zoneLevels, SELL_ZONE_ID);
  }, [displayLevels]);
  const buyLevels = useMemo(() => {
    const zoneLevels = displayLevels.filter(
      level => level.derivedSide === 'BUY'
    );
    return sortLevelsForZone(zoneLevels, BUY_ZONE_ID);
  }, [displayLevels]);
  const sellLevelIds = useMemo(
    () => sellLevels.map(level => level.gridId),
    [sellLevels]
  );
  const buyLevelIds = useMemo(
    () => buyLevels.map(level => level.gridId),
    [buyLevels]
  );
  const sortableLevelIds = useMemo(
    () => [...sellLevelIds, BASE_MARKER_ID, ...buyLevelIds],
    [sellLevelIds, buyLevelIds]
  );

  const updateLevel = (gridId: string, patch: Partial<GridBookLevel>) => {
    setLevels(prev =>
      prev.map(level =>
        level.gridId === gridId
          ? (() => {
              const nextPrice = Number(patch.price ?? level.price ?? 0);
              const nextShares = Number(
                patch.plannedShares ?? level.plannedShares ?? 0
              );
              const shouldResetExpectedProfit =
                patch.price !== undefined || patch.plannedShares !== undefined;
              const basePrice = Number(book?.basePrice || 0);

              return {
                ...level,
                ...patch,
                amount: nextPrice * nextShares,
                expectedProfit:
                  patch.expectedProfit !== undefined
                    ? patch.expectedProfit
                    : shouldResetExpectedProfit
                      ? null
                      : level.expectedProfit,
                pctFromBase:
                  patch.pctFromBase !== undefined
                    ? patch.pctFromBase
                    : patch.price !== undefined && basePrice > 0
                      ? ((nextPrice - basePrice) / basePrice) * 100
                      : level.pctFromBase,
              };
            })()
          : level
      )
    );
    setDirty(true);
  };

  const moveLevelToZone = (sourceGridId: string, overId: string) => {
    const sourceZoneId = sellLevelIds.includes(sourceGridId)
      ? SELL_ZONE_ID
      : buyLevelIds.includes(sourceGridId)
        ? BUY_ZONE_ID
        : null;
    if (!sourceZoneId) return;

    const sourceLevels = sourceZoneId === SELL_ZONE_ID ? sellLevels : buyLevels;
    const sourceIndex = sourceLevels.findIndex(
      level => level.gridId === sourceGridId
    );
    if (
      sourceIndex < 0 ||
      lockedStatuses.has(sourceLevels[sourceIndex].status)
    ) {
      return;
    }

    const targetZoneId =
      overId === SELL_ZONE_ID || sellLevelIds.includes(overId)
        ? SELL_ZONE_ID
        : overId === BUY_ZONE_ID || buyLevelIds.includes(overId)
          ? BUY_ZONE_ID
          : null;
    if (!targetZoneId) return;

    const targetLevels = targetZoneId === SELL_ZONE_ID ? sellLevels : buyLevels;
    const overIndex = targetLevels.findIndex(level => level.gridId === overId);
    const targetIndex = overIndex >= 0 ? overIndex : targetLevels.length;

    if (overIndex >= 0 && lockedStatuses.has(targetLevels[overIndex].status)) {
      return;
    }

    let nextSellIds = sellLevelIds;
    let nextBuyIds = buyLevelIds;

    if (sourceZoneId === targetZoneId) {
      if (sourceIndex === targetIndex) return;
      const currentIds =
        sourceZoneId === SELL_ZONE_ID ? sellLevelIds : buyLevelIds;
      const moveStart = Math.min(sourceIndex, targetIndex);
      const moveEnd = Math.max(sourceIndex, targetIndex);
      const affectedLevels = sourceLevels.slice(moveStart, moveEnd + 1);
      if (affectedLevels.some(level => lockedStatuses.has(level.status))) {
        return;
      }
      const movedIds = arrayMove(currentIds, sourceIndex, targetIndex);
      if (sourceZoneId === SELL_ZONE_ID) {
        nextSellIds = movedIds;
      } else {
        nextBuyIds = movedIds;
      }
    } else {
      const sourceIds =
        sourceZoneId === SELL_ZONE_ID ? sellLevelIds : buyLevelIds;
      const targetIds =
        targetZoneId === SELL_ZONE_ID ? sellLevelIds : buyLevelIds;
      const sourceAffected = sourceLevels.slice(sourceIndex);
      const targetAffected = targetLevels.slice(targetIndex);
      if (
        [...sourceAffected, ...targetAffected].some(level =>
          lockedStatuses.has(level.status)
        )
      ) {
        return;
      }
      const nextSourceIds = sourceIds.filter(id => id !== sourceGridId);
      const nextTargetIds = [
        ...targetIds.slice(0, targetIndex),
        sourceGridId,
        ...targetIds.slice(targetIndex),
      ];
      if (sourceZoneId === SELL_ZONE_ID) {
        nextSellIds = nextSourceIds;
        nextBuyIds = nextTargetIds;
      } else {
        nextBuyIds = nextSourceIds;
        nextSellIds = nextTargetIds;
      }
    }

    const nextZoneByGridId = new Map<
      string,
      { zoneId: GridZoneId; index: number }
    >();
    nextSellIds.forEach((gridId, index) => {
      nextZoneByGridId.set(gridId, { zoneId: SELL_ZONE_ID, index });
    });
    nextBuyIds.forEach((gridId, index) => {
      nextZoneByGridId.set(gridId, { zoneId: BUY_ZONE_ID, index });
    });

    setLevels(prev =>
      prev.map(level => {
        const nextZone = nextZoneByGridId.get(level.gridId);
        return nextZone === undefined
          ? level
          : withZoneIntent(
              level,
              nextZone.zoneId,
              nextZone.index,
              nextZone.zoneId === SELL_ZONE_ID
                ? nextSellIds.length
                : nextBuyIds.length
            );
      })
    );
    setDirty(true);
  };

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const activeId = String(active.id);
    const overId = String(over.id);
    if (activeId === BASE_MARKER_ID) return;

    if (overId === BASE_MARKER_ID) {
      const boundaryOverId = sellLevelIds.includes(activeId)
        ? buyLevelIds[0] || BUY_ZONE_ID
        : buyLevelIds.includes(activeId)
          ? SELL_ZONE_ID
          : null;
      if (!boundaryOverId) return;
      moveLevelToZone(activeId, boundaryOverId);
      return;
    }

    moveLevelToZone(activeId, overId);
  };

  const addLevel = () => {
    const maxIndex = levels.reduce(
      (max, level) => Math.max(max, level.levelIndex),
      -1
    );
    const basePrice = Number(book?.basePrice || levels[0]?.price || 0);
    const price = basePrice > 0 ? Math.round(basePrice * 0.98 * 100) / 100 : 0;
    const level: GridBookLevel = {
      gridId: `grid-${Date.now()}`,
      levelIndex: maxIndex + 1,
      side: 'BUY',
      role: 'BUY_SLOT',
      price,
      plannedShares: 100,
      amount: price * 100,
      enabled: true,
      status: 'PLANNED',
      monitoring: false,
      pendingShares: 0,
      filledShares: 0,
    };
    setLevels(prev => [...prev, level]);
    setDirty(true);
  };

  const deleteLevel = (gridId: string) => {
    setLevels(prev => prev.filter(level => level.gridId !== gridId));
    setDirty(true);
  };

  const handleSave = async () => {
    if (!runId || !book) return;
    const normalizedLevels = withDerivedGridIdentity(levels);
    const result = await updateGridBook({
      instanceId: runId,
      input: {
        basePrice: book.basePrice,
        levels: normalizedLevels.map(level => ({
          gridId: level.gridId,
          levelIndex: Number(level.derivedLevelIndex || 0),
          side: level.derivedSide,
          price: Number(level.price || 0),
          plannedShares: Number(level.plannedShares || 0),
          pctFromBase: level.pctFromBase ?? null,
          expectedProfit: level.expectedProfit ?? null,
          enabled: level.enabled,
        })),
      },
    });
    if (result.error) {
      setSaveError(result.error.message);
      return;
    }
    setSaveError(null);
    setDirty(false);
    reexecuteQuery({ requestPolicy: 'network-only' });
  };

  if (!instance || !runId) {
    return (
      <Card className="p-10 text-center">
        <p className="text-sm font-bold text-slate-500">请先选择策略实例。</p>
      </Card>
    );
  }

  if (fetching && !book) {
    return (
      <Card className="rounded-[2rem] border border-slate-200 bg-white p-12 text-center shadow-xl dark:border-white/10 dark:bg-slate-900/60">
        <p className="text-xs font-black uppercase tracking-[0.24em] text-slate-400">
          正在加载网格簿...
        </p>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="rounded-[2rem] border border-rose-500/20 bg-rose-500/5 p-8 text-rose-400">
        <div className="flex items-center gap-2 text-xs font-bold">
          <AlertCircle className="h-4 w-4" />
          {error.message}
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-5 pb-12">
      <GridBookSummaryCard
        instrumentCode={instance.instrumentCode}
        book={book}
        summary={summary}
        editable={editable}
        backtestId={backtestId}
        saveError={saveError}
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.55fr)_minmax(360px,0.9fr)]">
        <GridLevelsPanel
          book={book}
          editable={editable}
          dirty={dirty}
          sellLevels={sellLevels}
          buyLevels={buyLevels}
          displayLevels={displayLevels}
          sortableLevelIds={sortableLevelIds}
          sensors={sensors}
          onAddLevel={addLevel}
          onSave={handleSave}
          onDragEnd={handleDragEnd}
          updateLevel={updateLevel}
          deleteLevel={deleteLevel}
        />

        <SidePanels book={book} />
      </div>
    </div>
  );
}
