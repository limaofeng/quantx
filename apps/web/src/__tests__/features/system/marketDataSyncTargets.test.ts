import { describe, expect, it } from 'vitest';

import { getActiveHoldingStockCodes } from '@/features/system/pages/marketDataSyncTargets';

describe('getActiveHoldingStockCodes', () => {
  it('keeps only unique active holdings in a deterministic order', () => {
    expect(
      getActiveHoldingStockCodes([
        { stockCode: '601318.sh', volume: 100 },
        { stockCode: '000543.SZ', volume: 200 },
        { stockCode: '601318.SH', volume: 50 },
        { stockCode: '002594.SZ', volume: 0 },
      ])
    ).toEqual(['000543.SZ', '601318.SH']);
  });

  it('returns an empty list when there are no positions', () => {
    expect(getActiveHoldingStockCodes(undefined)).toEqual([]);
  });
});
