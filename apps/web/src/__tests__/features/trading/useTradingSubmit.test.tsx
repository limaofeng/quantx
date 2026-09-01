import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useTradingSubmit } from '@/features/trading/components/TradingCard/hooks/useTradingSubmit';
import {
  ManualOrderExecutionMode,
  ManualOrderPriceType,
  ManualOrderSide,
} from '@/generated/gql/graphql';
import type { Stock } from '@/shared/types';

const mocks = vi.hoisted(() => ({
  confirmManualOrder: vi.fn(),
  manualOrderAttempt: null as Record<string, unknown> | null,
  onQueued: vi.fn(),
  previewManualOrder: vi.fn(),
  refreshManualOrderAttempt: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('@/features/dashboard/hooks', () => ({
  useCurrentAccount: () => ({
    data: {
      currentAccount: {
        id: '300000013250',
      },
    },
    error: null,
    loading: false,
  }),
}));

vi.mock('@/features/trading/hooks', () => ({
  useConfirmManualOrder: () => ({
    execute: mocks.confirmManualOrder,
    loading: false,
  }),
  useManualOrderCapabilities: () => ({
    capabilities: {
      accountId: '300000013250',
      canLiveBuy: true,
      canLiveSell: true,
      canManualTrade: true,
      defaultExecutionMode: ManualOrderExecutionMode.Paper,
      executionModes: [
        ManualOrderExecutionMode.Paper,
        ManualOrderExecutionMode.Live,
      ],
      instrumentCode: '688577.SH',
      liveBlockedReasons: [],
      liveReady: true,
      supportedPriceTypes: [
        ManualOrderPriceType.Limit,
        ManualOrderPriceType.Best,
      ],
      supportedSides: [ManualOrderSide.Buy, ManualOrderSide.Sell],
      warnings: [],
    },
    error: null,
    loading: false,
  }),
  useManualOrderAttempt: (
    _accountId: string | undefined,
    clientOrderId: string | null
  ) => ({
    attempt: clientOrderId ? mocks.manualOrderAttempt : null,
    error: null,
    loading: false,
    refresh: mocks.refreshManualOrderAttempt,
  }),
  usePreviewManualOrder: () => ({
    execute: mocks.previewManualOrder,
    loading: false,
  }),
}));

vi.mock('@/hooks/use-toast', () => ({
  useToast: () => ({
    toast: mocks.toast,
  }),
}));

function makeStock(): Stock {
  return {
    id: '688577.SH',
    name: '浙海德曼',
    quote: {
      changePercent: -1.67,
      lastPrice: 48.76,
    },
    stockCode: '688577.SH',
  };
}

function previewPayload() {
  return {
    accountId: '300000013250',
    availableCash: 100000,
    availableVolume: 420,
    challengeExpiresAt: new Date(Date.now() + 60_000).toISOString(),
    challengeId: 'challenge-1',
    confirmationToken: 'one-time-token',
    estimatedAmount: 20479.2,
    estimatedFees: 10.24,
    executionMode: 'PAPER',
    finalVolume: 420,
    idempotencyKey: 'manual-order-test',
    instrumentCode: '688577.SH',
    limitPrice: 48.76,
    priceType: ManualOrderPriceType.Limit,
    quoteTimestamp: new Date().toISOString(),
    referencePrice: 48.7,
    requestedVolume: 420,
    riskAction: 'ALLOW',
    riskDecisionId: 'risk-1',
    riskReasonCode: 'OK',
    riskReasonDetail: '统一风控允许',
    side: ManualOrderSide.Sell,
    warnings: [],
  };
}

function SubmitHarness({
  orderType = 'limit',
}: {
  orderType?: 'best' | 'limit';
}) {
  const submission = useTradingSubmit('688577.SH', mocks.onQueued);

  return (
    <>
      <button
        type="button"
        onClick={event =>
          submission.handleSubmit(event, {
            executionMode: ManualOrderExecutionMode.Paper,
            orderType,
            price: orderType === 'best' ? '' : '48.76',
            quantity: '420',
            selectedStock: makeStock(),
            tradeType: 'sell',
          })
        }
      >
        preview
      </button>
      {submission.preview && (
        <button type="button" onClick={() => void submission.confirmPreview()}>
          confirm
        </button>
      )}
    </>
  );
}

describe('useTradingSubmit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.manualOrderAttempt = null;
    mocks.previewManualOrder.mockResolvedValue({
      data: {
        previewManualOrder: {
          code: 'PREVIEW_READY',
          message: '请核对后确认',
          preview: previewPayload(),
          success: true,
        },
      },
    });
    mocks.confirmManualOrder.mockResolvedValue({
      data: {
        confirmManualOrder: {
          challengeId: 'challenge-1',
          clientOrderId: 'client-order-1',
          code: 'MANUAL_ORDER_QUEUED',
          message:
            '下单请求已进入可靠队列，尚未生成券商委托；正在等待 QMT Agent 下单前复核和券商回报',
          status: 'QUEUED',
          success: true,
        },
      },
    });
  });

  it('generates a server preview with an independent idempotency key', async () => {
    render(<SubmitHarness />);

    fireEvent.click(screen.getByRole('button', { name: 'preview' }));

    await waitFor(() =>
      expect(mocks.previewManualOrder).toHaveBeenCalledTimes(1)
    );
    expect(mocks.previewManualOrder).toHaveBeenCalledWith({
      input: {
        accountId: '300000013250',
        executionMode: ManualOrderExecutionMode.Paper,
        idempotencyKey: expect.any(String),
        instrumentCode: '688577.SH',
        limitPrice: 48.76,
        priceType: ManualOrderPriceType.Limit,
        side: ManualOrderSide.Sell,
        volume: 420,
      },
    });
    expect(mocks.confirmManualOrder).not.toHaveBeenCalled();
    expect(
      await screen.findByRole('button', { name: 'confirm' })
    ).toBeVisible();
  });

  it('only queues after consuming the preview confirmation challenge', async () => {
    render(<SubmitHarness />);

    fireEvent.click(screen.getByRole('button', { name: 'preview' }));
    fireEvent.click(await screen.findByRole('button', { name: 'confirm' }));

    await waitFor(() =>
      expect(mocks.confirmManualOrder).toHaveBeenCalledWith({
        input: {
          challengeId: 'challenge-1',
          confirmationToken: 'one-time-token',
        },
      })
    );
    expect(mocks.onQueued).toHaveBeenCalledTimes(1);
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        description:
          '下单请求已进入可靠队列，尚未生成券商委托；正在等待 QMT Agent 下单前复核和券商回报',
        title: '下单请求已进入队列',
      })
    );
  });

  it('reports an Agent pre-submit rejection as no broker order', async () => {
    mocks.manualOrderAttempt = {
      accountId: '300000013250',
      brokerOrderId: null,
      clientOrderId: 'client-order-1',
      createdAt: new Date().toISOString(),
      deliveryStatus: 'REJECTED',
      executionMode: ManualOrderExecutionMode.Live,
      instrumentCode: '688577.SH',
      message: 'QMT Agent 下单前行情已超过 30 秒，未向券商提交',
      side: ManualOrderSide.Sell,
      status: 'REJECTED',
      statusReason: 'stale live quote',
      updatedAt: new Date().toISOString(),
      volume: 420,
    };
    render(<SubmitHarness />);

    fireEvent.click(screen.getByRole('button', { name: 'preview' }));
    fireEvent.click(await screen.findByRole('button', { name: 'confirm' }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith({
        description: 'QMT Agent 下单前行情已超过 30 秒，未向券商提交',
        title: '未生成券商委托',
        variant: 'destructive',
      })
    );
  });

  it('does not open confirmation when the server rejects preview', async () => {
    mocks.previewManualOrder.mockResolvedValue({
      data: {
        previewManualOrder: {
          code: 'RISK_REJECTED',
          message: '可用持仓不足',
          preview: null,
          success: false,
        },
      },
    });

    render(<SubmitHarness />);
    fireEvent.click(screen.getByRole('button', { name: 'preview' }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          description: '可用持仓不足',
          title: '无法生成安全预览',
          variant: 'destructive',
        })
      )
    );
    expect(screen.queryByRole('button', { name: 'confirm' })).toBeNull();
  });

  it('omits the limit price for a BEST quote preview', async () => {
    render(<SubmitHarness orderType="best" />);
    fireEvent.click(screen.getByRole('button', { name: 'preview' }));

    await waitFor(() =>
      expect(mocks.previewManualOrder).toHaveBeenCalledWith({
        input: expect.objectContaining({
          limitPrice: undefined,
          priceType: ManualOrderPriceType.Best,
        }),
      })
    );
  });
});
