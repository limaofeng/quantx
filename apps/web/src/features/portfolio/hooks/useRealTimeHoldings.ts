import { useEffect, useMemo, useState } from 'react';
import { useQuery, useSubscription } from 'urql';

import type { Position } from '../types';

import {
  LatestMarketQuotesQuery,
  MarketQuotesSubscription,
} from './usePortfolio';

export type LiveMarketQuote = {
  stockCode: string;
  currentPrice: number;
  change?: number | null;
  changePercent?: number | null;
  high: number;
  low: number;
  open: number;
  preClose?: number | null;
  volume: number;
  time: string;
};

interface UseLatestMarketQuotesOptions {
  stockCodes: string[];
  enabled?: boolean;
}

export function useLatestMarketQuotes({
  stockCodes,
  enabled = true,
}: UseLatestMarketQuotesOptions) {
  const symbols = useMemo(
    () => Array.from(new Set(stockCodes.filter(Boolean))).sort(),
    [stockCodes]
  );
  const [quotes, setQuotes] = useState<Map<string, LiveMarketQuote>>(new Map());

  useEffect(() => {
    const wanted = new Set(symbols);
    setQuotes(previous => {
      const next = new Map(
        Array.from(previous.entries()).filter(([stockCode]) =>
          wanted.has(stockCode)
        )
      );
      return next.size === previous.size ? previous : next;
    });
  }, [symbols]);

  const [latestQuotesResult, refreshLatestQuotes] = useQuery({
    query: LatestMarketQuotesQuery,
    variables: { stockList: symbols },
    pause: !enabled || symbols.length === 0,
    requestPolicy: 'cache-and-network',
  });
  const [subscriptionResult] = useSubscription({
    query: MarketQuotesSubscription,
    variables: { stockList: symbols },
    pause: !enabled || symbols.length === 0,
  });

  useEffect(() => {
    const rows = latestQuotesResult.data?.latestMarketQuotes;
    if (!rows?.length) return;
    setQuotes(previous => {
      const next = new Map(previous);
      for (const quote of rows) {
        next.set(quote.stockCode, {
          stockCode: quote.stockCode,
          currentPrice: quote.lastPrice,
          change: quote.change,
          changePercent: quote.changePercent,
          high: quote.high,
          low: quote.low,
          open: quote.open,
          preClose: quote.preClose,
          volume: quote.volume,
          time: quote.time,
        });
      }
      return next;
    });
  }, [latestQuotesResult.data?.latestMarketQuotes]);

  useEffect(() => {
    const quote = subscriptionResult.data?.marketQuotes;
    if (!quote) return;
    setQuotes(previous => {
      const next = new Map(previous);
      next.set(quote.stockCode, quote);
      return next;
    });
  }, [subscriptionResult.data?.marketQuotes]);

  const latestQuoteAt = Array.from(quotes.values()).reduce<string | undefined>(
    (latest, quote) => (!latest || quote.time > latest ? quote.time : latest),
    undefined
  );

  return {
    quotes,
    isConnected: enabled && quotes.size > 0 && !subscriptionResult.error,
    error: subscriptionResult.error || latestQuotesResult.error,
    latestQuoteAt,
    refreshLatestQuotes: () =>
      refreshLatestQuotes({ requestPolicy: 'network-only' }),
  };
}

interface UseRealTimeHoldingsOptions {
  holdings: Position[];
  enabled?: boolean;
}

/**
 * Overlay Engine hot-cache and WebSocket quotes on the durable broker snapshot.
 */
export function useRealTimeHoldings({
  holdings,
  enabled = true,
}: UseRealTimeHoldingsOptions) {
  const stockCodes = useMemo(
    () => holdings.map(holding => holding.stockCode),
    [holdings]
  );
  const quoteState = useLatestMarketQuotes({ stockCodes, enabled });

  const realTimeHoldings = useMemo(
    () =>
      holdings.map(holding => {
        const quote = quoteState.quotes.get(holding.stockCode);
        if (!quote) return holding;

        const avgPrice = holding.avgPrice || 0;
        const volume = holding.volume || 0;
        const currentPrice = quote.currentPrice;
        const preClosePrice = quote.preClose || 0;
        const marketValue = currentPrice * volume;
        const profitLoss = (currentPrice - avgPrice) * volume;
        const profitRate =
          avgPrice > 0 && volume > 0
            ? (profitLoss / (avgPrice * volume)) * 100
            : 0;
        const todayProfitLoss =
          preClosePrice > 0 ? (currentPrice - preClosePrice) * volume : 0;
        const todayProfitRate =
          preClosePrice > 0
            ? ((currentPrice - preClosePrice) / preClosePrice) * 100
            : 0;

        return {
          ...holding,
          lastPrice: currentPrice,
          marketValue,
          profitLoss,
          profitRate,
          todayProfitLoss,
          todayProfitRate,
          change: quote.change,
          changePercent: quote.changePercent,
          quoteTime: quote.time,
        };
      }),
    [holdings, quoteState.quotes]
  );

  return {
    holdings: realTimeHoldings,
    isConnected: quoteState.isConnected,
    error: quoteState.error,
    latestQuoteAt: quoteState.latestQuoteAt,
    refreshLatestQuotes: quoteState.refreshLatestQuotes,
  };
}
