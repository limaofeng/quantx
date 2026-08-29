import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { ManualOrderPreviewTicket } from '@/features/trading/components/TradingCard/hooks/useTradingSubmit';
import { ManualOrderConfirmationDialog } from '@/features/trading/components/TradingCard/ManualOrderConfirmationDialog';
import { ManualOrderPriceType, ManualOrderSide } from '@/generated/gql/graphql';

function preview(
  overrides: Partial<ManualOrderPreviewTicket> = {}
): ManualOrderPreviewTicket {
  return {
    accountId: '300000013250',
    availableCash: 100000,
    availableVolume: null,
    challengeExpiresAt: new Date(Date.now() + 60_000).toISOString(),
    challengeId: 'challenge-1',
    confirmationToken: 'secret-token',
    estimatedAmount: 4876,
    estimatedFees: 5,
    executionMode: 'LIVE',
    finalVolume: 100,
    idempotencyKey: 'manual-order-1',
    instrumentCode: '688577.SH',
    limitPrice: 48.76,
    priceType: ManualOrderPriceType.Limit,
    quoteTimestamp: new Date().toISOString(),
    referencePrice: 48.7,
    requestedVolume: 120,
    riskAction: 'CAP',
    riskDecisionId: 'risk-1',
    riskReasonCode: 'LOT_SIZE_CAPPED',
    riskReasonDetail: '按市场整手规则缩减',
    side: ManualOrderSide.Buy,
    warnings: ['确认时仍会重新校验行情'],
    ...overrides,
  };
}

describe('ManualOrderConfirmationDialog', () => {
  it('shows the server-sized LIVE order and only confirms the final volume', () => {
    const onConfirm = vi.fn().mockResolvedValue(true);

    render(
      <ManualOrderConfirmationDialog
        confirmationError=""
        isConfirming={false}
        onConfirm={onConfirm}
        onDismiss={vi.fn()}
        preview={preview()}
      />
    );

    expect(screen.getByText('LIVE 实盘委托')).toBeVisible();
    expect(
      screen.getByText(/风控已将请求的 120 股缩减为合法数量 100 股/)
    ).toBeVisible();
    expect(screen.getByText('LOT_SIZE_CAPPED')).toBeVisible();
    expect(screen.getByText('确认时仍会重新校验行情')).toBeVisible();
    expect(screen.queryByText('secret-token')).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '确认实盘买入 100 股' })
    );
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('disables confirmation after the one-time preview expires', () => {
    render(
      <ManualOrderConfirmationDialog
        confirmationError=""
        isConfirming={false}
        onConfirm={vi.fn()}
        onDismiss={vi.fn()}
        preview={preview({ challengeExpiresAt: '2020-01-01T00:00:00Z' })}
      />
    );

    expect(screen.getByRole('button', { name: '预览已过期' })).toBeDisabled();
  });
});
