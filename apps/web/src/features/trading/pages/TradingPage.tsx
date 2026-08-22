import { Activity, ArrowLeftRight } from 'lucide-react';
import * as React from 'react';
import { useSearch } from 'wouter';

import { StudioWorkbench } from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import type { Position } from '@/features/portfolio/types';
import {
  StockDetailWorkspace,
  type StockWorkspaceView,
} from '@/features/stocks/components';
import { holdingsWorkspaceModes } from '@/features/stocks/components/stockWorkspaceConfig';
import type { Stock } from '@/shared/types';

import { TradingHoldingsSidebar } from '../components/TradingHoldingsSidebar';
import { useTodayOrders } from '../hooks';

type OrderLike = { status?: string | null };

const ACTIVE_ORDER_STATUSES = new Set([
  'UNREPORTED',
  'WAIT_REPORTING',
  'REPORTED',
  'PART_SUCC',
]);

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

function getInitialView(search: string): StockWorkspaceView {
  const requested = new URLSearchParams(search).get('mode')?.toUpperCase();
  return holdingsWorkspaceModes.some(mode => mode.id === requested)
    ? (requested as StockWorkspaceView)
    : 'CHART';
}

function makeSymbolStock(symbol: string): Stock {
  return {
    id: symbol,
    stockCode: symbol,
    name: symbol,
    quote: { lastPrice: 0, changePercent: 0 },
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

export default function TradingPage() {
  const search = useSearch();
  const openStudioTab = useStudioNavigate();
  const urlSymbol = React.useMemo(() => getUrlSymbol(search), [search]);
  const initialSide = React.useMemo(() => getUrlSide(search), [search]);
  const [activeView, setActiveView] = React.useState<StockWorkspaceView>(() =>
    getInitialView(search)
  );

  const { data: accountData } = useCurrentAccount();
  const {
    error: holdingsError,
    holdings,
    isLoading: holdingsLoading,
    portfolioSummary,
    refetch: refetchHoldings,
  } = useHoldings();
  const account = accountData?.currentAccount;
  const { orders } = useTodayOrders(account?.id);

  const selectedHolding = React.useMemo(
    () =>
      holdings.find(
        holding => normalizeSymbol(holding.stockCode) === urlSymbol
      ) || null,
    [holdings, urlSymbol]
  );
  const fallbackSymbol = normalizeSymbol(holdings[0]?.stockCode);
  const selectedStockCode = urlSymbol || fallbackSymbol;
  const selectedStock = React.useMemo(
    () =>
      selectedHolding
        ? makeHoldingStock(selectedHolding)
        : selectedStockCode
          ? makeSymbolStock(selectedStockCode)
          : null,
    [selectedHolding, selectedStockCode]
  );
  const activeOrderCount = React.useMemo(
    () =>
      ((orders || []) as OrderLike[]).filter(order =>
        ACTIVE_ORDER_STATUSES.has(order.status || '')
      ).length,
    [orders]
  );
  const accountName = account?.accountName || 'DEMO_PRO_001';

  React.useEffect(() => {
    if (urlSymbol || !fallbackSymbol) return;
    openStudioTab(buildHoldingsSymbolPath(fallbackSymbol, search));
  }, [fallbackSymbol, openStudioTab, search, urlSymbol]);

  const handleStockSelect = React.useCallback(
    (stock: Stock | null) => {
      const symbol = normalizeSymbol(stock?.stockCode || stock?.id);
      if (!symbol || symbol === urlSymbol) return;
      openStudioTab(buildHoldingsSymbolPath(symbol, search));
    },
    [openStudioTab, search, urlSymbol]
  );

  const handleHoldingSelect = React.useCallback(
    (holding: Position) => {
      handleStockSelect(makeHoldingStock(holding));
      setActiveView('ORDER');
    },
    [handleStockSelect]
  );

  const content = (
    <StockDetailWorkspace
      accountId={account?.id}
      accountName={accountName}
      accountSummary={account}
      activeOrderCount={activeOrderCount}
      activeView={activeView}
      context="holdings"
      hasActiveOrders={activeOrderCount > 0}
      holding={selectedHolding}
      holdings={holdings}
      initialSide={initialSide}
      onOpenDetail={
        selectedStockCode
          ? () => openStudioTab(`/stock/${selectedStockCode}`)
          : undefined
      }
      onStockSelect={handleStockSelect}
      onViewChange={setActiveView}
      portfolioSummary={portfolioSummary}
      selectedStock={selectedStock}
      stockCode={selectedStockCode}
    />
  );

  const sidebar = (
    <TradingHoldingsSidebar
      accountName={accountName}
      error={holdingsError}
      holdings={holdings}
      isLoading={holdingsLoading}
      onAccountOpen={() => setActiveView('ACCOUNT')}
      onHoldingSelect={handleHoldingSelect}
      onRefresh={refetchHoldings}
      onStockInfoOpen={holding =>
        openStudioTab(`/stock/${normalizeSymbol(holding.stockCode)}`)
      }
      portfolioSummary={portfolioSummary}
      selectedStockCode={selectedStockCode}
      totalAsset={account?.totalAsset}
    />
  );

  return (
    <StudioWorkbench
      activeMode={activeView}
      className="h-full min-h-0"
      content={content}
      isPage
      modes={holdingsWorkspaceModes}
      onModeChange={mode => setActiveView(mode as StockWorkspaceView)}
      sidebar={sidebar}
      sidebarSizing={{
        defaultWidth: 312,
        maxWidth: 420,
        minWidth: 260,
        storageScope: 'trading-studio',
      }}
      showSidebar
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            交易连接正常
          </span>
          <span className="text-slate-700">|</span>
          <span>{accountName}</span>
          <span className="text-slate-700">|</span>
          <span className="font-mono">
            {typeof account?.totalAsset === 'number'
              ? `¥${account.totalAsset.toLocaleString()}`
              : '资产读取中'}
          </span>
        </>
      }
      statusBarRight={
        <>
          <span className="inline-flex items-center gap-2">
            <Activity className="h-3 w-3 text-red-400" />
            {selectedStockCode || '待选标的'}
          </span>
          <span className="text-slate-700">|</span>
          <span>活跃委托 {activeOrderCount}</span>
        </>
      }
      theme={{ icon: ArrowLeftRight, name: 'blue', title: '持仓' }}
    />
  );
}
