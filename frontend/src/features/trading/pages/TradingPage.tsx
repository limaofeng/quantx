import {
  Activity,
  ArrowLeftRight,
  BarChart3,
  ClipboardList,
  Columns,
  History,
  PanelLeft,
  Wallet,
} from 'lucide-react';
import * as React from 'react';
import { useSearch } from 'wouter';

import {
  StudioWorkbench,
  type StudioMode,
} from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';
import { TradingChart } from '@/components/trading-chart';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import type { Position } from '@/features/portfolio/types';
import { useIsMobile } from '@/hooks/use-mobile';
import type { Stock } from '@/shared/types';
import { cn } from '@/utils/cn';

import { useCurrentAccount } from '../../dashboard/hooks';
import { AccountInfo } from '../components/AccountInfo';
import { ActiveOrders } from '../components/ActiveOrders';
import { MarketDepth } from '../components/MarketDepth';
import { OrderRecords } from '../components/OrderRecords';
import { TradeRecords } from '../components/TradeRecords';
import { TradingCard } from '../components/TradingCard';
import { TradingHoldingsSidebar } from '../components/TradingHoldingsSidebar';
import { TradingInstrumentHeader } from '../components/TradingInstrumentHeader';
import { useTodayOrders } from '../hooks';

import MobileTradingPage from './MobileTradingPage';

type TradingStudioMode = 'ACCOUNT' | 'CHART' | 'ORDER' | 'ORDERS' | 'TRADES';
type TradingLayoutMode = 'standard' | 'wide';
type OrderLike = { status?: string | null };

const studioModes: StudioMode[] = [
  { id: 'CHART', icon: BarChart3, label: '图表盘口' },
  { id: 'ORDER', icon: ArrowLeftRight, label: '下单' },
  { id: 'ORDERS', icon: ClipboardList, label: '委托' },
  { id: 'TRADES', icon: History, label: '成交' },
  { id: 'ACCOUNT', icon: Wallet, label: '账户' },
];

const layoutModes: {
  icon: React.ElementType;
  id: TradingLayoutMode;
  label: string;
}[] = [
  { id: 'wide', icon: Columns, label: '三栏' },
  { id: 'standard', icon: PanelLeft, label: '两栏' },
];

const compactTabTriggerClass =
  'h-7 rounded-md px-3 text-[11px] font-bold text-slate-500 transition-colors data-[state=active]:bg-red-500 data-[state=active]:text-white dark:text-slate-400 dark:data-[state=active]:text-white';

function getTradingStudioMode(mode: TradingStudioMode) {
  return studioModes.find(item => item.id === mode) || studioModes[0];
}

function normalizeSymbol(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

function getUrlSymbol(search: string) {
  return normalizeSymbol(new URLSearchParams(search).get('symbol'));
}

function makeSymbolStock(symbol: string): Stock {
  return {
    id: symbol,
    stockCode: symbol,
    name: symbol,
    quote: {
      lastPrice: 0,
      changePercent: 0,
    },
  };
}

function makeHoldingStock(holding: Position): Stock {
  const stockCode = normalizeSymbol(holding.stockCode);
  const lastPrice = holding.lastPrice ?? 0;

  return {
    id: stockCode,
    stockCode,
    name: holding.instrumentName || stockCode,
    quote: {
      lastPrice,
      changePercent: holding.changePercent ?? holding.profitRate ?? 0,
    },
    currentPrice: lastPrice,
  };
}

function buildHoldingsSymbolPath(symbol: string, search: string) {
  const params = new URLSearchParams(search);
  params.set('symbol', symbol);
  return `/holdings?${params.toString()}`;
}

function getSelectedStockCode(selectedStock: unknown) {
  if (typeof selectedStock === 'string') return selectedStock;
  if (!selectedStock || typeof selectedStock !== 'object') return undefined;

  const candidate = selectedStock as { id?: unknown; stockCode?: unknown };
  if (typeof candidate.stockCode === 'string') return candidate.stockCode;
  if (typeof candidate.id === 'string') return candidate.id;
  return undefined;
}

function isTerminalMode(mode: TradingStudioMode) {
  return mode === 'CHART' || mode === 'ORDER';
}

function toFiniteNumber(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCompactCurrency(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';

  const abs = Math.abs(amount);
  const sign = amount < 0 ? '-' : '';
  if (abs >= 100000000) return `${sign}¥${(abs / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${sign}¥${(abs / 10000).toFixed(2)}万`;
  return `${sign}¥${abs.toFixed(2)}`;
}

function formatSignedCurrency(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return `${amount >= 0 ? '+' : ''}${formatCompactCurrency(amount)}`;
}

function formatShares(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return Math.round(amount).toLocaleString('zh-CN');
}

function formatPriceMetric(value: unknown) {
  const price = toFiniteNumber(value);
  if (price === null || price <= 0) return '--';
  return `¥${price.toFixed(price >= 10 ? 2 : 3)}`;
}

function formatPercentMetric(value: unknown, signed = true) {
  const percent = toFiniteNumber(value);
  if (percent === null) return '--';
  const prefix = signed && percent > 0 ? '+' : '';
  return `${prefix}${percent.toFixed(2)}%`;
}

function formatDateTime(value: unknown) {
  if (typeof value !== 'string' || !value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return date.toLocaleString('zh-CN', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
    second: '2-digit',
    year: 'numeric',
  });
}

function getToneClass(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null || amount === 0) return 'text-slate-200';
  return amount > 0 ? 'text-rose-300' : 'text-emerald-300';
}

function getProgressPercent(part: unknown, total: unknown) {
  const partNumber = toFiniteNumber(part);
  const totalNumber = toFiniteNumber(total);
  if (partNumber === null || totalNumber === null || totalNumber <= 0) return 0;
  return Math.max(0, Math.min(100, (partNumber / totalNumber) * 100));
}

function DetailMetricCard({
  label,
  subValue,
  toneValue,
  value,
}: {
  label: string;
  subValue?: string;
  toneValue?: unknown;
  value: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-white/5 bg-white/[0.03] px-3 py-2.5">
      <div className="truncate text-[10px] font-bold text-slate-500">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 truncate font-mono text-sm font-black tabular-nums',
          toneValue === undefined ? 'text-slate-100' : getToneClass(toneValue)
        )}
      >
        {value}
      </div>
      {subValue && (
        <div className="mt-1 truncate text-[10px] font-bold text-slate-600">
          {subValue}
        </div>
      )}
    </div>
  );
}

function DetailPanel({
  children,
  icon: Icon,
  title,
}: {
  children: React.ReactNode;
  icon: React.ElementType;
  title: string;
}) {
  return (
    <section className="min-w-0 border border-white/5 bg-[#0b1120]/70">
      <div className="flex h-10 items-center gap-2 border-b border-white/5 px-3">
        <Icon className="h-3.5 w-3.5 text-red-300" />
        <h3 className="truncate text-xs font-black text-slate-200">{title}</h3>
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

export default function TradingPage() {
  const isMobile = useIsMobile();
  const itemsPerPage = 10;
  const search = useSearch();
  const urlSymbol = React.useMemo(() => getUrlSymbol(search), [search]);
  const [activeMode, setActiveMode] =
    React.useState<TradingStudioMode>('CHART');
  const [selectedStock, setSelectedStock] = React.useState<Stock | null>(() =>
    urlSymbol ? makeSymbolStock(urlSymbol) : null
  );
  const [layoutMode, setLayoutMode] =
    React.useState<TradingLayoutMode>('wide');
  const [priceUpdate, setPriceUpdate] = React.useState<{
    price: string;
    timestamp: number;
  } | null>(null);
  const openStudioTab = useStudioNavigate();

  const { data: accountData } = useCurrentAccount();
  const {
    error: holdingsError,
    holdings,
    isLoading: holdingsLoading,
    portfolioSummary,
    refetch: refetchHoldings,
  } = useHoldings();
  const { orders } = useTodayOrders(accountData?.currentAccount?.id);
  const hasActiveOrders = React.useMemo(() => {
    return ((orders || []) as OrderLike[]).some(order =>
      ['UNREPORTED', 'WAIT_REPORTING', 'REPORTED', 'PART_SUCC'].includes(
        order.status || ''
      )
    );
  }, [orders]);

  const selectedStockSymbol = normalizeSymbol(
    getSelectedStockCode(selectedStock)
  );
  const selectedStockCode = urlSymbol || selectedStockSymbol;
  const selectedDisplayStock = React.useMemo(() => {
    if (!selectedStockCode) return null;
    if (selectedStock && selectedStockSymbol === selectedStockCode) {
      return selectedStock;
    }
    return makeSymbolStock(selectedStockCode);
  }, [selectedStock, selectedStockCode, selectedStockSymbol]);
  const selectedHolding = React.useMemo(
    () =>
      holdings.find(
        holding => normalizeSymbol(holding.stockCode) === selectedStockCode
      ) || null,
    [holdings, selectedStockCode]
  );
  const accountName =
    accountData?.currentAccount?.accountName || 'DEMO_PRO_001';
  const totalAsset = accountData?.currentAccount?.totalAsset;
  const activeOrderCount = React.useMemo(() => {
    return ((orders || []) as OrderLike[]).filter(order =>
      ['UNREPORTED', 'WAIT_REPORTING', 'REPORTED', 'PART_SUCC'].includes(
        order.status || ''
      )
    ).length;
  }, [orders]);

  const openTradingTab = React.useCallback((mode: TradingStudioMode) => {
    setActiveMode(mode);
  }, []);

  React.useEffect(() => {
    if (!urlSymbol) return;
    if (selectedStockSymbol === urlSymbol) return;
    setSelectedStock(makeSymbolStock(urlSymbol));
  }, [selectedStockSymbol, urlSymbol]);

  const handleSelectedStockChange = React.useCallback(
    (stock: Stock | null) => {
      const nextSymbol = normalizeSymbol(getSelectedStockCode(stock));
      if (!nextSymbol) {
        if (!urlSymbol) setSelectedStock(null);
        return;
      }

      setSelectedStock(stock);
      if (nextSymbol === urlSymbol) return;
      openStudioTab(buildHoldingsSymbolPath(nextSymbol, search));
    },
    [openStudioTab, search, urlSymbol]
  );

  const handleHoldingSelect = React.useCallback(
    (holding: Position) => {
      handleSelectedStockChange(makeHoldingStock(holding));
      openTradingTab('ORDER');
    },
    [handleSelectedStockChange, openTradingTab]
  );

  const renderTradingToolbar = () => (
    <TooltipProvider delayDuration={120}>
      <div className="flex h-12 shrink-0 items-center justify-between gap-2 overflow-hidden bg-[#07111f]/95 px-3 shadow-[inset_0_-1px_0_rgba(148,163,184,0.05)]">
        <nav
          className="flex h-full min-w-0 flex-1 items-stretch"
          aria-label="交易工作区"
        >
          <Tabs
            value={activeMode}
            onValueChange={mode => openTradingTab(mode as TradingStudioMode)}
            className="flex h-full min-w-0 max-w-full"
          >
            <TabsList className="flex h-full min-w-0 justify-start gap-5 overflow-x-auto rounded-none bg-transparent p-0 text-slate-500 no-scrollbar">
              {studioModes.map(mode => {
                const isActive = activeMode === mode.id;

                return (
                  <TabsTrigger
                    key={mode.id}
                    value={mode.id}
                    className={cn(
                      'relative h-full shrink-0 rounded-none bg-transparent px-0 text-[12px] font-bold text-slate-500 shadow-none transition-colors after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:rounded-full after:bg-transparent hover:text-slate-200 focus-visible:ring-red-500/70 focus-visible:ring-offset-0 data-[state=active]:bg-transparent data-[state=active]:text-red-200 data-[state=active]:shadow-none data-[state=active]:after:bg-red-400',
                      isActive
                        ? 'text-red-200'
                        : 'text-slate-500 hover:text-slate-200'
                    )}
                  >
                    {mode.label}
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </Tabs>
        </nav>

        <div className="flex min-w-0 shrink-0 items-center gap-2">
          <div className="hidden h-8 items-center gap-2 rounded-md bg-white/[0.025] px-2.5 lg:flex">
            <span className="max-w-24 truncate font-mono text-[10px] font-bold text-slate-300">
              {selectedStockCode || '待选标的'}
            </span>
            <span className="h-3 w-px bg-white/10" />
            <span
              className={cn(
                'inline-flex items-center gap-1.5 whitespace-nowrap text-[10px] font-bold',
                hasActiveOrders ? 'text-amber-200' : 'text-slate-500'
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  hasActiveOrders
                    ? 'bg-amber-300 shadow-[0_0_8px_rgba(252,211,77,0.65)]'
                    : 'bg-slate-600'
                )}
              />
              委托 {activeOrderCount}
            </span>
          </div>

          {isTerminalMode(activeMode) && (
            <div
              className="hidden items-center gap-1 rounded-md bg-white/[0.025] p-1 sm:flex"
              role="group"
              aria-label="布局切换"
            >
              {layoutModes.map(mode => {
                const Icon = mode.icon;
                const isActive = layoutMode === mode.id;

                return (
                  <Tooltip key={mode.id}>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        onClick={() => setLayoutMode(mode.id)}
                        className={cn(
                          'flex h-8 w-8 items-center justify-center rounded-md text-slate-500 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70',
                          isActive
                            ? 'bg-red-500/15 text-red-100'
                            : 'hover:bg-white/[0.05] hover:text-slate-200'
                        )}
                        aria-label={`${mode.label}布局`}
                        aria-pressed={isActive}
                      >
                        <Icon className="h-3.5 w-3.5" />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent side="bottom">
                      {mode.label}布局
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </TooltipProvider>
  );

  const renderDetailSections = () => {
    const account = accountData?.currentAccount;
    const displayName =
      selectedHolding?.instrumentName ||
      selectedDisplayStock?.name ||
      selectedStockCode ||
      '待选标的';
    const displayCode = selectedStockCode || '--';
    const lastPrice =
      selectedHolding?.lastPrice ??
      selectedDisplayStock?.quote?.lastPrice ??
      selectedDisplayStock?.currentPrice ??
      null;
    const changePercent =
      selectedHolding?.changePercent ??
      selectedDisplayStock?.quote?.changePercent ??
      null;
    const profitLoss = selectedHolding?.profitLoss ?? null;
    const profitRate = selectedHolding?.profitRate ?? null;
    const todayProfitLoss = selectedHolding?.todayProfitLoss ?? null;
    const todayProfitRate =
      selectedHolding?.todayProfitRate ?? changePercent ?? null;
    const volume = selectedHolding?.volume ?? null;
    const canUseVolume = selectedHolding?.canUseVolume ?? null;
    const frozenVolume = selectedHolding?.frozenVolume ?? null;
    const onRoadVolume = selectedHolding?.onRoadVolume ?? null;
    const yesterdayVolume = selectedHolding?.yesterdayVolume ?? null;
    const avgPrice = selectedHolding?.avgPrice ?? null;
    const volumeNumber = toFiniteNumber(volume);
    const avgPriceNumber = toFiniteNumber(avgPrice);
    const costAmount =
      volumeNumber !== null && avgPriceNumber !== null
        ? volumeNumber * avgPriceNumber
        : null;
    const portfolioMarketValue =
      portfolioSummary?.totalMarketValue ?? account?.marketValue ?? null;
    const portfolioMarketValueNumber = toFiniteNumber(portfolioMarketValue);
    const marketValue = selectedHolding?.marketValue ?? null;
    const marketValueNumber = toFiniteNumber(marketValue);
    const marketValuePercent =
      selectedHolding?.marketValuePercent ??
      (marketValueNumber !== null &&
      portfolioMarketValueNumber !== null &&
      portfolioMarketValueNumber > 0
        ? (marketValueNumber / portfolioMarketValueNumber) * 100
        : null);
    const availablePercent = getProgressPercent(canUseVolume, volume);
    const frozenPercent = getProgressPercent(frozenVolume, volume);
    const totalPortfolioAsset =
      totalAsset ?? portfolioSummary?.totalAsset ?? account?.totalAsset ?? null;
    const cashAmount = portfolioSummary?.cash ?? account?.cash ?? null;
    const frozenCash = account?.frozenCash ?? null;
    const totalPortfolioProfit =
      portfolioSummary?.totalProfitLoss ?? account?.totalProfitLoss ?? null;
    const totalPortfolioProfitPercent =
      portfolioSummary?.totalProfitLossPercent ??
      account?.profitLossPercent ??
      null;

    const summaryMetrics = [
      {
        label: '最新价',
        subValue: `涨跌幅 ${formatPercentMetric(changePercent)}`,
        toneValue: changePercent,
        value: formatPriceMetric(lastPrice),
      },
      {
        label: '持仓市值',
        subValue: `组合占比 ${formatPercentMetric(marketValuePercent, false)}`,
        value: formatCompactCurrency(marketValue),
      },
      {
        label: '浮动盈亏',
        subValue: `收益率 ${formatPercentMetric(profitRate)}`,
        toneValue: profitLoss ?? profitRate,
        value: formatSignedCurrency(profitLoss),
      },
      {
        label: '可用库存',
        subValue: `冻结 ${formatShares(frozenVolume)} 股`,
        value: `${formatShares(canUseVolume)} 股`,
      },
    ];

    const holdingRows = [
      { label: '证券代码', value: displayCode },
      { label: '持仓数量', value: `${formatShares(volume)} 股` },
      { label: '可用数量', value: `${formatShares(canUseVolume)} 股` },
      { label: '冻结数量', value: `${formatShares(frozenVolume)} 股` },
      { label: '在途数量', value: `${formatShares(onRoadVolume)} 股` },
      { label: '昨日持仓', value: `${formatShares(yesterdayVolume)} 股` },
      { label: '平均成本', value: formatPriceMetric(avgPrice) },
      { label: '持仓成本', value: formatCompactCurrency(costAmount) },
      {
        label: '更新时间',
        value: formatDateTime(
          selectedHolding?.quoteTime ?? selectedHolding?.updatedAt
        ),
      },
    ];

    const executionRows = [
      { label: '账户', value: accountName },
      { label: '总资产', value: formatCompactCurrency(totalPortfolioAsset) },
      { label: '可用资金', value: formatCompactCurrency(cashAmount) },
      { label: '冻结资金', value: formatCompactCurrency(frozenCash) },
      {
        label: '组合盈亏',
        toneValue: totalPortfolioProfit,
        value: formatSignedCurrency(totalPortfolioProfit),
      },
      {
        label: '组合收益率',
        toneValue: totalPortfolioProfitPercent,
        value: formatPercentMetric(totalPortfolioProfitPercent),
      },
    ];

    return (
      <div className="border-t border-white/5 bg-[#08101d] px-4 py-4">
        <div className="mx-auto flex max-w-[1480px] flex-col gap-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0">
              <div className="text-[10px] font-black uppercase text-red-300">
                Detail
              </div>
              <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
                <h2 className="truncate text-lg font-black text-slate-100">
                  {displayName} 详情
                </h2>
                <span className="rounded border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-[10px] font-bold text-slate-500">
                  {displayCode}
                </span>
                <span
                  className={cn(
                    'rounded border px-2 py-1 text-[10px] font-black',
                    selectedHolding
                      ? 'border-red-500/25 bg-red-500/10 text-red-200'
                      : 'border-amber-400/20 bg-amber-500/10 text-amber-200'
                  )}
                >
                  {selectedHolding ? '已持仓' : '未持仓'}
                </span>
              </div>
            </div>

            <button
              type="button"
              disabled={!selectedStockCode}
              onClick={() => {
                if (selectedStockCode)
                  openStudioTab(`/stock/${selectedStockCode}`);
              }}
              className="inline-flex h-8 items-center gap-2 rounded-md border border-white/10 px-3 text-xs font-bold text-slate-300 transition-colors hover:border-red-500/35 hover:bg-red-500/10 hover:text-red-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <BarChart3 className="h-3.5 w-3.5" />
              个股信息
            </button>
          </div>

          <div className="grid grid-cols-2 gap-2 xl:grid-cols-4">
            {summaryMetrics.map(metric => (
              <DetailMetricCard
                key={metric.label}
                label={metric.label}
                subValue={metric.subValue}
                toneValue={metric.toneValue}
                value={metric.value}
              />
            ))}
          </div>

          <div className="grid gap-3 xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
            <DetailPanel icon={ClipboardList} title="持仓明细">
              <div className="grid gap-2 sm:grid-cols-2 2xl:grid-cols-3">
                {holdingRows.map(row => (
                  <div
                    key={row.label}
                    className="min-w-0 rounded-md border border-white/5 bg-[#08101d]/80 px-3 py-2"
                  >
                    <div className="truncate text-[10px] font-bold text-slate-500">
                      {row.label}
                    </div>
                    <div className="mt-1 truncate font-mono text-xs font-black text-slate-200">
                      {row.value}
                    </div>
                  </div>
                ))}
              </div>

              <div className="mt-3 border-t border-white/5 pt-3">
                <div className="flex items-center justify-between gap-3 text-[10px] font-bold text-slate-500">
                  <span>可用库存</span>
                  <span className="font-mono text-slate-300">
                    {availablePercent.toFixed(0)}%
                  </span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
                  <div
                    className="h-full rounded-full bg-red-400"
                    style={{ width: `${availablePercent}%` }}
                  />
                </div>
                <div className="mt-2 flex items-center justify-between gap-3 text-[10px] font-bold text-slate-600">
                  <span>冻结库存</span>
                  <span className="font-mono">{frozenPercent.toFixed(0)}%</span>
                </div>
              </div>
            </DetailPanel>

            <DetailPanel icon={Wallet} title="账户与执行">
              <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                {executionRows.map(row => (
                  <div
                    key={row.label}
                    className="min-w-0 rounded-md border border-white/5 bg-[#08101d]/80 px-3 py-2"
                  >
                    <div className="truncate text-[10px] font-bold text-slate-500">
                      {row.label}
                    </div>
                    <div
                      className={cn(
                        'mt-1 truncate font-mono text-xs font-black',
                        row.toneValue === undefined
                          ? 'text-slate-200'
                          : getToneClass(row.toneValue)
                      )}
                    >
                      {row.value}
                    </div>
                  </div>
                ))}
              </div>
            </DetailPanel>
          </div>

          <DetailPanel icon={Activity} title="交易视图">
            <div className="grid gap-2 md:grid-cols-3">
              <DetailMetricCard
                label="当前模块"
                subValue="固定高度交易插件"
                value={getTradingStudioMode(activeMode).label}
              />
              <DetailMetricCard
                label="盘口布局"
                subValue="图表、五档、下单区"
                value={layoutMode === 'wide' ? '三栏' : '两栏'}
              />
              <DetailMetricCard
                label="活跃委托"
                subValue={hasActiveOrders ? '待处理委托' : '执行队列空闲'}
                value={`${activeOrderCount} 笔`}
              />
            </div>

            <div className="mt-3 grid gap-2 md:grid-cols-3">
              <div className="rounded-md border border-white/5 bg-[#08101d]/80 px-3 py-2">
                <div className="text-[10px] font-bold text-slate-500">
                  当日盈亏
                </div>
                <div
                  className={cn(
                    'mt-1 font-mono text-xs font-black',
                    getToneClass(todayProfitLoss)
                  )}
                >
                  {formatSignedCurrency(todayProfitLoss)}
                </div>
              </div>
              <div className="rounded-md border border-white/5 bg-[#08101d]/80 px-3 py-2">
                <div className="text-[10px] font-bold text-slate-500">
                  当日涨跌幅
                </div>
                <div
                  className={cn(
                    'mt-1 font-mono text-xs font-black',
                    getToneClass(todayProfitRate)
                  )}
                >
                  {formatPercentMetric(todayProfitRate)}
                </div>
              </div>
              <div className="rounded-md border border-white/5 bg-[#08101d]/80 px-3 py-2">
                <div className="text-[10px] font-bold text-slate-500">
                  账户类型
                </div>
                <div className="mt-1 truncate font-mono text-xs font-black text-slate-200">
                  {account?.accountType || selectedHolding?.accountType || '--'}
                </div>
              </div>
            </div>
          </DetailPanel>
        </div>
      </div>
    );
  };

  const renderTerminalLayout = () => (
    <div className="h-full min-h-0 overflow-y-auto bg-[#08101d] custom-scrollbar">
      <div className="flex h-[680px] shrink-0 flex-col bg-[#08101d]">
        <TradingInstrumentHeader
          accountCash={accountData?.currentAccount?.cash}
          selectedStock={selectedDisplayStock}
          stockCode={selectedStockCode}
        />
        {renderTradingToolbar()}
        <div className="min-h-0 flex-1">
          {activeMode === 'ORDERS' ? (
            renderOrdersPanel()
          ) : activeMode === 'TRADES' ? (
            renderTradesPanel()
          ) : activeMode === 'ACCOUNT' ? (
            renderAccountPanel()
          ) : (
            <ResizablePanelGroup direction="horizontal" className="h-full">
              <ResizablePanel
                id="trading-left-chart"
                order={1}
                defaultSize={layoutMode === 'wide' ? 60 : 78}
                minSize={40}
                className="relative overflow-hidden bg-[#08101d]"
              >
                <TradingChart stockCode={selectedStockCode} />
              </ResizablePanel>

              <ResizableHandle className="w-px bg-white/5 transition-colors hover:bg-red-500/40" />

              {layoutMode === 'wide' ? (
                <>
                  <ResizablePanel
                    id="trading-mid-depth"
                    order={2}
                    defaultSize={20}
                    minSize={15}
                    className="relative overflow-hidden border-r border-white/5 bg-[#0b1120]"
                  >
                    <MarketDepth
                      selectedStock={selectedDisplayStock}
                      onPriceSelect={price =>
                        setPriceUpdate({ price, timestamp: Date.now() })
                      }
                    />
                  </ResizablePanel>

                  <ResizableHandle className="w-px bg-white/5 transition-colors hover:bg-red-500/40" />

                  <ResizablePanel
                    id="trading-right-trade"
                    order={3}
                    defaultSize={20}
                    minSize={15}
                    className="flex flex-col overflow-hidden bg-[#08101d]"
                  >
                    <ResizablePanelGroup
                      direction="vertical"
                      className="h-full w-full"
                    >
                      <ResizablePanel
                        id="trading-right-trade-internal"
                        order={1}
                        defaultSize={hasActiveOrders ? 65 : 100}
                        minSize={40}
                        className="relative overflow-hidden"
                      >
                        <div className="h-full w-full overflow-y-auto custom-scrollbar bg-[#0b1120]/60">
                          <TradingCard
                            initialStockCode={urlSymbol}
                            onStockSelect={handleSelectedStockChange}
                            priceUpdate={priceUpdate}
                          />
                        </div>
                      </ResizablePanel>

                      {hasActiveOrders && (
                        <>
                          <ResizableHandle className="h-px bg-white/5 transition-colors hover:bg-red-500/40" />
                          <ResizablePanel
                            id="trading-right-orders-internal"
                            order={2}
                            defaultSize={35}
                            minSize={20}
                            className="relative overflow-hidden bg-[#09111f]"
                          >
                            <ActiveOrders
                              accountId={accountData?.currentAccount?.id}
                            />
                          </ResizablePanel>
                        </>
                      )}
                    </ResizablePanelGroup>
                  </ResizablePanel>
                </>
              ) : (
                <ResizablePanel
                  id="trading-right-combined"
                  order={2}
                  defaultSize={22}
                  minSize={20}
                  className="flex flex-col overflow-hidden border-l border-white/5 bg-[#08101d]"
                >
                  <ResizablePanelGroup
                    direction="vertical"
                    className="h-full w-full"
                  >
                    <ResizablePanel
                      id="trading-right-combined-depth"
                      order={1}
                      defaultSize={30}
                      minSize={10}
                      className="relative overflow-hidden bg-[#0b1120]"
                    >
                      <MarketDepth
                        selectedStock={selectedDisplayStock}
                        onPriceSelect={price =>
                          setPriceUpdate({ price, timestamp: Date.now() })
                        }
                      />
                    </ResizablePanel>

                    <ResizableHandle className="h-px bg-white/5 transition-colors hover:bg-red-500/40" />

                    <ResizablePanel
                      id="trading-right-combined-trade"
                      order={2}
                      defaultSize={hasActiveOrders ? 50 : 70}
                      minSize={30}
                      className="relative overflow-hidden"
                    >
                      <div className="h-full w-full overflow-y-auto custom-scrollbar bg-[#0b1120]/60">
                        <TradingCard
                          initialStockCode={urlSymbol}
                          onStockSelect={handleSelectedStockChange}
                          priceUpdate={priceUpdate}
                        />
                      </div>
                    </ResizablePanel>

                    {hasActiveOrders && (
                      <>
                        <ResizableHandle className="h-px bg-white/5 transition-colors hover:bg-red-500/40" />
                        <ResizablePanel
                          id="trading-right-combined-orders"
                          order={3}
                          defaultSize={20}
                          minSize={10}
                          className="relative overflow-hidden bg-[#09111f]"
                        >
                          <ActiveOrders
                            accountId={accountData?.currentAccount?.id}
                          />
                        </ResizablePanel>
                      </>
                    )}
                  </ResizablePanelGroup>
                </ResizablePanel>
              )}
            </ResizablePanelGroup>
          )}
        </div>
      </div>
      {renderDetailSections()}
    </div>
  );

  const renderOrdersPanel = () => (
    <div className="flex h-full min-h-0 flex-col bg-[#0b1120] p-3">
      <Tabs
        defaultValue="today_orders"
        className="flex min-h-0 flex-1 flex-col"
      >
        <TabsList className="mb-3 flex h-8 w-fit gap-1 rounded-md border border-white/10 bg-white/[0.04] p-0.5">
          <TabsTrigger value="today_orders" className={compactTabTriggerClass}>
            当日委托
          </TabsTrigger>
          <TabsTrigger
            value="history_orders"
            className={compactTabTriggerClass}
          >
            历史委托
          </TabsTrigger>
        </TabsList>
        <TabsContent
          value="today_orders"
          className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <OrderRecords
            accountId={accountData?.currentAccount?.id}
            filterType="all"
            viewMode="table"
          />
        </TabsContent>
        <TabsContent
          value="history_orders"
          className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <OrderRecords
            accountId={accountData?.currentAccount?.id}
            filterType="history"
            viewMode="table"
          />
        </TabsContent>
      </Tabs>
    </div>
  );

  const renderTradesPanel = () => (
    <div className="flex h-full min-h-0 flex-col bg-[#0b1120] p-3">
      <Tabs
        defaultValue="today_trades"
        className="flex min-h-0 flex-1 flex-col"
      >
        <TabsList className="mb-3 flex h-8 w-fit gap-1 rounded-md border border-white/10 bg-white/[0.04] p-0.5">
          <TabsTrigger value="today_trades" className={compactTabTriggerClass}>
            当日成交
          </TabsTrigger>
          <TabsTrigger
            value="history_trades"
            className={compactTabTriggerClass}
          >
            历史成交
          </TabsTrigger>
        </TabsList>
        <TabsContent
          value="today_trades"
          className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <TradeRecords initialTimeFilter="today" itemsPerPage={itemsPerPage} />
        </TabsContent>
        <TabsContent
          value="history_trades"
          className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <TradeRecords
            initialTimeFilter="30days"
            itemsPerPage={itemsPerPage}
          />
        </TabsContent>
      </Tabs>
    </div>
  );

  const renderAccountPanel = () => (
    <div className="h-full min-h-0 overflow-y-auto bg-[#0b1120] p-4 custom-scrollbar">
      <div className="mx-auto max-w-5xl">
        <div className="mb-4 border-b border-white/5 pb-3">
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">
            Account
          </div>
          <div className="mt-1 flex min-w-0 items-center gap-3">
            <h2 className="text-lg font-bold text-slate-100">账户资产</h2>
            <span className="truncate font-mono text-xs font-bold text-slate-500">
              {accountName}
            </span>
          </div>
        </div>
        <AccountInfo summary={accountData?.currentAccount} />
      </div>
    </div>
  );

  const content = renderTerminalLayout();

  const sidebar = (
    <TradingHoldingsSidebar
      accountName={accountName}
      error={holdingsError}
      holdings={holdings}
      isLoading={holdingsLoading}
      onAccountOpen={() => openTradingTab('ACCOUNT')}
      onHoldingSelect={handleHoldingSelect}
      onRefresh={refetchHoldings}
      onStockInfoOpen={holding =>
        openStudioTab(`/stock/${normalizeSymbol(holding.stockCode)}`)
      }
      portfolioSummary={portfolioSummary}
      selectedStockCode={selectedStockCode}
      totalAsset={totalAsset}
    />
  );

  const statusBarLeft = React.useMemo(
    () => (
      <>
        <span className="inline-flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
          交易连接正常
        </span>
        <span className="text-slate-700">|</span>
        <span>{accountName}</span>
        <span className="text-slate-700">|</span>
        <span className="font-mono">
          {typeof totalAsset === 'number'
            ? `¥${totalAsset.toLocaleString()}`
            : '资产读取中'}
        </span>
      </>
    ),
    [accountName, totalAsset]
  );

  const statusBarRight = React.useMemo(
    () => (
      <>
        <span className="inline-flex items-center gap-2">
          <Activity className="h-3 w-3 text-red-400" />
          {selectedStockCode || '待选标的'}
        </span>
        <span className="text-slate-700">|</span>
        <span>活跃委托 {activeOrderCount}</span>
        <span className="text-slate-700">|</span>
        <span>{layoutMode === 'wide' ? '三栏' : '两栏'}</span>
      </>
    ),
    [activeOrderCount, layoutMode, selectedStockCode]
  );

  if (isMobile) {
    return <MobileTradingPage />;
  }

  return (
    <StudioWorkbench
      activeMode={activeMode}
      className="h-full min-h-0"
      content={content}
      isPage
      modes={studioModes}
      onModeChange={mode => openTradingTab(mode as TradingStudioMode)}
      sidebar={sidebar}
      sidebarSizing={{
        defaultWidth: 312,
        maxWidth: 420,
        minWidth: 260,
        storageScope: 'trading-studio',
      }}
      showSidebar
      statusBarLeft={statusBarLeft}
      statusBarRight={statusBarRight}
      theme={{
        icon: ArrowLeftRight,
        name: 'red',
        title: '持仓',
      }}
    />
  );
}
