/**
 * useStrategyTicks - 策略实时 Tick 订阅 Hook
 *
 * 通过 GraphQL Subscription 订阅策略运行实例的实时行情数据。
 */

import { useEffect, useState } from 'react';
import { useSubscription } from 'urql';

import { gql } from '@/generated/gql';

// ===== GraphQL 定义 =====

const STRATEGY_TICKS_SUBSCRIPTION = gql(`
  subscription StrategyTicks($runId: String!) {
    strategyTicks(runId: $runId) {
      stockCode
      lastPrice
      volume
      amount
      bidPrice
      askPrice
      bidVolume
      askVolume
      openPrice
      highPrice
      lowPrice
      preClose
      time
    }
  }
`);

// ===== 类型定义 =====

export interface StrategyTickData {
  stockCode: string;
  lastPrice: number;
  volume: number;
  amount: number;
  bidPrice?: number;
  askPrice?: number;
  bidVolume?: number;
  askVolume?: number;
  openPrice?: number;
  highPrice?: number;
  lowPrice?: number;
  preClose?: number;
  time: string;
}

export interface UseStrategyTicksOptions {
  /** 暂停订阅 */
  paused?: boolean;
  /** 最大保留 Tick 数量（默认 100） */
  maxTicks?: number;
}

export interface UseStrategyTicksResult {
  /** Tick 列表 */
  ticks: StrategyTickData[];
  /** 最新 Tick */
  latestTick: StrategyTickData | null;
  /** 是否已连接 */
  isConnected: boolean;
  /** 错误信息 */
  error: Error | null;
}

// ===== Hook 实现 =====

export function useStrategyTicks(
  runId: string | null | undefined,
  options: UseStrategyTicksOptions = {}
): UseStrategyTicksResult {
  const { paused = false, maxTicks = 100 } = options;

  const [ticks, setTicks] = useState<StrategyTickData[]>([]);
  const [latestTick, setLatestTick] = useState<StrategyTickData | null>(null);

  // 订阅 Tick 数据
  const [{ data, error, fetching }] = useSubscription({
    query: STRATEGY_TICKS_SUBSCRIPTION as any,
    variables: { runId: runId ?? '' },
    pause: paused || !runId,
  });

  // 当收到新 Tick 时更新列表
  useEffect(() => {
    if (data?.strategyTicks) {
      const newTick: StrategyTickData = {
        stockCode: data.strategyTicks.stockCode,
        lastPrice: data.strategyTicks.lastPrice,
        volume: data.strategyTicks.volume,
        amount: data.strategyTicks.amount,
        bidPrice: data.strategyTicks.bidPrice,
        askPrice: data.strategyTicks.askPrice,
        bidVolume: data.strategyTicks.bidVolume,
        askVolume: data.strategyTicks.askVolume,
        openPrice: data.strategyTicks.openPrice,
        highPrice: data.strategyTicks.highPrice,
        lowPrice: data.strategyTicks.lowPrice,
        preClose: data.strategyTicks.preClose,
        time: data.strategyTicks.time,
      };

      setLatestTick(newTick);
      setTicks(prev => {
        const updated = [...prev, newTick];
        // 保持最大 Tick 数量
        if (updated.length > maxTicks) {
          return updated.slice(-maxTicks);
        }
        return updated;
      });
    }
  }, [data, maxTicks]);

  // 当 runId 改变时清空数据
  useEffect(() => {
    setTicks([]);
    setLatestTick(null);
  }, [runId]);

  return {
    ticks,
    latestTick,
    isConnected: fetching && !error,
    error: error ?? null,
  };
}
