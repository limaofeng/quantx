import { describe, expect, it } from 'vitest';

import {
  formatDepthVolume,
  resolveMarketSnapshot,
} from '@/features/trading/components/marketDepthUtils';

describe('MarketDepth quote snapshot', () => {
  it('uses real tick price and computes change percent from pre-close', () => {
    const snapshot = resolveMarketSnapshot({
      bestAsk: 95.48,
      bestBid: 95.47,
      selectedStock: {
        quote: {
          lastPrice: 95.03,
          changePercent: -10.5,
        },
      },
      tick: {
        lastPrice: 95.46,
        preClose: 93.65,
      },
    });

    expect(snapshot.price).toBe(95.46);
    expect(snapshot.changePercent).toBeCloseTo(1.93, 2);
  });

  it('does not treat holding profit rate as quote change percent', () => {
    const snapshot = resolveMarketSnapshot({
      bestAsk: 95.48,
      bestBid: 95.46,
      selectedStock: {
        quote: {
          lastPrice: 95.03,
          changePercent: -10.5,
        },
      },
      tick: null,
    });

    expect(snapshot.price).toBe(95.03);
    expect(snapshot.changePercent).toBeNull();
  });

  it('keeps miniQMT depth volume in the original order-book unit', () => {
    expect(formatDepthVolume(18)).toBe('18');
    expect(formatDepthVolume(1)).toBe('1');
    expect(formatDepthVolume(0)).toBe('--');
  });
});
