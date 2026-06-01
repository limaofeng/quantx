import { useMemo, useCallback } from 'react';
import { gql as urqlGql, useQuery, useMutation } from 'urql';

import { gql } from '@/generated/gql';

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
  query Trading_GetKLines($stockCode: String!, $period: KLinePeriod!, $startTime: DateTime, $endTime: DateTime) {
    klines(stockCode: $stockCode, period: $period, startTime: $startTime, endTime: $endTime) {
      stockCode
      period
      time
      open
      high
      low
      close
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
    }
  }
`);

/**
 * 今日委托 Hook
 */
export function useTodayOrders(accountId?: string) {
  const [result] = useQuery({
    query: GetTodayOrdersQuery as any,
    variables: { accountId },
    pause: !accountId,
  });

  return useMemo(
    () => ({
      orders: result.data?.todayOrders || [],
      loading: result.fetching,
      error: result.error,
    }),
    [result.data, result.fetching, result.error]
  );
}

/**
 * 今日成交 Hook
 */
export function useTodayTrades(accountId?: string) {
  const [result] = useQuery({
    query: GetTodayTradesQuery as any,
    variables: { accountId },
    pause: !accountId,
  });

  return useMemo(
    () => ({
      trades: result.data?.todayTrades || [],
      loading: result.fetching,
      error: result.error,
    }),
    [result.data, result.fetching, result.error]
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
  const [result] = useQuery({
    query: GetHistoryOrdersQuery as any,
    variables: { accountId, startDate, endDate },
    pause: !accountId || !startDate || !endDate,
  });

  return useMemo(
    () => ({
      orders: result.data?.historyOrders || [],
      loading: result.fetching,
      error: result.error,
    }),
    [result.data, result.fetching, result.error]
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
  const [result] = useQuery({
    query: GetHistoryTradesQuery as any,
    variables: { accountId, startDate, endDate },
    pause: !accountId || !startDate || !endDate,
  });

  return useMemo(
    () => ({
      trades: result.data?.historyTrades || [],
      loading: result.fetching,
      error: result.error,
    }),
    [result.data, result.fetching, result.error]
  );
}

/**
 * 撤销订单 Hook
 */
export function useCancelOrder() {
  const [result, executeMutation] = useMutation(CancelOrderMutation as any);

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
  const [result, executeMutation] = useMutation(PlaceOrderMutation as any);

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
  options: { limit?: number; order?: 'asc' | 'desc' } = {}
) {
  const [result] = useQuery({
    query: GetTicksQuery as any,
    variables: {
      stockCode,
      startTime,
      endTime,
      limit: options.limit,
      order: options.order || 'desc',
    },
    pause: !stockCode,
  });

  return useMemo(
    () => ({
      data: result.data?.ticks || [],
      loading: result.fetching,
      error: result.error,
      refresh: () => {}, // urql usually handles this via cache/subscription
    }),
    [result.data, result.fetching, result.error]
  );
}

/**
 * 获取K线分页数据 (K-Lines Page)
 */
export const GetKLinesPageQuery = gql(`
  query GetKLinesPage($page: KLinePageInput!) {
    klinesPage(page: $page) {
      items {
        stockCode
        period
        time
        open
        high
        low
        close
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
  endTime?: string
) {
  const [result] = useQuery({
    query: GetKLinesQuery as any,
    variables: { stockCode, period, startTime, endTime },
    pause: !stockCode || !period,
  });

  return useMemo(
    () => ({
      data: result.data?.klines || [],
      loading: result.fetching,
      error: result.error,
    }),
    [result.data, result.fetching, result.error]
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
  const [result] = useQuery({
    query: GetKLinesPageQuery as any,
    variables: {
      page: {
        stockCode,
        period,
        limit,
        cursor,
        direction: 'PREV', // Default to fetching history
      },
    },
    pause: !stockCode || !period,
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
