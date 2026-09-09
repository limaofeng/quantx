import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ExecutionEnvironment,
  ExecutionOwnerType,
  Portfolio_PreviewTAssistantLiveEntryDocument,
  type Portfolio_PreviewTAssistantLiveEntryMutation,
  type Portfolio_TAssistantLiveApprovalQueueQuery,
} from '@/generated/t-assistant-live/graphql';

import { TAssistantLivePanel } from './TAssistantLivePanel';

const mocks = vi.hoisted(() => ({
  issue: vi.fn(),
  confirm: vi.fn(),
  query: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock('urql', () => ({
  useQuery: () => [mocks.query(), mocks.refresh],
  useMutation: (document: unknown) => [
    { fetching: false },
    document === Portfolio_PreviewTAssistantLiveEntryDocument
      ? mocks.issue
      : mocks.confirm,
  ],
}));
type PreviewResult =
  Portfolio_PreviewTAssistantLiveEntryMutation['previewTAssistantLiveEntry'];
let data: Portfolio_TAssistantLiveApprovalQueueQuery;
let preview: PreviewResult;

beforeEach(() => {
  vi.clearAllMocks();
  const future = new Date(Date.now() + 60000).toISOString();
  data = {
    tAssistantLiveApprovalQueue: {
      executionId: 'live-1',
      status: 'RUNNING',
      entryReadiness: 'READY',
      entryAuthorization: 'MANUAL_CONFIRM',
      reasonCodes: [],
      truncated: false,
      entries: [
        {
          intentId: 'intent-1',
          instrumentCode: '600000.SH',
          status: 'AWAITING_APPROVAL',
          requestedAmount: 1000,
          reason: '机会',
          expiresAt: future,
          canPreview: true,
          confirmationStatus: 'NONE',
        },
      ],
    },
  };
  preview = {
    success: true,
    code: 'PREVIEW_READY',
    message: '请核对',
    preview: {
      challengeId: 'challenge-1',
      confirmationToken: 'original-token',
      accountId: 'account-1',
      intentId: 'intent-1',
      instrumentCode: '600000.SH',
      side: 'BUY',
      bucket: 'swing',
      reason: '机会',
      targetVolume: 100,
      referencePrice: 10,
      estimatedAmount: 1000,
      signalExpiresAt: future,
      challengeExpiresAt: future,
      warnings: [],
      environment: ExecutionEnvironment.Live,
      executionOwner: {
        ownerType: ExecutionOwnerType.TAssistantExecution,
        ownerId: 'live-1',
      },
      tTradeAutoExitAuthorization: {
        planId: 'exit-1',
        maxProtectedVolume: 100,
        rules: [{ kind: 'stop_loss', price: 9 }],
        t1Policy: 'T_PLUS_ONE',
        executionPolicy: {},
        executionSemantics: '保护仅适用于此批次',
        authorizationExpiresAt: future,
      },
    },
  };
  mocks.query.mockImplementation(() => ({
    data,
    operation: { variables: { accountId: 'account-1' } },
    fetching: false,
  }));
  mocks.issue.mockImplementation(async () => ({
    data: { previewTAssistantLiveEntry: preview },
  }));
  mocks.confirm.mockResolvedValue({
    data: {
      confirmTAssistantLiveEntry: {
        success: true,
        code: 'T_ASSISTANT_CONFIRMATION_QUEUED',
        message: '确认已提交，等待重新分配；尚未成交',
        challengeId: 'challenge-1',
      },
    },
  });
});

async function open() {
  fireEvent.click(screen.getByRole('button', { name: '核对并确认' }));
  await screen.findByRole('region', { name: '买入与自动退出确认预览' });
}

describe('独立 LIVE 人工确认', () => {
  it('submits exactly the preview owner and token, then prevents another queued confirmation', async () => {
    render(<TAssistantLivePanel accountId="account-1" />);
    await open();
    expect(screen.getByText('保护仅适用于此批次')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: '确认买入及自动退出保护' })
    );
    await screen.findByText('确认已提交，等待重新分配；尚未成交');
    expect(mocks.confirm).toHaveBeenCalledWith({
      accountId: 'account-1',
      executionId: 'live-1',
      intentId: 'intent-1',
      confirmationToken: 'original-token',
    });
    expect(screen.getByRole('button', { name: '核对并确认' })).toBeDisabled();
  });

  it('retains the original token after a lost response and blocks issuing another preview', async () => {
    mocks.confirm.mockRejectedValueOnce(new Error('response lost'));
    render(<TAssistantLivePanel accountId="account-1" />);
    await open();
    fireEvent.click(
      screen.getByRole('button', { name: '确认买入及自动退出保护' })
    );
    const retry = await screen.findByRole('button', { name: '重试原确认' });
    expect(screen.getByRole('button', { name: '关闭预览' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '核对并确认' })).toBeDisabled();
    fireEvent.click(retry);
    await screen.findByText('确认已提交，等待重新分配；尚未成交');
    expect(mocks.confirm.mock.calls[0]).toEqual(mocks.confirm.mock.calls[1]);
    expect(mocks.issue).toHaveBeenCalledTimes(1);
  });

  it('rejects another owner and expired previews', async () => {
    if (!preview.preview) throw new Error('fixture');
    preview.preview.executionOwner.ownerId = 'foreign';
    render(<TAssistantLivePanel accountId="account-1" />);
    fireEvent.click(screen.getByRole('button', { name: '核对并确认' }));
    await screen.findByText('确认材料与当前执行不一致，请刷新后重试');
    expect(
      screen.queryByRole('button', { name: '确认买入及自动退出保护' })
    ).not.toBeInTheDocument();
    preview.preview.executionOwner.ownerId = 'live-1';
    preview.preview.challengeExpiresAt = new Date(
      Date.now() - 1000
    ).toISOString();
    await open();
    expect(
      screen.getByRole('button', { name: '确认买入及自动退出保护' })
    ).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it('clears preview on account change and hides cached facts from the old account', async () => {
    const view = render(<TAssistantLivePanel accountId="account-1" />);
    await open();
    view.rerender(<TAssistantLivePanel accountId="other" />);
    expect(
      screen.queryByRole('region', { name: '买入与自动退出确认预览' })
    ).not.toBeInTheDocument();
    expect(screen.queryByText('600000.SH')).not.toBeInTheDocument();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it('honors read-only queue and fails closed on query error', () => {
    data.tAssistantLiveApprovalQueue.entries[0].canPreview = false;
    const view = render(<TAssistantLivePanel accountId="account-1" />);
    expect(screen.getByRole('button', { name: '核对并确认' })).toBeDisabled();
    mocks.query.mockReturnValue({
      data,
      operation: { variables: { accountId: 'account-1' } },
      error: new Error('unavailable'),
    });
    view.rerender(<TAssistantLivePanel accountId="account-1" />);
    expect(screen.getByRole('alert')).toHaveTextContent('读取确认队列失败');
    expect(screen.queryByText('600000.SH')).not.toBeInTheDocument();
  });

  it('guards double clicks while the confirmation response is outstanding', async () => {
    let resolve: (value: unknown) => void = () => {};
    mocks.confirm.mockImplementation(
      () =>
        new Promise(value => {
          resolve = value;
        })
    );
    render(<TAssistantLivePanel accountId="account-1" />);
    await open();
    const button = screen.getByRole('button', {
      name: '确认买入及自动退出保护',
    });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mocks.confirm).toHaveBeenCalledTimes(1);
    resolve({
      data: {
        confirmTAssistantLiveEntry: {
          success: false,
          code: 'EXPIRED',
          message: '确认已过期',
        },
      },
    });
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent('确认已过期')
    );
  });
});
