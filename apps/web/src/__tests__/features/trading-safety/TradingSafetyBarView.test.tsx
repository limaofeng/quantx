import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

import { TradingSafetyContext } from '@/features/trading-safety/trading-safety-context';
import { TradingSafetyBar } from '@/features/trading-safety/TradingSafetyBar';

const mocks = vi.hoisted(() => ({
  useQuery: vi.fn(),
}));

vi.mock('urql', () => ({
  useQuery: mocks.useQuery,
}));

describe('TradingSafetyBar', () => {
  it('uses the success color for a healthy and available safety service', () => {
    mocks.useQuery.mockReturnValue([
      {
        data: {
          accountExecutionSafety: {
            agentMode: 'live',
            canIncreaseRisk: false,
            executionMode: 'REDUCE_ONLY',
            healthStatus: 'HEALTHY',
            protocolVersion: '1.1',
            reconciliationAgeSeconds: 12,
            reconcileStatus: 'READY',
          },
        },
        fetching: false,
      },
    ]);

    render(
      <TradingSafetyContext.Provider
        value={{
          accountId: '300000013250',
          blockedReasons: [],
          canIncreaseRisk: false,
          canReduceRisk: true,
          executionMode: 'REDUCE_ONLY',
          fetching: false,
          refreshSafety: vi.fn(),
        }}
      >
        <TradingSafetyBar currentUserLabel="QuantX 管理员" />
      </TradingSafetyContext.Provider>
    );

    expect(screen.getByText('正常')).toHaveClass(
      'bg-emerald-400/15',
      'text-emerald-300'
    );
  });
});
