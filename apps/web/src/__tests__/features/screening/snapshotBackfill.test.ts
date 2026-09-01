import { describe, expect, it } from 'vitest';

import {
  buildSnapshotBackfillParameters,
  findActiveSnapshotBackfillRun,
} from '@/features/screening/snapshotBackfill';

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

describe('findActiveSnapshotBackfillRun', () => {
  const now = Date.parse('2026-09-01T08:45:00+08:00');

  it('recovers the running manual backfill and ignores future schedules', () => {
    expect(
      findActiveSnapshotBackfillRun(
        [
          {
            id: 'future-schedule',
            state: 'Scheduled',
            expectedStartTime: '2026-09-02T15:05:00+08:00',
          },
          {
            id: 'active-backfill',
            state: 'Running',
            expectedStartTime: '2026-09-01T08:31:42+08:00',
            startedAt: '2026-09-01T08:31:48+08:00',
          },
        ],
        now
      )?.id
    ).toBe('active-backfill');
  });

  it('treats a due scheduled run as active before the worker starts it', () => {
    expect(
      findActiveSnapshotBackfillRun(
        [
          {
            id: 'due-run',
            state: 'Scheduled',
            expectedStartTime: '2026-09-01T08:44:59+08:00',
          },
        ],
        now
      )?.id
    ).toBe('due-run');
  });

  it('does not recover terminal runs', () => {
    expect(
      findActiveSnapshotBackfillRun(
        [
          {
            id: 'completed-run',
            state: 'Completed',
            expectedStartTime: '2026-09-01T08:31:42+08:00',
            startedAt: '2026-09-01T08:31:48+08:00',
          },
        ],
        now
      )
    ).toBeNull();
  });
});
