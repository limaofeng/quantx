import { describe, expect, it } from 'vitest';

import { accountActivityStatus } from '@/features/trading-safety/accountActivityStatus';

describe('accountActivityStatus', () => {
  it('does not expose network activity as an account execution state', () => {
    expect(
      accountActivityStatus({
        canIncreaseRisk: false,
        canReduceRisk: false,
        executionMode: 'OBSERVE_ONLY',
        fetching: true,
        hasSnapshot: false,
      })
    ).toEqual({ detail: '待确认', label: 'UNKNOWN', tone: 'checking' });
  });

  it('keeps the same unknown badge after a failed initial request and during retries', () => {
    const input = {
      canIncreaseRisk: false,
      canReduceRisk: false,
      executionMode: 'OBSERVE_ONLY',
      hasSnapshot: false,
    };
    expect(accountActivityStatus({ ...input, fetching: false })).toEqual(
      accountActivityStatus({ ...input, fetching: true })
    );
  });

  it('retains the authoritative execution state during a background refresh', () => {
    expect(
      accountActivityStatus({
        canIncreaseRisk: true,
        canReduceRisk: true,
        executionMode: 'TRADING',
        fetching: true,
        hasSnapshot: true,
      })
    ).toEqual({
      detail: '实盘',
      label: 'READY',
      tone: 'ready',
    });
  });
});
