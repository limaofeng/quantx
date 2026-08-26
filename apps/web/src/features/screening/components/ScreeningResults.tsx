import {
  AlertTriangle,
  ArrowDownIcon,
  ArrowDownZA,
  ArrowUpAZ,
  ArrowUpIcon,
  Check,
  Copy,
  Eye,
  LayoutList,
  MoreHorizontal,
  Pin,
  PinOff,
  RotateCcw,
  Search,
  X,
  Zap,
} from 'lucide-react';
import { useState } from 'react';
import { useLocation } from 'wouter';

import {
  StudioDataTable,
  StudioMenu,
  useStudioMenu,
  type StudioDataTableApi,
} from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import {
  normalizeWatchlistCode,
  useWatchlistWorkspace,
} from '@/features/watchlist/hooks';
import { mergeWatchlistGroupIds } from '@/features/watchlist/utils';
import { useToast } from '@/hooks/use-toast';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import {
  type ScreeningMode,
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
  activeMode?: ScreeningMode;
  onRetry?: () => void;
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

const DAILY_COLUMNS: ScreeningColumn[] = [
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
    align: 'center',
    id: 'amountRatio',
    label: '额比',
    sortField: 'AMOUNT_RATIO_20',
    width: 90,
    widthClass: 'min-w-[90px] w-[90px]',
  },
  {
    align: 'center',
    id: 'turnoverRate',
    label: '换手',
    sortField: 'TURNOVER_RATE',
    width: 90,
    widthClass: 'min-w-[90px] w-[90px]',
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
    label: 'ROE（TTM）',
    sortField: 'ROE',
    width: 120,
    widthClass: 'min-w-[120px] w-[120px]',
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
    width: 92,
    widthClass: 'min-w-[92px] w-[92px]',
  },
];

const INTRADAY_COLUMNS: ScreeningColumn[] = [
  {
    alwaysFrozen: true,
    defaultDirection: 'ASC',
    id: 'identity',
    label: '代码 / 名称',
    sortField: 'CODE',
    width: 240,
    widthClass: 'min-w-[240px] w-[240px]',
  },
  {
    align: 'right',
    id: 'price',
    label: '价格',
    sortField: 'CURRENT_PRICE',
    width: 100,
    widthClass: 'min-w-[100px] w-[100px]',
  },
  {
    align: 'right',
    id: 'changePct',
    label: '涨跌幅',
    sortField: 'CHANGE_PCT',
    width: 110,
    widthClass: 'min-w-[110px] w-[110px]',
  },
  {
    align: 'center',
    id: 'volumeRatio',
    label: '量比',
    sortField: 'VOLUME_RATIO',
    width: 90,
    widthClass: 'min-w-[90px] w-[90px]',
  },
  {
    align: 'center',
    id: 'volumePace',
    label: '量速',
    width: 90,
    widthClass: 'min-w-[90px] w-[90px]',
  },
  {
    align: 'center',
    id: 'amountPace',
    label: '额速',
    width: 90,
    widthClass: 'min-w-[90px] w-[90px]',
  },
  {
    align: 'center',
    id: 'last5mVolume',
    label: '5m 放量',
    width: 100,
    widthClass: 'min-w-[100px] w-[100px]',
  },
  {
    align: 'center',
    id: 'intradayTurnover',
    label: '盘中换手',
    width: 110,
    widthClass: 'min-w-[110px] w-[110px]',
  },
  {
    align: 'center',
    id: 'depthImbalance',
    label: '买盘失衡',
    width: 110,
    widthClass: 'min-w-[110px] w-[110px]',
  },
  {
    align: 'right',
    id: 'updateTime',
    label: '更新',
    width: 112,
    widthClass: 'min-w-[112px] w-[112px]',
  },
  {
    id: 'status',
    label: '状态',
    width: 100,
    widthClass: 'min-w-[100px] w-[100px]',
  },
  {
    align: 'right',
    id: 'actions',
    label: '',
    locked: true,
    width: 92,
    widthClass: 'min-w-[92px] w-[92px]',
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

function describeFinancialQualityFlag(flag: string) {
  const exactLabels: Record<string, string> = {
    extreme_roe_ttm: 'ROE 数值超出常规范围',
    financial_report_stale: '报告期未达到当前披露期限要求',
    financial_sync_empty: '最近同步未返回该股票财务数据',
    financial_sync_unverified: '最近同步未验证该股票',
    missing_roe_metric: '缺少 ROE 指标快照',
    roe_quality_unverified: '历史指标尚未重新验证',
  };
  if (exactLabels[flag]) return exactLabels[flag];
  if (flag.includes('announce_after_verification_date')) {
    return `公告日晚于验证时间（${flag}）`;
  }
  if (flag.includes('announce_before_report_date')) {
    return `公告日早于报告期（${flag}）`;
  }
  if (flag.includes('balance_equation_mismatch')) {
    return `资产负债勾稽不一致（${flag}）`;
  }
  if (flag.startsWith('non_positive_')) {
    return `归母权益非正（${flag}）`;
  }
  if (flag.startsWith('mismatched_')) {
    return `报告期不匹配（${flag}）`;
  }
  if (flag.startsWith('missing_')) {
    return `ROE 计算依赖缺失（${flag}）`;
  }
  return `质量校验未通过（${flag}）`;
}

export function ScreeningResults({
  screeningLoading,
  results,
  meta,
  error,
  sort,
  onSortChange,
  activeMode = 'DAILY',
  onRetry,
}: ScreeningResultsProps) {
  const isIntradayMode = activeMode === 'INTRADAY';
  const columns = isIntradayMode ? INTRADAY_COLUMNS : DAILY_COLUMNS;
  const [warningsExpanded, setWarningsExpanded] = useState(false);
  const { toast } = useToast();
  const [, setLocation] = useLocation();
  const watchlist = useWatchlistWorkspace();
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

  const displayData = results ?? [];
  const loadedCount = meta?.loadedCount ?? displayData.length;
  const intradayStaleRowCount =
    meta?.intradayStaleRowCount ??
    displayData.filter(stock => stock.isStale).length;
  const hasNotice =
    (error?.length ?? 0) > 0 || (meta?.warnings?.length ?? 0) > 0;

  const handleAction = (action: string, code: string, name: string) => {
    toast({
      title: '操作已记录',
      description: `已请求 ${action}: ${name} (${code})`,
    });
  };

  const handleAddWatchlist = async (stock: StockScreeningResult) => {
    let result: { message?: string | null; success: boolean };
    try {
      const existingItem = watchlist.items.find(
        item =>
          normalizeWatchlistCode(item.stockCode) ===
          normalizeWatchlistCode(stock.code)
      );
      result = await watchlist.saveItem({
        groupIds: mergeWatchlistGroupIds(existingItem),
        instrumentName: stock.name,
        stockCode: stock.code,
      });
    } catch (error) {
      result = {
        message: error instanceof Error ? error.message : '请求失败',
        success: false,
      };
    }

    toast({
      title: result?.success ? '已加入自选' : '加入自选失败',
      description:
        result?.message || `${stock.name} (${stock.code}) 已提交到后端自选池`,
      variant: result?.success === false ? 'destructive' : 'default',
    });
  };

  const copyText = (text: string) => {
    if (!navigator.clipboard) return;
    void navigator.clipboard.writeText(text);
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
    const colorClass = financialToneClass(val);

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

  const formatOptionalRatio = (value?: number | null) => {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return '--';
    }
    return value.toFixed(1);
  };

  const formatUpdateTime = (value?: string | null) => {
    if (!value) return '--';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '--';
    return date.toLocaleTimeString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const getFinancialValueClass = (value?: number | null) => {
    if (value === null || value === undefined || !Number.isFinite(value)) {
      return 'text-slate-700';
    }
    return financialToneClass(value);
  };

  const getFinancialTitle = (stock: StockScreeningResult) => {
    const parts = [];
    const hasAnyFinancialData =
      stock.financialReportDate ||
      stock.financialAnnounceDate ||
      stock.financialAsOfDate ||
      stock.financialVerifiedAt ||
      stock.roeQualityStatus ||
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
    if (stock.financialAsOfDate) {
      parts.push(`财务可用日: ${stock.financialAsOfDate}`);
    }
    if (stock.financialVerifiedAt) {
      parts.push(
        `验证时间: ${new Date(stock.financialVerifiedAt).toLocaleString('zh-CN')}`
      );
    }
    const qualityStatus =
      stock.roeQualityStatus ??
      (stock.roe !== undefined ? 'VALID' : 'UNVERIFIED');
    const qualityLabels = {
      INVALID: '无效：依赖报表缺失或不满足计算约束',
      STALE: '过期：未达到当前日期要求的最低报告期',
      SUSPICIOUS: '可疑：数值、公告日期或报表勾稽异常',
      UNVERIFIED: '未验证：最近同步未确认该股票数据',
      VALID: '有效：截至筛选快照已披露且验证通过',
    } as const;
    parts.push(`ROE 质量: ${qualityLabels[qualityStatus]}`);
    parts.push(`净利单季同比: ${formatOptionalPercent(stock.netProfitGrowth)}`);
    parts.push(
      `净利累计同比: ${formatOptionalPercent(stock.netProfitAccumGrowth)}`
    );
    parts.push(`营收单季同比: ${formatOptionalPercent(stock.yoyGrowth)}`);
    parts.push(
      `营收累计同比: ${formatOptionalPercent(stock.revenueAccumGrowth)}`
    );
    if (stock.financialQualityFlags?.length) {
      parts.push(
        `质量原因: ${stock.financialQualityFlags
          .map(describeFinancialQualityFlag)
          .join('；')}`
      );
    }
    return parts.join('\n');
  };

  const getKDJColor = (val: number) => {
    if (val < 20) return 'text-cyan-300 font-bold';
    if (val > 80) return 'text-rose-400 font-bold';
    return 'text-slate-500';
  };

  const getRSIColor = (val: number) => {
    if (val < 30) return 'text-emerald-400 font-bold';
    if (val > 70) return 'text-rose-400 font-bold';
    return 'text-slate-500';
  };

  const getSignalBadgeClass = (signal: string) => {
    const oversold = ['超跌反弹', '布林下轨反弹', 'RSI 超卖', '缩量调整'];
    const momentum = [
      '强势股',
      '布林上轨突破',
      'RSI 强势',
      '放量突破',
      '放量上涨',
      '成交额放大',
      '高换手',
      '盘中放量',
      '成交额加速',
      '近5分钟放量',
      '盘中高换手',
      '买盘占优',
      '成交活跃',
    ];
    const risk = ['放量下跌', '高位放量滞涨', '卖盘占优'];
    const crossover = ['KDJ 金叉', '均线金叉'];
    if (oversold.includes(signal))
      return 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10';
    if (momentum.includes(signal))
      return 'border-rose-500/30 text-rose-400 bg-rose-500/10';
    if (risk.includes(signal))
      return 'border-cyan-500/30 text-cyan-300 bg-cyan-500/10';
    if (crossover.includes(signal))
      return 'border-amber-500/30 text-amber-400 bg-amber-500/10';
    return 'border-blue-500/30 text-blue-300 bg-blue-500/10';
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
                'h-4 shrink-0 whitespace-nowrap px-1.5 text-ui-caption font-normal leading-none',
                getSignalBadgeClass(s)
              )}
            >
              {s}
            </Badge>
          ))}
        </div>
      </div>
    );
  };

  const renderBodyCell = (
    stock: StockScreeningResult,
    column: ScreeningColumn,
    table: StudioDataTableApi<StockScreeningResult, ScreeningColumn>
  ) => {
    const bodyStyle = table.getColumnStyle(column, 'body');
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
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <div
              className="flex min-w-0 items-center gap-2 font-sans"
              title={`${stock.name} ${stock.code} ${stock.industry}`}
            >
              <button
                type="button"
                onClick={() => setLocation(`/stock/${stock.code}`)}
                className="min-w-0 flex-1 truncate text-left text-ui-body font-semibold leading-4 text-slate-200 transition-colors hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                aria-label={`${stock.name} ${stock.code} 详情`}
              >
                {stock.name}
              </button>
              <span className="shrink-0 font-mono text-ui-caption leading-4 text-slate-500">
                {stock.code}
              </span>
              <span
                className={cn(
                  'inline-flex shrink-0 items-center rounded-sm border px-1.5 py-0 text-ui-caption leading-4',
                  getInstrumentTypeClass(stock.instrumentType)
                )}
              >
                {getInstrumentTypeLabel(stock.instrumentType)}
              </span>
              <span className="inline-flex max-w-[72px] shrink-0 items-center truncate rounded-sm border border-slate-800 px-1.5 py-0 text-ui-caption leading-4 text-slate-600">
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
              table.getFrozenClass(column, 'bg-[#08101d]')
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
              table.getFrozenClass(column, 'bg-[#08101d]')
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
            className={cn(
              baseCellClass,
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
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
              table.getFrozenClass(column, backgroundClass)
            )}
          >
            <div className="flex items-center justify-center gap-3 text-ui-caption">
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
              table.getFrozenClass(column, backgroundClass)
            )}
          >
            <div className="flex items-center justify-center gap-2 text-ui-caption">
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
        const backgroundClass = stock.matchedStrategies.some(signal =>
          ['放量突破', '放量上涨', '放量下跌', '盘中放量'].includes(signal)
        )
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
              table.getFrozenClass(column, backgroundClass)
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
      case 'amountRatio':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                (stock.amountRatio20 ?? 0) > 1.5
                  ? 'text-amber-500'
                  : 'text-slate-500'
              )}
            >
              {formatOptionalRatio(stock.amountRatio20)}
            </span>
          </td>
        );
      case 'turnoverRate':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                (stock.turnoverRatePct ?? stock.intradayTurnoverRatePct ?? 0) >
                  3
                  ? 'text-rose-400'
                  : 'text-slate-500'
              )}
            >
              {formatOptionalPercent(
                stock.turnoverRatePct ?? stock.intradayTurnoverRatePct
              )}
            </span>
          </td>
        );
      case 'intradayTurnover':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span className="font-medium text-slate-300">
              {formatOptionalPercent(stock.intradayTurnoverRatePct)}
            </span>
          </td>
        );
      case 'volumePace':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                (stock.volumePaceRatio ?? 0) > 2
                  ? 'text-cyan-300'
                  : 'text-slate-500'
              )}
            >
              {formatOptionalRatio(stock.volumePaceRatio)}
            </span>
          </td>
        );
      case 'amountPace':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                (stock.amountPaceRatio ?? 0) > 2
                  ? 'text-cyan-300'
                  : 'text-slate-500'
              )}
            >
              {formatOptionalRatio(stock.amountPaceRatio)}
            </span>
          </td>
        );
      case 'last5mVolume':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                (stock.last5mVolumeRatio ?? 0) > 2
                  ? 'text-cyan-300'
                  : 'text-slate-500'
              )}
            >
              {formatOptionalRatio(stock.last5mVolumeRatio)}
            </span>
          </td>
        );
      case 'depthImbalance':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                (stock.depthImbalance5 ?? 0) > 0.2
                  ? 'text-market-up'
                  : (stock.depthImbalance5 ?? 0) < -0.2
                    ? 'text-market-down'
                    : 'text-slate-500'
              )}
            >
              {formatOptionalRatio(stock.depthImbalance5)}
            </span>
          </td>
        );
      case 'updateTime':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right text-slate-500',
              stock.isStale && 'text-amber-300',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            {formatUpdateTime(stock.updatedAt ?? stock.calculatedAt)}
          </td>
        );
      case 'status':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-center',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'inline-flex items-center gap-1.5 text-ui-label',
                stock.isStale ? 'text-amber-300' : 'text-cyan-300'
              )}
            >
              <Zap className="h-3 w-3" />
              {stock.isStale ? '数据延迟' : '实时'}
            </span>
          </td>
        );
      case 'drawdown':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'font-medium',
                stock.priceDropPct < -20 ? 'text-market-down' : 'text-slate-300'
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
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            {stock.daysSincePeak}天
          </td>
        );
      case 'roe': {
        const roeQualityStatus =
          stock.roeQualityStatus ??
          (stock.roe !== undefined ? 'VALID' : 'UNVERIFIED');
        const qualityBadge = {
          INVALID: ['无效', 'border-rose-500/30 text-rose-300'],
          STALE: ['过期', 'border-amber-500/30 text-amber-300'],
          SUSPICIOUS: ['可疑', 'border-orange-500/30 text-orange-300'],
          UNVERIFIED: ['未验证', 'border-slate-500/30 text-slate-400'],
          VALID: null,
        } as const;
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              table.getFrozenClass(column, 'bg-[#08101d]')
            )}
          >
            <span
              className={cn(
                'flex items-center justify-end gap-1 font-mono',
                getFinancialValueClass(stock.roe)
              )}
              title={getFinancialTitle(stock)}
            >
              {formatOptionalPercent(stock.roe)}
              {qualityBadge[roeQualityStatus] && (
                <Badge
                  variant="outline"
                  className={cn(
                    'h-4 px-1 text-ui-caption font-normal',
                    qualityBadge[roeQualityStatus]?.[1]
                  )}
                >
                  {qualityBadge[roeQualityStatus]?.[0]}
                </Badge>
              )}
            </span>
          </td>
        );
      }
      case 'netProfitGrowth':
        return (
          <td
            key={column.id}
            style={bodyStyle}
            className={cn(
              baseCellClass,
              'text-right',
              table.getFrozenClass(column, 'bg-[#08101d]')
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
              table.getFrozenClass(column, 'bg-[#08101d]')
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
            className="h-[33px] overflow-hidden whitespace-nowrap border-b border-white/5 px-2 py-1.5 text-right"
          >
            <div className="inline-flex items-center gap-1">
              <button
                type="button"
                onClick={() => setLocation(`/stock/${stock.code}`)}
                className="rounded px-1.5 py-1 text-ui-label font-semibold text-blue-300 transition-colors hover:bg-blue-500/10 hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                aria-label={`${stock.name} 详情`}
              >
                详情
              </button>
              <button
                type="button"
                onClick={event => openRowMenuFromElement(event, stock)}
                className="inline-flex h-6 w-6 items-center justify-center rounded-sm text-slate-600 transition-colors hover:bg-white/[0.08] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                aria-label={`${stock.name} 操作`}
                title={`${stock.name} 操作`}
              >
                <MoreHorizontal className="h-4 w-4" />
              </button>
            </div>
          </td>
        );
      default:
        return null;
    }
  };

  return (
    <StudioDataTable<StockScreeningResult, ScreeningColumn>
      ariaLabel="筛选结果列表"
      className="h-full min-h-0 rounded-none border-0 bg-transparent"
      columns={columns}
      columnMenuTestIdPrefix="screening-sort-menu"
      defaultFrozenColumnIds={DEFAULT_FROZEN_COLUMN_IDS}
      emptyState={
        error ? null : (
          <tr>
            <td
              colSpan={columns.length}
              className="h-[400px] border-b border-white/5 px-ui-panel text-center"
            >
              <div className="flex flex-col items-center justify-center space-y-3 text-slate-500">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-800/50">
                  <Search className="h-6 w-6 text-slate-400" />
                </div>
                <div className="space-y-1">
                  <p className="font-bold text-slate-300">
                    未找到符合条件的股票
                  </p>
                  <p className="text-ui-label">
                    请尝试放宽筛选条件，或减少选定的信号策略
                  </p>
                </div>
              </div>
            </td>
          </tr>
        )
      }
      getRowKey={stock => stock.code}
      isColumnSorted={column =>
        Boolean(column.sortField && sort?.field === column.sortField)
      }
      loading={screeningLoading}
      loadingOverlay={
        screeningLoading && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-slate-950/45 backdrop-blur-[1px]">
            <div className="flex flex-col items-center">
              <div className="mb-2 h-8 w-8 animate-spin rounded-full border-2 border-slate-700 border-t-cyan-400 motion-reduce:animate-none" />
              <p className="flex items-center gap-2 font-mono text-ui-label text-slate-400">
                <Zap className="h-3 w-3" /> 正在执行筛选...
              </p>
            </div>
          </div>
        )
      }
      notice={
        hasNotice ? (
          <div className="space-y-2 border-b border-amber-500/20 bg-amber-500/[0.08] px-ui-section py-3 text-ui-label text-amber-100">
            {error && (
              <div
                role="alert"
                className="flex items-start gap-2 text-rose-200"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-rose-300" />
                <span className="min-w-0 break-words">{error}</span>
                {onRetry && (
                  <button
                    type="button"
                    onClick={onRetry}
                    className="ml-auto shrink-0 rounded border border-rose-300/30 px-2 py-1 text-ui-caption font-semibold text-rose-200 hover:bg-rose-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                  >
                    重试
                  </button>
                )}
              </div>
            )}
            {meta?.warnings?.length ? (
              <div className="flex items-start gap-2 text-amber-200">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="break-words">
                    {warningsExpanded
                      ? meta.warnings.join('；')
                      : meta.warnings[0]}
                  </div>
                  {meta.warnings.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setWarningsExpanded(value => !value)}
                      className="mt-1 text-ui-caption font-semibold text-amber-300 underline decoration-amber-300/50 underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                    >
                      {warningsExpanded
                        ? '收起警告'
                        : `展开其余 ${meta.warnings.length - 1} 条警告`}
                    </button>
                  )}
                </div>
              </div>
            ) : null}
          </div>
        ) : null
      }
      onColumnContextMenu={(event, column) => {
        if (column.locked) return;
        openColumnMenuAtPointer(event, column);
        closeMenu();
      }}
      onColumnMenuOpen={(event, column) => {
        openSortMenuFromElement(event, column, {
          offset: 6,
          placement: 'bottom-end',
        });
        closeMenu();
      }}
      onColumnSortToggle={onSortChange ? toggleColumnSort : undefined}
      onRowContextMenu={(event, stock) => openAtPointer(event, stock)}
      rowClassName={stock =>
        isIntradayMode && stock.isStale ? 'bg-amber-500/[0.025]' : undefined
      }
      renderCell={() => null}
      renderCellElement={({ column, row, table }) =>
        renderBodyCell(row, column, table)
      }
      renderOverlays={table => (
        <>
          <StudioMenu
            ariaLabel="筛选列菜单"
            items={[
              {
                checked: Boolean(
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
                checked: Boolean(
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
                  if (sortMenu?.payload)
                    setColumnSort(sortMenu.payload, 'DESC');
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
                  sortMenu?.payload &&
                  table.isFrozenColumn(sortMenu.payload) ? (
                    <PinOff className="h-3.5 w-3.5 text-primary" />
                  ) : (
                    <Pin className="h-3.5 w-3.5 text-primary" />
                  ),
                id: 'toggle-frozen',
                label: sortMenu?.payload?.alwaysFrozen
                  ? '已固定'
                  : sortMenu?.payload && table.isFrozenColumn(sortMenu.payload)
                    ? '取消固定列'
                    : '固定列',
                onSelect: () => {
                  if (sortMenu?.payload)
                    table.toggleFrozenColumn(sortMenu.payload);
                },
              },
              {
                icon: <RotateCcw className="h-3.5 w-3.5 text-slate-500" />,
                id: 'reset-column-layout',
                label: '重置列布局',
                onSelect: table.resetColumnLayout,
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
        </>
      )}
      renderSortIndicator={renderSortIndicator}
      rows={displayData}
      sortTestIdPrefix="screening-sort"
      testId="screening-results-grid"
      toolbarLeft={
        <>
          <LayoutList className="h-4 w-4 shrink-0 text-slate-400" />
          <Badge
            variant="outline"
            aria-live="polite"
            className="font-mono text-ui-caption font-normal text-slate-300"
          >
            已加载 {loadedCount} / 共 {meta?.total ?? loadedCount}
          </Badge>
          {isIntradayMode ? (
            <>
              <Badge
                variant="outline"
                className={cn(
                  'font-mono text-ui-caption font-normal',
                  meta?.intradayScannerRunning
                    ? 'border-cyan-400/30 bg-cyan-500/10 text-cyan-200'
                    : 'border-amber-400/30 bg-amber-500/10 text-amber-200'
                )}
              >
                {meta?.intradayScannerRunning ? '扫描器运行中' : '扫描器已停止'}
              </Badge>
              <Badge
                variant="outline"
                className="font-mono text-ui-caption font-normal text-slate-400"
              >
                5 秒自动刷新
              </Badge>
              <Badge
                variant="outline"
                className="font-mono text-ui-caption font-normal text-slate-400"
              >
                更新{' '}
                {formatUpdateTime(
                  meta?.intradayUpdatedAt ?? meta?.calculatedAt
                )}
              </Badge>
              <Badge
                variant="outline"
                className={cn(
                  'font-mono text-ui-caption font-normal',
                  intradayStaleRowCount > 0
                    ? 'border-amber-400/30 bg-amber-500/10 text-amber-200'
                    : 'text-slate-400'
                )}
              >
                陈旧行 {intradayStaleRowCount}
              </Badge>
            </>
          ) : (
            <>
              <Badge
                variant="outline"
                className={cn(
                  'font-mono text-ui-caption font-normal',
                  meta?.hasStaleData
                    ? 'border-amber-400/30 bg-amber-500/10 text-amber-200'
                    : 'text-slate-400'
                )}
              >
                快照 {meta?.snapshotDate || '--'} ·{' '}
                {meta?.hasStaleData ? '最近可用' : '新鲜'}
              </Badge>
              <Badge
                variant="outline"
                className={cn(
                  'font-mono text-ui-caption font-normal',
                  (meta?.missingSnapshotDates.length ?? 0) > 0
                    ? 'border-amber-400/30 bg-amber-500/10 text-amber-200'
                    : 'text-slate-400'
                )}
              >
                历史缺口 {meta?.missingSnapshotDates.length ?? 0}
              </Badge>
              {meta?.financialHealth && (
                <Badge
                  variant="outline"
                  className={cn(
                    'font-mono text-ui-caption font-normal',
                    meta.financialHealth.status === 'SUCCESS'
                      ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300'
                      : 'border-orange-500/30 bg-orange-500/10 text-orange-300'
                  )}
                  title={`已验证 ${meta.financialHealth.verifiedCount}；可筛选 ${meta.financialHealth.selectableCount}；过期 ${meta.financialHealth.excludedStaleCount}；可疑 ${meta.financialHealth.excludedSuspiciousCount}；无效 ${meta.financialHealth.excludedInvalidCount}；未验证 ${meta.financialHealth.excludedUnverifiedCount}`}
                >
                  财务 {meta.financialHealth.status} · 可筛选{' '}
                  {meta.financialHealth.selectableCount}
                </Badge>
              )}
            </>
          )}
        </>
      }
    />
  );
}
