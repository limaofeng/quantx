import { describe, expect, it } from 'vitest';

import { shouldSubscribeToEntryPlan } from '@/features/entry-plans/model/realtime';

describe('entry plan realtime subscription scope', () => {
  it.each(['COMPLETED', 'CANCELLED', 'EXPIRED'] as const)(
    'does not open WebSocket subscriptions for terminal status %s',
    status => {
      expect(shouldSubscribeToEntryPlan(status)).toBe(false);
    }
  );

  it.each([
    'ARMED',
    'ACCUMULATING',
    'AWAITING_APPROVAL',
    'ENTRY_PENDING',
    'PAUSED',
    'DRAINING',
    'ERROR',
  ] as const)('keeps %s subscribed because it can still advance', status => {
    expect(shouldSubscribeToEntryPlan(status)).toBe(true);
  });
});
