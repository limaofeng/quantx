import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useIntradayKLines } from '@/hooks/useIntradayKLines';

const mocks = vi.hoisted(() => ({
  refreshAuctionTicks: vi.fn(),
  refreshKLines: vi.fn(),
  useKLines: vi.fn(),
  useSubscription: vi.fn(),
  useTicks: vi.fn(),
  useTradingDays: vi.fn(),
}));

vi.mock('urql', () => ({
  useSubscription: mocks.useSubscription,
}));

vi.mock('@/features/trading/hooks/useTrading', () => ({
  useKLines: mocks.useKLines,
  useTicks: mocks.useTicks,
}));

vi.mock('@/hooks/useTradingDays', () => ({
  useTradingDays: mocks.useTradingDays,
}));

describe('useIntradayKLines loading coordination', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-20T07:00:00.000Z'));
    vi.clearAllMocks();

    mocks.useKLines.mockReturnValue({
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: false,
    });
    mocks.useTicks.mockReturnValue({
      data: [],
      error: undefined,
      loading: true,
      refresh: mocks.refreshAuctionTicks,
      stale: false,
    });
    mocks.useSubscription.mockReturnValue([
      { data: undefined, error: undefined },
    ]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts the primary K-line request from an authoritative date while the calendar refreshes', () => {
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: true,
      refreshing: false,
      tradingDays: [],
    });

    const { result } = renderHook(() =>
      useIntradayKLines('000001.SH', '1d', {
        targetTradingDate: '2026-08-19',
      })
    );

    expect(mocks.useKLines).toHaveBeenNthCalledWith(
      1,
      '000001.SH',
      'MIN_1',
      '2026-08-19 00:00:00',
      '2026-08-19 23:59:59',
      expect.objectContaining({
        pause: false,
        requestPolicy: 'cache-and-network',
      })
    );
    expect(result.current.loading).toBe(false);
  });

  it('does not keep the primary chart loading for a noncritical auction-tick refresh', () => {
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });

    const { result } = renderHook(() => useIntradayKLines('000001.SH', '1d'));

    expect(mocks.useTicks).toHaveBeenCalledWith(
      '000001.SH',
      expect.any(String),
      expect.any(String),
      expect.objectContaining({ requestPolicy: 'network-only' })
    );
    expect(result.current.loading).toBe(false);
  });

  it('keeps loading during call auction while auction ticks are the only possible chart data', () => {
    vi.setSystemTime(new Date('2026-08-20T01:20:00.000Z'));
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });

    const { result } = renderHook(() => useIntradayKLines('000001.SH', '1d'));

    expect(result.current.loading).toBe(true);
  });

  it('does not settle or start fallback from a cached empty stale result', () => {
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockReturnValue({
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: true,
    });

    const { result } = renderHook(() => useIntradayKLines('000001.SH', '1d'));

    expect(mocks.useKLines).toHaveBeenNthCalledWith(
      2,
      '000001.SH',
      'MIN_1',
      '2026-08-19 00:00:00',
      '2026-08-19 23:59:59',
      expect.objectContaining({ pause: true })
    );
    expect(result.current.loading).toBe(true);
  });

  it('renders cached bars immediately while refreshing them in the background', () => {
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          period: '1m',
          time: '2026-08-20 09:30:00',
          open: 3900,
          high: 3901,
          low: 3899,
          close: 3900,
          volume: 100,
          amount: 390000,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: true,
    });

    const { result } = renderHook(() => useIntradayKLines('000001.SH', '1d'));

    expect(result.current.data).toHaveLength(1);
    expect(result.current.loading).toBe(false);
  });

  it('does not expose stale bars after a rapid stock switch', () => {
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          period: '1m',
          time: '2026-08-20 09:30:00',
          open: 3900,
          high: 3901,
          low: 3899,
          close: 3900,
          volume: 100,
          amount: 390000,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: true,
    });

    const { result, rerender } = renderHook(
      ({ stockCode }) =>
        useIntradayKLines(stockCode, '1d', {
          targetTradingDate: '2026-08-20',
        }),
      { initialProps: { stockCode: '000001.SH' } }
    );
    expect(result.current.data).toHaveLength(1);

    rerender({ stockCode: '399001.SZ' });

    expect(result.current.data).toEqual([]);
  });

  it('keeps a WebSocket K-line update ahead of the initial cached bar', () => {
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          period: '1m',
          time: '2026-08-20 09:30:00',
          open: 10,
          high: 10,
          low: 10,
          close: 10,
          volume: 100,
          amount: 1000,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: true,
    });
    mocks.useTicks.mockReturnValue({
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshAuctionTicks,
      stale: false,
    });
    const emptyKlineSubscriptionResult = [
      { data: undefined, error: undefined },
    ];
    const liveKlineSubscriptionResult = [
      {
        data: {
          marketKlines: {
            stockCode: '000001.SH',
            period: '1m',
            time: '2026-08-20 09:30:00',
            open: 10,
            high: 11,
            low: 10,
            close: 11,
            volume: 120,
            amount: 1260,
          },
        },
        error: undefined,
      },
    ];
    const tickSubscriptionResult = [{ data: undefined, error: undefined }];
    let klineSubscriptionResult = emptyKlineSubscriptionResult;
    mocks.useSubscription.mockImplementation(
      ({ variables }: { variables?: { periods?: string[] } }) =>
        variables?.periods ? klineSubscriptionResult : tickSubscriptionResult
    );

    const { result, rerender } = renderHook(() =>
      useIntradayKLines('000001.SH', '1d', {
        targetTradingDate: '2026-08-20',
      })
    );

    expect(result.current.data).toHaveLength(1);
    expect(result.current.data[0]).toMatchObject({ close: 10, volume: 100 });

    klineSubscriptionResult = liveKlineSubscriptionResult;
    rerender();

    expect(result.current.data[0]).toMatchObject({ close: 11, volume: 120 });

    mocks.useKLines.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          period: '1m',
          time: '2026-08-20 09:30:00',
          open: 10,
          high: 10.5,
          low: 10,
          close: 10.5,
          volume: 110,
          amount: 1155,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: false,
    });
    rerender();

    expect(result.current.data[0]).toMatchObject({ close: 11, volume: 120 });
  });

  it('keeps a WebSocket auction tick ahead of the initial tick response', () => {
    vi.setSystemTime(new Date('2026-08-20T01:20:00.000Z'));
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockReturnValue({
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: false,
    });
    mocks.useTicks.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          time: '2026-08-20 09:20:00',
          lastPrice: 10,
          preClose: 9.8,
          volume: 100,
          amount: 1000,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshAuctionTicks,
      stale: false,
    });
    const klineSubscriptionResult = [{ data: undefined, error: undefined }];
    const emptyTickSubscriptionResult = [{ data: undefined, error: undefined }];
    const liveTickSubscriptionResult = [
      {
        data: {
          marketTicks: {
            stockCode: '000001.SH',
            period: 'tick',
            time: '2026-08-20 09:20:00',
            lastPrice: 11,
            preClose: 9.8,
            volume: 120,
            amount: 1320,
          },
        },
        error: undefined,
      },
    ];
    let tickSubscriptionResult = emptyTickSubscriptionResult;
    mocks.useSubscription.mockImplementation(
      ({ variables }: { variables?: { periods?: string[] } }) =>
        variables?.periods ? klineSubscriptionResult : tickSubscriptionResult
    );

    const { result, rerender } = renderHook(() =>
      useIntradayKLines('000001.SH', '1d', {
        targetTradingDate: '2026-08-20',
      })
    );

    expect(result.current.data).toHaveLength(1);
    expect(result.current.data[0]).toMatchObject({ close: 10, volume: 100 });

    tickSubscriptionResult = liveTickSubscriptionResult;
    rerender();

    expect(result.current.data[0]).toMatchObject({ close: 11, volume: 120 });

    mocks.useTicks.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          time: '2026-08-20 09:20:00',
          lastPrice: 10.5,
          preClose: 9.8,
          volume: 110,
          amount: 1155,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshAuctionTicks,
      stale: false,
    });
    rerender();

    expect(result.current.data[0]).toMatchObject({ close: 11, volume: 120 });
  });

  it('drops retained fallback rows and errors when switching to the same authoritative date', () => {
    const fallbackError = new Error('previous-day fallback failed');
    const initialResult = {
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: false,
    };
    const fallbackResult = {
      data: [
        {
          stockCode: '000001.SH',
          period: '1m',
          time: '2026-08-19 15:00:00',
          open: 3890,
          high: 3900,
          low: 3880,
          close: 3895,
          volume: 100,
          amount: 389500,
        },
      ],
      error: fallbackError,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: false,
    };
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockImplementation(
      (_stockCode: string, _period: string, startTime?: string) =>
        startTime?.startsWith('2026-08-19') ? fallbackResult : initialResult
    );
    mocks.useTicks.mockReturnValue({
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshAuctionTicks,
      stale: false,
    });

    const { result, rerender } = renderHook(
      ({ targetTradingDate }: { targetTradingDate: string | null }) =>
        useIntradayKLines('000001.SH', '1d', { targetTradingDate }),
      { initialProps: { targetTradingDate: null } }
    );

    expect(result.current.data).toHaveLength(1);
    expect(result.current.error).toBe(fallbackError);

    rerender({ targetTradingDate: '2026-08-20' });

    expect(result.current.data).toEqual([]);
    expect(result.current.error).toBeUndefined();
  });

  it('keeps K-lines usable and polls them when the live tick subscription fails', () => {
    const tickSubscriptionError = new Error('tick subscription disconnected');
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          period: '1m',
          time: '2026-08-20 15:00:00',
          open: 3900,
          high: 3901,
          low: 3899,
          close: 3900,
          volume: 100,
          amount: 390000,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: false,
    });
    mocks.useTicks.mockReturnValue({
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshAuctionTicks,
      stale: false,
    });
    mocks.useSubscription.mockImplementation(
      ({ variables }: { variables?: { periods?: string[] } }) => [
        {
          data: undefined,
          error: variables?.periods ? undefined : tickSubscriptionError,
        },
      ]
    );

    const { result, unmount } = renderHook(() =>
      useIntradayKLines('000001.SH', '1d')
    );

    expect(result.current.data).toHaveLength(1);
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBe(tickSubscriptionError);

    act(() => vi.advanceTimersByTime(30_000));
    expect(mocks.refreshKLines).toHaveBeenCalledTimes(1);

    unmount();
    act(() => vi.advanceTimersByTime(30_000));
    expect(mocks.refreshKLines).toHaveBeenCalledTimes(1);
  });

  it('does not poll settled current-range K-lines while live ticks are healthy', () => {
    mocks.useTradingDays.mockReturnValue({
      error: undefined,
      loading: false,
      refreshing: false,
      tradingDays: ['2026-08-19', '2026-08-20'],
    });
    mocks.useKLines.mockReturnValue({
      data: [
        {
          stockCode: '000001.SH',
          period: '1m',
          time: '2026-08-20 15:00:00',
          open: 3900,
          high: 3901,
          low: 3899,
          close: 3900,
          volume: 100,
          amount: 390000,
        },
      ],
      error: undefined,
      loading: false,
      refresh: mocks.refreshKLines,
      stale: false,
    });
    mocks.useTicks.mockReturnValue({
      data: [],
      error: undefined,
      loading: false,
      refresh: mocks.refreshAuctionTicks,
      stale: false,
    });

    renderHook(() => useIntradayKLines('000001.SH', '1d'));

    act(() => vi.advanceTimersByTime(30_000));
    expect(mocks.refreshKLines).not.toHaveBeenCalled();
  });
});
