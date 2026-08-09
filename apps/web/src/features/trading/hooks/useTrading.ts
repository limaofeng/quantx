import { useMemo, useCallback } from 'react';
import { gql as urqlGql, useQuery, useMutation } from 'urql';
import type { RequestPolicy } from 'urql';

import { gql } from '@/generated/gql';
import { KLinePeriod, PageDirection } from '@/generated/gql/graphql';

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
 * 创建订单
 */
export const PlaceOrderMutation = gql(`
  mutation Trading_PlaceOrder($input: OrderInput!) {
    placeOrder(input: $input) {
      success
      message
      orderId
      clientOrderId
      status
      order {
        id
        stockCode
        status
      }
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

/**
 * 创建订单 Hook
 */
export function useCreateOrder() {
  const [result, executeMutation] = useMutation(PlaceOrderMutation);

  const createOrder = useCallback(
    async (input: {
      stockCode: string;
      price: number;
      volume: number;
      type: string;
      priceType: string;
      accountId?: string;
      orderRemark?: string;
      strategyName?: string;
    }) => {
      return executeMutation({ input });
    },
    [executeMutation]
  );

  return useMemo(
    () => ({
      createOrder,
      loading: result.fetching,
      error: result.error,
      data: result.data,
    }),
    [createOrder, result.fetching, result.error, result.data]
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
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.error, refresh]
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
      error: result.error,
      refresh,
    }),
    [result.data, result.fetching, result.error, refresh]
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
