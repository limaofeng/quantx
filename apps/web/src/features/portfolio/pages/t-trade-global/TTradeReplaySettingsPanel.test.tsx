import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { TTradeTimeExitMode } from '@/generated/gql/graphql';

import {
  defaultReplayCostForm,
  settingsFormFromReplaySettings,
} from './replaySettings';
import { defaultSignalPolicyForm, signalPolicyInput } from './signalPolicy';
import {
  TTradeReplayFrozenSettings,
  TTradeReplaySettingsEditor,
} from './TTradeReplaySettingsPanel';

const form = settingsFormFromReplaySettings({
  targetTradeAmount: 10_000,
  maxTradeAmount: 12_000,
  maxConcurrentBatches: 3,
  maxTotalTExposurePct: 0.1,
  signalPolicy: signalPolicyInput(defaultSignalPolicyForm),
  maxPriceDeviationPct: 0.3,
  targetProfitPct: 2,
  baseFloorPct: 0.5,
  initialGapPct: 1.5,
  trailingGapSlope: 0.25,
  maxGapPct: 3,
  highProfitLockEnabled: true,
  highProfitArmPct: 4,
  highProfitMaxDrawdownPct: 1.2,
  rapidReversalEnabled: true,
  rapidReversalWindowSeconds: 15,
  rapidReversalDrawdownPct: 0.8,
  rapidReversalConfirmTicks: 2,
  limitUpTouchExitEnabled: true,
  limitUpTouchToleranceTicks: 0,
  hardStopEnabled: false,
  hardStopPct: -0.8,
  timeExitMode: TTradeTimeExitMode.Unlimited,
  timeExitTime: '14:50',
  maxHoldingTradingDays: 5,
  cooldownSeconds: 300,
});

describe('TTradeReplaySettingsPanel', () => {
  it('keeps replay edits isolated and exposes the restore action', async () => {
    const user = userEvent.setup();
    const onCostChange = vi.fn();
    const onRestore = vi.fn();
    render(
      <TTradeReplaySettingsEditor
        costs={defaultReplayCostForm}
        differenceCount={2}
        errors={[]}
        form={form}
        liveConfigVersion={7}
        liveSettingsStale={false}
        onCostChange={onCostChange}
        onFieldChange={vi.fn()}
        onRestore={onRestore}
        onSignalPolicyChange={vi.fn()}
        restoring={false}
      />
    );

    expect(screen.getByText('下次回测参数')).toBeVisible();
    expect(screen.getByText('实盘 v7')).toBeVisible();
    expect(screen.queryByLabelText('运行模式')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '还原当前实盘参数' }));
    expect(onRestore).toHaveBeenCalledOnce();
    const slippage = screen.getByLabelText('滑点率');
    await user.clear(slippage);
    expect(onCostChange).toHaveBeenLastCalledWith('slippageRatePct', '');
  });

  it('renders a frozen historical snapshot and copy action', async () => {
    const user = userEvent.setup();
    const onCopy = vi.fn();
    render(
      <TTradeReplayFrozenSettings
        costs={defaultReplayCostForm}
        differenceCount={3}
        form={form}
        onCopy={onCopy}
        onRestore={vi.fn()}
        restoring={false}
      />
    );

    expect(screen.getByText('冻结参数快照')).toBeVisible();
    expect(screen.getByText('与参考实盘差异 3 项')).toBeVisible();
    expect(screen.getByText(/V3 信号规则 · 100 项/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: '复制为新回测参数' }));
    expect(onCopy).toHaveBeenCalledOnce();
  });
});
