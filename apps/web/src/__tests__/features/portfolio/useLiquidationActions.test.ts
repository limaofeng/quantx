import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useLiquidationActions } from '@/features/portfolio/hooks/useLiquidationActions';

const mocks = vi.hoisted(() => ({
  confirm: vi.fn(),
  dialogConfirm: vi.fn(),
  confirmPreview: vi.fn(),
  mutationSlot: 0,
  preview: vi.fn(),
}));

vi.mock('urql', () => ({
  useMutation: () => {
    const execute = mocks.mutationSlot++ % 2 === 0 ? mocks.preview : mocks.confirm;
    return [{ fetching: false }, execute];
  },
}));

vi.mock('@/features/dashboard/hooks', () => ({
  useCurrentAccount: () => ({
    data: { currentAccount: { id: 'ACCOUNT-1' } },
  }),
}));

vi.mock('@/components/ui/app-dialog-context', () => ({
  useAppDialog: () => ({ confirm: mocks.dialogConfirm }),
}));

describe('useLiquidationActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.mutationSlot = 0;
    mocks.confirmPreview.mockResolvedValue(true);
    mocks.preview.mockResolvedValue({
      data: {
        previewLiquidation: {
          success: true,
          preview: {
            challengeId: 'challenge-1',
            confirmationToken: 'token-1',
            includedCount: 1,
            skippedCount: 0,
            warnings: ['T+1 与可卖量将在确认时重新校验'],
            items: [
              {
                included: true,
                instrumentCode: '300917.SZ',
                instrumentName: '测试股份',
                protectedVolume: 100,
                reasonDetail: '可创建退出计划',
              },
            ],
          },
        },
      },
    });
    mocks.confirm.mockResolvedValue({
      data: {
        confirmLiquidation: {
          success: true,
          challengeId: 'challenge-1',
          commandId: 'command-1',
          message: '清仓命令已入队',
          status: 'QUEUED',
          plans: [],
        },
      },
    });
  });

  it('confirms the server snapshot before consuming the challenge', async () => {
    const { result } = renderHook(() => useLiquidationActions());

    let outcome: Awaited<ReturnType<typeof result.current.liquidateMultiple>>;
    await act(async () => {
      outcome = await result.current.liquidateMultiple(['300917.sz'], {
        completionStrategy: 'AVAILABLE_NOW',
        conflictStrategy: 'REPLACE_CANCELLABLE',
        executionMode: 'live',
        confirmPreview: mocks.confirmPreview,
      });
    });

    expect(mocks.preview).toHaveBeenCalledWith({
      input: expect.objectContaining({
        accountId: 'ACCOUNT-1',
        completionStrategy: 'AVAILABLE_NOW',
        conflictStrategy: 'REPLACE_CANCELLABLE',
        executionMode: 'LIVE',
        instrumentCodes: ['300917.SZ'],
        scope: 'SELECTED',
      }),
    });
    expect(mocks.confirmPreview).toHaveBeenCalledWith(
      expect.objectContaining({ challengeId: 'challenge-1' }),
      expect.stringContaining('不代表已经委托或成交')
    );
    expect(mocks.confirm).toHaveBeenCalledWith({
      input: {
        challengeId: 'challenge-1',
        confirmationToken: 'token-1',
      },
    });
    expect(outcome!).toMatchObject({
      commandId: 'command-1',
      status: 'QUEUED',
      success: true,
    });
    expect(outcome!.message).toContain('实际成交请以成交回报为准');
  });

  it('does not consume a challenge when the user rejects its snapshot', async () => {
    mocks.confirmPreview.mockResolvedValue(false);
    const { result } = renderHook(() => useLiquidationActions());

    let outcome: Awaited<ReturnType<typeof result.current.liquidateAll>>;
    await act(async () => {
      outcome = await result.current.liquidateAll({
        completionStrategy: 'UNTIL_SNAPSHOT_CLEARED',
        conflictStrategy: 'UNALLOCATED_ONLY',
        executionMode: 'paper',
        confirmPreview: mocks.confirmPreview,
      });
    });

    expect(mocks.confirm).not.toHaveBeenCalled();
    expect(outcome!).toMatchObject({
      challengeId: 'challenge-1',
      status: 'CANCELLED_BY_USER',
      success: false,
    });
  });
});
