import { useEffect, useState } from 'react';
import { useSubscription } from 'urql';

import type { Position } from '../types';

interface MarketQuotesData {
  marketQuotes: {
    stockCode: string;
    currentPrice: number;
    change: number;
    changePercent: number;
    high: number;
    low: number;
    open: number;
    preClose: number;
    volume: number;
    time: string;
  };
}

interface UseRealTimeHoldingsOptions {
  holdings: Position[];
  enabled?: boolean;
}

/**
 * 实时持仓 Hook
 * 订阅持仓股票的实时价格更新
 */
export function useRealTimeHoldings({
  holdings,
  enabled = true,
}: UseRealTimeHoldingsOptions) {
  const [realTimeHoldings, setRealTimeHoldings] =
    useState<Position[]>(holdings);

  // 缓存实时报价数据
  const [quotesCache, setQuotesCache] = useState<
    Map<string, MarketQuotesData['marketQuotes']>
  >(new Map());

  // 获取所有持仓股票代码
  const stockCodes = holdings.map(h => h.stockCode);

  // 订阅实时报价
  const [subscriptionResult] = useSubscription<
    { symbols: string[] },
    MarketQuotesData
  >({
    query: `
      subscription marketQuotesSubscription($symbols: [String!]!) {
        marketQuotes(stockList: $symbols) {
          stockCode
          currentPrice
          change
          changePercent
          high
          low
          open
          preClose
          volume
          time
        }
      }
    `,
    variables: { symbols: stockCodes },
    pause: !enabled || stockCodes.length === 0,
  });

  // 当收到新的实时报价时,更新缓存
  useEffect(() => {
    if (!subscriptionResult.data?.marketQuotes) return;

    const quote = subscriptionResult.data.marketQuotes;

    setQuotesCache(prevCache => {
      const newCache = new Map(prevCache);
      newCache.set(quote.stockCode, quote);
      return newCache;
    });
  }, [subscriptionResult.data]);

  // 当持仓或报价缓存更新时,更新实时持仓数据
  useEffect(() => {
    if (quotesCache.size === 0) {
      setRealTimeHoldings(holdings);
      return;
    }

    const updated = holdings.map(holding => {
      const quote = quotesCache.get(holding.stockCode);
      if (!quote) return holding;

      // 计算新的市值和盈亏
      const avgPrice = holding.avgPrice || 0;
      const volume = holding.volume || 0;
      const currentPrice = quote.currentPrice;
      const preClosePrice = quote.preClose;

      const marketValue = currentPrice * volume;

      // 持仓盈亏(相对于成本价)
      const profitLoss = (currentPrice - avgPrice) * volume;
      const profitRate =
        avgPrice > 0 ? (profitLoss / (avgPrice * volume)) * 100 : 0;

      // 当日盈亏(相对于昨收价)
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
        // 添加当日盈亏字段
        todayProfitLoss,
        todayProfitRate,
        // 保存涨跌额和涨跌幅
        change: quote.change,
        changePercent: quote.changePercent,
        quoteTime: quote.time,
      };
    });

    setRealTimeHoldings(updated);
  }, [holdings, quotesCache]);

  return {
    holdings: realTimeHoldings,
    isConnected: !subscriptionResult.fetching && !subscriptionResult.error,
    error: subscriptionResult.error,
  };
}
