import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useLiquidationData } from '@/features/portfolio/hooks/useLiquidationData';

const mocks = vi.hoisted(() => ({
  orders: [] as Array<Record<string, unknown>>,
  trades: [] as Array<Record<string, unknown>>,
}));

vi.mock('urql', () => ({
  useQuery: () => [
    { data: { liquidationSummary: undefined }, fetching: false },
    vi.fn(),
  ],
}));

vi.mock('@/features/dashboard/hooks', () => ({
  useCurrentAccount: () => ({
    data: { currentAccount: { id: 'ACCOUNT-1' } },
    loading: false,
  }),
}));

vi.mock('@/features/portfolio/hooks/useHoldings', () => ({
  useHoldings: () => ({
    holdings: [
      {
        avgPrice: 9.5,
        instrumentName: '测试股份',
        stockCode: '300917.SZ',
        volume: 100,
      },
    ],
    isLoading: false,
    refetch: vi.fn(),
  }),
}));

vi.mock('@/features/trading/hooks', () => ({
  useTodayOrders: () => ({
    loading: false,
    orders: mocks.orders,
    refresh: vi.fn(),
  }),
  useTodayTrades: () => ({
    loading: false,
    refresh: vi.fn(),
    trades: mocks.trades,
  }),
}));

describe('useLiquidationData actual fills', () => {
  beforeEach(() => {
    mocks.orders = [];
    mocks.trades = [];
  });

  it('maps each authoritative SELL trade and ignores order volume', () => {
    mocks.orders = [
      {
        direction: 'SELL',
        orderId: 'cancelled-order',
        orderStatus: 'CANCELLED',
        tradedVolume: 0,
        volume: 900,
      },
    ];
    mocks.trades = [
      {
        direction: 'SELL',
        orderId: 'order-1',
        stockCode: '300917.SZ',
        tradedId: 'trade-1',
        tradedPrice: 10.25,
        tradedTime: 1_787_978_400,
        tradedVolume: 40,
      },
      {
        direction: 'BUY',
        orderId: 'order-2',
        stockCode: '300917.SZ',
        tradedId: 'trade-2',
        tradedPrice: 10.2,
        tradedTime: 1_787_978_460,
        tradedVolume: 100,
      },
      {
        direction: 'SELL',
        orderId: 'order-3',
        stockCode: '300917.SZ',
        tradedId: 'trade-3',
        tradedPrice: 10.1,
        tradedTime: 1_787_978_520,
        tradedVolume: 0,
      },
    ];

    const { result } = renderHook(() => useLiquidationData());

    expect(result.current.todayOrders).toHaveLength(1);
    expect(result.current.liquidatedStocks).toEqual([
      expect.objectContaining({
        id: 'trade-trade-1',
        orderId: 'order-1',
        quantity: 40,
        sellPrice: 10.25,
        source: 'TRADE',
        status: 'FILLED',
      }),
    ]);
    expect(result.current.liquidatedStocks[0]).toMatchObject({
      realizedPnL: null,
      realizedPnLPercent: null,
    });
  });
});
