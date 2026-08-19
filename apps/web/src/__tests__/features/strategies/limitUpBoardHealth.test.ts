import { describe, expect, it } from 'vitest';

import {
  deriveLimitUpBoardHealth,
  type LimitUpBoardHealthInput,
} from '@/features/strategies/domain/limitUpBoardHealth';

function input(
  overrides: Partial<LimitUpBoardHealthInput> = {}
): LimitUpBoardHealthInput {
  return {
    marketSessionPhase: overrides.marketSessionPhase ?? 'morning',
    radar: {
      scannerRunning: true,
      updating: false,
      staleCount: 0,
      warnings: [],
      ...overrides.radar,
    },
    assistant: {
      enabled: true,
      promotionModelMode: 'ACTIVE',
      runStatus: 'RUNNING',
      reconcileStatus: 'READY',
      projectionVersion: '12',
      projectionGeneratedAt: '2026-08-19T10:00:00+08:00',
      lastError: null,
      canApprove: true,
      killSwitch: false,
      blockedReasons: [],
      monitoredCount: 8,
      pendingSignalCount: 0,
      activeExitPlanCount: 1,
      ...overrides.assistant,
    },
    exitPlanErrorCount: overrides.exitPlanErrorCount ?? 0,
  };
}

function item(
  summary: ReturnType<typeof deriveLimitUpBoardHealth>,
  id: ReturnType<typeof deriveLimitUpBoardHealth>['items'][number]['id']
) {
  return summary.items.find(candidate => candidate.id === id);
}

describe('deriveLimitUpBoardHealth', () => {
  it('returns six healthy business items without global infrastructure checks', () => {
    const summary = deriveLimitUpBoardHealth(input());

    expect(summary.tone).toBe('healthy');
    expect(summary.items.map(candidate => candidate.id)).toEqual([
      'radar',
      'assistant',
      'projection',
      'entry-gate',
      'coverage',
      'exits',
    ]);
    expect(summary.items).toHaveLength(6);
  });

  it('keeps disabled shadow mode and expected post-close staleness informational', () => {
    const summary = deriveLimitUpBoardHealth(
      input({
        marketSessionPhase: 'post-close',
        radar: {
          scannerRunning: false,
          updating: false,
          staleCount: 221,
          warnings: [],
        },
        assistant: {
          enabled: false,
          promotionModelMode: 'SHADOW',
          runStatus: 'STOPPED',
          reconcileStatus: 'UNKNOWN',
          projectionVersion: '0',
          projectionGeneratedAt: null,
          lastError: 'historic shutdown message',
          canApprove: false,
          killSwitch: false,
          blockedReasons: ['非交易时段'],
          monitoredCount: 0,
          pendingSignalCount: 0,
          activeExitPlanCount: 0,
        },
      })
    );

    expect(summary.tone).toBe('healthy');
    expect(item(summary, 'radar')?.tone).toBe('neutral');
    expect(item(summary, 'assistant')).toMatchObject({
      tone: 'neutral',
      value: '已停用',
    });
    expect(item(summary, 'projection')?.tone).toBe('neutral');
    expect(item(summary, 'entry-gate')?.tone).toBe('neutral');
    expect(item(summary, 'coverage')?.tone).toBe('neutral');
  });

  it('reports an approval gate failure as critical when a signal is pending', () => {
    const summary = deriveLimitUpBoardHealth(
      input({
        assistant: {
          enabled: true,
          promotionModelMode: 'SHADOW',
          runStatus: 'RUNNING',
          reconcileStatus: 'READY',
          projectionVersion: '12',
          projectionGeneratedAt: '2026-08-19T10:00:00+08:00',
          lastError: null,
          canApprove: false,
          killSwitch: false,
          blockedReasons: ['账户执行门禁未通过'],
          monitoredCount: 8,
          pendingSignalCount: 2,
          activeExitPlanCount: 0,
        },
      })
    );

    expect(summary.tone).toBe('error');
    expect(item(summary, 'assistant')?.tone).toBe('neutral');
    expect(item(summary, 'entry-gate')).toMatchObject({
      tone: 'error',
      value: '2 条待确认受阻',
      detail: '账户执行门禁未通过',
    });
  });

  it('keeps exit errors critical even when the assistant is disabled', () => {
    const summary = deriveLimitUpBoardHealth(
      input({
        marketSessionPhase: 'closed',
        assistant: {
          enabled: false,
          promotionModelMode: 'SHADOW',
          runStatus: 'DRAINING',
          reconcileStatus: 'READY',
          projectionVersion: '15',
          projectionGeneratedAt: '2026-08-19T15:01:00+08:00',
          lastError: null,
          canApprove: false,
          killSwitch: false,
          blockedReasons: [],
          monitoredCount: 0,
          pendingSignalCount: 0,
          activeExitPlanCount: 2,
        },
        exitPlanErrorCount: 1,
      })
    );

    expect(summary.tone).toBe('error');
    expect(item(summary, 'exits')).toMatchObject({
      tone: 'error',
      value: '1 项异常',
    });
  });

  it('distinguishes an open-session radar outage from a normal refresh', () => {
    const outage = deriveLimitUpBoardHealth(
      input({
        radar: {
          scannerRunning: false,
          updating: false,
          staleCount: 0,
          warnings: [],
        },
      })
    );
    const refresh = deriveLimitUpBoardHealth(
      input({
        radar: {
          scannerRunning: false,
          updating: true,
          staleCount: 0,
          warnings: [],
        },
      })
    );

    expect(outage.tone).toBe('error');
    expect(item(outage, 'radar')?.tone).toBe('error');
    expect(refresh.tone).toBe('healthy');
    expect(item(refresh, 'radar')?.tone).toBe('neutral');
  });

  it('uses warnings for incomplete projections and board-specific radar notices', () => {
    const summary = deriveLimitUpBoardHealth(
      input({
        radar: {
          scannerRunning: true,
          updating: false,
          staleCount: 0,
          warnings: ['候选资格计算暂未完成'],
        },
        assistant: {
          enabled: true,
          promotionModelMode: 'ACTIVE',
          runStatus: 'RUNNING',
          reconcileStatus: 'UNKNOWN',
          projectionVersion: '0',
          projectionGeneratedAt: null,
          lastError: null,
          canApprove: true,
          killSwitch: false,
          blockedReasons: [],
          monitoredCount: 8,
          pendingSignalCount: 0,
          activeExitPlanCount: 0,
        },
      })
    );

    expect(summary.tone).toBe('warning');
    expect(item(summary, 'radar')?.tone).toBe('warning');
    expect(item(summary, 'projection')?.tone).toBe('warning');
  });
});
