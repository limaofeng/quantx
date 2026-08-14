import { describe, expect, it } from 'vitest';

import { ageSecondsLabel } from '@/features/trading-safety/time';

describe('ageSecondsLabel', () => {
  it('formats the authoritative reconciliation age without parsing a timestamp', () => {
    expect(ageSecondsLabel(8.9)).toBe('8 秒前');
    expect(ageSecondsLabel(125)).toBe('2 分钟前');
    expect(ageSecondsLabel(7_201)).toBe('2 小时前');
  });

  it('handles missing and invalid ages', () => {
    expect(ageSecondsLabel(null)).toBe('无记录');
    expect(ageSecondsLabel(Number.NaN)).toBe('时间异常');
    expect(ageSecondsLabel(-1)).toBe('时间异常');
  });
});
