import { describe, expect, it } from 'vitest';

import {
  ACTIVE_REPLAY_POLL_MS,
  IDLE_REPLAY_POLL_MS,
  isNewerReplayRevision,
  replayFallbackPollInterval,
  replayNoticeRefreshTargets,
  stableValueByKey,
} from '@/features/portfolio/pages/t-trade-global/replaySync';

describe('replay synchronization policy', () => {
  it('uses subscriptions while connected and adapts polling after disconnect', () => {
    expect(replayFallbackPollInterval('connected', true)).toBeNull();
    expect(replayFallbackPollInterval('connected', false)).toBeNull();
    expect(replayFallbackPollInterval('reconnecting', true)).toBe(
      ACTIVE_REPLAY_POLL_MS
    );
    expect(replayFallbackPollInterval('closed', false)).toBe(
      IDLE_REPLAY_POLL_MS
    );
  });

  it('accepts only monotonically newer numeric revisions', () => {
    expect(isNewerReplayRevision(undefined, '1')).toBe(true);
    expect(isNewerReplayRevision('9', '10')).toBe(true);
    expect(isNewerReplayRevision('10', '10')).toBe(false);
    expect(isNewerReplayRevision('10', '9')).toBe(false);
    expect(isNewerReplayRevision('10', 'invalid')).toBe(false);
  });

  it('does not cache prior query data under a newly selected key', () => {
    const cache = new Map<string, { runId: string }>();
    const runOne = { runId: 'run-1' };

    expect(stableValueByKey(cache, 'run-1', runOne, 'run-1')).toBe(runOne);
    expect(stableValueByKey(cache, 'run-2', runOne, 'run-1')).toBeUndefined();

    const runTwo = { runId: 'run-2' };
    expect(stableValueByKey(cache, 'run-2', runTwo, 'run-2')).toBe(runTwo);
    expect(stableValueByKey(cache, 'run-1', runTwo, 'run-2')).toBe(runOne);
  });

  it('refreshes cycles only when the selected replay result is ready', () => {
    expect(replayNoticeRefreshTargets('PROGRESS', 'run-1', 'run-1')).toEqual({
      history: true,
      replay: true,
      cycles: false,
    });
    expect(
      replayNoticeRefreshTargets('RESULT_READY', 'run-1', 'run-1')
    ).toEqual({ history: true, replay: true, cycles: true });
    expect(
      replayNoticeRefreshTargets('RESULT_READY', 'run-2', 'run-1')
    ).toEqual({ history: true, replay: false, cycles: false });
  });
});
