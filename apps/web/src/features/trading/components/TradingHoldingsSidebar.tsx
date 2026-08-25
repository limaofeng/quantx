import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  type DragEndEvent,
  useSensor,
  useSensors,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  ArrowDownUp,
  BriefcaseBusiness,
  CandlestickChart,
  ExternalLink,
  GripVertical,
  Loader2,
  RefreshCw,
  Settings,
  TrendingDown,
  TrendingUp,
  Wallet,
} from 'lucide-react';
import * as React from 'react';
import { useQuery } from 'urql';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import type {
  PortfolioSummaryData,
  Position,
} from '@/features/portfolio/types';
import { gql } from '@/generated/gql';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';
import { formatPercent } from '@/utils/transform/data';

import { resolveHoldingInstrumentName } from './instrumentNameUtils';

interface TradingHoldingsSidebarProps {
  accountName: string;
  error?: unknown;
  holdings: Position[];
  isLoading: boolean;
  onAccountOpen: () => void;
  onHoldingOpenInNewWindow?: (holding: Position) => void;
  onHoldingSelect: (holding: Position) => void;
  onStockInfoOpen: (holding: Position) => void;
  onRefresh: () => void;
  portfolioSummary?: PortfolioSummaryData;
  selectedStockCode?: string;
  totalAsset?: number;
}

type HoldingSortKey =
  | 'MARKET_VALUE'
  | 'MARKET_VALUE_PERCENT'
  | 'PROFIT_LOSS'
  | 'PROFIT_RATE'
  | 'TODAY_PROFIT_LOSS'
  | 'TODAY_PROFIT_RATE'
  | 'DAY_CHANGE_PERCENT'
  | 'VOLUME'
  | 'AVAILABLE_VOLUME'
  | 'MANUAL';
type HoldingSortDirection = 'ASC' | 'DESC';

interface HoldingSortPreference {
  direction: HoldingSortDirection;
  manualOrder: string[];
  sortKey: HoldingSortKey;
}

const HOLDING_SORT_PREFERENCE_STORAGE_KEY =
  'quantx.tradingHoldingsSidebar.sortPreference.v1';

const TradingHoldingInstrumentNameQuery = gql(`
  query Trading_HoldingInstrumentName($stockCode: String!) {
    instrument(stockCode: $stockCode) {
      id
      name
    }
  }
`);

const holdingSortOptions: Array<{
  id: HoldingSortKey;
  label: string;
  shortLabel: string;
}> = [
  { id: 'MARKET_VALUE', label: '市值', shortLabel: '市值' },
  { id: 'MARKET_VALUE_PERCENT', label: '仓位', shortLabel: '仓位' },
  { id: 'PROFIT_LOSS', label: '持仓盈亏', shortLabel: '盈亏' },
  { id: 'PROFIT_RATE', label: '持仓收益率', shortLabel: '收益率' },
  { id: 'TODAY_PROFIT_LOSS', label: '当日盈亏', shortLabel: '当日盈亏' },
  { id: 'TODAY_PROFIT_RATE', label: '当日收益率', shortLabel: '当日收益率' },
  { id: 'DAY_CHANGE_PERCENT', label: '实时涨跌幅', shortLabel: '涨跌幅' },
  { id: 'VOLUME', label: '持仓数量', shortLabel: '数量' },
  { id: 'AVAILABLE_VOLUME', label: '可用数量', shortLabel: '可用' },
  { id: 'MANUAL', label: '手动排序', shortLabel: '手动' },
];

function normalizeStockCode(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

interface HoldingInstrumentNameProps {
  className?: string;
  positionName?: string | null;
  stockCode: string;
}

function CatalogInstrumentName({
  className,
  positionName,
  stockCode,
}: HoldingInstrumentNameProps) {
  const [{ data }] = useQuery({
    query: TradingHoldingInstrumentNameQuery,
    variables: { stockCode },
  });
  const instrumentName = resolveHoldingInstrumentName(
    stockCode,
    positionName,
    data?.instrument?.name
  );

  return <span className={className}>{instrumentName}</span>;
}

function HoldingInstrumentName({
  className,
  positionName,
  stockCode,
}: HoldingInstrumentNameProps) {
  const instrumentName = resolveHoldingInstrumentName(stockCode, positionName);
  if (instrumentName !== stockCode) {
    return <span className={className}>{instrumentName}</span>;
  }

  return (
    <CatalogInstrumentName
      className={className}
      positionName={positionName}
      stockCode={stockCode}
    />
  );
}

function getHoldingSortId(holding: Position) {
  return normalizeStockCode(holding.stockCode) || String(holding.id ?? '');
}

function isHoldingSortKey(value: unknown): value is HoldingSortKey {
  return holdingSortOptions.some(option => option.id === value);
}

function getHoldingSortOption(sortKey: HoldingSortKey) {
  return (
    holdingSortOptions.find(option => option.id === sortKey) ??
    holdingSortOptions[0]
  );
}

function getHoldingSortValue(holding: Position, sortKey: HoldingSortKey) {
  switch (sortKey) {
    case 'AVAILABLE_VOLUME':
      return toFiniteNumber(holding.canUseVolume);
    case 'DAY_CHANGE_PERCENT':
      return (
        toFiniteNumber(holding.changePercent) ??
        toFiniteNumber(holding.todayProfitRate)
      );
    case 'MARKET_VALUE':
      return toFiniteNumber(holding.marketValue);
    case 'MARKET_VALUE_PERCENT':
      return toFiniteNumber(holding.marketValuePercent);
    case 'PROFIT_LOSS':
      return toFiniteNumber(holding.profitLoss);
    case 'PROFIT_RATE':
      return toFiniteNumber(holding.profitRate);
    case 'TODAY_PROFIT_LOSS':
      return toFiniteNumber(holding.todayProfitLoss);
    case 'TODAY_PROFIT_RATE':
      return toFiniteNumber(holding.todayProfitRate);
    case 'VOLUME':
      return toFiniteNumber(holding.volume);
    case 'MANUAL':
      return null;
  }
}

function compareHoldingText(left: Position, right: Position) {
  const leftName = left.instrumentName || normalizeStockCode(left.stockCode);
  const rightName = right.instrumentName || normalizeStockCode(right.stockCode);
  const nameCompare = leftName.localeCompare(rightName, 'zh-CN');
  if (nameCompare !== 0) return nameCompare;
  return normalizeStockCode(left.stockCode).localeCompare(
    normalizeStockCode(right.stockCode),
    'zh-CN'
  );
}

function compareHoldingsByField(
  left: Position,
  right: Position,
  sortKey: HoldingSortKey,
  direction: HoldingSortDirection
) {
  const leftValue = getHoldingSortValue(left, sortKey);
  const rightValue = getHoldingSortValue(right, sortKey);

  if (leftValue === null && rightValue === null) {
    return compareHoldingText(left, right);
  }
  if (leftValue === null) return 1;
  if (rightValue === null) return -1;
  if (leftValue !== rightValue) {
    const valueCompare = leftValue - rightValue;
    return direction === 'ASC' ? valueCompare : -valueCompare;
  }

  const marketValueCompare =
    (toFiniteNumber(right.marketValue) ?? 0) -
    (toFiniteNumber(left.marketValue) ?? 0);
  if (marketValueCompare !== 0) return marketValueCompare;
  return compareHoldingText(left, right);
}

function getStockIconText(name: string) {
  if (!name) return '--';
  if (name.length === 1) return name;
  return `${name.charAt(0)}${name.charAt(name.length - 1)}`;
}

function formatCompactCurrency(value?: number | null) {
  const amount = Number(value ?? 0);
  const abs = Math.abs(amount);
  const sign = amount < 0 ? '-' : '';

  if (abs >= 100000000) return `${sign}¥${(abs / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${sign}¥${(abs / 10000).toFixed(2)}万`;
  return `${sign}¥${abs.toFixed(2)}`;
}

function formatCompactCurrencyOrDash(value?: number | null) {
  const amount = toFiniteNumber(value);
  return amount === null ? '--' : formatCompactCurrency(amount);
}

function formatSignedCurrency(value?: number | null) {
  const amount = Number(value ?? 0);
  return `${amount >= 0 ? '+' : ''}${formatCompactCurrency(amount)}`;
}

function formatShares(value?: number | null) {
  return Number(value ?? 0).toLocaleString('zh-CN');
}

function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatPercentOrDash(value: number | null) {
  return value === null ? '--' : formatPercent(value);
}

function HoldingsSkeleton() {
  return (
    <div className="space-y-2 px-2 py-1">
      {Array.from({ length: 5 }).map((_, index) => (
        <div
          key={index}
          className="h-[86px] animate-pulse rounded-md border border-white/5 bg-white/[0.035]"
        />
      ))}
    </div>
  );
}

function EmptyHoldingsState() {
  return (
    <div className="mx-2 mt-6 rounded-md border border-dashed border-white/10 bg-white/[0.025] px-ui-section py-ui-panel text-center">
      <BriefcaseBusiness className="mx-auto h-5 w-5 text-slate-600" />
      <div className="mt-3 text-ui-label font-black text-slate-300">
        暂无持仓
      </div>
      <div className="mt-1 text-ui-caption leading-relaxed text-slate-600">
        当前账户没有可展示的持仓股票。
      </div>
    </div>
  );
}

function readHoldingSortPreference() {
  const fallback: HoldingSortPreference = {
    direction: 'DESC',
    manualOrder: [],
    sortKey: 'MARKET_VALUE',
  };

  if (typeof window === 'undefined') {
    return fallback;
  }

  try {
    const raw = window.localStorage.getItem(
      HOLDING_SORT_PREFERENCE_STORAGE_KEY
    );
    if (!raw) {
      return fallback;
    }

    const parsed = JSON.parse(raw) as {
      direction?: unknown;
      manualOrder?: unknown;
      mode?: unknown;
      sortKey?: unknown;
    };
    const legacyMode = parsed.mode === 'MANUAL' ? 'MANUAL' : 'MARKET_VALUE';
    const sortKey = isHoldingSortKey(parsed.sortKey)
      ? parsed.sortKey
      : legacyMode;

    return {
      direction: parsed.direction === 'ASC' ? 'ASC' : 'DESC',
      manualOrder: Array.isArray(parsed.manualOrder)
        ? parsed.manualOrder
            .map(item => normalizeStockCode(item))
            .filter(Boolean)
        : [],
      sortKey,
    } satisfies HoldingSortPreference;
  } catch {
    return fallback;
  }
}

function writeHoldingSortPreference(
  sortKey: HoldingSortKey,
  direction: HoldingSortDirection,
  manualOrder: string[]
) {
  if (typeof window === 'undefined') return;

  try {
    window.localStorage.setItem(
      HOLDING_SORT_PREFERENCE_STORAGE_KEY,
      JSON.stringify({ direction, manualOrder, sortKey })
    );
  } catch {
    // Sorting is still usable if local storage is unavailable.
  }
}

function SortableHoldingItem({
  children,
  holdingName,
  id,
}: {
  children: React.ReactNode;
  holdingName: string;
  id: string;
}) {
  const {
    attributes,
    isDragging,
    listeners,
    setNodeRef,
    transform,
    transition,
  } = useSortable({ id });

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    position: isDragging ? 'relative' : undefined,
    zIndex: isDragging ? 30 : undefined,
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        'flex min-w-0 items-stretch gap-1.5',
        isDragging && 'opacity-80'
      )}
    >
      <button
        type="button"
        aria-label={`拖拽持仓 ${holdingName}`}
        title="拖拽排序"
        className="flex w-6 shrink-0 cursor-grab touch-none items-center justify-center rounded-md border border-white/5 bg-white/[0.02] text-slate-600 transition-colors hover:border-blue-500/40 hover:bg-blue-500/10 hover:text-blue-200 active:cursor-grabbing focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        {...attributes}
        {...(listeners || {})}
      >
        <GripVertical className="h-3.5 w-3.5" />
      </button>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function TradingHoldingsSidebar({
  accountName,
  error,
  holdings,
  isLoading,
  onAccountOpen,
  onHoldingOpenInNewWindow,
  onHoldingSelect,
  onStockInfoOpen,
  onRefresh,
  portfolioSummary,
  selectedStockCode,
  totalAsset,
}: TradingHoldingsSidebarProps) {
  const { closeMenu, menu, openAtPointer } = useStudioMenu<Position>();
  const [sortPreference, setSortPreference] = React.useState(
    readHoldingSortPreference
  );
  const [isManualSortDialogOpen, setIsManualSortDialogOpen] =
    React.useState(false);
  const sortKey = sortPreference.sortKey;
  const sortDirection = sortPreference.direction;
  const manualOrder = sortPreference.manualOrder;
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const normalizedSelectedStockCode = normalizeStockCode(selectedStockCode);
  const marketValueSortedHoldings = React.useMemo(
    () =>
      [...holdings].sort(
        (left, right) => (right.marketValue ?? 0) - (left.marketValue ?? 0)
      ),
    [holdings]
  );
  const sortedHoldings = React.useMemo(() => {
    if (sortKey !== 'MANUAL') {
      return [...holdings].sort((left, right) =>
        compareHoldingsByField(left, right, sortKey, sortDirection)
      );
    }

    const manualIndexById = new Map(
      manualOrder.map((holdingId, index) => [holdingId, index])
    );
    return [...marketValueSortedHoldings].sort((left, right) => {
      const leftIndex = manualIndexById.get(getHoldingSortId(left));
      const rightIndex = manualIndexById.get(getHoldingSortId(right));
      if (leftIndex !== undefined || rightIndex !== undefined) {
        return (
          (leftIndex ?? Number.MAX_SAFE_INTEGER) -
          (rightIndex ?? Number.MAX_SAFE_INTEGER)
        );
      }
      return (right.marketValue ?? 0) - (left.marketValue ?? 0);
    });
  }, [
    manualOrder,
    marketValueSortedHoldings,
    holdings,
    sortDirection,
    sortKey,
  ]);
  const sortedHoldingIds = React.useMemo(
    () => sortedHoldings.map(getHoldingSortId).filter(Boolean),
    [sortedHoldings]
  );
  const totalMarketValue =
    portfolioSummary?.totalMarketValue ??
    holdings.reduce((sum, holding) => sum + (holding.marketValue ?? 0), 0);
  const displayTotalAsset = totalAsset ?? portfolioSummary?.totalAsset;
  const hasError = Boolean(error);
  const isManualSortMode = sortKey === 'MANUAL';
  const selectedSortOption = getHoldingSortOption(sortKey);

  React.useEffect(() => {
    writeHoldingSortPreference(sortKey, sortDirection, manualOrder);
  }, [manualOrder, sortDirection, sortKey]);

  const handleSortKeyChange = React.useCallback((value: string) => {
    if (!isHoldingSortKey(value)) return;
    setSortPreference(prev => ({
      ...prev,
      sortKey: value,
    }));
  }, []);

  const handleSortDirectionChange = React.useCallback((value: string) => {
    if (value !== 'ASC' && value !== 'DESC') return;
    setSortPreference(prev => ({
      ...prev,
      direction: value,
    }));
  }, []);

  const handleManualDragEnd = React.useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;

      const activeId = String(active.id);
      const overId = String(over.id);
      const oldIndex = sortedHoldingIds.indexOf(activeId);
      const newIndex = sortedHoldingIds.indexOf(overId);
      if (oldIndex < 0 || newIndex < 0) return;

      setSortPreference(prev => ({
        ...prev,
        manualOrder: arrayMove(sortedHoldingIds, oldIndex, newIndex),
        sortKey: 'MANUAL',
      }));
    },
    [sortedHoldingIds]
  );

  const handleManualSettingsOpenChange = React.useCallback((open: boolean) => {
    setIsManualSortDialogOpen(open);
  }, []);

  const renderHoldingCard = (holding: Position) => {
    const stockCode = normalizeStockCode(holding.stockCode);
    const stockName = resolveHoldingInstrumentName(
      stockCode,
      holding.instrumentName
    );
    const isSelected = stockCode === normalizedSelectedStockCode;
    const profitLoss = holding.profitLoss ?? 0;
    const averageCost = toFiniteNumber(holding.avgPrice);
    const volume = toFiniteNumber(holding.volume);
    const costAmount =
      averageCost !== null && volume !== null ? averageCost * volume : null;
    const holdingReturnRate =
      toFiniteNumber(holding.profitRate) ??
      (costAmount !== null && costAmount > 0
        ? (profitLoss / costAmount) * 100
        : null);
    const dayChangePercent =
      toFiniteNumber(holding.changePercent) ??
      toFiniteNumber(holding.todayProfitRate);
    const isDayChangePositive =
      dayChangePercent !== null && dayChangePercent >= 0;
    const ToneIcon =
      dayChangePercent === null
        ? null
        : isDayChangePositive
          ? TrendingUp
          : TrendingDown;
    const metricRows = [
      [
        {
          label: '数量',
          value: formatShares(holding.volume),
          valueClassName: 'text-slate-300',
        },
        {
          label: '可用',
          value: formatShares(holding.canUseVolume),
          valueClassName: 'text-slate-300',
        },
        {
          label: '持有收益',
          value: formatPercentOrDash(holdingReturnRate),
          valueClassName: financialToneClass(holdingReturnRate, 'holding'),
        },
      ],
      [
        {
          label: '成本额',
          value: formatCompactCurrencyOrDash(costAmount),
          valueClassName: 'text-slate-300',
        },
        {
          label: '市值',
          value: formatCompactCurrencyOrDash(holding.marketValue),
          valueClassName: 'text-slate-300',
        },
        {
          label: '盈亏',
          value: formatSignedCurrency(profitLoss),
          valueClassName: financialToneClass(profitLoss, 'holding'),
        },
      ],
    ];

    return (
      <button
        key={holding.id}
        type="button"
        onClick={() => onHoldingSelect(holding)}
        onContextMenu={event => openAtPointer(event, holding)}
        className={cn(
          'group w-full rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
          isSelected
            ? 'border-blue-500/30 bg-blue-500/10 text-blue-100'
            : 'border-white/5 bg-white/[0.025] text-slate-300 hover:border-blue-500/40 hover:bg-white/[0.055] hover:text-slate-100'
        )}
        aria-current={isSelected ? 'true' : undefined}
      >
        <div className="flex min-w-0 items-start gap-2.5">
          <div
            className={cn(
              'flex h-8 w-8 shrink-0 items-center justify-center rounded-md border text-ui-caption font-black',
              isSelected
                ? 'border-blue-500/30 bg-blue-500/15 text-blue-200'
                : 'border-white/10 bg-[#08101d] text-slate-400 group-hover:text-slate-200'
            )}
          >
            {getStockIconText(stockName)}
          </div>
          <div className="flex min-w-0 flex-1 items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <HoldingInstrumentName
                className="block min-w-0 truncate text-ui-label font-black"
                positionName={holding.instrumentName}
                stockCode={stockCode}
              />
              <div className="mt-0.5 flex min-w-0 items-center gap-1.5">
                <span className="truncate font-mono text-ui-caption font-bold text-slate-600">
                  {stockCode}
                </span>
              </div>
            </div>
            <div className="shrink-0 space-y-1 text-right font-mono leading-none">
              <div
                className={cn(
                  'inline-flex items-center justify-end gap-1 font-mono text-ui-caption font-black leading-none',
                  dayChangePercent === null
                    ? 'text-slate-500'
                    : financialToneClass(dayChangePercent, 'holding')
                )}
              >
                {ToneIcon && <ToneIcon className="h-3 w-3" />}
                {formatPercentOrDash(dayChangePercent)}
              </div>
              <div className="flex items-baseline justify-end gap-2">
                <span className="inline-flex items-baseline gap-1">
                  <span className="text-ui-micro font-black leading-none text-slate-600">
                    现价
                  </span>
                  <span className="text-ui-micro font-black leading-none text-slate-300">
                    {formatCompactCurrencyOrDash(holding.lastPrice)}
                  </span>
                </span>
                <span className="inline-flex items-baseline gap-1">
                  <span className="text-ui-micro font-black leading-none text-slate-600">
                    成本
                  </span>
                  <span className="text-ui-micro font-bold leading-none text-slate-500">
                    {formatCompactCurrencyOrDash(holding.avgPrice)}
                  </span>
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="mt-2 rounded border border-white/5 bg-[#08101d]/70 px-2 py-1.5">
          <div className="space-y-1">
            {metricRows.map((metrics, rowIndex) => (
              <div
                key={rowIndex}
                className="grid min-w-0 grid-cols-3 items-baseline gap-x-2"
              >
                {metrics.map((metric, metricIndex) => (
                  <div
                    key={metric.label}
                    className={cn(
                      'flex min-w-0 items-baseline gap-1 leading-none',
                      metricIndex === 1 && 'justify-center',
                      metricIndex === 2 && 'justify-end'
                    )}
                  >
                    <span className="shrink-0 text-ui-micro font-black leading-none tracking-wider text-slate-600">
                      {metric.label}
                    </span>
                    <span
                      className={cn(
                        'min-w-0 max-w-[4.75rem] truncate font-mono text-ui-caption font-bold leading-none',
                        metric.valueClassName
                      )}
                    >
                      {metric.value}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      </button>
    );
  };

  return (
    <>
      <aside className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
        <div className="shrink-0 border-b border-white/5 px-ui-section py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <div className="text-ui-caption font-black uppercase tracking-[0.24em] text-blue-400">
                Holdings
              </div>
              <div className="mt-1 truncate text-ui-body font-black text-slate-100">
                持仓
              </div>
            </div>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={onRefresh}
              className="h-control-compact w-8 shrink-0 rounded-md border border-white/10 text-slate-500 transition-colors hover:border-blue-500/30 hover:bg-blue-500/10 hover:text-blue-200"
              aria-label="刷新持仓"
              title="刷新持仓"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </Button>
          </div>

          <div className="mt-3 grid grid-cols-2 gap-2">
            <div className="rounded-md border border-white/5 bg-white/[0.03] px-2.5 py-2">
              <div className="text-ui-micro font-black uppercase tracking-[0.18em] text-slate-600">
                总资产
              </div>
              <div className="mt-1 truncate font-mono text-ui-caption font-black text-slate-200">
                {displayTotalAsset === undefined
                  ? '读取中'
                  : formatCompactCurrency(displayTotalAsset)}
              </div>
            </div>
            <div className="rounded-md border border-white/5 bg-white/[0.03] px-2.5 py-2">
              <div className="text-ui-micro font-black uppercase tracking-[0.18em] text-slate-600">
                持仓市值
              </div>
              <div className="mt-1 truncate font-mono text-ui-caption font-black text-slate-200">
                {formatCompactCurrency(totalMarketValue)}
              </div>
            </div>
          </div>
        </div>

        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          <div className="flex h-8 shrink-0 items-center justify-between border-b border-white/5 px-ui-section">
            <span className="text-ui-caption font-black uppercase tracking-[0.2em] text-slate-600">
              持仓
            </span>
            <div className="flex items-center gap-1.5">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label="选择持仓排序"
                    title="选择持仓排序"
                    className={cn(
                      'h-6 rounded-md border px-1.5 text-ui-caption font-black transition-colors',
                      isManualSortMode
                        ? 'border-blue-500/30 bg-blue-500/10 text-blue-200 hover:bg-blue-500/10'
                        : 'border-white/5 bg-white/[0.025] text-slate-500 hover:border-blue-500/40 hover:bg-blue-500/10 hover:text-blue-200'
                    )}
                  >
                    {isManualSortMode ? (
                      <GripVertical className="mr-1 h-3 w-3" />
                    ) : (
                      <ArrowDownUp className="mr-1 h-3 w-3" />
                    )}
                    {selectedSortOption.shortLabel}
                    {!isManualSortMode && (
                      <span className="ml-1 font-mono text-ui-micro">
                        {sortDirection === 'ASC' ? '↑' : '↓'}
                      </span>
                    )}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  className="w-40 border-white/10 bg-[#0b1120] text-slate-200"
                >
                  <DropdownMenuLabel className="px-2 py-1 text-ui-caption font-black text-slate-500">
                    排序字段
                  </DropdownMenuLabel>
                  <DropdownMenuRadioGroup
                    value={sortKey}
                    onValueChange={handleSortKeyChange}
                  >
                    {holdingSortOptions.map(option => (
                      <DropdownMenuRadioItem
                        key={option.id}
                        value={option.id}
                        className="text-ui-caption"
                      >
                        {option.label}
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                  <DropdownMenuSeparator className="bg-white/10" />
                  {isManualSortMode ? (
                    <>
                      <DropdownMenuLabel className="px-2 py-1 text-ui-caption font-black text-slate-500">
                        手动排序
                      </DropdownMenuLabel>
                      <DropdownMenuItem
                        onSelect={() => setIsManualSortDialogOpen(true)}
                        className="text-ui-caption"
                      >
                        <Settings className="h-3.5 w-3.5" />
                        编辑手动顺序
                      </DropdownMenuItem>
                    </>
                  ) : (
                    <>
                      <DropdownMenuLabel className="px-2 py-1 text-ui-caption font-black text-slate-500">
                        排序方向
                      </DropdownMenuLabel>
                      <DropdownMenuRadioGroup
                        value={sortDirection}
                        onValueChange={handleSortDirectionChange}
                      >
                        <DropdownMenuRadioItem
                          value="DESC"
                          className="text-ui-caption"
                        >
                          降序优先
                        </DropdownMenuRadioItem>
                        <DropdownMenuRadioItem
                          value="ASC"
                          className="text-ui-caption"
                        >
                          升序优先
                        </DropdownMenuRadioItem>
                      </DropdownMenuRadioGroup>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              <span className="font-mono text-ui-caption font-bold text-slate-500">
                {sortedHoldings.length} 只
              </span>
            </div>
          </div>

          <div className="custom-scrollbar min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-contain py-2">
            {isLoading ? (
              <HoldingsSkeleton />
            ) : sortedHoldings.length === 0 ? (
              <EmptyHoldingsState />
            ) : (
              <div className="space-y-1.5 px-2">
                {sortedHoldings.map(holding => renderHoldingCard(holding))}
              </div>
            )}

            {hasError && (
              <div className="mx-2 mt-2 rounded-md border border-amber-400/20 bg-amber-500/10 px-3 py-2 text-ui-caption font-bold leading-relaxed text-amber-200/80">
                持仓数据读取异常，已保留当前可用数据。
              </div>
            )}
          </div>
        </div>

        <div className="shrink-0 border-t border-white/5 p-3">
          <button
            type="button"
            onClick={onAccountOpen}
            className="flex w-full items-center gap-3 rounded-md border border-white/10 px-2.5 py-2 text-left text-slate-400 transition-colors hover:border-blue-500/40 hover:bg-blue-500/10 hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            <Wallet className="h-4 w-4 shrink-0" />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-ui-label font-bold">
                {accountName}
              </span>
              <span className="mt-0.5 block truncate font-mono text-ui-caption text-slate-600">
                {displayTotalAsset === undefined
                  ? '资产读取中'
                  : formatCompactCurrency(displayTotalAsset)}
              </span>
            </span>
            {isLoading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          </button>
        </div>

        <StudioMenu
          ariaLabel="持仓菜单"
          items={[
            {
              icon: <BriefcaseBusiness size={14} />,
              id: 'select-holding',
              label: '切换到该持仓',
              onSelect: () => {
                if (menu?.payload) onHoldingSelect(menu.payload);
              },
            },
            {
              icon: <CandlestickChart size={14} />,
              id: 'open-stock-info',
              label: '查看股票信息',
              onSelect: () => {
                if (menu?.payload) onStockInfoOpen(menu.payload);
              },
            },
            ...(onHoldingOpenInNewWindow
              ? [
                  {
                    icon: <ExternalLink size={14} />,
                    id: 'open-liquidation-new-tab',
                    label: '新窗口打开清仓',
                    onSelect: () => {
                      if (menu?.payload) onHoldingOpenInNewWindow(menu.payload);
                    },
                  },
                ]
              : []),
          ]}
          menu={menu}
          onClose={closeMenu}
          width={180}
        />
      </aside>

      <Dialog
        open={isManualSortDialogOpen}
        onOpenChange={handleManualSettingsOpenChange}
      >
        <DialogContent className="border-white/10 bg-[#0b1120] p-0 text-slate-100 sm:max-w-md">
          <DialogHeader className="border-b border-white/5 px-ui-section py-3">
            <DialogTitle className="text-ui-body font-black text-slate-100">
              设置手动排序
            </DialogTitle>
            <DialogDescription className="text-ui-caption text-slate-500">
              拖拽调整左侧持仓列表顺序，设置会保存在本机。
            </DialogDescription>
          </DialogHeader>

          <div className="max-h-[62vh] overflow-y-auto px-3 pb-3 custom-scrollbar">
            {sortedHoldings.length === 0 ? (
              <div className="rounded-md border border-dashed border-white/10 px-ui-section py-ui-panel text-center text-ui-label font-bold text-slate-500">
                暂无可排序持仓。
              </div>
            ) : (
              <DndContext
                sensors={sensors}
                collisionDetection={closestCenter}
                onDragEnd={handleManualDragEnd}
              >
                <SortableContext
                  items={sortedHoldingIds}
                  strategy={verticalListSortingStrategy}
                >
                  <div className="space-y-1.5 pt-3">
                    {sortedHoldings.map((holding, index) => {
                      const sortId = getHoldingSortId(holding);
                      const stockCode = normalizeStockCode(holding.stockCode);
                      const stockName = resolveHoldingInstrumentName(
                        stockCode,
                        holding.instrumentName
                      );
                      return (
                        <SortableHoldingItem
                          key={sortId}
                          id={sortId}
                          holdingName={stockName}
                        >
                          <div className="flex min-w-0 items-center gap-2 rounded-md border border-white/5 bg-white/[0.025] px-2.5 py-2">
                            <span className="w-5 shrink-0 text-right font-mono text-ui-caption font-black text-slate-600">
                              {index + 1}
                            </span>
                            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-[#08101d] text-ui-micro font-black text-slate-400">
                              {getStockIconText(stockName)}
                            </div>
                            <div className="min-w-0 flex-1">
                              <HoldingInstrumentName
                                className="block truncate text-ui-label font-black text-slate-200"
                                positionName={holding.instrumentName}
                                stockCode={stockCode}
                              />
                              <div className="truncate font-mono text-ui-caption font-bold text-slate-600">
                                {stockCode}
                              </div>
                            </div>
                            <div className="shrink-0 text-right font-mono text-ui-caption font-black text-slate-400">
                              {formatCompactCurrencyOrDash(holding.marketValue)}
                            </div>
                          </div>
                        </SortableHoldingItem>
                      );
                    })}
                  </div>
                </SortableContext>
              </DndContext>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
