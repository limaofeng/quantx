import { describe, expect, it } from 'vitest';

import { formatDate, formatDateTime, formatTradeTime, parseDate } from './date';

describe('China business timestamps', () => {
  it.each([
    '2026-08-03T09:30:00.123',
    '2026-08-03 09:30:00.123',
    '2026-08-03T09:30:00.123+08:00',
    '2026-08-03T01:30:00.123Z',
    '2026-08-02T18:30:00.123-07:00',
  ])('normalizes %s to the same China trading instant', value => {
    expect(parseDate(value).toISOString()).toBe('2026-08-03T01:30:00.123Z');
    expect(formatDateTime(value)).toBe('2026-08-03 09:30:00');
    expect(formatTradeTime(value)).toBe('09:30:00');
  });

  it('uses the China calendar date at a UTC day boundary', () => {
    expect(formatDate('2026-08-02T16:05:00Z')).toBe('2026-08-03');
    expect(formatDateTime('2026-08-02T16:05:00Z')).toBe('2026-08-03 00:05:00');
  });
});
