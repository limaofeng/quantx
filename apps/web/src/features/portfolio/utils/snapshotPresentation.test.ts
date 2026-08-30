import { describe, expect, it } from 'vitest';

import { resolvePortfolioSnapshotPresentation } from './snapshotPresentation';

describe('resolvePortfolioSnapshotPresentation', () => {
  it('keeps retained holdings read-only when the latest sync failed', () => {
    const result = resolvePortfolioSnapshotPresentation({
      hasOverview: true,
      renderedPositionCount: 2,
      snapshot: {
        sequence: 20,
        receivedAt: '2026-08-30T12:00:00Z',
        positionCount: 2,
        isComplete: false,
        lastError: 'SNAPSHOT_ACCOUNT_STATUS_INVALID:ACCOUNT_STATUS_FAIL',
      },
    });

    expect(result.state).toBe('STALE');
    expect(result.canTrade).toBe(false);
    expect(result.isAuthoritativeEmpty).toBe(false);
    expect(result.lastSuccessfulSyncAt).toBe('2026-08-30T12:00:00Z');
  });

  it('does not turn a GraphQL error without cache into an empty account', () => {
    const result = resolvePortfolioSnapshotPresentation({
      hasOverview: false,
      renderedPositionCount: 0,
      queryError: 'query capacity exhausted',
    });

    expect(result.state).toBe('NO_CACHE');
    expect(result.isAuthoritativeEmpty).toBe(false);
    expect(result.message).toContain('没有可展示的成功快照');
  });

  it('shows cached data as stale when a refresh fails', () => {
    const result = resolvePortfolioSnapshotPresentation({
      hasOverview: true,
      renderedPositionCount: 1,
      queryError: 'network unavailable',
      snapshot: {
        sequence: 20,
        receivedAt: '2026-08-30T12:00:00Z',
        positionCount: 1,
        isComplete: true,
      },
    });

    expect(result.state).toBe('STALE');
    expect(result.canTrade).toBe(false);
  });

  it('shows true empty only for an explicit complete zero-position snapshot', () => {
    const result = resolvePortfolioSnapshotPresentation({
      hasOverview: true,
      renderedPositionCount: 0,
      snapshot: {
        sequence: 21,
        receivedAt: '2026-08-30T12:01:00Z',
        positionCount: 0,
        isComplete: true,
        lastError: null,
      },
    });

    expect(result.state).toBe('CURRENT');
    expect(result.canTrade).toBe(true);
    expect(result.isAuthoritativeEmpty).toBe(true);
  });

  it('fails closed when snapshot count and rendered rows disagree', () => {
    const result = resolvePortfolioSnapshotPresentation({
      hasOverview: true,
      renderedPositionCount: 0,
      snapshot: {
        positionCount: 2,
        isComplete: true,
      },
    });

    expect(result.state).toBe('INCONSISTENT');
    expect(result.canTrade).toBe(false);
    expect(result.isAuthoritativeEmpty).toBe(false);
  });
});
