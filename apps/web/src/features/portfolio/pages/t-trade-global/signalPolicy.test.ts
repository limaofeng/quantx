import { describe, expect, it } from 'vitest';

import {
  defaultSignalPolicyForm,
  localSignalPolicyErrors,
  signalPolicyForm,
  signalPolicyInput,
} from './signalPolicy';

describe('V3 signal policy form', () => {
  it('round-trips all 100 typed policy fields without hidden defaults', () => {
    const input = signalPolicyInput(defaultSignalPolicyForm);
    expect(Object.keys(input)).toHaveLength(100);
    expect(input.previewScore).toBe(55);
    expect(input.candidateTtlSeconds).toBe(30);
    expect(signalPolicyInput(signalPolicyForm(input))).toEqual(input);
  });

  it('catches threshold and causal-window cross-field errors before preview', () => {
    expect(
      localSignalPolicyErrors({
        ...defaultSignalPolicyForm,
        previewScore: '70',
        revalidateScore: '60',
        momentumWindowSeconds: '600',
      })
    ).toEqual([
      '分数必须满足：0 ≤ 再武装 < 重点观察 < 确认重验 < 候选锁存 ≤ 100',
      '动量短窗不能长于成交基线窗口',
    ]);
  });

  it('rejects blank numeric values instead of silently restoring defaults', () => {
    const form = { ...defaultSignalPolicyForm, maxSamples: '' };

    expect(signalPolicyInput(form).maxSamples).toBe(0);
    expect(localSignalPolicyErrors(form)).toEqual([
      '以下参数必须是有效数字：maxSamples',
    ]);
  });

  it('validates collection, time, weights, and coupled penalty contracts', () => {
    expect(
      localSignalPolicyErrors({
        ...defaultSignalPolicyForm,
        allowedSessionCodes: [],
        continuousAmStartTime: '9:30',
        pullbackDepthWeight: '24',
        momentumOverextensionPenaltyStartPremiumPct: '4',
      })
    ).toEqual(
      expect.arrayContaining([
        '允许时段必须非空、无重复且只包含连续竞价时段',
        '交易时段必须使用 HH:mm 或 HH:mm:ss 24 小时格式',
        '回撤正向权重合计必须为 100（当前 99）',
        '动量过度延伸惩罚起点必须等于 VWAP 追涨上限',
      ])
    );
  });

  it('accepts server-supported HH:mm input and zero baseline coverage', () => {
    expect(
      localSignalPolicyErrors({
        ...defaultSignalPolicyForm,
        continuousAmStartTime: '09:30',
        continuousAmEndTime: '11:30',
        continuousPmStartTime: '13:00',
        continuousPmEndTime: '14:57',
        momentumBaselineCoverageRatio: '0',
        momentumMinCoverageSeconds: '15',
      })
    ).toEqual([]);
  });
});
