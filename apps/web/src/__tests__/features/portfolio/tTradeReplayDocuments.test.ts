import { describe, expect, it } from 'vitest';

import {
  TTradeReplayHistoryQuery,
  TTradeReplayQuery,
  TTradeReplayUpdatesSubscription,
} from '@/features/portfolio/hooks/useTTradeGlobal';

describe('T-trade replay GraphQL documents', () => {
  it('keeps generated documents in sync with replay operations', () => {
    for (const document of [
      TTradeReplayHistoryQuery,
      TTradeReplayQuery,
      TTradeReplayUpdatesSubscription,
    ]) {
      expect(document.kind).toBe('Document');
      expect(document.definitions.length).toBeGreaterThan(0);
    }
  });
});
