import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { TTradeReplaySignals } from '../TTradeGlobalPage';

describe('TTradeReplaySignals', () => {
  it('renders a no-intent evaluation without blank trade columns or internal tags', () => {
    render(
      <TTradeReplaySignals
        decisions={[
          {
            id: 'decision-1',
            instanceId: 'run-replay',
            decidedAt: '2026-08-29T08:25:28+08:00',
            inputSummary: { instrument_code: '600519.SH' },
            outputSummary: {},
            tradeIntents: [],
            statePatch: {},
            decisionTrace: [
              'strategy_output',
              'MINIMUM_COVERAGE_NOT_REACHED',
              'opportunity_observed',
            ],
          },
        ]}
        executions={[]}
        fetching={false}
        hasReplay
      />
    );

    expect(screen.getByText('600519.SH')).toBeInTheDocument();
    expect(screen.getByText('无交易意图')).toBeInTheDocument();
    expect(screen.getByText('未发意图')).toBeInTheDocument();
    expect(
      screen.getByText('MINIMUM_COVERAGE_NOT_REACHED')
    ).toBeInTheDocument();
    expect(screen.queryByText('strategy_output')).not.toBeInTheDocument();
  });
});
