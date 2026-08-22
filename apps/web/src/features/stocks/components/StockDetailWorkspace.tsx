import { Activity, Columns, PanelLeft } from 'lucide-react';
import * as React from 'react';

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
import type {
  PortfolioSummaryData,
  Position,
} from '@/features/portfolio/types';
import {
  AccountInfo,
  MarketDepth,
  OrderRecords,
  TradeRecords,
  TradingCard,
} from '@/features/trading/components';
import { ActiveOrders } from '@/features/trading/components/ActiveOrders';
import { TradingInstrumentHeader } from '@/features/trading/components/TradingInstrumentHeader';
import type { Stock } from '@/shared/types';
import { cn } from '@/utils/cn';

import { useStockDisclosures, useStockWorkspaceFinancials } from '../hooks';

import { StockFinancialPanel } from './stock-detail-workbench/StockFinancialPanel';
import {
  StockAnnouncementsPanel,
  StockFinancialRail,
  StockOverviewPanel,
  StockSummaryStrip,
} from './stock-detail-workbench/StockResearchPanels';
import {
  detailWorkspaceModes,
  holdingsWorkspaceModes,
  type StockWorkspaceContext,
  type StockWorkspaceView,
} from './stockWorkspaceConfig';

type TradingLayoutMode = 'standard' | 'wide';

interface AccountSummary {
  accountName?: string | null;
  cash: number;
  frozenCash: number;
  marketValue: number;
  profitLossPercent?: number | null;
  totalAsset: number;
  totalProfitLoss?: number | null;
}

interface StockDetailWorkspaceProps {
  accountId?: string;
  accountName?: string;
  accountSummary?: AccountSummary | null;
  activeOrderCount?: number;
  activeView: StockWorkspaceView;
  context: StockWorkspaceContext;
  hasActiveOrders?: boolean;
  holding?: Position | null;
  holdings: Position[];
  initialSide?: 'BUY' | 'SELL';
  onOpenDetail?: () => void;
  onStockSelect?: (stock: Stock | null) => void;
  onViewChange: (view: StockWorkspaceView) => void;
  portfolioSummary?: PortfolioSummaryData;
  selectedStock: Stock | null;
  stockCode: string;
}

const compactTabTriggerClass =
  'h-7 rounded-md px-3 text-[11px] font-bold text-slate-500 transition-colors data-[state=active]:bg-blue-600 data-[state=active]:text-white dark:text-slate-400 dark:data-[state=active]:text-white';

function isTerminalView(view: StockWorkspaceView) {
  return ['CHART', 'ORDER', 'TRADING'].includes(view);
}

function WorkspaceToolbar({
  activeOrderCount,
  activeView,
  context,
  hasActiveOrders,
  layoutMode,
  onLayoutModeChange,
  onViewChange,
  stockCode,
}: {
  activeOrderCount: number;
  activeView: StockWorkspaceView;
  context: StockWorkspaceContext;
  hasActiveOrders: boolean;
  layoutMode: TradingLayoutMode;
  onLayoutModeChange: (mode: TradingLayoutMode) => void;
  onViewChange: (view: StockWorkspaceView) => void;
  stockCode: string;
}) {
  const modes =
    context === 'holdings' ? holdingsWorkspaceModes : detailWorkspaceModes;
  const nextLayoutMode = layoutMode === 'wide' ? 'standard' : 'wide';
  const LayoutIcon = layoutMode === 'wide' ? Columns : PanelLeft;

  return (
    <TooltipProvider delayDuration={120}>
      <div className="flex h-11 shrink-0 items-center justify-between gap-2 border-y border-white/5 bg-[#07111f]/95 px-3">
        <nav
          className="flex h-full min-w-0 flex-1 items-stretch"
          aria-label="个股工作区"
        >
          <Tabs
            value={activeView}
            onValueChange={value => onViewChange(value as StockWorkspaceView)}
            className="flex h-full min-w-0 max-w-full"
          >
            <TabsList className="flex h-full min-w-0 justify-start gap-5 overflow-x-auto rounded-none bg-transparent p-0 no-scrollbar">
              {modes.map(mode => (
                <TabsTrigger
                  key={mode.id}
                  value={mode.id}
                  className="relative h-full shrink-0 rounded-none bg-transparent px-0 text-[12px] font-bold text-slate-500 shadow-none after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-transparent hover:text-slate-200 focus-visible:ring-blue-400/70 focus-visible:ring-offset-0 data-[state=active]:bg-transparent data-[state=active]:text-blue-200 data-[state=active]:shadow-none data-[state=active]:after:bg-blue-500"
                >
                  {mode.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          <div className="hidden h-7 items-center gap-2 border border-white/5 bg-white/[0.02] px-2.5 lg:flex">
            <span className="max-w-24 truncate font-mono text-[10px] font-bold text-slate-300">
              {stockCode || '待选标的'}
            </span>
            <span className="h-3 w-px bg-white/10" />
            <span
              className={cn(
                'inline-flex items-center gap-1.5 text-[10px] font-bold',
                hasActiveOrders ? 'text-amber-200' : 'text-slate-500'
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  hasActiveOrders ? 'bg-amber-300' : 'bg-slate-600'
                )}
              />
              委托 {activeOrderCount}
            </span>
          </div>

          {isTerminalView(activeView) && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  onClick={() => onLayoutModeChange(nextLayoutMode)}
                  className="hidden h-7 w-7 items-center justify-center text-slate-500 transition-colors hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 sm:flex"
                  aria-label={`切换为${nextLayoutMode === 'wide' ? '三栏' : '两栏'}布局`}
                >
                  <LayoutIcon className="h-4 w-4" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                切换为{nextLayoutMode === 'wide' ? '三栏' : '两栏'}布局
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}

function OrdersPanel({ accountId }: { accountId?: string }) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#0b1120] p-3">
      <Tabs defaultValue="today" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mb-3 flex h-8 w-fit gap-1 rounded-md border border-white/10 bg-white/[0.04] p-0.5">
          <TabsTrigger value="today" className={compactTabTriggerClass}>
            当日委托
          </TabsTrigger>
          <TabsTrigger value="history" className={compactTabTriggerClass}>
            历史委托
          </TabsTrigger>
        </TabsList>
        <TabsContent
          value="today"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <OrderRecords
            accountId={accountId}
            filterType="all"
            viewMode="table"
          />
        </TabsContent>
        <TabsContent
          value="history"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <OrderRecords
            accountId={accountId}
            filterType="history"
            viewMode="table"
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function TradesPanel({ accountId }: { accountId?: string }) {
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#0b1120] p-3">
      <Tabs defaultValue="today" className="flex min-h-0 flex-1 flex-col">
        <TabsList className="mb-3 flex h-8 w-fit gap-1 rounded-md border border-white/10 bg-white/[0.04] p-0.5">
          <TabsTrigger value="today" className={compactTabTriggerClass}>
            当日成交
          </TabsTrigger>
          <TabsTrigger value="history" className={compactTabTriggerClass}>
            历史成交
          </TabsTrigger>
        </TabsList>
        <TabsContent
          value="today"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <TradeRecords
            accountId={accountId}
            initialTimeFilter="today"
            itemsPerPage={10}
          />
        </TabsContent>
        <TabsContent
          value="history"
          className="mt-0 min-h-0 flex-1 overflow-hidden"
        >
          <TradeRecords
            accountId={accountId}
            initialTimeFilter="30days"
            itemsPerPage={10}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function AccountPanel({
  accountName,
  accountSummary,
}: {
  accountName?: string;
  accountSummary?: AccountSummary | null;
}) {
  return (
    <div className="h-full min-h-0 overflow-y-auto bg-[#0b1120] p-4 custom-scrollbar">
      <div className="mx-auto max-w-5xl">
        <div className="mb-4 border-b border-white/5 pb-3">
          <div className="text-[10px] font-black uppercase tracking-[0.22em] text-slate-500">
            Account
          </div>
          <div className="mt-1 flex min-w-0 items-center gap-3">
            <h2 className="text-lg font-bold text-slate-100">账户资产</h2>
            <span className="truncate font-mono text-xs font-bold text-slate-500">
              {accountName || '--'}
            </span>
          </div>
        </div>
        <AccountInfo summary={accountSummary} />
      </div>
    </div>
  );
}

function TradingTerminal({
  accountId,
  hasActiveOrders,
  holdings,
  initialSide,
  layoutMode,
  onStockSelect,
  portfolioSummary,
  selectedStock,
  stockCode,
}: Pick<
  StockDetailWorkspaceProps,
  | 'accountId'
  | 'hasActiveOrders'
  | 'holdings'
  | 'initialSide'
  | 'onStockSelect'
  | 'portfolioSummary'
  | 'selectedStock'
  | 'stockCode'
> & { layoutMode: TradingLayoutMode }) {
  const [priceUpdate, setPriceUpdate] = React.useState<{
    price: string;
    timestamp: number;
  } | null>(null);
  const showActiveOrders = Boolean(hasActiveOrders);
  const depth = (
    <MarketDepth
      selectedStock={selectedStock}
      onPriceSelect={price => setPriceUpdate({ price, timestamp: Date.now() })}
    />
  );
  const orderTicket = (
    <div className="h-full overflow-y-auto bg-[#0b1120]/60 custom-scrollbar">
      <TradingCard
        holdings={holdings}
        initialSide={initialSide}
        initialStockCode={stockCode}
        onStockSelect={onStockSelect}
        portfolioSummary={portfolioSummary}
        priceUpdate={priceUpdate}
      />
    </div>
  );

  return (
    <ResizablePanelGroup direction="horizontal" className="h-full">
      <ResizablePanel
        id="stock-workspace-chart"
        order={1}
        defaultSize={layoutMode === 'wide' ? 60 : 76}
        minSize={40}
        className="relative overflow-hidden bg-[#08101d]"
      >
        <TradingChart stockCode={stockCode} />
      </ResizablePanel>
      <ResizableHandle className="w-px bg-white/5 hover:bg-blue-500/40" />

      {layoutMode === 'wide' ? (
        <>
          <ResizablePanel
            id="stock-workspace-depth"
            order={2}
            defaultSize={20}
            minSize={15}
            className="overflow-hidden bg-[#0b1120]"
          >
            {depth}
          </ResizablePanel>
          <ResizableHandle className="w-px bg-white/5 hover:bg-blue-500/40" />
          <ResizablePanel
            id="stock-workspace-ticket"
            order={3}
            defaultSize={20}
            minSize={15}
            className="overflow-hidden bg-[#08101d]"
          >
            <ResizablePanelGroup direction="vertical" className="h-full">
              <ResizablePanel
                id="stock-workspace-order-form"
                order={1}
                defaultSize={showActiveOrders ? 66 : 100}
                minSize={42}
              >
                {orderTicket}
              </ResizablePanel>
              {showActiveOrders && (
                <>
                  <ResizableHandle className="h-px bg-white/5 hover:bg-blue-500/40" />
                  <ResizablePanel
                    id="stock-workspace-active-orders"
                    order={2}
                    defaultSize={34}
                    minSize={20}
                    className="overflow-hidden bg-[#09111f]"
                  >
                    <ActiveOrders accountId={accountId} />
                  </ResizablePanel>
                </>
              )}
            </ResizablePanelGroup>
          </ResizablePanel>
        </>
      ) : (
        <ResizablePanel
          id="stock-workspace-combined"
          order={2}
          defaultSize={24}
          minSize={20}
          className="overflow-hidden bg-[#08101d]"
        >
          <ResizablePanelGroup direction="vertical" className="h-full">
            <ResizablePanel
              id="stock-workspace-combined-depth"
              order={1}
              defaultSize={30}
              minSize={12}
            >
              {depth}
            </ResizablePanel>
            <ResizableHandle className="h-px bg-white/5 hover:bg-blue-500/40" />
            <ResizablePanel
              id="stock-workspace-combined-ticket"
              order={2}
              defaultSize={showActiveOrders ? 50 : 70}
              minSize={32}
            >
              {orderTicket}
            </ResizablePanel>
            {showActiveOrders && (
              <>
                <ResizableHandle className="h-px bg-white/5 hover:bg-blue-500/40" />
                <ResizablePanel
                  id="stock-workspace-combined-orders"
                  order={3}
                  defaultSize={20}
                  minSize={10}
                >
                  <ActiveOrders accountId={accountId} />
                </ResizablePanel>
              </>
            )}
          </ResizablePanelGroup>
        </ResizablePanel>
      )}
    </ResizablePanelGroup>
  );
}

export function StockDetailWorkspace({
  accountId,
  accountName,
  accountSummary,
  activeOrderCount = 0,
  activeView,
  context,
  hasActiveOrders = false,
  holding,
  holdings,
  initialSide,
  onOpenDetail,
  onStockSelect,
  onViewChange,
  portfolioSummary,
  selectedStock,
  stockCode,
}: StockDetailWorkspaceProps) {
  const [layoutMode, setLayoutMode] = React.useState<TradingLayoutMode>('wide');
  const disclosures = useStockDisclosures(stockCode, 20);
  const financials = useStockWorkspaceFinancials(stockCode, 12);
  const lastPrice =
    holding?.lastPrice ??
    selectedStock?.quote?.lastPrice ??
    selectedStock?.currentPrice ??
    null;

  let content: React.ReactNode;
  if (activeView === 'OVERVIEW') {
    content = (
      <StockOverviewPanel
        disclosure={disclosures.summary}
        disclosureLoading={disclosures.isLoading}
        financialLoading={financials.isLoading}
        financialSummary={financials.summary}
        holding={holding}
        holdings={holdings}
        onStockSelect={onStockSelect}
        portfolioSummary={portfolioSummary}
        stockCode={stockCode}
      />
    );
  } else if (activeView === 'ANNOUNCEMENTS') {
    content = (
      <StockAnnouncementsPanel
        disclosure={disclosures.summary}
        error={disclosures.error}
        isLoading={disclosures.isLoading}
        isRefreshing={disclosures.isRefreshing}
        onRefresh={() => void disclosures.refresh()}
      />
    );
  } else if (activeView === 'FINANCIAL') {
    content = (
      <div className="grid h-full min-h-0 gap-2 overflow-y-auto bg-[#08101d] p-2 custom-scrollbar 2xl:grid-cols-[minmax(0,1fr)_330px]">
        <StockFinancialPanel
          error={financials.error}
          isLoading={financials.isLoading}
          onRetry={financials.refresh}
          statements={financials.statements}
          summary={financials.summary}
        />
        <StockFinancialRail
          financialLoading={financials.isLoading}
          financialSummary={financials.summary}
          holding={holding}
          holdings={holdings}
          onStockSelect={onStockSelect}
          portfolioSummary={portfolioSummary}
          stockCode={stockCode}
        />
      </div>
    );
  } else if (activeView === 'ORDERS') {
    content = <OrdersPanel accountId={accountId} />;
  } else if (activeView === 'TRADES') {
    content = <TradesPanel accountId={accountId} />;
  } else if (activeView === 'ACCOUNT') {
    content = (
      <AccountPanel accountName={accountName} accountSummary={accountSummary} />
    );
  } else {
    content = (
      <div className="flex h-full min-h-0 flex-col overflow-y-auto bg-[#08101d] custom-scrollbar">
        <div
          className={cn(
            'min-h-[560px] flex-1',
            context === 'holdings' && 'h-[600px] flex-none'
          )}
        >
          <TradingTerminal
            accountId={accountId}
            hasActiveOrders={hasActiveOrders}
            holdings={holdings}
            initialSide={initialSide}
            layoutMode={layoutMode}
            onStockSelect={onStockSelect}
            portfolioSummary={portfolioSummary}
            selectedStock={selectedStock}
            stockCode={stockCode}
          />
        </div>
        {context === 'holdings' && (
          <button
            type="button"
            className="block w-full p-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
            onClick={onOpenDetail}
            disabled={!onOpenDetail}
            aria-label="打开个股研究详情"
          >
            <StockSummaryStrip
              disclosure={disclosures.summary}
              financialSummary={financials.summary}
              holding={holding}
              lastPrice={lastPrice}
            />
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#08101d]">
      <TradingInstrumentHeader
        accountCash={accountSummary?.cash}
        holdings={holdings}
        portfolioSummary={portfolioSummary}
        selectedStock={selectedStock}
        stockCode={stockCode}
      />
      <WorkspaceToolbar
        activeOrderCount={activeOrderCount}
        activeView={activeView}
        context={context}
        hasActiveOrders={hasActiveOrders}
        layoutMode={layoutMode}
        onLayoutModeChange={setLayoutMode}
        onViewChange={onViewChange}
        stockCode={stockCode}
      />
      <div className="min-h-0 flex-1">{content}</div>
      {context === 'detail' && (
        <div className="flex h-6 shrink-0 items-center justify-between border-t border-white/5 bg-[#07111f] px-3 text-[9px] font-bold text-slate-600">
          <span className="inline-flex items-center gap-1.5">
            <Activity className="h-3 w-3 text-emerald-400" />
            行情、公告与财务模块独立刷新
          </span>
          <span className="font-mono">{stockCode}</span>
        </div>
      )}
    </div>
  );
}
