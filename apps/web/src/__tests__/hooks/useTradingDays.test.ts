import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useTradingDays } from '@/hooks/useTradingDays';

const mocks = vi.hoisted(() => ({
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useQuery: mocks.useQuery,
}));

describe('useTradingDays cache state', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses cached calendar days without blocking consumers during revalidation', () => {
    mocks.useQuery.mockReturnValue([
      {
        data: { tradingCalendar: ['2026-08-19', '2026-08-20'] },
        error: undefined,
        fetching: false,
        stale: true,
      },
    ]);

    const { result } = renderHook(() => useTradingDays());

    expect(result.current.tradingDays).toEqual(['2026-08-19', '2026-08-20']);
    expect(result.current.loading).toBe(false);
    expect(result.current.refreshing).toBe(true);
  });

  it('keeps consumers loading when only an empty stale calendar is cached', () => {
    mocks.useQuery.mockReturnValue([
      {
        data: { tradingCalendar: [] },
        error: undefined,
        fetching: false,
        stale: true,
      },
    ]);

    const { result } = renderHook(() => useTradingDays());

    expect(result.current.loading).toBe(true);
    expect(result.current.refreshing).toBe(false);
  });
});
