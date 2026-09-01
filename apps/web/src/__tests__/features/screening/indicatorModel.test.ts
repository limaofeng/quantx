import { describe, expect, it } from 'vitest';

import {
  buildIndicatorReportRequests,
  canonicalIndicatorConditions,
  validateIndicatorConditions,
} from '@/features/screening/indicatorModel';
import type { IndicatorCondition } from '@/features/screening/types';

const conditions: IndicatorCondition[] = [
  { indicatorId: 'change_pct', operator: 'lte', value: 0 },
  { indicatorId: 'volume_ratio', operator: 'between', value: 1, valueTo: 2 },
];

describe('indicator condition research identity', () => {
  it('preserves zero and negative values and ignores condition order', () => {
    expect(validateIndicatorConditions(conditions)).toBeNull();
    expect(
      validateIndicatorConditions([
        { indicatorId: 'change_pct', operator: 'lte', value: -5 },
      ])
    ).toBeNull();
    expect(canonicalIndicatorConditions(conditions)).toEqual(
      canonicalIndicatorConditions([...conditions].reverse())
    );
    const requests = buildIndicatorReportRequests({
      indicatorConditions: conditions,
      excludeST: false,
    });
    expect(requests.find(item => item.kind === 'joint')?.conditions).toEqual(
      canonicalIndicatorConditions(conditions)
    );
    expect(requests.every(item => !item.excludeSt)).toBe(true);
  });
  it('keeps overview reports separate from exact thresholds and changes joint identity', () => {
    const before = buildIndicatorReportRequests({
      indicatorConditions: conditions,
    });
    const after = buildIndicatorReportRequests({
      indicatorConditions: [{ ...conditions[0], value: -2 }, conditions[1]],
    });
    expect(before.filter(item => item.kind === 'single')).toEqual(
      after.filter(item => item.kind === 'single')
    );
    expect(before.find(item => item.kind === 'joint')).not.toEqual(
      after.find(item => item.kind === 'joint')
    );
  });
  it('never drops an incomplete condition to obtain a joint report', () => {
    const requests = buildIndicatorReportRequests({
      indicatorConditions: [
        ...conditions,
        { indicatorId: 'rsi12', operator: 'gte', value: null },
      ],
    });
    expect(requests.every(item => item.kind === 'single')).toBe(true);
    expect(requests).toHaveLength(3);
    expect(
      validateIndicatorConditions([
        { indicatorId: 'rsi12', operator: 'between', value: 70, valueTo: 30 },
      ])
    ).toMatch('上限');
  });
  it('does not lose unsupported universe or financial filters in matching requests', () => {
    const requests = buildIndicatorReportRequests({
      universe: 'ETF',
      excludeST: true,
      includeIndustries: ['银行'],
      indicatorConditions: [
        ...conditions,
        { indicatorId: 'roe_ttm', operator: 'gte', value: 0 },
      ],
    });
    expect(requests.find(item => item.kind === 'joint')).toMatchObject({
      universe: 'ETF',
      excludeSt: true,
      includeIndustries: ['银行'],
      indicatorIds: ['change_pct', 'roe_ttm', 'volume_ratio'],
    });
  });
});
