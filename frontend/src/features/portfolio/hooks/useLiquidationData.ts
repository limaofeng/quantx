import { useCallback, useMemo } from 'react';
import { useQuery } from 'urql';

import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useTodayOrders, useTodayTrades } from '@/features/trading/hooks';

import type {
  LiquidatedStock,
  LiquidationSummaryData,
  LiquidationTodayOrder,
  LiquidationTodayTrade,
  Position,
  PortfolioSummaryData,
} from '../types';

import { useHoldings } from './useHoldings';
import { LiquidationSummaryQuery } from './usePortfolio';

interface UseLiquidationDataResult {
  accountId?: string;
  currentHoldings: Position[];
  error: Error | null;
  isLoading: boolean;
  liquidatedStocks: LiquidatedStock[];
  liquidationSummary?: LiquidationSummaryData;
  portfolioSummary?: PortfolioSummaryData;
  refetch: () => void;
  todayOrders: LiquidationTodayOrder[];
  todayTrades: LiquidationTodayTrade[];
}

function normalizeStockCode(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isSellOrderType(value: unknown) {
  const text = String(value ?? '').toUpperCase();
  return text === 'SELL' || text.endsWith('.SELL');
}

function getPositivePrice(...values: unknown[]) {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null && parsed > 0) return parsed;
  }
  return null;
}

function buildHoldingNameMap(holdings: Position[]) {
  return new Map(
    holdings.map(holding => [
      normalizeStockCode(holding.stockCode),
      holding.instrumentName || normalizeStockCode(holding.stockCode),
    ])
  );
}

function mapOrderToLiquidatedStock(
  order: LiquidationTodayOrder,
  holdingNameMap: Map<string, string>,
  holdings: Position[]
): LiquidatedStock {
  const stockCode = normalizeStockCode(order.stockCode);
  const matchingHolding = holdings.find(
    holding => normalizeStockCode(holding.stockCode) === stockCode
  );

  return {
    id: `order-${order.id}`,
    name: order.stockName || holdingNameMap.get(stockCode) || stockCode,
    orderId: order.id,
    originalCost: matchingHolding?.avgPrice ?? null,
    quantity: Number(order.tradedVolume || order.volume || 0),
    realizedPnL: null,
    realizedPnLPercent: null,
    sellDate: order.time,
    sellPrice: getPositivePrice(order.tradedPrice, order.price),
    source: 'ORDER',
    status: order.status,
    symbol: stockCode,
  };
}

/**
 * 清仓数据查询 Hook
 * 组合真实持仓、清仓预检、当日委托和成交回报。
 */
export function useLiquidationData(): UseLiquidationDataResult {
  const {
    error: holdingsError,
    holdings,
    isLoading: holdingsLoading,
    portfolioSummary,
    refetch: refetchHoldings,
  } = useHoldings();
  const {
    data: accountData,
    error: accountError,
    loading: accountLoading,
  } = useCurrentAccount();

  const accountId =
    accountData?.currentAccount?.id || portfolioSummary?.accountId;

  const [summaryResult, reexecuteSummaryQuery] = useQuery({
    query: LiquidationSummaryQuery,
    variables: { accountId },
  });

  const {
    error: ordersError,
    loading: ordersLoading,
    orders,
    refresh: refreshOrders,
  } = useTodayOrders(accountId);
  const {
    error: tradesError,
    loading: tradesLoading,
    refresh: refreshTrades,
    trades,
  } = useTodayTrades(accountId);
  const todayOrders = orders as LiquidationTodayOrder[];
  const todayTrades = trades as LiquidationTodayTrade[];

  const currentHoldings = useMemo(
    () =>
      holdings.filter(holding => {
        const volume = toFiniteNumber(holding.volume);
        return volume !== null && volume > 0;
      }),
    [holdings]
  );

  const liquidatedStocks = useMemo(() => {
    const holdingNameMap = buildHoldingNameMap(currentHoldings);

    return todayOrders
      .filter(order => isSellOrderType(order.type))
      .map(order =>
        mapOrderToLiquidatedStock(order, holdingNameMap, currentHoldings)
      );
  }, [currentHoldings, todayOrders]);

  const refetch = useCallback(() => {
    refetchHoldings();
    reexecuteSummaryQuery({ requestPolicy: 'network-only' });
    refreshOrders();
    refreshTrades();
  }, [
    refetchHoldings,
    reexecuteSummaryQuery,
    refreshOrders,
    refreshTrades,
  ]);

  const error =
    holdingsError || accountError || summaryResult.error || ordersError || tradesError;

  return {
    accountId,
    currentHoldings,
    error: error instanceof Error ? error : error ? new Error(String(error)) : null,
    isLoading:
      holdingsLoading ||
      accountLoading ||
      summaryResult.fetching ||
      ordersLoading ||
      tradesLoading,
    liquidatedStocks,
    liquidationSummary: summaryResult.data?.liquidationSummary,
    portfolioSummary,
    refetch,
    todayOrders,
    todayTrades,
  };
}
