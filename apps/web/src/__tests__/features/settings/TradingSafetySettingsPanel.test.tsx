import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TradingSafetySettingsPanel } from '@/features/settings/components/TradingSafetySettingsPanel';

const mocks = vi.hoisted(() => ({
  confirmControl: vi.fn(),
  previewControl: vi.fn(),
  refreshSafety: vi.fn(),
  useMutation: vi.fn(),
  useQuery: vi.fn(),
  safety: {
    accountId: '300000013250',
    authorizationState: 'DISABLED',
    stateVersion: 1,
    healthStatus: 'HEALTHY',
    executionMode: 'REDUCE_ONLY',
    canIncreaseRisk: false,
    canReduceRisk: true,
    canActivateAutomation: false,
    summary: '账户事实已收敛；当前仅允许减仓',
    engineStatus: 'READY',
    agentStatus: 'READY',
    agentMode: 'live',
    protocolVersion: '1.1',
    reconcileStatus: 'READY',
    killSwitch: false,
    blockedReasons: ['尚未基于最新完整快照建立账户实盘窗口'],
    executionWindowActive: false,
    snapshotId: 'snapshot-1',
    snapshotHash: 'snapshot-hash-1',
    snapshotAt: '2026-08-25T06:00:00Z',
    reconciliationAgeSeconds: 10,
    queuedCommandCount: 0,
    queueDelaySeconds: 0,
    deadLetterCount: 0,
    unresolvedCriticalAlertCount: 0,
    externalOrderCount: 0,
    externalTradeCount: 0,
    newExternalOrderCount: 0,
    newExternalTradeCount: 0,
    workingExternalOrderCount: 0,
    lastBackupAt: '2026-08-25T04:00:00Z',
    checkedAt: '2026-08-25T06:00:10Z',
    checks: [] as Array<{
      code: string;
      status: 'PASSED' | 'STANDBY' | 'FAILED';
      message: string;
      scope: string;
    }>,
  },
}));

vi.mock('urql', () => ({
  useMutation: mocks.useMutation,
  useQuery: mocks.useQuery,
}));

vi.mock('@/features/trading-safety', () => ({
  ConfirmAccountExecutionControlMutation: 'confirm-account-execution-control',
  AccountExecutionSafetyHistoryQuery: 'account-execution-safety-history',
  PreviewAccountExecutionControlMutation: 'preview-account-execution-control',
  useTradingSafety: () => ({
    accountId: '300000013250',
    fetching: false,
    refreshSafety: mocks.refreshSafety,
    safety: mocks.safety,
  }),
}));

describe('TradingSafetySettingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.useMutation.mockImplementation((document: string) =>
      document === 'preview-account-execution-control'
        ? [{ fetching: false }, mocks.previewControl]
        : [{ fetching: false }, mocks.confirmControl]
    );
    mocks.useQuery.mockReturnValue([
      {
        fetching: false,
        data: {
          accountExecutionSafetyHistory: {
            available: true,
            range: 'DAYS_30',
            generatedAt: '2026-08-25T06:00:10Z',
            firstObservedAt: '2026-08-24T06:00:10Z',
            lastObservedAt: '2026-08-25T06:00:10Z',
            observerFresh: true,
            bucketSeconds: 14_400,
            incidentsTruncated: false,
            checks: [
              {
                code: 'MARKET_STREAM_READY',
                currentStatus: 'STANDBY',
                checkedAt: '2026-08-25T06:00:10Z',
                reasonCode: 'MARKET_CLOSED_STANDBY',
                publicMessage: '当前休市，等待下一交易时段',
                coveragePct: 100,
                incidentCount: 0,
                points: [
                  {
                    start: '2026-08-25T04:00:00Z',
                    status: 'STANDBY',
                    coveragePct: 100,
                    sampleCount: 120,
                    passedCount: 0,
                    standbyCount: 120,
                    failedCount: 0,
                    unknownCount: 0,
                  },
                ],
              },
            ],
            incidents: [],
          },
        },
      },
    ]);
    mocks.previewControl.mockResolvedValue({
      data: {
        previewAccountExecutionControl: {
          success: true,
          message: '账户执行控制预览已创建',
          preview: {
            challengeId: 'challenge-1',
            confirmationToken: 'confirmation-token-1',
          },
        },
      },
    });
  });

  afterEach(() => {
    mocks.safety.checks = [];
    vi.unstubAllGlobals();
  });

  it('presents a closed market as standby instead of an error', () => {
    mocks.safety.checks = [
      {
        code: 'MARKET_STREAM_READY',
        status: 'STANDBY',
        message: '当前休市，Agent、API 与 Engine 权威水位已收敛',
        scope: 'INCREASE_RISK',
      },
    ];

    render(<TradingSafetySettingsPanel />);

    expect(
      screen.getByRole('article', { name: '全市场行情链路：休市待机' })
    ).toBeInTheDocument();
    expect(screen.getByText('0 项通过 · 1 项休市待机')).toBeInTheDocument();
    expect(screen.queryByText('需处理')).not.toBeInTheDocument();
  });

  it('creates a preview on an insecure LAN origin without Web Crypto', async () => {
    vi.stubGlobal('crypto', undefined);
    render(<TradingSafetySettingsPanel />);

    fireEvent.click(screen.getByRole('button', { name: '建立实盘窗口' }));

    await waitFor(() => {
      expect(mocks.previewControl).toHaveBeenCalledWith({
        input: expect.objectContaining({
          accountId: '300000013250',
          action: 'BEGIN_CONTROLLED_WINDOW',
          snapshotId: 'snapshot-1',
          stateVersion: 1,
          idempotencyKey: expect.stringMatching(
            /^account-execution:client-[a-z0-9]+-[a-z0-9]+$/
          ),
        }),
      });
    });
    expect(
      screen.getByText('预览已锁定 60 秒，请核对后确认。')
    ).toBeInTheDocument();
  });

  it('shows standby history without creating an incident', () => {
    render(<TradingSafetySettingsPanel />);

    fireEvent.click(screen.getByRole('button', { name: '异常历史' }));

    expect(screen.getAllByText('休市待机')).not.toHaveLength(0);
    expect(screen.getByText('0 次异常')).toBeInTheDocument();
    expect(
      screen.getByText('所选范围内没有确认的准入异常。')
    ).toBeInTheDocument();
    expect(mocks.useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        query: 'account-execution-safety-history',
        variables: expect.objectContaining({ range: 'DAYS_30' }),
      })
    );
  });
});
