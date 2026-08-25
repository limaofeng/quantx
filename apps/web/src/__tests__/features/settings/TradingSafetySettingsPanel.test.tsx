import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { TradingSafetySettingsPanel } from '@/features/settings/components/TradingSafetySettingsPanel';

const mocks = vi.hoisted(() => ({
  confirmControl: vi.fn(),
  previewControl: vi.fn(),
  refreshSafety: vi.fn(),
  useMutation: vi.fn(),
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
    checks: [],
  },
}));

vi.mock('urql', () => ({
  useMutation: mocks.useMutation,
}));

vi.mock('@/features/trading-safety', () => ({
  ConfirmAccountExecutionControlMutation: 'confirm-account-execution-control',
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
    vi.unstubAllGlobals();
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
});
