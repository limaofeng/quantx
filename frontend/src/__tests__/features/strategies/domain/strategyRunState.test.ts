import { describe, expect, it } from 'vitest';

import {
  getStrategyRunState,
  normalizeStrategyRunMode,
  normalizeStrategyRunStatus,
} from '@/features/strategies/domain/strategyRunState';
import { StrategyRunMode, StrategyRunStatus } from '@/generated/gql/graphql';

const modes = ['BACKTEST', 'PAPER', 'LIVE'] as const;
const statuses = [
  'PENDING',
  'RUNNING',
  'PAUSED',
  'STOPPED',
  'COMPLETED',
  'ERROR',
] as const;

describe('strategy run state matrix', () => {
  it('normalizes generated enum values and backend-style strings', () => {
    expect(normalizeStrategyRunMode(StrategyRunMode.Backtest)).toBe('BACKTEST');
    expect(normalizeStrategyRunMode('paper')).toBe('PAPER');
    expect(normalizeStrategyRunStatus(StrategyRunStatus.Completed)).toBe(
      'COMPLETED'
    );
    expect(normalizeStrategyRunStatus('failed')).toBe('ERROR');
  });

  it('covers every mode and status combination', () => {
    for (const mode of modes) {
      for (const status of statuses) {
        const state = getStrategyRunState(mode, status);
        expect(state.mode).toBe(mode);
        expect(state.status).toBe(status);
        expect(state.statusLabel.length).toBeGreaterThan(0);
        expect(state.listPrimaryAction).toMatchObject({
          id: 'view_detail',
          label: '查看详情',
        });
      }
    }
  });

  it('marks only terminal states as list-deletable', () => {
    expect(getStrategyRunState('BACKTEST', 'RUNNING').canDelete).toBe(false);
    expect(getStrategyRunState('PAPER', 'PAUSED').canDelete).toBe(false);
    expect(getStrategyRunState('BACKTEST', 'COMPLETED').canDelete).toBe(true);
    expect(getStrategyRunState('LIVE', 'ERROR').canDelete).toBe(true);
  });

  it('keeps completed backtests on the safe detail action set', () => {
    const state = getStrategyRunState('BACKTEST', 'COMPLETED');
    expect(state.statusLabel).toBe('回测完成');
    expect(state.detailPrimaryAction).toMatchObject({
      id: 'rerun_backtest',
      label: '重新回测',
    });
    expect(state.detailSecondaryActions.map(action => action.id)).toEqual(
      expect.arrayContaining(['clone_to_paper', 'clone_to_live', 'delete'])
    );
  });

  it('does not offer a primary live action after live terminal states', () => {
    expect(getStrategyRunState('LIVE', 'STOPPED').detailPrimaryAction).toBe(
      undefined
    );
    expect(getStrategyRunState('LIVE', 'ERROR').detailPrimaryAction).toBe(
      undefined
    );
  });
});
