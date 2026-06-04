import {
  AlertTriangle,
  ArrowDownIcon,
  ArrowDownZA,
  ArrowUpAZ,
  ArrowUpIcon,
  Check,
  Copy,
  Download,
  Eye,
  GripVertical,
  LayoutList,
  MoreHorizontal,
  MoreVertical,
  Pin,
  PinOff,
  RotateCcw,
  Search,
  X,
  Zap,
} from 'lucide-react';
import {
  type CSSProperties,
  type DragEvent,
  type MouseEvent,
  type PointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import {
  type StockScreenSortDirection,
  type StockScreenSortField,
  type StockScreenSortState,
  type StockScreeningMeta,
  type StockScreeningResult,
} from '../types';

interface ScreeningResultsProps {
  screeningLoading: boolean;
  results?: StockScreeningResult[];
  meta?: StockScreeningMeta;
  error?: string;
  sort?: StockScreenSortState | null;
  onSortChange?: (sort: StockScreenSortState | null) => void;
}

interface ScreeningColumn {
  align?: 'center' | 'left' | 'right';
  alwaysFrozen?: boolean;
  defaultDirection?: StockScreenSortDirection;
  id: string;
  label: string;
  locked?: boolean;
  sortField?: StockScreenSortField;
  width: number;
  widthClass: string;
}

const SORTABLE_COLUMNS: ScreeningColumn[] = [
  {
    alwaysFrozen: true,
    defaultDirection: 'ASC',
    id: 'identity',
    label: '代码 / 名称',
    sortField: 'CODE',
    width: 260,
    widthClass: 'min-w-[260px] w-[260px]',
  },
  {
    align: 'right',
    id: 'price',
    label: '价格',
    sortField: 'CURRENT_PRICE',
    width: 110,
    widthClass: 'min-w-[110px] w-[110px]',
  },
  {
    align: 'right',
    id: 'changePct',
    label: '涨跌幅',
    sortField: 'CHANGE_PCT',
    width: 120,
    widthClass: 'min-w-[120px] w-[120px]',
  },
  {
    id: 'signals',
    label: '信号',
    sortField: 'SIGNAL_COUNT',
    width: 340,
    widthClass: 'min-w-[340px] w-[340px]',
  },
  {
    align: 'center',
    id: 'kdj',
    label: 'KDJ (9,3,3)',
    sortField: 'KDJ_J',
    width: 150,
    widthClass: 'min-w-[150px] w-[150px]',
  },
  {
    align: 'center',
    id: 'rsi',
    label: 'RSI (6/12/24)',
    sortField: 'RSI12',
    width: 150,
    widthClass: 'min-w-[150px] w-[150px]',
  },
  {
    align: 'center',
    id: 'volumeRatio',
    label: '量比',
    sortField: 'VOLUME_RATIO',
    width: 100,
    widthClass: 'min-w-[100px] w-[100px]',
  },
  {
    align: 'right',
    defaultDirection: 'ASC',
    id: 'drawdown',
    label: '距高回撤',
    sortField: 'PRICE_DROP_PCT',
    width: 120,
    widthClass: 'min-w-[120px] w-[120px]',
  },
  {
    align: 'right',
    defaultDirection: 'ASC',
    id: 'daysSincePeak',
    label: '高点天数',
    sortField: 'DAYS_SINCE_PEAK',
    width: 110,
    widthClass: 'min-w-[110px] w-[110px]',
  },
  {
    align: 'right',
    id: 'roe',
    label: 'ROE',
    sortField: 'ROE',
    width: 90,
    widthClass: 'min-w-[90px] w-[90px]',
  },
  {
    align: 'right',
    id: 'netProfitGrowth',
    label: '净利单季同比',
    sortField: 'NET_PROFIT_GROWTH',
    width: 132,
    widthClass: 'min-w-[132px] w-[132px]',
  },
  {
    align: 'right',
    id: 'yoyGrowth',
    label: '营收单季同比',
    sortField: 'YOY_GROWTH',
    width: 132,
    widthClass: 'min-w-[132px] w-[132px]',
  },
  {
    align: 'right',
    id: 'actions',
    label: '',
    locked: true,
    width: 46,
    widthClass: 'min-w-[46px] w-[46px]',
  },
];

const DEFAULT_FROZEN_COLUMN_IDS = ['identity'] as const;

const OPPOSITE_DIRECTION: Record<
  StockScreenSortDirection,
  StockScreenSortDirection
> = {
  ASC: 'DESC',
  DESC: 'ASC',
};

const SCROLLBAR_ACTIVE_CLASS = 'scrollbar-active';
const SCROLLBAR_HIDE_DELAY_MS = 1000;

interface GridDragScrollState {
  isDragging: boolean;
  pointerId: number;
  previousCursor: string;
  previousUserSelect: string;
  startScrollLeft: number;
  startX: number;
}

export function ScreeningResults({
  screeningLoading,
  results,
  meta,
  error,
  sort,
  onSortChange,
}: ScreeningResultsProps) {
  const { toast } = useToast();
  const [, setLocation] = useLocation();
  const {
    closeMenu,
    menu: rowMenu,
    openAtPointer,
    openFromElement: openRowMenuFromElement,
  } = useStudioMenu<StockScreeningResult>();
  const {
    closeMenu: closeSortMenu,
    menu: sortMenu,
    openAtPointer: openColumnMenuAtPointer,
    openFromElement: openSortMenuFromElement,
  } = useStudioMenu<ScreeningColumn>();
  const [columnOrder, setColumnOrder] = useState(() =>
    SORTABLE_COLUMNS.map(column => column.id)
  );
  const [frozenColumnIds, setFrozenColumnIds] = useState<Set<string>>(
    () => new Set(DEFAULT_FROZEN_COLUMN_IDS)
  );
  const [draggingColumnId, setDraggingColumnId] = useState<string | null>(null);
  const [isGridDragScrolling, setIsGridDragScrolling] = useState(false);
  const [isGridScrollbarActive, setIsGridScrollbarActive] = useState(false);
  const gridDragScrollRef = useRef<GridDragScrollState | null>(null);
  const gridScrollbarTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (gridScrollbarTimerRef.current !== null) {
        window.clearTimeout(gridScrollbarTimerRef.current);
      }
    };
  }, []);

  const displayData =
    results && results.length > 0 ? results : screeningLoading ? [] : [];

  const orderedColumns = useMemo(() => {
    const columnsById = new Map(
      SORTABLE_COLUMNS.map(column => [column.id, column])
    );
    const normalizedIds = [
      ...columnOrder.filter(id => columnsById.has(id)),
      ...SORTABLE_COLUMNS.map(column => column.id).filter(
        id => !columnOrder.includes(id)
      ),
    ];
    const columns = normalizedIds
      .map(id => columnsById.get(id))
      .filter((column): column is ScreeningColumn => Boolean(column));
    const lockedColumns = columns.filter(column => column.locked);
    const movableColumns = columns.filter(column => !column.locked);
    const frozenColumns = movableColumns.filter(
      column => column.alwaysFrozen || frozenColumnIds.has(column.id)
    );
    const regularColumns = movableColumns.filter(
      column => !column.alwaysFrozen && !frozenColumnIds.has(column.id)
    );

    return [...frozenColumns, ...regularColumns, ...lockedColumns];
  }, [columnOrder, frozenColumnIds]);

  const frozenColumnOffsets = useMemo(() => {
    const offsets = new Map<string, number>();
    let left = 0;

    orderedColumns.forEach(column => {
      if (!column.alwaysFrozen && !frozenColumnIds.has(column.id)) return;
      offsets.set(column.id, left);
      left += column.width;
    });

    return offsets;
  }, [frozenColumnIds, orderedColumns]);

  const lastFrozenColumnId = useMemo(() => {
    const frozenColumns = orderedColumns.filter(
      column => column.alwaysFrozen || frozenColumnIds.has(column.id)
    );
    return frozenColumns[frozenColumns.length - 1]?.id;
  }, [frozenColumnIds, orderedColumns]);

  const handleExport = () => {
    toast({
      title: '正在生成导出文件...',
      description: '结果将以 CSV 格式下载',
    });
  };

  const handleAction = (action: string, code: string, name: string) => {
    toast({
      title: '操作已记录',
      description: `已请求 ${action}: ${name} (${code})`,
    });
  };

  const handleAddWatchlist = async (stock: StockScreeningResult) => {
    const result = await addWatchlistItem({
      stockCode: stock.code,
      instrumentName: stock.name,
    });

    toast({
      title: result?.success ? '已加入自选' : '加入自选失败',
      description:
        result?.message ||
        `${stock.name} (${stock.code}) 已提交到后端自选池`,
      variant: result?.success === false ? 'destructive' : 'default',
    });
  };

  const copyText = (text: string) => {
    if (!navigator.clipboard) return;
    void navigator.clipboard.writeText(text);
  };

  const isFrozenColumn = (column: ScreeningColumn) =>
    column.alwaysFrozen || frozenColumnIds.has(column.id);

  const canMoveColumn = (column: ScreeningColumn) =>
    !column.locked && !column.alwaysFrozen;

  const getColumnStyle = (
    column: ScreeningColumn,
    layer: 'body' | 'header'
  ): CSSProperties => {
    const baseStyle: CSSProperties = {
      maxWidth: column.width,
      minWidth: column.width,
      width: column.width,
    };
    const left = frozenColumnOffsets.get(column.id);
    if (left === undefined) return baseStyle;

    return {
      ...baseStyle,
      left,
      position: 'sticky',
      zIndex: layer === 'header' ? 30 : 20,
    };
  };

  const getFrozenClass = (
    column: ScreeningColumn,
    backgroundClass = 'bg-[#08101d]'
  ) => {
    if (!isFrozenColumn(column)) return '';
    return cn(
      backgroundClass,
      lastFrozenColumnId === column.id &&
        'shadow-[8px_0_14px_-12px_rgba(16,185,129,0.95)]'
    );
  };

  const toggleFrozenColumn = (column: ScreeningColumn) => {
    if (column.locked || column.alwaysFrozen) return;
    setFrozenColumnIds(current => {
      const next = new Set(current);
      if (next.has(column.id)) next.delete(column.id);
      else next.add(column.id);
      return next;
    });
  };

  const resetColumnLayout = () => {
    setColumnOrder(SORTABLE_COLUMNS.map(column => column.id));
    setFrozenColumnIds(new Set(DEFAULT_FROZEN_COLUMN_IDS));
  };

  const activateGridScrollbar = () => {
    if (gridScrollbarTimerRef.current !== null) {
      window.clearTimeout(gridScrollbarTimerRef.current);
    }

    setIsGridScrollbarActive(true);
    gridScrollbarTimerRef.current = window.setTimeout(() => {
      setIsGridScrollbarActive(false);
      gridScrollbarTimerRef.current = null;
    }, SCROLLBAR_HIDE_DELAY_MS);
  };

  const shouldIgnoreGridDragScroll = (target: EventTarget | null) => {
    if (!(target instanceof HTMLElement)) return true;
    return Boolean(
      target.closest(
        'thead, button, a, input, textarea, select, [role="button"], [data-drag-scroll-ignore="true"]'
      )
    );
  };

  const endGridDragScroll = (event: PointerEvent<HTMLDivElement>) => {
    const dragState = gridDragScrollRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;

    try {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
    } catch {
      // Synthetic pointer events may not have an active browser capture target.
    }
    document.body.style.cursor = dragState.previousCursor;
    document.body.style.userSelect = dragState.previousUserSelect;
    gridDragScrollRef.current = null;
    setIsGridDragScrolling(false);
  };

  const handleGridPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || shouldIgnoreGridDragScroll(event.target)) return;

    gridDragScrollRef.current = {
      isDragging: false,
      pointerId: event.pointerId,
      previousCursor: document.body.style.cursor,
      previousUserSelect: document.body.style.userSelect,
      startScrollLeft: event.currentTarget.scrollLeft,
      startX: event.clientX,
    };
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Non-native pointer events can still drive scroll math in tests/tools.
    }
  };

  const handleGridPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const dragState = gridDragScrollRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;

    const deltaX = event.clientX - dragState.startX;
    if (!dragState.isDragging && Math.abs(deltaX) < 4) return;

    if (!dragState.isDragging) {
      dragState.isDragging = true;
      document.body.style.cursor = 'grabbing';
      document.body.style.userSelect = 'none';
      setIsGridDragScrolling(true);
    }

    event.preventDefault();
    event.currentTarget.scrollLeft = dragState.startScrollLeft - deltaX;
    activateGridScrollbar();
    event.currentTarget.dispatchEvent(new Event('scroll'));
  };

  const moveColumn = (sourceId: string, targetId: string) => {
    if (sourceId === targetId || targetId === 'actions') return;
    const sourceColumn = SORTABLE_COLUMNS.find(column => column.id === sourceId);
    const targetColumn = SORTABLE_COLUMNS.find(column => column.id === targetId);
    if (
      !sourceColumn ||
      !targetColumn ||
      !canMoveColumn(sourceColumn) ||
      !canMoveColumn(targetColumn)
    )
      return;

    setColumnOrder(current => {
      const baseOrder = [
        ...current.filter(id => id !== 'actions'),
        ...SORTABLE_COLUMNS.map(column => column.id).filter(
          id => id !== 'actions' && !current.includes(id)
        ),
      ];
      const sourceIndex = baseOrder.indexOf(sourceId);
      const targetIndex = baseOrder.indexOf(targetId);
      if (sourceIndex === -1 || targetIndex === -1) return current;

      const nextOrder = [...baseOrder];
      nextOrder.splice(sourceIndex, 1);
      nextOrder.splice(targetIndex, 0, sourceId);
      return [...nextOrder, 'actions'];
    });
  };

  const handleColumnDragStart = (
    event: DragEvent<HTMLTableCellElement>,
    column: ScreeningColumn
  ) => {
    if (!canMoveColumn(column)) return;
    setDraggingColumnId(column.id);
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', column.id);
  };

  const handleColumnDrop = (
    event: DragEvent<HTMLTableCellElement>,
    targetColumn: ScreeningColumn
  ) => {
    event.preventDefault();
    const sourceId = event.dataTransfer.getData('text/plain') || draggingColumnId;
    if (sourceId) moveColumn(sourceId, targetColumn.id);
    setDraggingColumnId(null);
  };

  const handleColumnMenuOpen = (
    event: MouseEvent,
    column: ScreeningColumn
  ) => {
    if (column.locked) return;
    openColumnMenuAtPointer(event, column);
    closeMenu();
  };

  const setColumnSort = (
    column: ScreeningColumn,
    direction: StockScreenSortDirection
  ) => {
    if (!column.sortField) return;
    onSortChange?.({ field: column.sortField, direction });
  };

  const toggleColumnSort = (column: ScreeningColumn) => {
    if (!column.sortField) return;
    const defaultDirection = column.defaultDirection ?? 'DESC';
    if (!sort || sort.field !== column.sortField) {
      onSortChange?.({ field: column.sortField, direction: defaultDirection });
      return;
    }
    if (sort.direction === defaultDirection) {
      onSortChange?.({
        field: column.sortField,
        direction: OPPOSITE_DIRECTION[defaultDirection],
      });
      return;
    }
    onSortChange?.(null);
  };

  const formatPercent = (val: number, bold = false) => {
    const isPositive = val > 0;
    const isNegative = val < 0;
    const colorClass = isPositive
      ? 'text-red-400'
      : isNegative
        ? 'text-emerald-400'
        : 'text-slate-500';

    return (
      <span
        className={cn(
          'flex items-center justify-end font-mono',
          colorClass,
          bold && 'font-bold'
        )}
      >
        {isPositive ? (
          <ArrowUpIcon className="mr-0.5 h-3 w-3" />
        ) : isNegative ? (
          <ArrowDownIcon className="mr-0.5 h-3 w-3" />
        ) : null}
        {Math.abs(val).toFixed(2)}%
      </span>
    );
  };

  const formatPrice = (val: number) => `¥${val.toFixed(2)}`;

  const formatOptionalPercent = (value?: number | null) => {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return '--';
    }
    return `${value.toFixed(1)}%`;
  };

  const getFinancialValueClass = (value?: number | null) => {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return 'text-slate-700';
    }
    if (value > 0) return 'text-red-400';
    if (value < 0) return 'text-emerald-400';
    return 'text-slate-500';
  };

  const getFinancialTitle = (stock: StockScreeningResult) => {
    const parts = [];
    const hasAnyFinancialData =
      stock.financialReportDate ||
      stock.financialAnnounceDate ||
      stock.financialQualityFlags?.length ||
      stock.roe !== undefined ||
      stock.netProfitGrowth !== undefined ||
      stock.netProfitAccumGrowth !== undefined ||
      stock.yoyGrowth !== undefined ||
      stock.revenueAccumGrowth !== undefined;
    if (!hasAnyFinancialData) {
      return '暂无可用财务指标';
    }
    if (stock.financialReportDate) {
      parts.push(`报告期: ${stock.financialReportDate}`);
    }
    if (stock.financialAnnounceDate) {
      parts.push(`公告日: ${stock.financialAnnounceDate}`);
    }
    parts.push(`净利单季同比: ${formatOptionalPercent(stock.netProfitGrowth)}`);
    parts.push(`净利累计同比: ${formatOptionalPercent(stock.netProfitAccumGrowth)}`);
    parts.push(`营收单季同比: ${formatOptionalPercent(stock.yoyGrowth)}`);
    parts.push(`营收累计同比: ${formatOptionalPercent(stock.revenueAccumGrowth)}`);
    if (stock.financialQualityFlags?.length) {
      parts.push(`质量标记: ${stock.financialQualityFlags.join(', ')}`);
    }
    return parts.join('\n');
  };

  const getKDJColor = (val: number) => {
    if (val < 20) return 'text-purple-400 font-bold';
    if (val > 80) return 'text-rose-400 font-bold';
    return 'text-slate-500';
  };

  const getRSIColor = (val: number) => {
    if (val < 30) return 'text-emerald-400 font-bold';
    if (val > 70) return 'text-rose-400 font-bold';
    return 'text-slate-500';
  };

  const getSignalBadgeClass = (signal: string) => {
    const oversold = ['超跌反弹', '布林下轨反弹', 'RSI 超卖'];
    const momentum = ['强势股', '布林上轨突破', 'RSI 强势', '放量突破'];
    const crossover = ['KDJ 金叉', '均线金叉'];
    if (oversold.includes(signal))
      return 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10';
    if (momentum.includes(signal))
      return 'border-rose-500/30 text-rose-400 bg-rose-500/10';
    if (crossover.includes(signal))
      return 'border-amber-500/30 text-amber-400 bg-amber-500/10';
    return 'border-purple-500/30 text-purple-400 bg-purple-500/10';
  };

  const getInstrumentTypeLabel = (instrumentType: string) => {
    return instrumentType?.toLowerCase() === 'etf' ? 'ETF' : '股票';
  };

  const getInstrumentTypeClass = (instrumentType: string) => {
    return instrumentType?.toLowerCase() === 'etf'
      ? 'border-cyan-500/30 bg-cyan-500/10 text-cyan-300'
      : 'border-slate-700 bg-slate-900/60 text-slate-500';
  };

  const renderSortIndicator = (column: ScreeningColumn) => {
    if (!column.sortField || sort?.field !== column.sortField) {
      return <span className="h-3 w-3 text-slate-700" />;
    }
    return sort.direction === 'ASC' ? (
      <ArrowUpAZ className="h-3.5 w-3.5 text-cyan-300" />
    ) : (
      <ArrowDownZA className="h-3.5 w-3.5 text-cyan-300" />
    );
  };

  const renderSignalBadges = (signals: string[]) => {
    const signalText = signals.join(' / ');

    return (
      <div
        className="relative w-[316px] max-w-full overflow-hidden"
        title={signalText}
      >
        <div
          data-testid="screening-signal-strip"
          aria-label={`命中信号：${signals.join('、')}`}
          className="flex h-4 flex-nowrap items-center gap-1 overflow-x-auto overflow-y-hidden pr-5 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {signals.map((s, idx) => (
            <Badge
              key={`${s}-${idx}`}
              variant="outline"
              className={cn(
                'h-4 shrink-0 whitespace-nowrap px-1.5 text-[9px] font-normal leading-none',
                getSignalBadgeClass(s)
              )}
            >
              {s}
            </Badge>
          ))}
        </div>
        {signals.length > 4 && (
          <div className="pointer-events-none absolute inset-y-0 right-0 w-6 bg-gradient-to-l from-[#08101d] to-transparent" />
        )}
      </div>
    );
  };

  const renderHeaderCell = (column: ScreeningColumn) => {
    const isSorted = Boolean(column.sortField && sort?.field === column.sortField);
    const textAlign =
      column.align === 'right'
        ? 'text-right'
        : column.align === 'center'
          ? 'text-center'
          : 'text-left';
    const ariaSort =
      isSorted && sort?.direction === 'ASC'
        ? 'ascending'
        : isSorted && sort?.direction === 'DESC'
          ? 'descending'
          : 'none';

    return (
      <th
        key={column.id}
        aria-sort={ariaSort}
        draggable={canMoveColumn(column)}
        onContextMenu={event => handleColumnMenuOpen(event, column)}
        onDragEnd={() => setDraggingColumnId(null)}
        onDragOver={event => {
          if (canMoveColumn(column)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = 'move';
          }
        }}
        onDragStart={event => handleColumnDragStart(event, column)}
        onDrop={event => handleColumnDrop(event, column)}
        style={getColumnStyle(column, 'header')}
        className={cn(
          'group/header sticky top-0 z-10 border-b border-r border-white/10 bg-slate-900/95 px-3 py-2.5 text-[10px] font-bold text-slate-500 backdrop-blur last:border-r-0',
          column.widthClass,
          textAlign,
          canMoveColumn(column) && 'cursor-grab active:cursor-grabbing',
          draggingColumnId === column.id && 'opacity-60',
          isSorted && 'bg-[#0c222a] text-emerald-100',
          isFrozenColumn(column) &&
            getFrozenClass(
              column,
              isSorted ? 'bg-[#0c222a]' : 'bg-slate-900'
            )
        )}
        scope="col"
      >
        <div className="relative min-w-0 pr-7">
          <button
            type="button"
            data-testid={`screening-sort-${column.id}`}
            disabled={!column.sortField || !onSortChange}
            onClick={() => toggleColumnSort(column)}
            className={cn(
              'flex min-w-0 items-center gap-1.5 leading-3 outline-none transition-colors disabled:cursor-default',
              column.align === 'right' && 'ml-auto justify-end',
              column.align === 'center' && 'mx-auto justify-center',
              column.sortField &&
                onSortChange &&
                'cursor-pointer hover:text-slate-200 focus-visible:text-slate-100'
            )}
            title={column.sortField ? `${column.label} 排序` : column.label}
          >
            {canMoveColumn(column) && (
              <GripVertical className="h-3 w-3 shrink-0 text-slate-700 opacity-0 transition-opacity group-hover/header:opacity-100" />
            )}
            {isFrozenColumn(column) && (
              <Pin className="h-3 w-3 shrink-0 text-emerald-300" />
            )}
            <span className="min-w-0 overflow-hidden text-ellipsis whitespace-nowrap">
              {column.label}
            </span>
            {renderSortIndicator(column)}
          </button>
          {!column.locked && (
            <button
              type="button"
              data-testid={`screening-sort-menu-${column.id}`}
              onClick={event =>
                openSortMenuFromElement(event, column, {
                  offset: 6,
                  placement: 'bottom-end',
                })
              }
              className={cn(
                'absolute right-0 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-[4px] text-slate-500 opacity-0 transition-all duration-150 hover:bg-white/[0.08] hover:text-slate-100 focus:bg-white/[0.08] focus:text-slate-100 focus:opacity-100 focus:outline-none group-hover/header:opacity-100',
                sortMenu?.payload.id === column.id && 'opacity-100'
              )}
              aria-label={`${column.label || '操作列'} 列菜单`}
              title={`${column.label || '操作列'} 列菜单`}
            >
              <MoreVertical className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </th>
    );
  };

  const renderBodyCell = (
    stock: StockScreeningResult,
    column: ScreeningColumn
  ) => {
    const bodyStyle = getColumnStyle(column, 'body');
    const baseCellClass =
      'h-[33px] overflow-hidden whitespace-nowrap border-b border-r border-white/5 px-3 py-1.5';

    switch (column.id) {
      case 'identity':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <div
              className="flex min-w-0 items-center gap-2 font-sans"
              title={`${stock.name} ${stock.code} ${stock.industry}`}
            >
              <span className="min-w-0 flex-1 truncate text-sm font-semibold leading-4 text-slate-200">
                {stock.name}
              </span>
              <span className="shrink-0 font-mono text-[10px] leading-4 text-slate-500">
                {stock.code}
              </span>
              <span
                className={cn(
                  'inline-flex shrink-0 items-center rounded-sm border px-1.5 py-0 text-[9px] leading-4',
                  getInstrumentTypeClass(stock.instrumentType)
                )}
              >
                {getInstrumentTypeLabel(stock.instrumentType)}
              </span>
              <span className="inline-flex max-w-[72px] shrink-0 items-center truncate rounded-sm border border-slate-800 px-1.5 py-0 text-[9px] leading-4 text-slate-600">
                {stock.industry}
              </span>
            </div>
          </td>
        );
      case 'price':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right font-medium text-slate-300',
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            {formatPrice(stock.currentPrice)}
          </td>
        );
      case 'changePct':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            {formatPercent(stock.changePct, true)}
          </td>
        );
      case 'signals':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(baseCellClass, getFrozenClass(column, 'bg-[#08101d]'))}
          >
            {renderSignalBadges(stock.matchedStrategies)}
          </td>
        );
      case 'kdj': {
        const backgroundClass = stock.matchedStrategies.includes('KDJ 金叉')
          ? 'bg-amber-500/5 ring-1 ring-inset ring-amber-500/20'
          : 'bg-white/[0.02]';

        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              backgroundClass,
              getFrozenClass(column, backgroundClass)
            )}
          >
            <div className="flex items-center justify-center gap-3 text-[11px]">
              <span className={cn('leading-none', getKDJColor(stock.k))}>
                {stock.k.toFixed(0)}
              </span>
              <span className="leading-none text-slate-500">
                {stock.d.toFixed(0)}
              </span>
              <span className={cn('leading-none', getKDJColor(stock.j))}>
                {stock.j.toFixed(0)}
              </span>
            </div>
          </td>
        );
      }
      case 'rsi': {
        const backgroundClass = stock.matchedStrategies.some(
          s => s === 'RSI 超卖' || s === 'RSI 强势'
        )
          ? 'bg-rose-500/5 ring-1 ring-inset ring-rose-500/20'
          : 'bg-white/[0.02]';

        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              backgroundClass,
              getFrozenClass(column, backgroundClass)
            )}
          >
            <div className="flex items-center justify-center gap-2 text-[11px]">
              <span className={getRSIColor(stock.rsi6)}>
                {stock.rsi6.toFixed(0)}
              </span>
              <span className="text-slate-700">/</span>
              <span className={getRSIColor(stock.rsi12)}>
                {stock.rsi12.toFixed(0)}
              </span>
              <span className="text-slate-700">/</span>
              <span className="text-slate-600">{stock.rsi24.toFixed(0)}</span>
            </div>
          </td>
        );
      }
      case 'volumeRatio': {
        const backgroundClass = stock.matchedStrategies.includes('放量突破')
          ? 'bg-amber-500/5 ring-1 ring-inset ring-amber-500/20'
          : 'bg-white/[0.02]';

        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              backgroundClass,
              getFrozenClass(column, backgroundClass)
            )}
          >
            <span
              className={cn(
                'font-medium',
                stock.volumeRatio > 1.5 ? 'text-amber-500' : 'text-slate-500'
              )}
            >
              {stock.volumeRatio.toFixed(1)}
            </span>
          </td>
        );
      }
      case 'drawdown':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                stock.priceDropPct < -20 ? 'text-emerald-400' : 'text-slate-300'
              )}
            >
              {stock.priceDropPct.toFixed(1)}%
            </span>
          </td>
        );
      case 'daysSincePeak':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right text-slate-500',
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            {stock.daysSincePeak}天
          </td>
        );
      case 'roe':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn('font-mono', getFinancialValueClass(stock.roe))}
              title={getFinancialTitle(stock)}
            >
              {formatOptionalPercent(stock.roe)}
            </span>
          </td>
        );
      case 'netProfitGrowth':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-mono',
                getFinancialValueClass(stock.netProfitGrowth)
              )}
              title={getFinancialTitle(stock)}
            >
              {formatOptionalPercent(stock.netProfitGrowth)}
            </span>
          </td>
        );
      case 'yoyGrowth':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-mono',
                getFinancialValueClass(stock.yoyGrowth)
              )}
              title={getFinancialTitle(stock)}
            >
              {formatOptionalPercent(stock.yoyGrowth)}
            </span>
          </td>
        );
      case 'actions':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className="h-[33px] w-[46px] overflow-hidden whitespace-nowrap border-b border-white/5 px-2 py-1.5 text-right"
          >
            <button
              type="button"
              onClick={event => openRowMenuFromElement(event, stock)}
              className="inline-flex h-6 w-6 items-center justify-center rounded-[4px] text-slate-600 transition-colors hover:bg-white/[0.08] hover:text-white"
              aria-label={`${stock.name} 操作`}
              title={`${stock.name} 操作`}
            >
              <MoreHorizontal className="h-4 w-4" />
            </button>
          </td>
        );
      default:
        return null;
    }
  };

  return (
    <div className="flex h-full flex-col bg-transparent">
      <div className="flex items-center justify-between border-b border-white/5 bg-slate-900/40 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <LayoutList className="h-4 w-4 shrink-0 text-slate-400" />
          <Badge
            variant="outline"
            className="font-mono text-[10px] font-normal text-slate-400"
          >
            总数: {meta?.total ?? displayData.length}
          </Badge>
          {meta?.signalVersion && (
            <Badge
              variant="outline"
              className="max-w-[260px] truncate font-mono text-[10px] font-normal text-slate-400"
            >
              版本: {meta.signalVersion}
            </Badge>
          )}
          {meta?.calculatedAt && (
            <Badge
              variant="outline"
              className="font-mono text-[10px] font-normal text-slate-400"
            >
              最后计算: {new Date(meta.calculatedAt).toLocaleString()}
            </Badge>
          )}
          {meta?.hasStaleData && (
            <Badge
              variant="outline"
              className="border-amber-500/30 bg-amber-500/10 font-mono text-[10px] font-normal text-amber-300"
            >
              <AlertTriangle className="mr-1 h-3 w-3" />
              非今日快照
            </Badge>
          )}
        </div>
        <button
          type="button"
          onClick={handleExport}
          className="flex h-7 shrink-0 items-center gap-1 rounded-[4px] px-2 text-xs text-slate-400 transition-colors hover:bg-white/5 hover:text-white"
        >
          <Download className="h-3.5 w-3.5" />
          导出
        </button>
      </div>

      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        {(error || meta?.warnings?.length) && (
          <div className="flex items-center gap-2 border-b border-amber-500/20 bg-amber-500/10 px-4 py-2 text-[11px] text-amber-200">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
              {error || meta?.warnings?.join('；')}
            </span>
          </div>
        )}

        {screeningLoading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/60 backdrop-blur-[2px]">
            <div className="flex flex-col items-center">
              <div className="mb-2 h-8 w-8 animate-spin rounded-full border-2 border-slate-700 border-t-purple-500" />
              <p className="flex items-center gap-2 font-mono text-xs text-slate-400">
                <Zap className="h-3 w-3" /> 正在执行筛选...
              </p>
            </div>
          </div>
        )}

        <div
          data-testid="screening-results-grid"
          onPointerCancel={endGridDragScroll}
          onPointerDown={handleGridPointerDown}
          onPointerLeave={endGridDragScroll}
          onPointerMove={handleGridPointerMove}
          onPointerUp={endGridDragScroll}
          className={cn(
            'custom-scrollbar min-h-0 flex-1 overflow-auto bg-[#08101d] [touch-action:pan-y]',
            isGridDragScrolling ? 'cursor-grabbing' : 'cursor-grab',
            isGridScrollbarActive && SCROLLBAR_ACTIVE_CLASS
          )}
        >
          <table className="w-max min-w-full border-collapse text-xs">
            <thead className="sticky top-0 z-10 bg-slate-900/95 backdrop-blur">
              <tr>{orderedColumns.map(renderHeaderCell)}</tr>
            </thead>
            <tbody className="font-mono">
              {displayData.length === 0 && !screeningLoading && (
                <tr>
                  <td
                    colSpan={orderedColumns.length}
                    className="h-[400px] border-b border-white/5 text-center"
                  >
                    <div className="flex flex-col items-center justify-center space-y-3 text-slate-500">
                      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-800/50">
                        <Search className="h-6 w-6 text-slate-400" />
                      </div>
                      <div className="space-y-1">
                        <p className="font-bold text-slate-300">
                          未找到符合条件的股票
                        </p>
                        <p className="text-xs">
                          请尝试放宽筛选条件，或减少选定的信号策略
                        </p>
                      </div>
                    </div>
                  </td>
                </tr>
              )}

              {displayData.map(stock => (
                <tr
                  key={stock.code}
                  onContextMenu={event => openAtPointer(event, stock)}
                  className="group transition-colors hover:bg-white/[0.02]"
                >
                  {orderedColumns.map(column => renderBodyCell(stock, column))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <StudioMenu
        ariaLabel="筛选列菜单"
        items={[
          {
            checked:
              Boolean(
                sort &&
                  sortMenu?.payload.sortField &&
                  sort.field === sortMenu.payload.sortField &&
                  sort.direction === 'ASC'
              ),
            disabled: !sortMenu?.payload.sortField,
            icon: <ArrowUpAZ className="h-3.5 w-3.5 text-cyan-300" />,
            id: 'sort-asc',
            label: '升序',
            onSelect: () => {
              if (sortMenu?.payload) setColumnSort(sortMenu.payload, 'ASC');
            },
          },
          {
            checked:
              Boolean(
                sort &&
                  sortMenu?.payload.sortField &&
                  sort.field === sortMenu.payload.sortField &&
                  sort.direction === 'DESC'
              ),
            disabled: !sortMenu?.payload.sortField,
            icon: <ArrowDownZA className="h-3.5 w-3.5 text-cyan-300" />,
            id: 'sort-desc',
            label: '降序',
            onSelect: () => {
              if (sortMenu?.payload) setColumnSort(sortMenu.payload, 'DESC');
            },
          },
          { id: 'sort-separator', type: 'separator' },
          {
            disabled: !sort,
            icon: sort ? (
              <X className="h-3.5 w-3.5 text-slate-500" />
            ) : (
              <Check className="h-3.5 w-3.5 text-slate-600" />
            ),
            id: 'clear-sort',
            label: '清除排序',
            onSelect: () => onSortChange?.(null),
          },
          { id: 'pin-separator', type: 'separator' },
          {
            disabled:
              !sortMenu?.payload ||
              sortMenu.payload.locked ||
              sortMenu.payload.alwaysFrozen,
            icon:
              sortMenu?.payload && isFrozenColumn(sortMenu.payload) ? (
                <PinOff className="h-3.5 w-3.5 text-emerald-300" />
              ) : (
                <Pin className="h-3.5 w-3.5 text-emerald-300" />
              ),
            id: 'toggle-frozen',
            label:
              sortMenu?.payload?.alwaysFrozen
                ? '已固定'
                : sortMenu?.payload && isFrozenColumn(sortMenu.payload)
                ? '取消固定列'
                : '固定列',
            onSelect: () => {
              if (sortMenu?.payload) toggleFrozenColumn(sortMenu.payload);
            },
          },
          {
            icon: <RotateCcw className="h-3.5 w-3.5 text-slate-500" />,
            id: 'reset-column-layout',
            label: '重置列布局',
            onSelect: resetColumnLayout,
          },
        ]}
        menu={sortMenu}
        onClose={closeSortMenu}
        width={150}
      />

      <StudioMenu
        ariaLabel="筛选结果菜单"
        items={[
          {
            disabled: !rowMenu?.payload,
            icon: <Eye className="h-3.5 w-3.5" />,
            id: 'open-detail',
            label: '打开个股详情',
            onSelect: () => {
              if (rowMenu?.payload)
                setLocation(`/stock/${rowMenu.payload.code}`);
            },
          },
          {
            disabled: !rowMenu?.payload,
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-code',
            label: '复制股票代码',
            onSelect: () => {
              if (rowMenu?.payload) copyText(rowMenu.payload.code);
            },
          },
          {
            disabled: !rowMenu?.payload,
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-name',
            label: '复制股票名称',
            onSelect: () => {
              if (rowMenu?.payload) copyText(rowMenu.payload.name);
            },
          },
          { id: 'separator-actions', type: 'separator' },
          {
            disabled: !rowMenu?.payload,
            id: 'watchlist',
            label: '加入自选',
            onSelect: () => {
              if (rowMenu?.payload) {
                void handleAddWatchlist(rowMenu.payload);
              }
            },
          },
          {
            disabled: !rowMenu?.payload,
            id: 'trend',
            label: '趋势分析',
            onSelect: () => {
              if (rowMenu?.payload) {
                handleAction(
                  '趋势分析',
                  rowMenu.payload.code,
                  rowMenu.payload.name
                );
              }
            },
          },
        ]}
        menu={rowMenu}
        onClose={closeMenu}
        width={180}
      />
    </div>
  );
}

async function addWatchlistItem(input: {
  instrumentName?: string | null;
  stockCode: string;
}) {
  try {
    const response = await fetch('/graphql', {
      body: JSON.stringify({
        query: `
          mutation Screening_AddWatchlistItem($input: AddWatchlistItemInput!) {
            addWatchlistItem(input: $input) {
              success
              message
            }
          }
        `,
        variables: { input },
      }),
      headers: { 'Content-Type': 'application/json' },
      method: 'POST',
    });
    const payload = await response.json();
    return payload?.data?.addWatchlistItem as
      | { message?: string | null; success: boolean }
      | undefined;
  } catch (error) {
    return {
      success: false,
      message: error instanceof Error ? error.message : '请求失败',
    };
  }
}
