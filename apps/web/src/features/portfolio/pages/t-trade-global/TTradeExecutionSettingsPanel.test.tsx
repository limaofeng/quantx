import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { TTradeTimeExitMode } from '@/generated/gql/graphql';

import { defaultSignalPolicyForm } from './signalPolicy';
import { TTradeExecutionSettingsPanel } from './TTradeExecutionSettingsPanel';
import type { SettingsForm } from './types';

const form: SettingsForm = {
  mode: 'paper',
  acknowledged: false,
  targetTradeAmount: '10000',
  maxTradeAmount: '12000',
  maxConcurrentBatches: '3',
  maxTotalTExposurePct: '10',
  targetProfitPct: '2',
  baseFloorPct: '0.5',
  initialGapPct: '1.5',
  trailingGapSlope: '0.25',
  maxGapPct: '3',
  highProfitLockEnabled: true,
  highProfitArmPct: '4',
  highProfitMaxDrawdownPct: '1.2',
  rapidReversalEnabled: true,
  rapidReversalWindowSeconds: '15',
  rapidReversalDrawdownPct: '0.8',
  rapidReversalConfirmTicks: '2',
  hardStopEnabled: false,
  hardStopPct: '-0.8',
  signalPolicy: defaultSignalPolicyForm,
  maxPriceDeviationPct: '0.3',
  limitUpTouchExitEnabled: true,
  limitUpTouchToleranceTicks: '0',
  timeExitMode: TTradeTimeExitMode.Unlimited,
  timeExitTime: '14:50',
  maxHoldingTradingDays: '5',
  cooldownSeconds: '300',
};

describe('TTradeExecutionSettingsPanel', () => {
  it('starts with a readable strategy blueprint instead of all inputs', () => {
    render(
      <TTradeExecutionSettingsPanel form={form} onFieldChange={vi.fn()} />
    );

    const panel = screen.getByRole('region', {
      name: '运行与资金约束',
    });
    expect(within(panel).getByText('¥ 10,000')).toBeInTheDocument();
    expect(within(panel).getByText('1.5% → 3%')).toBeInTheDocument();
    expect(
      within(panel).getByRole('img', {
        name: /收益达到 2% 后启动保护/,
      })
    ).toBeInTheDocument();
    expect(
      within(panel).queryByLabelText('目标单次金额')
    ).not.toBeInTheDocument();
    expect(within(panel).queryByLabelText('反转窗口')).not.toBeInTheDocument();
  });

  it('opens the funds editor from a summary and emits the original field key', async () => {
    const user = userEvent.setup();
    const onFieldChange = vi.fn();
    render(
      <TTradeExecutionSettingsPanel form={form} onFieldChange={onFieldChange} />
    );

    const summary = screen.getByRole('button', {
      name: /目标单次金额/,
    });
    expect(summary).toHaveAttribute('aria-expanded', 'false');
    await user.click(summary);
    expect(summary).toHaveAttribute('aria-expanded', 'true');

    fireEvent.change(screen.getByLabelText('目标单次金额'), {
      target: { value: '15000' },
    });
    expect(onFieldChange).toHaveBeenCalledWith('targetTradeAmount', '15000');
  });

  it('expands one risk guardrail at a time and keeps precise inputs labeled', async () => {
    const user = userEvent.setup();
    render(
      <TTradeExecutionSettingsPanel form={form} onFieldChange={vi.fn()} />
    );

    await user.click(screen.getByRole('button', { name: /极速反转退出/ }));
    expect(screen.getByLabelText('反转窗口')).toHaveValue('15');
    expect(screen.getByLabelText('回吐阈值')).toHaveValue('0.8');
    expect(screen.getByLabelText('连续确认')).toHaveValue('2');

    await user.click(screen.getByRole('button', { name: /高利润保护/ }));
    expect(screen.queryByLabelText('反转窗口')).not.toBeInTheDocument();
    expect(screen.getByLabelText('高利润武装线')).toHaveValue('4');
    expect(screen.getByLabelText('峰值最大回吐')).toHaveValue('1.2');
  });

  it('keeps mode and protection toggles explicitly named', async () => {
    const user = userEvent.setup();
    const onFieldChange = vi.fn();
    render(
      <TTradeExecutionSettingsPanel form={form} onFieldChange={onFieldChange} />
    );

    const paper = screen.getByRole('button', { name: '模拟观察' });
    const live = screen.getByRole('button', { name: '实盘执行' });
    expect(paper).toHaveAttribute('aria-pressed', 'true');
    expect(live).toHaveAttribute('aria-pressed', 'false');

    await user.click(live);
    await user.click(screen.getByRole('switch', { name: '启用极速反转退出' }));

    expect(onFieldChange).toHaveBeenCalledWith('mode', 'live');
    expect(onFieldChange).toHaveBeenCalledWith('rapidReversalEnabled', false);
  });

  it('keeps the long-hold warning visible when no automatic floor exists', () => {
    render(
      <TTradeExecutionSettingsPanel form={form} onFieldChange={vi.fn()} />
    );

    expect(
      screen.getByText(/未达到收益武装线的批次可能长期持有/)
    ).toBeInTheDocument();
  });
});
