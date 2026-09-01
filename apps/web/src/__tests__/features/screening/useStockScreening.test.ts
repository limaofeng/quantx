import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useStockScreening } from '@/features/screening/hooks/useStockScreening';
import {
  IntradayVolumeScreenDocument,
  StockProbabilityCandidatesDocument,
  StockScreenDocument,
  type IntradayVolumeScreenQuery,
  type StockProbabilityCandidatesQuery,
  type StockScreenQuery,
  type StockScreenSnapshotStatusQuery,
} from '@/generated/gql/graphql';

const queryMock = vi.hoisted(() => vi.fn());
const statusMock = vi.hoisted(() => vi.fn());
vi.mock('urql', () => ({ useQuery: queryMock }));
vi.mock('@/features/screening/hooks/useStockScreenSnapshotStatus', () => ({
  useStockScreenSnapshotStatus: statusMock,
}));

const completedPage: StockScreenQuery['stockScreen'] = {
  items: [],
  total: 0,
  limit: 200,
  offset: 0,
  snapshotDate: '2026-08-28',
  calculatedAt: '2026-08-28T15:30:00+08:00',
  calculationVersion: 'daily-indicator-v1',
  hasStaleData: true,
  isComplete: true,
  warnings: [],
  financialHealth: null,
};
const partialStatus: StockScreenSnapshotStatusQuery['stockScreenSnapshotStatus'] =
  {
    latestSnapshotDate: '2026-08-31',
    latestCalculatedAt: '2026-08-31T15:35:00+08:00',
    expectedSnapshotDate: '2026-08-31',
    missingSnapshotDates: ['2026-08-31'],
    latestRunStatus: 'partial_failure',
    isComplete: false,
    warnings: [],
  };
const intradayPage: IntradayVolumeScreenQuery['intradayVolumeScreen'] = {
  items: [],
  total: 0,
  limit: 200,
  offset: 0,
  updatedAt: '2026-08-31T14:05:00+08:00',
  isScannerRunning: true,
  warnings: [],
};
const shadowProbabilityPage: StockProbabilityCandidatesQuery['stockProbabilityCandidates'] =
  {
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
    asOf: '2026-08-31',
    targetDate: null,
    activeModelVersion: 'active-model',
    showingShadow: true,
    warnings: ['模型 shadow-model 最新运行状态为 FAILED，未复用旧候选'],
  };

function mockDailyPage(page: StockScreenQuery['stockScreen']) {
  const reexecute = vi.fn();
  queryMock.mockImplementation(({ query }: { query: unknown }) => [
    {
      data:
        query === StockScreenDocument
          ? { stockScreen: page }
          : query === IntradayVolumeScreenDocument
            ? { intradayVolumeScreen: intradayPage }
            : query === StockProbabilityCandidatesDocument
              ? { stockProbabilityCandidates: shadowProbabilityPage }
              : {},
      fetching: false,
    },
    reexecute,
  ]);
}

describe('useStockScreening result provenance', () => {
  beforeEach(() => {
    statusMock.mockReturnValue({
      status: partialStatus,
      fetching: false,
      refresh: vi.fn(),
    });
    mockDailyPage(completedPage);
  });

  it('keeps the returned completed-page date and time when newer snapshots are only partially calculated', () => {
    const { result } = renderHook(() => useStockScreening());

    expect(result.current.meta.snapshotDate).toBe('2026-08-28');
    expect(result.current.meta.calculatedAt).toBe('2026-08-28T15:30:00+08:00');
    expect(result.current.meta.expectedSnapshotDate).toBe('2026-08-31');
    expect(result.current.meta.missingSnapshotDates).toEqual(['2026-08-31']);
    expect(result.current.meta.latestRunStatus).toBe('partial_failure');
    expect(result.current.meta.isComplete).toBe(false);
  });

  it('does not manufacture a result snapshot date or timestamp from status when no eligible page exists', () => {
    mockDailyPage({
      ...completedPage,
      snapshotDate: null,
      calculatedAt: null,
      isComplete: false,
    });
    const { result } = renderHook(() => useStockScreening());

    expect(result.current.meta.snapshotDate).toBeNull();
    expect(result.current.meta.calculatedAt).toBeNull();
    expect(result.current.meta.latestRunStatus).toBe('partial_failure');
  });

  it('does not mark an old result page complete when status is complete for a newer date', () => {
    statusMock.mockReturnValue({
      status: {
        ...partialStatus,
        latestSnapshotDate: '2026-08-31',
        expectedSnapshotDate: '2026-08-31',
        missingSnapshotDates: [],
        latestRunStatus: 'success',
        isComplete: true,
      },
      fetching: false,
      refresh: vi.fn(),
    });
    mockDailyPage({
      ...completedPage,
      snapshotDate: '2026-08-28',
      hasStaleData: false,
      isComplete: true,
    });

    const { result } = renderHook(() => useStockScreening());

    expect(result.current.meta.snapshotDate).toBe('2026-08-28');
    expect(result.current.meta.expectedSnapshotDate).toBe('2026-08-31');
    expect(result.current.meta.isComplete).toBe(false);
    expect(result.current.meta.hasStaleData).toBe(true);
  });

  it('keeps intraday metadata independent of daily snapshot status', () => {
    const { result } = renderHook(() => useStockScreening());
    act(() => result.current.runScreening({ screeningMode: 'INTRADAY' }));

    expect(result.current.meta.snapshotDate).toBeNull();
    expect(result.current.meta.calculatedAt).toBe(intradayPage.updatedAt);
    expect(result.current.meta.intradayUpdatedAt).toBe(intradayPage.updatedAt);
    expect(result.current.meta.latestRunStatus).toBeNull();
    expect(result.current.meta.missingSnapshotDates).toEqual([]);
  });

  it('shows the requested shadow model instead of the unrelated active version', () => {
    const { result } = renderHook(() => useStockScreening());
    act(() =>
      result.current.runScreening({
        screeningMode: 'PROBABILITY',
        probabilityModelVersion: 'shadow-model',
      })
    );

    expect(result.current.meta.probabilityShowingShadow).toBe(true);
    expect(result.current.meta.probabilityModelVersion).toBe('shadow-model');
    expect(result.current.meta.warnings).toEqual(
      shadowProbabilityPage.warnings
    );
  });
});
