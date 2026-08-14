import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import type { LiveMarketQuote } from '../../hooks/useRealTimeHoldings';

import {
  TTradeLiveBoard,
  type TTradeMonitorLike,
} from './TTradeLiveMonitor';

const monitor = {
  enabled: true,
  mode: 'paper',
  holdingCount: 1,
  eligibleCount: 1,
  monitoredCount: 1,
  pendingSignalCount: 0,
  activeBatchCount: 0,
  drainingCount: 0,
  ignoredCount: 0,
  signalLookbackSeconds: 300,
  stabilizationSeconds: 15,
  pullbackThresholdPct: 0.8,
  reboundThresholdPct: 0.2,
  maxSpreadTicks: 3,
  momentumEnabled: true,
  momentumWindowSeconds: 60,
  momentumMinRisePct: 0.8,
  momentumMinMoveSeconds: 15,
  momentumBaselineSeconds: 300,
  momentumMinAmountVelocityRatio: 2,
  momentumMinVwapPremiumPct: 2,
  momentumMaxVwapPremiumPct: 3.5,
  momentumMaxSpreadTicks: 10,
  momentumMaxSpreadPct: 0.3,
  positionSnapshotComplete: true,
  rolloutStage: 'SHADOW',
  engineStatus: 'READY',
  agentStatus: 'READY',
  reconcileStatus: 'READY',
  killSwitch: false,
  canActivateLive: false,
  blockedReasons: [],
  sessions: [],
  holdings: [
    {
      stockCode: '600000.SH',
      instrumentName: '测试股票',
      volume: 1000,
      availableVolume: 800,
      ignored: false,
      eligible: true,
      status: 'MONITORED',
      reason: '监控中',
      session: {
        runId: 'run-1',
        runStatus: 'RUNNING',
        status: 'OBSERVING',
        mode: 'paper',
        entryOrderStatus: '',
        exitOrderStatus: '',
        entryFilledVolume: 0,
        entryAvgPrice: 0,
        exitFilledVolume: 0,
        exitAvgPrice: 0,
        activeVolume: 0,
        lastNetProfitPct: 0,
        peakNetProfitPct: 0,
        profitArmed: false,
        lastExitReason: '',
        completedCycles: 0,
        canCancel: true,
        latestEvaluation: {
          phase: 'ENTRY_SCAN',
          lastTickAt: '2026-08-13T10:00:00+08:00',
          processedTickCount: 12,
          windowSampleCount: 8,
          windowCoverageSeconds: 30,
          triggered: false,
          reason: 'WAITING_REBOUND',
          signalType: 'NONE',
          signalPrice: 10,
          pullbackPct: 0.6,
          reboundPct: 0.1,
        },
      },
    },
  ],
} satisfies TTradeMonitorLike;

const quotes = new Map<string, LiveMarketQuote>([
  [
    '600000.SH',
    {
      stockCode: '600000.SH',
      currentPrice: 10,
      changePercent: 1,
      high: 10.1,
      low: 9.9,
      open: 9.9,
      volume: 1000,
      time: '2026-08-13T10:00:00+08:00',
    },
  ],
]);

describe('TTradeLiveBoard inspector', () => {
  it('opens with localized metrics and returns focus to the row on Escape', async () => {
    const user = userEvent.setup();
    render(
      <TTradeLiveBoard
        historyByCode={new Map()}
        loading={false}
        monitor={monitor}
        quotes={quotes}
      />
    );
    const rowButton = screen.getByRole('button', { name: '检查 测试股票' });

    await user.click(rowButton);

    const inspector = screen.getByRole('dialog');
    expect(inspector).toBeInTheDocument();
    expect(within(inspector).getByText('等待企稳反弹')).toBeInTheDocument();
    expect(within(inspector).getByText('0.60%')).toBeInTheDocument();
    expect(within(inspector).getByText('阈值 ≥ 0.20%')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(rowButton).toHaveFocus();
  });
});
