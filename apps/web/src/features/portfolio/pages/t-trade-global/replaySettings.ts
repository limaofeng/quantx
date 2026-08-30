import { TTradeTimeExitMode } from '@/generated/gql/graphql';

import {
  signalPolicyForm,
  signalPolicyInput,
  type SignalPolicyLike,
  localSignalPolicyErrors,
} from './signalPolicy';
import type { SettingsForm, SignalPolicyFormValue } from './types';

export type ReplayCostForm = {
  commissionRatePct: string;
  minimumCommission: string;
  stampTaxRatePct: string;
  transferFeeRatePct: string;
  slippageRatePct: string;
};

export type ReplaySettingsLike = {
  targetTradeAmount: number;
  maxTradeAmount: number;
  maxConcurrentBatches: number;
  maxTotalTExposurePct: number;
  signalPolicy: SignalPolicyLike;
  maxPriceDeviationPct: number;
  targetProfitPct: number;
  baseFloorPct: number;
  initialGapPct: number;
  trailingGapSlope: number;
  maxGapPct: number;
  highProfitLockEnabled: boolean;
  highProfitArmPct: number;
  highProfitMaxDrawdownPct: number;
  rapidReversalEnabled: boolean;
  rapidReversalWindowSeconds: number;
  rapidReversalDrawdownPct: number;
  rapidReversalConfirmTicks: number;
  limitUpTouchExitEnabled: boolean;
  limitUpTouchToleranceTicks: number;
  hardStopEnabled: boolean;
  hardStopPct: number;
  timeExitMode: TTradeTimeExitMode;
  timeExitTime: string;
  maxHoldingTradingDays: number;
  cooldownSeconds: number;
  commissionRate?: number;
  minimumCommission?: number;
  stampTaxRate?: number;
  transferFeeRate?: number;
  slippageRate?: number;
};

type ReplayCostSettingsLike = Pick<
  ReplaySettingsLike,
  | 'commissionRate'
  | 'minimumCommission'
  | 'stampTaxRate'
  | 'transferFeeRate'
  | 'slippageRate'
>;

export const defaultReplayCostForm: ReplayCostForm = {
  commissionRatePct: '0.03',
  minimumCommission: '5',
  stampTaxRatePct: '0.05',
  transferFeeRatePct: '0.001',
  slippageRatePct: '0.01',
};

export function cloneSettingsForm(form: SettingsForm): SettingsForm {
  return {
    ...form,
    signalPolicy: Object.fromEntries(
      Object.entries(form.signalPolicy).map(([key, value]) => [
        key,
        Array.isArray(value) ? [...value] : value,
      ])
    ) as SettingsForm['signalPolicy'],
  };
}

export function cloneReplayCostForm(form: ReplayCostForm): ReplayCostForm {
  return { ...form };
}

export function settingsFormFromReplaySettings(
  settings: ReplaySettingsLike
): SettingsForm {
  return {
    mode: 'paper',
    acknowledged: false,
    targetTradeAmount: String(settings.targetTradeAmount),
    maxTradeAmount: String(settings.maxTradeAmount),
    maxConcurrentBatches: String(settings.maxConcurrentBatches),
    maxTotalTExposurePct: String(settings.maxTotalTExposurePct * 100),
    targetProfitPct: String(settings.targetProfitPct),
    baseFloorPct: String(settings.baseFloorPct),
    initialGapPct: String(settings.initialGapPct),
    trailingGapSlope: String(settings.trailingGapSlope),
    maxGapPct: String(settings.maxGapPct),
    highProfitLockEnabled: settings.highProfitLockEnabled,
    highProfitArmPct: String(settings.highProfitArmPct),
    highProfitMaxDrawdownPct: String(settings.highProfitMaxDrawdownPct),
    rapidReversalEnabled: settings.rapidReversalEnabled,
    rapidReversalWindowSeconds: String(settings.rapidReversalWindowSeconds),
    rapidReversalDrawdownPct: String(settings.rapidReversalDrawdownPct),
    rapidReversalConfirmTicks: String(settings.rapidReversalConfirmTicks),
    hardStopEnabled: settings.hardStopEnabled,
    hardStopPct: String(settings.hardStopPct),
    signalPolicy: signalPolicyForm(settings.signalPolicy),
    maxPriceDeviationPct: String(settings.maxPriceDeviationPct),
    limitUpTouchExitEnabled: settings.limitUpTouchExitEnabled,
    limitUpTouchToleranceTicks: String(settings.limitUpTouchToleranceTicks),
    timeExitMode: settings.timeExitMode,
    timeExitTime: settings.timeExitTime,
    maxHoldingTradingDays: String(settings.maxHoldingTradingDays),
    cooldownSeconds: String(settings.cooldownSeconds),
  };
}

export function costFormFromReplaySettings(
  settings: ReplayCostSettingsLike
): ReplayCostForm {
  return {
    commissionRatePct: String((settings.commissionRate ?? 0.0003) * 100),
    minimumCommission: String(settings.minimumCommission ?? 5),
    stampTaxRatePct: String((settings.stampTaxRate ?? 0.0005) * 100),
    transferFeeRatePct: String((settings.transferFeeRate ?? 0.00001) * 100),
    slippageRatePct: String((settings.slippageRate ?? 0.0001) * 100),
  };
}

function finiteNumber(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function integerNumber(value: string) {
  const parsed = finiteNumber(value);
  return Number.isInteger(parsed) ? parsed : Number.NaN;
}

export function replaySettingsInput(form: SettingsForm, costs: ReplayCostForm) {
  return {
    targetTradeAmount: finiteNumber(form.targetTradeAmount),
    maxTradeAmount: finiteNumber(form.maxTradeAmount),
    maxConcurrentBatches: integerNumber(form.maxConcurrentBatches),
    maxTotalTExposurePct: finiteNumber(form.maxTotalTExposurePct) / 100,
    signalPolicy: signalPolicyInput(form.signalPolicy),
    maxPriceDeviationPct: finiteNumber(form.maxPriceDeviationPct),
    targetProfitPct: finiteNumber(form.targetProfitPct),
    baseFloorPct: finiteNumber(form.baseFloorPct),
    initialGapPct: finiteNumber(form.initialGapPct),
    trailingGapSlope: finiteNumber(form.trailingGapSlope),
    maxGapPct: finiteNumber(form.maxGapPct),
    highProfitLockEnabled: form.highProfitLockEnabled,
    highProfitArmPct: finiteNumber(form.highProfitArmPct),
    highProfitMaxDrawdownPct: finiteNumber(form.highProfitMaxDrawdownPct),
    rapidReversalEnabled: form.rapidReversalEnabled,
    rapidReversalWindowSeconds: integerNumber(form.rapidReversalWindowSeconds),
    rapidReversalDrawdownPct: finiteNumber(form.rapidReversalDrawdownPct),
    rapidReversalConfirmTicks: integerNumber(form.rapidReversalConfirmTicks),
    limitUpTouchExitEnabled: form.limitUpTouchExitEnabled,
    limitUpTouchToleranceTicks: integerNumber(form.limitUpTouchToleranceTicks),
    hardStopEnabled: form.hardStopEnabled,
    hardStopPct: finiteNumber(form.hardStopPct),
    timeExitMode: form.timeExitMode,
    timeExitTime: form.timeExitTime,
    maxHoldingTradingDays: integerNumber(form.maxHoldingTradingDays),
    cooldownSeconds: integerNumber(form.cooldownSeconds),
    commissionRate: finiteNumber(costs.commissionRatePct) / 100,
    minimumCommission: finiteNumber(costs.minimumCommission),
    stampTaxRate: finiteNumber(costs.stampTaxRatePct) / 100,
    transferFeeRate: finiteNumber(costs.transferFeeRatePct) / 100,
    slippageRate: finiteNumber(costs.slippageRatePct) / 100,
  };
}

type NumericRule = {
  integer?: boolean;
  label: string;
  max: number;
  min: number;
  value: string;
};

export function validateReplaySettings(
  form: SettingsForm,
  costs: ReplayCostForm
): string[] {
  const rules: NumericRule[] = [
    {
      label: '目标单次金额',
      value: form.targetTradeAmount,
      min: 100,
      max: 1_000_000,
    },
    {
      label: '单次金额上限',
      value: form.maxTradeAmount,
      min: 100,
      max: 1_000_000,
    },
    {
      label: '账户并发',
      value: form.maxConcurrentBatches,
      min: 1,
      max: 20,
      integer: true,
    },
    {
      label: '账户总 T 暴露',
      value: form.maxTotalTExposurePct,
      min: 1,
      max: 100,
    },
    {
      label: '确认价偏离',
      value: form.maxPriceDeviationPct,
      min: 0.05,
      max: 2,
    },
    { label: '止盈武装线', value: form.targetProfitPct, min: 0.1, max: 20 },
    { label: '初始保护线', value: form.baseFloorPct, min: -2, max: 10 },
    { label: '初始回撤间距', value: form.initialGapPct, min: 0.1, max: 10 },
    { label: '回撤斜率', value: form.trailingGapSlope, min: 0, max: 2 },
    { label: '最大回撤间距', value: form.maxGapPct, min: 0.1, max: 15 },
    { label: '高利润武装线', value: form.highProfitArmPct, min: 0.5, max: 30 },
    {
      label: '高利润最大回吐',
      value: form.highProfitMaxDrawdownPct,
      min: 0.1,
      max: 10,
    },
    {
      label: '极速反转窗口',
      value: form.rapidReversalWindowSeconds,
      min: 3,
      max: 120,
      integer: true,
    },
    {
      label: '极速反转回撤',
      value: form.rapidReversalDrawdownPct,
      min: 0.1,
      max: 5,
    },
    {
      label: '极速反转确认 Tick',
      value: form.rapidReversalConfirmTicks,
      min: 1,
      max: 10,
      integer: true,
    },
    {
      label: '涨停触达容差 Tick',
      value: form.limitUpTouchToleranceTicks,
      min: 0,
      max: 20,
      integer: true,
    },
    {
      label: '批次冷却时间',
      value: form.cooldownSeconds,
      min: 0,
      max: 3600,
      integer: true,
    },
    { label: '佣金率', value: costs.commissionRatePct, min: 0, max: 1 },
    { label: '最低佣金', value: costs.minimumCommission, min: 0, max: 100 },
    { label: '印花税率', value: costs.stampTaxRatePct, min: 0, max: 1 },
    { label: '过户费率', value: costs.transferFeeRatePct, min: 0, max: 1 },
    { label: '滑点率', value: costs.slippageRatePct, min: 0, max: 1 },
  ];
  const errors = rules.flatMap(rule => {
    const value = rule.integer
      ? integerNumber(rule.value)
      : finiteNumber(rule.value);
    if (!Number.isFinite(value)) {
      return [`${rule.label}必须是${rule.integer ? '整数' : '有效数字'}`];
    }
    return value < rule.min || value > rule.max
      ? [`${rule.label}必须在 ${rule.min} 到 ${rule.max} 之间`]
      : [];
  });
  const value = (field: keyof SettingsForm) =>
    finiteNumber(String(form[field]));
  if (value('maxTradeAmount') < value('targetTradeAmount')) {
    errors.push('单次金额上限不能低于目标单次金额');
  }
  if (value('baseFloorPct') >= value('targetProfitPct')) {
    errors.push('初始保护线必须低于止盈武装线');
  }
  if (value('maxGapPct') < value('initialGapPct')) {
    errors.push('最大回撤间距不能低于初始回撤间距');
  }
  if (value('highProfitArmPct') <= value('targetProfitPct')) {
    errors.push('高利润武装线必须高于基础止盈武装线');
  }
  if (value('highProfitMaxDrawdownPct') >= value('highProfitArmPct')) {
    errors.push('高利润最大回吐必须低于高利润武装线');
  }
  if (
    form.hardStopEnabled &&
    (value('hardStopPct') <= -10 || value('hardStopPct') >= 0)
  ) {
    errors.push('启用硬止损时，止损比例必须大于 -10 且小于 0');
  }
  if (form.timeExitMode === TTradeTimeExitMode.MaxHoldingDays) {
    const days = integerNumber(form.maxHoldingTradingDays);
    if (!Number.isFinite(days) || days < 1 || days > 250) {
      errors.push('最长持有交易日必须在 1 到 250 之间');
    }
  }
  if (form.timeExitMode !== TTradeTimeExitMode.Unlimited) {
    const matched = /^(\d{2}):(\d{2})$/.exec(form.timeExitTime);
    const hour = Number(matched?.[1]);
    const minute = Number(matched?.[2]);
    if (
      !matched ||
      !Number.isInteger(hour) ||
      !Number.isInteger(minute) ||
      hour < 0 ||
      hour > 23 ||
      minute < 0 ||
      minute > 59
    ) {
      errors.push('时间退出时刻必须使用有效的 HH:MM 格式');
    }
  }
  return [...errors, ...localSignalPolicyErrors(form.signalPolicy)];
}

function flatten(value: unknown, prefix = ''): Map<string, unknown> {
  const result = new Map<string, unknown>();
  if (Array.isArray(value)) {
    result.set(prefix, JSON.stringify(value));
    return result;
  }
  if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      const path = prefix ? `${prefix}.${key}` : key;
      for (const [childKey, child] of flatten(item, path)) {
        result.set(childKey, child);
      }
    }
    return result;
  }
  result.set(prefix, value);
  return result;
}

export function replaySettingsDifferenceCount(
  leftForm: SettingsForm,
  leftCosts: ReplayCostForm,
  rightForm: SettingsForm,
  rightCosts: ReplayCostForm
) {
  const left = flatten(replaySettingsInput(leftForm, leftCosts));
  const right = flatten(replaySettingsInput(rightForm, rightCosts));
  return new Set([...left.keys(), ...right.keys()]).size
    ? [...new Set([...left.keys(), ...right.keys()])].filter(
        key => left.get(key) !== right.get(key)
      ).length
    : 0;
}

export function updateSignalPolicyValue(
  form: SettingsForm,
  field: keyof SettingsForm['signalPolicy'],
  value: SignalPolicyFormValue
) {
  return {
    ...form,
    signalPolicy: { ...form.signalPolicy, [field]: value },
  };
}
