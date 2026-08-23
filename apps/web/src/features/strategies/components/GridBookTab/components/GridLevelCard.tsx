import { Trash2 } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

import { lockedStatuses } from '../constants';
import {
  displayReasonLabel,
  formatMoney,
  formatNumber,
  reasonLabel,
} from '../formatters';
import {
  buyLevelAllocation,
  gridStateDisplay,
  sellLevelAllocation,
} from '../gridLogic';
import type { DisplayGridBookLevel, GridBook, UpdateGridLevel } from '../types';

import { SortableGridLevelRow } from './SortableGridLevelRow';

interface GridLevelCardProps {
  level: DisplayGridBookLevel;
  book?: GridBook;
  editable: boolean;
  updateLevel: UpdateGridLevel;
  deleteLevel: (gridId: string) => void;
}

export function GridLevelCard({
  level,
  book,
  editable,
  updateLevel,
  deleteLevel,
}: GridLevelCardProps) {
  const isSell = level.derivedSide === 'SELL';
  const isDisabled = !level.enabled || level.status === 'DISABLED';
  const locked = lockedStatuses.has(level.status);
  const editableLevel = editable && !locked;
  const allocation = isSell
    ? sellLevelAllocation(level, book?.inventoryLots)
    : buyLevelAllocation(level, book?.inventoryLots);
  const displayState = gridStateDisplay(level, allocation);
  const pctFromBase =
    level.pctFromBase ??
    (book?.basePrice
      ? ((level.price - book.basePrice) / book.basePrice) * 100
      : null);
  const plannedAmount = level.price * level.plannedShares;
  const estimatedProfit =
    isSell && allocation.shares > 0
      ? level.price * allocation.shares - allocation.cost
      : (level.expectedProfit ??
        Math.max(
          0,
          Math.abs(level.price - (book?.basePrice || level.price)) *
            level.plannedShares
        ));
  const metricLabel =
    allocation.shares > 0 ? '成本' : isSell ? '卖出额' : '买入额';
  const metricValue = allocation.shares > 0 ? allocation.cost : plannedAmount;
  const secondaryMetric = isSell
    ? allocation.shares > 0
      ? `预计收益 ${formatMoney(estimatedProfit)}`
      : '待匹配库存'
    : allocation.shares > 0
      ? `持仓 ${formatNumber(allocation.shares)}股`
      : `价差空间 ${formatMoney(estimatedProfit)}`;
  const levelTitle = isSell
    ? `卖${level.derivedLevelIndex}档`
    : `买${Math.abs(level.derivedLevelIndex)}档`;
  const detailText = isSell
    ? `匹配 ${formatNumber(allocation.shares)} / ${formatNumber(level.plannedShares)}`
    : allocation.shares > 0 && allocation.avgCost
      ? `成本价 ${formatNumber(allocation.avgCost)}`
      : `循环 ${level.cycleCount || 0} 次`;
  const reasonText = displayReasonLabel(level.waitingReason || level.reason);
  const valueInputClass =
    'h-7 rounded-md border border-transparent bg-transparent px-2 py-0 text-right font-mono text-[13px] font-black caret-blue-200 shadow-none ring-0 ring-transparent ring-offset-0 transition-none [appearance:textfield] hover:border-slate-500/20 hover:bg-white/[0.035] focus:border-blue-400/60 focus:bg-slate-950/45 focus:outline-none focus:ring-0 focus:ring-offset-0 focus-visible:border-blue-400/60 focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 dark:border-transparent dark:bg-transparent dark:text-slate-100 dark:hover:border-slate-500/20 dark:hover:bg-white/[0.035] dark:focus:bg-slate-950/45 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none';
  const valueDisplayClass =
    'flex h-7 items-center justify-end rounded-md bg-transparent px-1.5 font-mono text-[13px] font-black text-slate-900 dark:text-slate-100';
  const levelValueInputClass = isDisabled
    ? `${valueInputClass} text-slate-500 dark:text-slate-500 hover:bg-transparent dark:hover:bg-transparent focus:bg-slate-950/30 dark:focus:bg-slate-950/30`
    : valueInputClass;
  const levelValueDisplayClass = isDisabled
    ? `${valueDisplayClass} text-slate-500 dark:text-slate-500`
    : valueDisplayClass;
  const priceValue = String(level.price ?? '');
  const sharesValue = String(level.plannedShares ?? '');
  const priceWidthCh = Math.max(5.5, priceValue.length + 1.25);
  const sharesWidthCh = Math.max(6.5, sharesValue.length + 1.25);

  return (
    <SortableGridLevelRow
      key={level.gridId}
      level={level}
      canDragLevel={editableLevel}
      isSell={isSell}
      isDisabled={isDisabled}
      pctFromBase={pctFromBase}
    >
      {(dragHandle, isDraggingLevel) => (
        <div
          className={`group/level rounded-xl border px-3 py-2 shadow-md transition-all ${
            isDisabled
              ? 'border-slate-700/45 bg-[linear-gradient(90deg,rgba(51,65,85,0.12),rgba(15,23,42,0.34))] shadow-none hover:border-slate-600/55'
              : isSell
                ? 'border-market-down/25 bg-market-down/[0.07] hover:border-market-down/35'
                : 'border-market-up/25 bg-market-up/[0.07] hover:border-market-up/35'
          } ${isDraggingLevel ? 'shadow-xl ring-2 ring-blue-400/35' : ''}`}
        >
          <div className="grid grid-cols-[22px_minmax(122px,0.72fr)_minmax(148px,0.42fr)_minmax(190px,0.96fr)] items-center gap-3 text-[10px] font-bold">
            <div className="flex justify-center">{dragHandle}</div>

            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className={`font-mono text-[13px] font-black ${
                    isDisabled
                      ? 'text-slate-400'
                      : isSell
                        ? 'text-market-down'
                        : 'text-market-up'
                  }`}
                >
                  {levelTitle}
                </span>
              </div>
              <div className="mt-1 flex min-w-0 items-center gap-2">
                <Badge
                  variant="outline"
                  className={`shrink-0 rounded-full px-2 py-0 text-[8px] font-black ${displayState.className}`}
                >
                  {displayState.label}
                </Badge>
                <span
                  className={`min-w-0 truncate text-[9px] ${
                    isDisabled
                      ? 'text-slate-600'
                      : reasonText
                        ? 'text-amber-300'
                        : 'text-slate-500'
                  }`}
                >
                  {reasonText ? reasonLabel(reasonText) : detailText}
                </span>
              </div>
            </div>

            <div className="flex w-fit max-w-[220px] items-center gap-4 justify-self-center">
              <label className="flex items-baseline gap-1.5">
                <span className="shrink-0 text-[8px] font-black text-slate-500">
                  价
                </span>
                {editableLevel ? (
                  <Input
                    type="number"
                    step="0.01"
                    value={level.price}
                    style={{ width: `calc(${priceWidthCh}ch + 1.25rem)` }}
                    onChange={event =>
                      updateLevel(level.gridId, {
                        price: Number(event.target.value || 0),
                      })
                    }
                    className={levelValueInputClass}
                  />
                ) : (
                  <span className={levelValueDisplayClass}>
                    {formatNumber(level.price)}
                  </span>
                )}
              </label>
              <label className="flex items-baseline gap-1.5">
                <span className="shrink-0 text-[8px] font-black text-slate-500">
                  股
                </span>
                {editableLevel ? (
                  <Input
                    type="number"
                    step="100"
                    value={level.plannedShares}
                    style={{ width: `calc(${sharesWidthCh}ch + 1.25rem)` }}
                    onChange={event =>
                      updateLevel(level.gridId, {
                        plannedShares: Number(event.target.value || 0),
                      })
                    }
                    className={levelValueInputClass}
                  />
                ) : (
                  <span className={levelValueDisplayClass}>
                    {formatNumber(level.plannedShares)}
                  </span>
                )}
              </label>
            </div>

            <div className="flex min-w-0 items-center justify-end gap-2">
              <div className="min-w-0 flex-1 space-y-0.5 text-right font-mono">
                <div className="truncate text-[10px] text-slate-400">
                  {metricLabel}{' '}
                  <span
                    className={`font-black ${
                      isDisabled ? 'text-slate-500' : 'text-slate-200'
                    }`}
                  >
                    {formatMoney(metricValue)}
                  </span>
                </div>
                <div
                  className={`truncate text-[11px] font-black ${
                    isDisabled
                      ? 'text-slate-500'
                      : isSell
                        ? 'text-market-down'
                        : 'text-market-up'
                  }`}
                >
                  {secondaryMetric}
                </div>
              </div>

              <div className="flex shrink-0 justify-end gap-1">
                {editable && (
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={locked}
                    className={`h-7 min-w-[38px] rounded-lg px-2 text-[10px] ${
                      isDisabled
                        ? 'text-blue-300 hover:bg-blue-500/10 hover:text-blue-200'
                        : 'text-slate-300 hover:bg-white/10 hover:text-white'
                    }`}
                    onClick={() =>
                      updateLevel(level.gridId, {
                        enabled: !level.enabled,
                        status: !level.enabled ? 'PLANNED' : 'DISABLED',
                      })
                    }
                  >
                    {level.enabled ? '禁用' : '启用'}
                  </Button>
                )}
                {editable && (
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={locked}
                    className="h-7 w-7 rounded-lg text-rose-400 opacity-0 transition-opacity hover:bg-rose-500/10 hover:text-rose-300 group-hover/level:opacity-100 group-focus-within/level:opacity-100"
                    onClick={() => deleteLevel(level.gridId)}
                    title="删除档位"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </SortableGridLevelRow>
  );
}
