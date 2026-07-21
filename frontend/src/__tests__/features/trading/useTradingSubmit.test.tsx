import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useTradingSubmit } from '@/features/trading/components/TradingCard/hooks/useTradingSubmit';
import type { Stock } from '@/shared/types';

const mocks = vi.hoisted(() => ({
  createOrder: vi.fn(),
  onSuccess: vi.fn(),
  resetForm: vi.fn(),
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
  useCreateOrder: () => ({
    createOrder: mocks.createOrder,
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

function SubmitHarness() {
  const { handleSubmit } = useTradingSubmit('demo-user', mocks.onSuccess);

  return (
    <button
      type="button"
      onClick={event =>
        handleSubmit(
          event,
          'sell',
          'limit',
          makeStock(),
          '420',
          '48.76',
          mocks.resetForm
        )
      }
    >
      submit
    </button>
  );
}

describe('useTradingSubmit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createOrder.mockResolvedValue({
      data: {
        placeOrder: {
          message: '订单已提交',
          success: true,
        },
      },
    });
  });

  it('includes manual trade metadata required by OrderInput', async () => {
    render(<SubmitHarness />);

    fireEvent.click(screen.getByRole('button', { name: 'submit' }));

    await waitFor(() => expect(mocks.createOrder).toHaveBeenCalledTimes(1));
    expect(mocks.createOrder).toHaveBeenCalledWith({
      accountId: '300000013250',
      orderRemark: '交易控制台平仓: 688577.SH',
      price: 48.76,
      priceType: 'LIMIT',
      stockCode: '688577.SH',
      strategyName: '手动交易',
      type: 'SELL',
      volume: 420,
    });
  });

  it('shows a failure toast when placeOrder returns success false', async () => {
    mocks.createOrder.mockResolvedValue({
      data: {
        placeOrder: {
          message: '可用持仓不足',
          success: false,
        },
      },
    });

    render(<SubmitHarness />);

    fireEvent.click(screen.getByRole('button', { name: 'submit' }));

    await waitFor(() =>
      expect(mocks.toast).toHaveBeenCalledWith(
        expect.objectContaining({
          description: '可用持仓不足',
          title: '交易失败',
          variant: 'destructive',
        })
      )
    );
    expect(mocks.onSuccess).not.toHaveBeenCalled();
    expect(mocks.resetForm).not.toHaveBeenCalled();
  });
});
