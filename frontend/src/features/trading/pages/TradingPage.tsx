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
import { StockDetailWorkbench } from '@/features/stocks/components';
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

const TRADING_COMPACT_BREAKPOINT = 900;

const studioModes: StudioMode[] = [
  { id: 'CHART', icon: BarChart3, label: '图表盘口' },
  { id: 'ORDER', icon: ArrowLeftRight, label: '下单' },
  { id: 'ORDERS', icon: ClipboardList, label: '委托' },
  { id: 'TRADES', icon: History, label: '成交' },
  { id: 'ACCOUNT', icon: Wallet, label: '账户' },
];

const layoutModeMeta: Record<
  TradingLayoutMode,
  {
    icon: React.ElementType;
    label: string;
  }
> = {
  standard: { icon: PanelLeft, label: '两栏' },
  wide: { icon: Columns, label: '三栏' },
};

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

function getUrlSide(search: string): 'BUY' | 'SELL' {
  return new URLSearchParams(search).get('side')?.toUpperCase() === 'SELL'
    ? 'SELL'
    : 'BUY';
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

function getNextLayoutMode(mode: TradingLayoutMode): TradingLayoutMode {
  return mode === 'wide' ? 'standard' : 'wide';
}

function useCompactTradingLayout() {
  const [isCompact, setIsCompact] = React.useState<boolean | undefined>(
    undefined
  );

  React.useEffect(() => {
    const mql = window.matchMedia(
      `(max-width: ${TRADING_COMPACT_BREAKPOINT}px)`
    );
    const onChange = () => {
      setIsCompact(window.innerWidth <= TRADING_COMPACT_BREAKPOINT);
    };

    mql.addEventListener('change', onChange);
    onChange();
    return () => mql.removeEventListener('change', onChange);
  }, []);

  return !!isCompact;
}

export default function TradingPage() {
  const isMobile = useIsMobile();
  const isCompactTrading = useCompactTradingLayout();
  const itemsPerPage = 10;
  const search = useSearch();
  const urlSymbol = React.useMemo(() => getUrlSymbol(search), [search]);
  const urlSide = React.useMemo(() => getUrlSide(search), [search]);
  const [activeMode, setActiveMode] = React.useState<TradingStudioMode>(() =>
    new URLSearchParams(search).get('mode')?.toUpperCase() === 'ORDER'
      ? 'ORDER'
      : 'CHART'
  );
  const [selectedStock, setSelectedStock] = React.useState<Stock | null>(() =>
    urlSymbol ? makeSymbolStock(urlSymbol) : null
  );
  const [layoutMode, setLayoutMode] = React.useState<TradingLayoutMode>('wide');
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

  const renderTradingToolbar = () => {
    const currentLayout = layoutModeMeta[layoutMode];
    const nextLayoutMode = getNextLayoutMode(layoutMode);
    const nextLayout = layoutModeMeta[nextLayoutMode];
    const LayoutIcon = currentLayout.icon;

    return (
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
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => setLayoutMode(nextLayoutMode)}
                    className="hidden h-8 w-8 items-center justify-center text-slate-500 transition-colors hover:text-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 sm:flex"
                    aria-label={`当前${currentLayout.label}布局，切换为${nextLayout.label}布局`}
                  >
                    <LayoutIcon className="h-4 w-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom">
                  切换为{nextLayout.label}布局
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        </div>
      </TooltipProvider>
    );
  };

  const renderDetailSections = () => (
    <StockDetailWorkbench
      accountName={accountName}
      accountType={
        accountData?.currentAccount?.accountType || selectedHolding?.accountType
      }
      activeModeLabel={getTradingStudioMode(activeMode).label}
      activeOrderCount={activeOrderCount}
      cash={portfolioSummary?.cash ?? accountData?.currentAccount?.cash}
      changePercent={
        selectedHolding?.changePercent ??
        selectedDisplayStock?.quote?.changePercent ??
        null
      }
      displayName={
        selectedHolding?.instrumentName ||
        selectedDisplayStock?.name ||
        selectedStockCode ||
        '待选标的'
      }
      frozenCash={accountData?.currentAccount?.frozenCash}
      hasActiveOrders={hasActiveOrders}
      holding={selectedHolding}
      lastPrice={
        selectedHolding?.lastPrice ??
        selectedDisplayStock?.quote?.lastPrice ??
        selectedDisplayStock?.currentPrice ??
        null
      }
      layoutLabel={layoutMode === 'wide' ? '三栏' : '两栏'}
      onOpenStockInfo={() => {
        if (selectedStockCode) openStudioTab(`/stock/${selectedStockCode}`);
      }}
      portfolioSummary={portfolioSummary}
      stockCode={selectedStockCode}
      totalAsset={totalAsset ?? portfolioSummary?.totalAsset}
    />
  );

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
                            initialSide={urlSide}
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
                          initialSide={urlSide}
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
          <TradeRecords
            accountId={accountData?.currentAccount?.id}
            initialTimeFilter="today"
            itemsPerPage={itemsPerPage}
          />
        </TabsContent>
        <TabsContent
          value="history_trades"
          className="mt-0 min-h-0 flex-1 overflow-hidden data-[state=inactive]:hidden"
        >
          <TradeRecords
            accountId={accountData?.currentAccount?.id}
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

  if (isMobile || isCompactTrading) {
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
