import { useState, useEffect, useMemo } from 'react';
import { useSubscription } from 'urql';

import {
  getTickDateRange,
  parseMarketDate,
} from '@/components/trading-chart/utils/time-utils';
import { useTicks } from '@/features/trading/hooks/useTrading';
import { gql } from '@/generated/gql';
import { useTradingDays } from '@/hooks/useTradingDays';

const MarketTickSubscription = gql(`
  subscription Market_Ticks($stockList: [String!]!) {
    marketTicks(stockList: $stockList) {
      stockCode
      period
      time
      lastPrice
      open
      high
      low
      preClose
      volume
      amount
    }
  }
`);

type TradingTick = ReturnType<typeof useTicks>['data'][number] & {
  source?: string;
  sourceTime?: string | number | Date | null;
};

const getTickKey = (tick: TradingTick) => {
  const sourceTime = tick?.sourceTime ?? tick?.time;
  const timestamp = parseMarketDate(sourceTime)?.getTime() ?? NaN;
  return Number.isFinite(timestamp) ? timestamp : null;
};

const mergeTickLists = (...lists: TradingTick[][]) => {
  const byTime = new Map<number, TradingTick>();
  const withoutTime: TradingTick[] = [];

  lists.forEach(list => {
    list.forEach(tick => {
      const key = getTickKey(tick);
      if (key === null) {
        withoutTime.push(tick);
        return;
      }
      byTime.set(key, tick);
    });
  });

  return [...byTime.values(), ...withoutTime].sort((a, b) => {
    const left = getTickKey(a) ?? Number.MAX_SAFE_INTEGER;
    const right = getTickKey(b) ?? Number.MAX_SAFE_INTEGER;
    return left - right;
  });
};

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

  const {
    data: initialTicks,
    loading: initialLoading,
    refresh,
  } = useTicks(stockCode, startTime, endTime, {
    order: 'asc',
    pause: !startTime || !endTime,
    requestPolicy: 'network-only',
  });

  // 2. 实时行情订阅
  const [subResult] = useSubscription({
    query: MarketTickSubscription,
    variables: { stockList: [stockCode] },
    pause: !stockCode || !startTime || !endTime,
  });

  // 3. 本地合并数据状态
  const [mergedTicks, setMergedTicks] = useState<TradingTick[]>([]);

  useEffect(() => {
    setMergedTicks([]);
  }, [stockCode, mode]);

  useEffect(() => {
    if (!stockCode || !startTime || !endTime) return;

    const timer = window.setInterval(() => {
      refresh();
    }, 30_000);

    return () => window.clearInterval(timer);
  }, [endTime, refresh, startTime, stockCode]);

  // 当初始数据加载完成后，与订阅数据合并；不能让旧的整日缓存覆盖实时tick。
  useEffect(() => {
    if (initialTicks) {
      setMergedTicks(prev => mergeTickLists(prev, initialTicks));
    }
  }, [initialTicks]);

  // 监听订阅更新，合并新数据
  useEffect(() => {
    const newTickData = subResult.data?.marketTicks;
    if (newTickData && newTickData.stockCode === stockCode) {
      setMergedTicks(prev =>
        mergeTickLists(prev, [{ ...newTickData, source: 'marketTicks' }])
      );
    }
  }, [subResult.data, stockCode]);

  return {
    data: mergedTicks,
    loading: initialLoading && mergedTicks.length === 0,
    error: null, // 可以合并 initialError 和 subResult.error
  };
}
