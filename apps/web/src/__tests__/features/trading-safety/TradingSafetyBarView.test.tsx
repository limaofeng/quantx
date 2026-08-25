import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

import { TradingSafetyContext } from '@/features/trading-safety/trading-safety-context';
import { TradingSafetyBar } from '@/features/trading-safety/TradingSafetyBar';

describe('TradingSafetyBar', () => {
  it('uses the success color for a healthy and available safety service', () => {
    render(
      <TradingSafetyContext.Provider
        value={{
          accountId: '300000013250',
          blockedReasons: [],
          canIncreaseRisk: false,
          canReduceRisk: true,
          error: undefined,
          executionMode: 'REDUCE_ONLY',
          fetching: false,
          refreshSafety: vi.fn(),
          safety: {
            accountId: '300000013250',
            agentMode: 'live',
            agentStatus: 'READY',
            authorizationState: 'AUTHORIZED',
            blockedReasons: [],
            canActivateAutomation: true,
            canIncreaseRisk: false,
            canReduceRisk: true,
            checkedAt: '2026-08-25T10:21:31+08:00',
            checks: [],
            deadLetterCount: 0,
            engineStatus: 'RUNNING',
            executionMode: 'REDUCE_ONLY',
            executionWindowActive: true,
            externalOrderCount: 0,
            externalTradeCount: 0,
            healthStatus: 'HEALTHY',
            killSwitch: false,
            newExternalOrderCount: 0,
            newExternalTradeCount: 0,
            protocolVersion: '1.1',
            queueDelaySeconds: 0,
            queuedCommandCount: 0,
            reconciliationAgeSeconds: 12,
            reconcileStatus: 'READY',
            stateVersion: 2,
            summary: '账户已对账',
            unresolvedCriticalAlertCount: 0,
            workingExternalOrderCount: 0,
          },
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
