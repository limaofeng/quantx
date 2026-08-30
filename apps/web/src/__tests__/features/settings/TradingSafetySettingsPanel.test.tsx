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
    quarantinedOrders: [] as Array<{
      clientOrderId: string;
      planId: string;
      intentId: string;
      quarantineReason: string;
      brokerOrderId: string;
      repairable: boolean;
      blockedReason: string;
      quarantinedAt: string;
      sourceSequence: number;
    }>,
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
    mocks.safety.quarantinedOrders = [];
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
    expect(screen.getByText('准入链路正常，当前休市待机')).toBeInTheDocument();
    expect(screen.getByText('0 项通过 · 1 项待机')).toBeInTheDocument();
    expect(
      screen.getByText('休市待机属于预期状态，不计入异常。')
    ).toBeInTheDocument();
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

    expect(screen.getByText('其余 1 项无异常')).toBeInTheDocument();
    expect(
      screen.getByText('所选范围内没有确认的准入异常。')
    ).toBeInTheDocument();
    expect(
      screen.getByText('休市待机属于预期状态，不会在这里形成事件。')
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('article', { name: /全市场行情链路/ })
    ).not.toBeInTheDocument();
    expect(mocks.useQuery).toHaveBeenCalledWith(
      expect.objectContaining({
        query: 'account-execution-safety-history',
        variables: expect.objectContaining({ range: 'DAYS_30' }),
      })
    );
  });

  it('presents resolved failures as incidents and filters by affected check', () => {
    mocks.useQuery.mockReturnValue([
      {
        fetching: false,
        data: {
          accountExecutionSafetyHistory: {
            available: true,
            range: 'DAYS_30',
            generatedAt: '2026-08-25T06:02:00Z',
            firstObservedAt: '2026-08-24T06:00:10Z',
            lastObservedAt: '2026-08-25T06:02:00Z',
            observerFresh: true,
            bucketSeconds: 14_400,
            incidentsTruncated: false,
            checks: [
              {
                code: 'LIVE_AGENT_READY',
                currentStatus: 'PASSED',
                checkedAt: '2026-08-25T06:02:00Z',
                reasonCode: 'AGENT_READY',
                publicMessage: 'QMT 实盘代理在线',
                coveragePct: 100,
                incidentCount: 1,
                points: [],
              },
              {
                code: 'MARKET_STREAM_READY',
                currentStatus: 'STANDBY',
                checkedAt: '2026-08-25T06:02:00Z',
                reasonCode: 'MARKET_CLOSED_STANDBY',
                publicMessage: '当前休市，等待下一交易时段',
                coveragePct: 100,
                incidentCount: 1,
                points: [],
              },
              {
                code: 'SNAPSHOT_RECONCILED',
                currentStatus: 'PASSED',
                checkedAt: '2026-08-25T06:02:00Z',
                reasonCode: 'SNAPSHOT_RECONCILED',
                publicMessage: '账户快照已对账',
                coveragePct: 100,
                incidentCount: 0,
                points: [],
              },
            ],
            incidents: [
              {
                id: 'incident-agent',
                checkCode: 'LIVE_AGENT_READY',
                openedAt: '2026-08-25T06:00:00Z',
                resolvedAt: '2026-08-25T06:01:00Z',
                lastConfirmedFailedAt: '2026-08-25T06:00:30Z',
                active: false,
                observationFresh: true,
                openedReasonCode: 'AGENT_OFFLINE',
                lastReasonCode: 'AGENT_OFFLINE',
                openedMessage: 'QMT 实盘代理短暂离线',
                lastMessage: 'QMT 实盘代理短暂离线，连接已经恢复。',
              },
              {
                id: 'incident-market-stream',
                checkCode: 'MARKET_STREAM_READY',
                openedAt: '2026-08-25T06:00:00Z',
                resolvedAt: '2026-08-25T06:01:00Z',
                lastConfirmedFailedAt: '2026-08-25T06:00:30Z',
                active: false,
                observationFresh: true,
                openedReasonCode: 'STREAM_SYNC_FAILED',
                lastReasonCode: 'STREAM_SYNC_FAILED',
                openedMessage: '行情水位同步中断',
                lastMessage: '行情水位同步中断，三阶段链路已经重新收敛。',
              },
            ],
          },
        },
      },
    ]);

    render(<TradingSafetySettingsPanel />);
    fireEvent.click(screen.getByRole('button', { name: '异常历史' }));

    expect(
      screen.getByRole('article', { name: 'QMT 实盘代理就绪：已恢复' })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('article', { name: '全市场行情链路：已恢复' })
    ).toBeInTheDocument();
    expect(
      screen.getByText('QMT 实盘代理短暂离线，连接已经恢复。')
    ).toBeInTheDocument();
    expect(screen.queryByText(/观测覆盖/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /QMT 实盘代理就绪/ }));

    expect(
      screen.getByRole('article', { name: 'QMT 实盘代理就绪：已恢复' })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('article', { name: '全市场行情链路：已恢复' })
    ).not.toBeInTheDocument();
  });

  it('binds an explicit quarantine repair preview to one exact order and snapshot', async () => {
    mocks.safety.quarantinedOrders = [
      {
        clientOrderId: 'sell-client-1',
        planId: 'exit-plan-1',
        intentId: 'exit-intent-1',
        quarantineReason: 'ACCOUNT_WIDE_STALE_SELL',
        brokerOrderId: 'broker-order-1',
        repairable: true,
        blockedReason: '',
        quarantinedAt: '2026-08-25T06:00:05Z',
        sourceSequence: 41,
      },
    ];

    render(<TradingSafetySettingsPanel />);

    fireEvent.change(screen.getByLabelText('暂停、紧急停止或隔离修复原因'), {
      target: { value: '已核对券商终态' },
    });

    fireEvent.click(
      screen.getByRole('button', { name: '修复委托 sell-client-1' })
    );

    await waitFor(() => {
      expect(mocks.previewControl).toHaveBeenCalledWith({
        input: expect.objectContaining({
          accountId: '300000013250',
          action: 'REPAIR_QUARANTINED_ORDER',
          clientOrderId: 'sell-client-1',
          quarantineReason: 'ACCOUNT_WIDE_STALE_SELL',
          snapshotId: 'snapshot-1',
          stateVersion: 1,
          reason: '已核对券商终态',
          idempotencyKey: expect.stringMatching(
            /^account-execution:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
          ),
        }),
      });
    });
    expect(
      screen.getByText('待确认：修复隔离委托 · sell-client-1')
    ).toBeInTheDocument();
  });

  it('keeps a quarantined order blocked until a newer full snapshot exists', () => {
    mocks.safety.quarantinedOrders = [
      {
        clientOrderId: 'sell-client-blocked',
        planId: 'exit-plan-2',
        intentId: 'exit-intent-2',
        quarantineReason: 'BROKER_EXECUTION_AFTER_RELEASE',
        brokerOrderId: '',
        repairable: false,
        blockedReason: 'SNAPSHOT_NOT_NEWER_THAN_QUARANTINE',
        quarantinedAt: '2026-08-25T06:00:05Z',
        sourceSequence: 42,
      },
    ];

    render(<TradingSafetySettingsPanel />);

    fireEvent.change(screen.getByLabelText('暂停、紧急停止或隔离修复原因'), {
      target: { value: '已尝试核对' },
    });

    expect(screen.getByText('快照必须严格晚于隔离事实')).toBeInTheDocument();
    expect(
      screen.getByRole('button', {
        name: '修复委托 sell-client-blocked',
      })
    ).toBeDisabled();
  });
});
