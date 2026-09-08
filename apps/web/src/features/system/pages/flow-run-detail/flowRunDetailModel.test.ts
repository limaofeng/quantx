import { describe, expect, it } from 'vitest';

import {
  filterFlowRunLogs,
  formatFlowRunDuration,
  getFlowRunStatusVisual,
  isLiveFlowRunState,
  mergeFlowRunLogs,
  resolveFlowRunElapsedSeconds,
  safeParseFlowRunParameters,
} from './flowRunDetailModel';

describe('flowRunDetailModel', () => {
  it('keeps running as an information state and connection success separate', () => {
    expect(getFlowRunStatusVisual('RUNNING')).toEqual({
      label: '运行中',
      tone: 'info',
    });
    expect(getFlowRunStatusVisual('COMPLETED').tone).toBe('success');
    expect(getFlowRunStatusVisual('FAILED').tone).toBe('danger');
    expect(isLiveFlowRunState('PAUSED')).toBe(true);
    expect(isLiveFlowRunState('COMPLETED')).toBe(false);
  });

  it('merges, deduplicates, and orders history with subscription logs', () => {
    const first = {
      time: '2026-09-01T08:31:48Z',
      level: 20,
      message: 'start',
    };
    const second = {
      time: '2026-09-01T08:38:45Z',
      level: 20,
      message: 'batch 1',
    };

    expect(mergeFlowRunLogs([second, first], [first])).toEqual([first, second]);
  });

  it('filters logs by level and text without losing original records', () => {
    const logs = [
      {
        time: '2026-09-01T08:31:48Z',
        level: 20,
        message: '行情批次完成',
      },
      {
        time: '2026-09-01T08:32:48Z',
        level: 30,
        message: '行情延迟',
      },
      {
        time: '2026-09-01T08:33:48Z',
        level: 40,
        message: '保存失败',
      },
    ];

    expect(filterFlowRunLogs(logs, 'WARN', '')).toEqual([logs[1]]);
    expect(filterFlowRunLogs(logs, 'ALL', '失败')).toEqual([logs[2]]);
    expect(logs).toHaveLength(3);
  });

  it('parses only object parameters', () => {
    expect(
      safeParseFlowRunParameters('{"periods":["1d"],"skip":false}')
    ).toEqual({ periods: ['1d'], skip: false });
    expect(safeParseFlowRunParameters('["1d"]')).toEqual({});
    expect(safeParseFlowRunParameters('invalid')).toEqual({});
  });

  it('uses wall-clock elapsed time for live runs and persisted duration for terminal runs', () => {
    const startedAt = '2026-09-01T08:00:00.000Z';
    const nowMs = new Date('2026-09-01T08:18:57.000Z').getTime();

    expect(
      resolveFlowRunElapsedSeconds({
        startedAt,
        totalRunTime: 0,
        live: true,
        nowMs,
      })
    ).toBe(1137);
    expect(
      resolveFlowRunElapsedSeconds({
        startedAt,
        totalRunTime: 65,
        live: false,
        nowMs,
      })
    ).toBe(65);
    expect(formatFlowRunDuration(1137)).toBe('00:18:57');
  });
});

it('bounds retained live log history', () => {
  const logs = Array.from({ length: 6000 }, (_, index) => ({
    time: new Date(1700000000000 + index).toISOString(),
    message: String(index),
    level: 20,
  }));
  const result = mergeFlowRunLogs(logs);
  expect(result).toHaveLength(5000);
  expect(result[0].message).toBe('1000');
});
