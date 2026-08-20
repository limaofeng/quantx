import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  EntryAuthorizationConfirmationDialog,
  EntryIntentConfirmationDialog,
} from '@/features/entry-plans/components/EntryPlanConfirmations';

describe('entry plan confirmation challenges', () => {
  it('requires an explicit second action before LIVE AUTO starts', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <EntryAuthorizationConfirmationDialog
        busy={false}
        challenge={{
          planId: 'plan-1',
          configVersion: 4,
          challengeId: 'challenge-1',
          confirmationToken: 'secret-confirmation-token',
          authorizationFingerprint: 'fingerprint-1',
          challengeExpiresAt: '2099-08-20T10:01:00+08:00',
          authorizationExpiresAt: '2099-08-20T11:00:00+08:00',
          summary: '授权 605499.SH 在硬预算内自动分批买入',
          riskEnvelope: {
            cash_buffer_pct: 0.25,
            instrument_code: '605499.SH',
            max_total_amount_cny: 20_000,
            max_buy_price: 128,
          },
        }}
        error={null}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />
    );

    expect(
      screen.getByRole('heading', { name: '确认实盘自动建仓授权' })
    ).toBeVisible();
    expect(screen.getByText('¥20,000')).toBeVisible();
    expect(screen.getByText('25.0%')).toBeVisible();
    expect(screen.queryByText('secret-confirmation-token')).toBeNull();
    expect(onConfirm).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: '确认授权并启动' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('shows a device-bound intent challenge before rerisk and order routing', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(
      <EntryIntentConfirmationDialog
        busy={false}
        error={null}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
        preview={{
          intentId: 'intent-1',
          planId: 'plan-1',
          instrumentCode: '605499.SH',
          valid: true,
          code: 'READY',
          message: '最新风控允许确认',
          signalPrice: 123,
          latestPrice: 123.5,
          priceDeviationBps: 40,
          requestedAmountCny: 12_350,
          sizedVolume: 100,
          finalVolume: 100,
          riskAction: 'ALLOW',
          expiresAtMs: Date.parse('2099-08-20T10:02:00+08:00'),
          challengeId: 'challenge-2',
          confirmationToken: 'one-time-token',
          challengeExpiresAt: '2099-08-20T10:01:00+08:00',
          warnings: ['价格已较信号价上涨 0.4%'],
        }}
      />
    );

    expect(
      screen.getByRole('list', { name: '本次买入确认警告' })
    ).toHaveTextContent('价格已较信号价上涨 0.4%');
    expect(screen.queryByText('one-time-token')).toBeNull();
    expect(screen.getByText(/券商成交回报才是成交事实/)).toBeVisible();

    await user.click(screen.getByRole('button', { name: '确认提交买入' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
