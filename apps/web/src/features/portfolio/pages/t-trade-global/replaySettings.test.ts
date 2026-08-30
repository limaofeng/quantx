import { describe, expect, it } from 'vitest';

import { TTradeTimeExitMode } from '@/generated/gql/graphql';

import {
  cloneSettingsForm,
  costFormFromReplaySettings,
  defaultReplayCostForm,
  replaySettingsDifferenceCount,
  replaySettingsInput,
  settingsFormFromReplaySettings,
  validateReplaySettings,
} from './replaySettings';
import { defaultSignalPolicyForm, signalPolicyInput } from './signalPolicy';
import type { SettingsForm } from './types';

const settings = {
  targetTradeAmount: 10_000,
  maxTradeAmount: 12_000,
  maxConcurrentBatches: 3,
  maxTotalTExposurePct: 0.1,
  signalPolicy: {
    ...signalPolicyInput(defaultSignalPolicyForm),
    policyVersion: 'ignored',
    featureSchemaVersion: 'ignored',
  },
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
  commissionRate: 0.0003,
  minimumCommission: 5,
  stampTaxRate: 0.0005,
  transferFeeRate: 0.00001,
  slippageRate: 0.0001,
};

describe('replay settings', () => {
  it('round-trips persisted fractions through percentage form values', () => {
    const form = settingsFormFromReplaySettings(settings);
    const costs = costFormFromReplaySettings(settings);

    expect(replaySettingsInput(form, costs)).toMatchObject({
      maxTotalTExposurePct: 0.1,
      commissionRate: 0.0003,
      stampTaxRate: 0.0005,
      transferFeeRate: 0.00001,
      slippageRate: 0.0001,
    });
    expect(validateReplaySettings(form, costs)).toEqual([]);
  });

  it('detects invalid cross-field and cost assumptions', () => {
    const form = settingsFormFromReplaySettings(settings);
    form.maxTradeAmount = '9000';
    const costs = { ...defaultReplayCostForm, slippageRatePct: '1.1' };

    expect(validateReplaySettings(form, costs)).toEqual(
      expect.arrayContaining([
        '单次金额上限不能低于目标单次金额',
        '滑点率必须在 0 到 1 之间',
      ])
    );
  });

  it('validates an enabled time exit using an actual clock value', () => {
    const form = settingsFormFromReplaySettings(settings);
    form.timeExitMode = TTradeTimeExitMode.EndOfDay;
    form.timeExitTime = '25:70';

    expect(validateReplaySettings(form, defaultReplayCostForm)).toContain(
      '时间退出时刻必须使用有效的 HH:MM 格式'
    );
  });

  it('counts custom leaves without mutating the live form', () => {
    const live = settingsFormFromReplaySettings(settings);
    const draft: SettingsForm = cloneSettingsForm(live);
    draft.targetTradeAmount = '15000';
    draft.signalPolicy.allowedSessionCodes = ['CONTINUOUS_AM'];

    expect(
      replaySettingsDifferenceCount(
        draft,
        defaultReplayCostForm,
        live,
        defaultReplayCostForm
      )
    ).toBe(2);
    expect(live.targetTradeAmount).toBe('10000');
    expect(live.signalPolicy.allowedSessionCodes).not.toEqual([
      'CONTINUOUS_AM',
    ]);
  });
});
