import { describe, expect, it } from 'vitest';

import { buildSnapshotBackfillParameters } from '@/features/screening/snapshotBackfill';

describe('buildSnapshotBackfillParameters', () => {
  it('uses missing-date bounds and always requests stock plus ETF 1d data', () => {
    expect(
      buildSnapshotBackfillParameters([
        '2026-07-29',
        '2026-07-23',
        '2026-07-24',
      ])
    ).toEqual({
      sectors: ['沪深A股', '沪深ETF'],
      start_time: '20260723',
      end_time: '20260729',
      periods: ['1d'],
      skip_download: false,
      compute_daily_signals: true,
    });
  });

  it('does not submit when no snapshot is missing', () => {
    expect(buildSnapshotBackfillParameters([])).toBeNull();
  });
});
