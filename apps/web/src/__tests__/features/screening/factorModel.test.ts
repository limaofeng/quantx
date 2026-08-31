import { describe, expect, it } from 'vitest';

import {
  buildFactorReportRequests,
  canonicalFactorConditions,
  validateFactorConditions,
} from '@/features/screening/factorModel';
import type { FactorCondition } from '@/features/screening/types';

const conditions: FactorCondition[] = [
  { factorId: 'change_pct', operator: 'lte', value: 0 },
  { factorId: 'volume_ratio', operator: 'between', value: 1, valueTo: 2 },
];

describe('factor condition research identity', () => {
  it('preserves zero and negative values and ignores condition order', () => {
    expect(validateFactorConditions(conditions)).toBeNull();
    expect(
      validateFactorConditions([
        { factorId: 'change_pct', operator: 'lte', value: -5 },
      ])
    ).toBeNull();
    expect(canonicalFactorConditions(conditions)).toEqual(
      canonicalFactorConditions([...conditions].reverse())
    );
    const requests = buildFactorReportRequests({
      factorConditions: conditions,
      excludeST: false,
    });
    expect(requests.find(item => item.kind === 'joint')?.conditions).toEqual(
      canonicalFactorConditions(conditions)
    );
    expect(requests.every(item => !item.excludeSt)).toBe(true);
  });
  it('keeps overview reports separate from exact thresholds and changes joint identity', () => {
    const before = buildFactorReportRequests({ factorConditions: conditions });
    const after = buildFactorReportRequests({
      factorConditions: [{ ...conditions[0], value: -2 }, conditions[1]],
    });
    expect(before.filter(item => item.kind === 'single')).toEqual(
      after.filter(item => item.kind === 'single')
    );
    expect(before.find(item => item.kind === 'joint')).not.toEqual(
      after.find(item => item.kind === 'joint')
    );
  });
  it('never drops an incomplete condition to obtain a joint report', () => {
    const requests = buildFactorReportRequests({
      factorConditions: [
        ...conditions,
        { factorId: 'rsi12', operator: 'gte', value: null },
      ],
    });
    expect(requests.every(item => item.kind === 'single')).toBe(true);
    expect(requests).toHaveLength(3);
    expect(
      validateFactorConditions([
        { factorId: 'rsi12', operator: 'between', value: 70, valueTo: 30 },
      ])
    ).toMatch('上限');
  });
  it('does not lose unsupported universe or financial filters in matching requests', () => {
    const requests = buildFactorReportRequests({
      universe: 'ETF',
      excludeST: true,
      includeIndustries: ['银行'],
      factorConditions: [
        ...conditions,
        { factorId: 'roe_ttm', operator: 'gte', value: 0 },
      ],
    });
    expect(requests.find(item => item.kind === 'joint')).toMatchObject({
      universe: 'ETF',
      excludeSt: true,
      includeIndustries: ['银行'],
      factorIds: ['change_pct', 'roe_ttm', 'volume_ratio'],
    });
  });
});
