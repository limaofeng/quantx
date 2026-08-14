import { describe, expect, it } from 'vitest';

import {
  FINANCIAL_CHART_COLORS,
  financialChartColor,
  financialDirection,
  financialToneBadgeClass,
  financialToneClass,
} from '@/shared/utils/financialColors';

describe('A-share financial colors', () => {
  it('uses red for rises and green for falls in market data', () => {
    expect(financialDirection(1.2)).toBe('up');
    expect(financialToneClass(1.2)).toBe('text-market-up');
    expect(financialToneClass(-1.2)).toBe('text-market-down');
    expect(financialChartColor(1.2)).toBe(FINANCIAL_CHART_COLORS.up);
    expect(financialChartColor(-1.2)).toBe(FINANCIAL_CHART_COLORS.down);
  });

  it('uses light blue for falls inside a holding context', () => {
    expect(financialToneClass(-1.2, 'holding')).toBe('text-holding-down');
    expect(financialToneBadgeClass(-1.2, 'holding')).toContain(
      'text-holding-down'
    );
    expect(financialChartColor(-1.2, 'holding')).toBe(
      FINANCIAL_CHART_COLORS.holdingDown
    );
  });

  it('keeps zero and unavailable values neutral', () => {
    expect(financialDirection(0)).toBe('flat');
    expect(financialToneClass(null)).toBe('text-market-flat');
    expect(financialToneClass(undefined, 'holding')).toBe('text-market-flat');
  });
});
