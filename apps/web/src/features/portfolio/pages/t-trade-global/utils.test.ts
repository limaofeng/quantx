import { describe, expect, it } from 'vitest';

import { formatQuoteTime, formatTime } from './utils';

describe('A-share evidence time', () => {
  it.each([
    '2026-08-03T09:30:00',
    '2026-08-03T01:30:00Z',
    '2026-08-03T09:30:00+08:00',
    '2026-08-02T18:30:00-07:00',
  ])('shows %s in China timezone', value => {
    expect(formatTime(value)).toBe('2026-08-03 09:30:00');
    expect(formatQuoteTime(value)).toBe('更新于 09:30:00');
  });

  it('does not invent dates for missing or invalid evidence', () => {
    expect(formatTime(null)).toBe('尚未同步');
    expect(formatQuoteTime(null)).toBe('行情接收中');
    expect(formatTime('not-a-date')).toBe('not-a-date');
  });
});
