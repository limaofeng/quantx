import { useState, useEffect, useMemo } from 'react';
import { useSubscription } from 'urql';

import { getTickDateRange } from '@/components/trading-chart/utils/time-utils';
import { useTicks } from '@/features/trading/hooks/useTrading';
import { gql } from '@/generated/gql';
import { useTradingDays } from '@/hooks/useTradingDays';

// 定义订阅的 GraphQL
const MarketQuoteSubscription = gql(`
  subscription Market_Quotes($stockList: [String!]!) {
    marketQuotes(stockList: $stockList) {
      stockCode
      currentPrice
      time
      volume
      amount
      high
      low
      open
      preClose
      change
      changePercent
    }
  }
`);

export function useRealTimeTicks(stockCode: string, mode: '1d' | '5d' = '1d') {
  // 0. 获取交易日数据 (默认获取过去30天)
  const { tradingDays } = useTradingDays();

  // 1. 获取初始数据 (Ticks)
  // 根据 mode 计算 start/end time
  const { startTime, endTime } = useMemo(() => {
    // 只有当 stockCode 存在时才计算，避免无效初始计算
    if (!stockCode) return { startTime: undefined, endTime: undefined };
    return getTickDateRange(tradingDays, mode);
  }, [mode, tradingDays, stockCode]);

  const { data: initialTicks, loading: initialLoading } = useTicks(
    stockCode,
    startTime,
    endTime
  );

  // 2. 实时行情订阅
  const [subResult] = useSubscription({
    query: MarketQuoteSubscription as any,
    variables: { stockList: [stockCode] },
    pause: !stockCode,
  });

  // 3. 本地合并数据状态
  const [mergedTicks, setMergedTicks] = useState<any[]>([]);

  // 当初始数据加载完成后，初始化 mergedTicks
  // 注意：切换 mode 时，initialTicks 会变化，这里会重置
  useEffect(() => {
    if (initialTicks) {
      setMergedTicks(initialTicks);
    }
  }, [initialTicks]);

  // 监听订阅更新，合并新数据
  useEffect(() => {
    const newQuote = subResult.data?.marketQuotes;
    if (newQuote && newQuote.stockCode === stockCode) {
      setMergedTicks(prev => {
        // 将 Quote 转换为 Tick 格式
        const newTick = {
          stockCode: newQuote.stockCode,
          time: newQuote.time,
          lastPrice: newQuote.currentPrice,
          volume: newQuote.volume,
          amount: newQuote.amount,
          high: newQuote.high,
          low: newQuote.low,
          open: newQuote.open,
          preClose: newQuote.preClose,
          // 保持其他字段兼容性
          period: '1m', // 默认周期
        };

        const lastTick = prev[prev.length - 1];
        if (lastTick && lastTick.time === newTick.time) {
          // 更新最后一条
          return [...prev.slice(0, -1), newTick];
        } else {
          // 追加新数据
          return [...prev, newTick];
        }
      });
    }
  }, [subResult.data, stockCode]);

  return {
    data: mergedTicks,
    loading: initialLoading && mergedTicks.length === 0,
    error: null, // 可以合并 initialError 和 subResult.error
  };
}
