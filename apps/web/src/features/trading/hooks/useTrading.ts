import { useMemo, useCallback, useEffect, useState } from 'react';
import { gql as urqlGql, useQuery, useMutation } from 'urql';
import type { CombinedError, RequestPolicy } from 'urql';

import { gql } from '@/generated/gql';
import type { Trading_ManualOrderAttemptsQuery } from '@/generated/gql/graphql';
import { KLinePeriod, PageDirection } from '@/generated/gql/graphql';

import {
  ConfirmManualOrderMutation,
  ManualOrderAttemptsQuery,
  ManualOrderCapabilitiesQuery,
  PreviewManualOrderMutation,
} from '../manualOrderOperations';

export type ManualOrderAttemptFeed =
  Trading_ManualOrderAttemptsQuery['manualOrderAttempts'];
export type ManualOrderAttemptItem = ManualOrderAttemptFeed['items'][number];

export interface ManualOrderAttemptsState {
  asOf: string | null;
  error: CombinedError | undefined;
  feed: ManualOrderAttemptFeed | null;
  hasSuccessfulData: boolean;
  isRefreshing: boolean;
  items: ManualOrderAttemptItem[];
  lastUpdatedAt: string | null;
  loading: boolean;
  refresh: () => void;
  requiresAttentionCount: number;
  totalCount: number;
  truncated: boolean;
  activeCount: number;
}

function resolveKLinePeriod(period: string): KLinePeriod | undefined {
  return Object.values(KLinePeriod).find(value => value === period);
}

/**
 * 获取今日委托列表
 */
export const GetTodayOrdersQuery = gql(`
  query Trading_TodayOrders($accountId: String) {
    todayOrders(accountId: $accountId) {
      id
      sysid
      stockCode
      stockName
      type
      status
      price
      volume
      tradedVolume
      tradedPrice
      strategyName
      orderRemark
      time
    }
  }
`);

/**
 * 获取今日成交列表
 */
export const GetTodayTradesQuery = gql(`
  query Trading_TodayTrades($accountId: String) {
    todayTrades(accountId: $accountId) {
      tradedId
      orderId
      stockCode
      stockName
      orderType
      direction
      tradedPrice
      tradedVolume
      tradedAmount
      tradedTime
      strategyName
      orderRemark
    }
  }
`);

/**
 * 获取历史委托列表
 */
export const GetHistoryOrdersQuery = gql(`
  query Trading_HistoryOrders($accountId: String!, $startDate: String!, $endDate: String!) {
    historyOrders(accountId: $accountId, startDate: $startDate, endDate: $endDate) {
      id
      sysid
      stockCode
      stockName
      type
      status
      price
      volume
      tradedVolume
      tradedPrice
      time
    }
  }
`);

/**
 * 获取历史成交列表
 */
export const GetHistoryTradesQuery = gql(`
  query Trading_HistoryTrades($accountId: String!, $startDate: String!, $endDate: String!) {
    historyTrades(accountId: $accountId, startDate: $startDate, endDate: $endDate) {
      tradedId
      orderId
      stockCode
      stockName
      orderType
      direction
      tradedPrice
      tradedVolume
      tradedAmount
      tradedTime
    }
  }
`);

/**
 * 获取分时数据 (Ticks)
 */
export const GetTicksQuery = urqlGql`
  query Trading_GetTicks($stockCode: String!, $startTime: DateTime, $endTime: DateTime, $limit: Int, $order: String! = "desc") {
    ticks(stockCode: $stockCode, startTime: $startTime, endTime: $endTime, limit: $limit, order: $order) {
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
`;

/**
 * 获取K线数据 (K-Lines)
 */
export const GetKLinesQuery = gql(`
  query Trading_GetKLines($stockCode: String!, $period: KLinePeriod!, $startTime: DateTime, $endTime: DateTime, $order: String! = "desc") {
    klines(stockCode: $stockCode, period: $period, startTime: $startTime, endTime: $endTime, order: $order) {
      stockCode
      period
      time
      open
      high
      low
      close
      preClose
      volume
      amount
    }
  }
`);

/**
 * 撤销订单
 */
export const CancelOrderMutation = gql(`
  mutation Trading_CancelOrder($input: CancelOrderInput!) {
    cancelOrder(input: $input) {
      success
      message
      orderId
      clientOrderId
      status
    }
  }
`);

/**
 * 今日委托 Hook
 */
export function useTodayOrders(accountId?: string) {
  const [result, reexecuteQuery] = useQuery({
    query: GetTodayOrdersQuery,
    variables: { accountId },
    pause: !accountId,
  });

  const refresh = useCallback(() => {
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [reexecuteQuery]);

  return useMemo(
    () => ({
      orders: result.data?.todayOrders || [],
      loading: result.fetching,
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.error, refresh]
  );
}

/**
 * 今日成交 Hook
 */
export function useTodayTrades(accountId?: string) {
  const [result, reexecuteQuery] = useQuery({
    query: GetTodayTradesQuery,
    variables: { accountId },
    pause: !accountId,
  });

  const refresh = useCallback(() => {
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [reexecuteQuery]);

  return useMemo(
    () => ({
      trades: result.data?.todayTrades || [],
      loading: result.fetching,
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.error, refresh]
  );
}

/**
 * 历史委托 Hook
 */
export function useHistoryOrders(
  accountId: string,
  startDate: string,
  endDate: string
) {
  const [result, reexecuteQuery] = useQuery({
    query: GetHistoryOrdersQuery,
    variables: { accountId, startDate, endDate },
    pause: !accountId || !startDate || !endDate,
  });

  const refresh = useCallback(() => {
    if (!accountId) return;
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [accountId, reexecuteQuery]);

  return useMemo(
    () => ({
      orders: result.data?.historyOrders || [],
      loading: result.fetching,
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.error, refresh]
  );
}

/**
 * 历史成交 Hook
 */
export function useHistoryTrades(
  accountId: string,
  startDate: string,
  endDate: string
) {
  const [result, reexecuteQuery] = useQuery({
    query: GetHistoryTradesQuery,
    variables: { accountId, startDate, endDate },
    pause: !accountId || !startDate || !endDate,
  });

  const refresh = useCallback(() => {
    if (!accountId) return;
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [accountId, reexecuteQuery]);

  return useMemo(
    () => ({
      trades: result.data?.historyTrades || [],
      loading: result.fetching,
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.error, refresh]
  );
}

/**
 * 撤销订单 Hook
 */
export function useCancelOrder() {
  const [result, executeMutation] = useMutation(CancelOrderMutation);

  const cancelOrder = useCallback(
    async (orderId: string | number, accountId?: string) => {
      // 确保 orderId 是整数
      const id = typeof orderId === 'string' ? parseInt(orderId, 10) : orderId;

      return executeMutation({
        input: {
          orderId: id,
          accountId,
        },
      });
    },
    [executeMutation]
  );

  return useMemo(
    () => ({
      cancelOrder,
      fetching: result.fetching,
      error: result.error,
      data: result.data,
    }),
    [cancelOrder, result.fetching, result.error, result.data]
  );
}

export function useManualOrderCapabilities(
  accountId: string | undefined,
  instrumentCode: string
) {
  const normalizedCode = instrumentCode.trim().toUpperCase();
  const canQuery =
    Boolean(accountId) && /^\d{6}\.(SH|SZ|BJ)$/.test(normalizedCode);
  const [result, reexecuteQuery] = useQuery({
    query: ManualOrderCapabilitiesQuery,
    variables: {
      accountId: accountId || '',
      instrumentCode: normalizedCode,
    },
    pause: !canQuery,
    requestPolicy: 'cache-and-network',
  });

  const refresh = useCallback(() => {
    if (!canQuery) return;
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [canQuery, reexecuteQuery]);

  const capabilities = result.data?.orderEntryCapabilities;
  const currentCapabilities =
    capabilities?.accountId === accountId &&
    capabilities?.instrumentCode === normalizedCode
      ? capabilities
      : null;

  return useMemo(
    () => ({
      capabilities: currentCapabilities,
      error: result.error,
      loading: result.fetching,
      refresh,
    }),
    [currentCapabilities, refresh, result.error, result.fetching]
  );
}

export function usePreviewManualOrder() {
  const [result, executeMutation] = useMutation(PreviewManualOrderMutation);

  return useMemo(
    () => ({
      execute: executeMutation,
      loading: result.fetching,
    }),
    [executeMutation, result.fetching]
  );
}

export function useConfirmManualOrder() {
  const [result, executeMutation] = useMutation(ConfirmManualOrderMutation);

  return useMemo(
    () => ({
      execute: executeMutation,
      loading: result.fetching,
    }),
    [executeMutation, result.fetching]
  );
}

const FAST_MANUAL_ORDER_PHASES = new Set([
  'QUEUED',
  'DELIVERED',
  'AGENT_ACKNOWLEDGED',
]);

function normalizedManualOrderPhase(value: unknown) {
  return String(value || '')
    .trim()
    .toUpperCase();
}

function isDocumentVisible() {
  return (
    typeof document === 'undefined' || document.visibilityState === 'visible'
  );
}

/**
 * 从服务端恢复手动委托请求，并按阶段自适应刷新。
 * 请求事实只来自 manualOrderAttempts，不写入 localStorage，也不依赖交易卡片。
 */
export function useManualOrderAttempts(
  accountId?: string,
  limit = 50
): ManualOrderAttemptsState {
  const accountKey = accountId || '';
  const [result, reexecuteQuery] = useQuery({
    query: ManualOrderAttemptsQuery,
    variables: { accountId: accountId || undefined, limit },
    pause: !accountId,
    requestPolicy: 'network-only',
  });
  const [isVisible, setIsVisible] = useState(isDocumentVisible);
  const [dataAccountKey, setDataAccountKey] = useState(accountKey);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);

  useEffect(() => {
    setDataAccountKey(accountKey);
    setLastUpdatedAt(null);
  }, [accountKey]);

  const refresh = useCallback(() => {
    if (!accountId) return;
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [accountId, reexecuteQuery]);

  useEffect(() => {
    if (!accountId || typeof document === 'undefined') return;
    const handleVisibilityChange = () => {
      const visible = document.visibilityState === 'visible';
      setIsVisible(visible);
      if (visible) refresh();
    };
    const handleFocus = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleFocus);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleFocus);
    };
  }, [accountId, refresh]);

  const candidateFeed = result.data?.manualOrderAttempts ?? null;
  const feed =
    dataAccountKey === accountKey &&
    (!accountId ||
      !candidateFeed ||
      candidateFeed.items.every(item => item.accountId === accountId))
      ? candidateFeed
      : null;
  const items = useMemo(() => feed?.items ?? [], [feed?.items]);
  const hasFastPhase = items.some(item =>
    FAST_MANUAL_ORDER_PHASES.has(normalizedManualOrderPhase(item.phase))
  );
  const hasReconcilePhase = items.some(
    item => normalizedManualOrderPhase(item.phase) === 'RECONCILE_REQUIRED'
  );
  const pollingInterval = hasFastPhase ? 2_000 : hasReconcilePhase ? 10_000 : 0;

  useEffect(() => {
    if (!accountId || !isVisible || !feed || pollingInterval <= 0) return;
    const timer = window.setInterval(refresh, pollingInterval);
    return () => window.clearInterval(timer);
  }, [accountId, feed, isVisible, pollingInterval, refresh]);

  useEffect(() => {
    if (feed?.asOf) setLastUpdatedAt(feed.asOf);
  }, [feed?.asOf]);

  return useMemo(
    () => ({
      activeCount: feed?.activeCount ?? 0,
      asOf: feed?.asOf ?? null,
      error: result.error,
      feed,
      hasSuccessfulData: feed !== null,
      isRefreshing: result.fetching && feed !== null,
      items,
      lastUpdatedAt: lastUpdatedAt ?? feed?.asOf ?? null,
      loading: result.fetching,
      refresh,
      requiresAttentionCount: feed?.requiresAttentionCount ?? 0,
      totalCount: feed?.totalCount ?? 0,
      truncated: feed?.truncated ?? false,
    }),
    [feed, items, lastUpdatedAt, refresh, result.error, result.fetching]
  );
}

/**
 * 分时数据 Hook
 */
export function useTicks(
  stockCode: string,
  startTime?: string,
  endTime?: string,
  options: {
    limit?: number;
    order?: 'asc' | 'desc';
    pause?: boolean;
    requestPolicy?: RequestPolicy;
  } = {}
) {
  const [result, reexecuteQuery] = useQuery({
    query: GetTicksQuery,
    variables: {
      stockCode,
      startTime,
      endTime,
      limit: options.limit,
      order: options.order || 'desc',
    },
    pause: options.pause || !stockCode,
    requestPolicy: options.requestPolicy || 'cache-and-network',
  });

  const refresh = useCallback(() => {
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [reexecuteQuery]);

  return useMemo(
    () => ({
      data: result.data?.ticks || [],
      loading: result.fetching,
      stale: result.stale,
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.stale, result.error, refresh]
  );
}

/**
 * 获取K线分页数据 (K-Lines Page)
 */
export const GetKLinesPageQuery = gql(`
  query Trading_GetKLinesPage($page: KLinePageInput!) {
    klinesPage(page: $page) {
      items {
        stockCode
        period
        time
        open
        high
        low
        close
        preClose
        volume
        amount
      }
      pageInfo {
        hasNextPage
        hasPreviousPage
        startCursor
        endCursor
      }
    }
  }
`);

export function useKLines(
  stockCode: string,
  period: string,
  startTime?: string,
  endTime?: string,
  options: {
    order?: 'asc' | 'desc';
    pause?: boolean;
    requestPolicy?: RequestPolicy;
  } = {}
) {
  const resolvedPeriod = resolveKLinePeriod(period);
  const [result, reexecuteQuery] = useQuery({
    query: GetKLinesQuery,
    variables: {
      stockCode,
      period: resolvedPeriod ?? KLinePeriod.Day_1,
      startTime,
      endTime,
      order: options.order || 'desc',
    },
    pause: options.pause || !stockCode || !resolvedPeriod,
    requestPolicy: options.requestPolicy || 'cache-and-network',
  });

  const refresh = useCallback(() => {
    reexecuteQuery({ requestPolicy: 'network-only' });
  }, [reexecuteQuery]);

  return useMemo(
    () => ({
      data: result.data?.klines || [],
      loading: result.fetching,
      stale: result.stale,
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.stale, result.error, refresh]
  );
}

/**
 * K线分页数据 Hook
 * @param stockCode 股票代码
 * @param period K线周期
 * @param limit 每页数量
 * @param cursor 游标（时间）
 * @param direction 方向 (PREV: 向前/历史, NEXT: 向后/最新)
 */
export function useKLinesPage(
  stockCode: string,
  period: string,
  limit: number = 200,
  cursor?: string | null
) {
  const resolvedPeriod = resolveKLinePeriod(period);
  const [result] = useQuery({
    query: GetKLinesPageQuery,
    variables: {
      page: {
        stockCode,
        period: resolvedPeriod,
        limit,
        cursor,
        direction: PageDirection.Prev, // Default to fetching history
      },
    },
    pause: !stockCode || !resolvedPeriod,
  });

  return useMemo(
    () => ({
      data: result.data?.klinesPage?.items || [],
      pageInfo: result.data?.klinesPage?.pageInfo,
      loading: result.fetching,
      error: result.error,
    }),
    [result.data, result.fetching, result.error]
  );
}
