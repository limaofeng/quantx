import type { SignalPolicyForm, SignalPolicyInput } from './types';

export type SignalPolicyLike = SignalPolicyInput & {
  policyVersion?: string;
  featureSchemaVersion?: string;
};

export const supportedRequiredFields = [
  'bid_price',
  'ask_price',
  'cumulative_amount',
  'cumulative_volume',
] as const;

export const supportedSessionCodes = [
  'CONTINUOUS_AM',
  'CONTINUOUS_PM',
] as const;

const defaultSignalPolicyInput: SignalPolicyInput = {
  maxSamples: 3000,
  maxQuoteAgeMs: 3000,
  pullbackMinSamples: 3,
  pullbackMinCoverageSeconds: 15,
  momentumMinSamples: 3,
  momentumMinCoverageSeconds: 240,
  sparseDegradedGapSeconds: 60,
  pullbackRequiredFields: [...supportedRequiredFields],
  momentumRequiredFields: [...supportedRequiredFields],
  allowedSessionCodes: [...supportedSessionCodes],
  continuousAmStartTime: '09:30:00',
  continuousAmEndTime: '11:30:00',
  continuousPmStartTime: '13:00:00',
  continuousPmEndTime: '14:57:00',
  closeProtectionSeconds: 0,
  pullbackLookbackSeconds: 300,
  pullbackStabilizationSeconds: 15,
  pullbackThresholdPct: 0.8,
  pullbackFormationThresholdMultiplier: 0.5,
  pullbackReboundThresholdPct: 0.2,
  pullbackMaxSpreadTicks: 3,
  pullbackVolumeShortWindowSeconds: 15,
  pullbackVolumeBaselineWindowSeconds: 60,
  momentumEnabled: true,
  momentumWindowSeconds: 60,
  momentumMinRisePct: 0.8,
  momentumFormationThresholdMultiplier: 0.5,
  momentumMinMoveSeconds: 15,
  momentumBaselineSeconds: 300,
  momentumBaselineCoverageRatio: 0.8,
  momentumMinAmountVelocityRatio: 2,
  momentumMinVwapPremiumPct: 2,
  momentumMaxVwapPremiumPct: 3.5,
  momentumHighToleranceTicks: 1,
  momentumMaxSpreadTicks: 10,
  momentumMaxSpreadPct: 0.3,
  profilePullbackThresholdMinMultiplier: 0.75,
  profilePullbackThresholdMaxMultiplier: 2,
  profileMomentumRiseMinMultiplier: 0.75,
  profileMomentumRiseMaxMultiplier: 2,
  profileMomentumVelocityMinRatio: 1.25,
  profileMomentumVelocityMaxRatio: 5,
  pullbackDepthWeight: 25,
  pullbackReboundWeight: 20,
  pullbackStabilizationWeight: 15,
  pullbackTurnSlopeWeight: 10,
  pullbackVwapWeight: 10,
  pullbackLiquidityWeight: 10,
  pullbackVolumeWeight: 10,
  momentumRiseWeight: 20,
  momentumTurnoverWeight: 20,
  momentumSlopeWeight: 15,
  momentumPersistenceWeight: 10,
  momentumVwapWeight: 15,
  momentumLiquidityWeight: 10,
  momentumBookImbalanceWeight: 10,
  pullbackDepthScoreMinPct: 0,
  pullbackDepthScoreTargetMultiplier: 1,
  pullbackReboundScoreMinPct: 0,
  pullbackReboundScoreMaxPct: 0.2,
  pullbackStabilizationScoreMinSeconds: 0,
  pullbackStabilizationScoreMaxSeconds: 15,
  pullbackTurnSlopeScoreMinPctPerSecond: 0,
  pullbackTurnSlopeScoreMaxPctPerSecond: 0.013333333333333334,
  pullbackVwapFullScoreMaxPremiumPct: 0,
  pullbackVwapZeroScorePremiumPct: 0.5,
  pullbackLiquidityFullScoreSpreadTicks: 1,
  pullbackLiquidityZeroScoreSpreadTicks: 4,
  pullbackVolumeScoreMinRatio: 0.8,
  pullbackVolumeScoreMaxRatio: 1.5,
  momentumRiseScoreMinPct: 0,
  momentumRiseScoreTargetMultiplier: 1,
  momentumTurnoverScoreMinRatio: 1,
  momentumTurnoverScoreTargetMultiplier: 1,
  momentumSlopeScoreMinPctPerSecond: 0,
  momentumSlopeScoreTargetMultiplier: 1,
  momentumPersistenceScoreMinRatio: 0.75,
  momentumPersistenceScoreMaxRatio: 1,
  momentumVwapZeroScoreMinPremiumPct: 0,
  momentumVwapZeroScoreMaxPremiumPct: 7,
  momentumLiquidityFullScoreSpreadTicks: 1,
  momentumLiquidityZeroScoreSpreadTicks: 11,
  momentumBookImbalanceScoreMinRatio: -0.2,
  momentumBookImbalanceScoreMaxRatio: 0.4,
  pullbackDataQualityPenaltyPoints: 10,
  pullbackChasePenaltyStartPremiumPct: 0,
  pullbackChasePenaltyFullPremiumPct: 1,
  pullbackChasePenaltyPoints: 20,
  momentumDataQualityPenaltyPoints: 10,
  momentumOverextensionPenaltyStartPremiumPct: 3.5,
  momentumOverextensionPenaltyFullPremiumPct: 7,
  momentumOverextensionPenaltyPoints: 30,
  previewScore: 55,
  candidateScore: 72,
  revalidateScore: 60,
  rearmScore: 45,
  candidateConfirmSeconds: 2,
  candidateConfirmTicks: 2,
  candidateTtlSeconds: 30,
  rearmSeconds: 15,
};

type PolicyField = keyof SignalPolicyInput;

const policyFields = Object.keys(defaultSignalPolicyInput) as PolicyField[];
const numericPolicyFields = policyFields.filter(
  field => typeof defaultSignalPolicyInput[field] === 'number'
);

const integerPolicyFields: readonly PolicyField[] = [
  'maxSamples',
  'maxQuoteAgeMs',
  'pullbackMinSamples',
  'pullbackMinCoverageSeconds',
  'momentumMinSamples',
  'momentumMinCoverageSeconds',
  'sparseDegradedGapSeconds',
  'closeProtectionSeconds',
  'pullbackLookbackSeconds',
  'pullbackStabilizationSeconds',
  'pullbackMaxSpreadTicks',
  'pullbackVolumeShortWindowSeconds',
  'pullbackVolumeBaselineWindowSeconds',
  'momentumWindowSeconds',
  'momentumMinMoveSeconds',
  'momentumBaselineSeconds',
  'momentumHighToleranceTicks',
  'momentumMaxSpreadTicks',
  'candidateConfirmSeconds',
  'candidateConfirmTicks',
  'candidateTtlSeconds',
  'rearmSeconds',
];

const policyValue = (
  form: SignalPolicyForm,
  field: PolicyField
): SignalPolicyInput[PolicyField] => {
  const value = form[field];
  const expected = defaultSignalPolicyInput[field];
  if (typeof expected === 'number') return Number(value);
  if (Array.isArray(expected)) return [...(value as string[])];
  return value;
};

export function signalPolicyInput(form: SignalPolicyForm): SignalPolicyInput {
  return Object.fromEntries(
    policyFields.map(field => [field, policyValue(form, field)])
  ) as SignalPolicyInput;
}

export function signalPolicyForm(policy: SignalPolicyLike): SignalPolicyForm {
  return Object.fromEntries(
    policyFields.map(field => {
      const value = policy[field];
      if (typeof value === 'boolean') return [field, value];
      if (Array.isArray(value)) return [field, [...value]];
      return [field, String(value)];
    })
  ) as SignalPolicyForm;
}

export const defaultSignalPolicyForm = signalPolicyForm(
  defaultSignalPolicyInput
);

const below = (
  errors: string[],
  lower: number,
  upper: number,
  message: string
) => {
  if (!(lower < upper)) errors.push(message);
};

const parseTimeSeconds = (value: string) => {
  const match = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value.trim());
  if (!match) return null;
  const [, hoursText, minutesText, secondsText] = match;
  const hours = Number(hoursText);
  const minutes = Number(minutesText);
  const seconds = Number(secondsText ?? 0);
  if (hours > 23 || minutes > 59 || seconds > 59) return null;
  return hours * 3600 + minutes * 60 + seconds;
};

export function localSignalPolicyErrors(form: SignalPolicyForm) {
  const policy = signalPolicyInput(form);
  const errors: string[] = [];
  const invalidNumbers = numericPolicyFields.filter(field => {
    const raw = form[field];
    return (
      typeof raw !== 'string' ||
      raw.trim() === '' ||
      !Number.isFinite(policy[field] as number)
    );
  });
  if (invalidNumbers.length > 0) {
    return [`以下参数必须是有效数字：${invalidNumbers.join('、')}`];
  }

  const nonInteger = integerPolicyFields.filter(
    field => !Number.isInteger(policy[field] as number)
  );
  if (nonInteger.length > 0) {
    errors.push(`以下参数必须是整数：${nonInteger.join('、')}`);
  }

  const requiredPositive = [
    'maxSamples',
    'maxQuoteAgeMs',
    'pullbackMinSamples',
    'pullbackMinCoverageSeconds',
    'momentumMinSamples',
    'momentumMinCoverageSeconds',
    'pullbackLookbackSeconds',
    'pullbackStabilizationSeconds',
    'pullbackVolumeShortWindowSeconds',
    'pullbackVolumeBaselineWindowSeconds',
    'momentumWindowSeconds',
    'momentumMinMoveSeconds',
    'momentumBaselineSeconds',
    'candidateConfirmSeconds',
    'candidateConfirmTicks',
    'candidateTtlSeconds',
    'rearmSeconds',
    'pullbackThresholdPct',
    'pullbackReboundThresholdPct',
    'momentumMinRisePct',
    'momentumMinAmountVelocityRatio',
    'momentumMaxVwapPremiumPct',
    'momentumMaxSpreadPct',
    'pullbackDepthScoreTargetMultiplier',
    'momentumRiseScoreTargetMultiplier',
    'momentumTurnoverScoreTargetMultiplier',
    'momentumSlopeScoreTargetMultiplier',
    'profilePullbackThresholdMinMultiplier',
    'profilePullbackThresholdMaxMultiplier',
    'profileMomentumRiseMinMultiplier',
    'profileMomentumRiseMaxMultiplier',
    'profileMomentumVelocityMinRatio',
    'profileMomentumVelocityMaxRatio',
  ] as const satisfies readonly PolicyField[];
  const invalidPositive = requiredPositive.filter(field => policy[field] <= 0);
  if (invalidPositive.length > 0) {
    errors.push(`以下参数必须大于 0：${invalidPositive.join('、')}`);
  }

  const requiredNonNegative = [
    'sparseDegradedGapSeconds',
    'closeProtectionSeconds',
    'pullbackMaxSpreadTicks',
    'momentumHighToleranceTicks',
    'momentumMaxSpreadTicks',
    'momentumMinVwapPremiumPct',
    'pullbackDataQualityPenaltyPoints',
    'pullbackChasePenaltyPoints',
    'momentumDataQualityPenaltyPoints',
    'momentumOverextensionPenaltyPoints',
  ] as const satisfies readonly PolicyField[];
  const invalidNonNegative = requiredNonNegative.filter(
    field => policy[field] < 0
  );
  if (invalidNonNegative.length > 0) {
    errors.push(`以下参数不能小于 0：${invalidNonNegative.join('、')}`);
  }

  if (!(
    0 <= policy.rearmScore &&
    policy.rearmScore < policy.previewScore &&
    policy.previewScore < policy.revalidateScore &&
    policy.revalidateScore < policy.candidateScore &&
    policy.candidateScore <= 100
  )) {
    errors.push(
      '分数必须满足：0 ≤ 再武装 < 重点观察 < 确认重验 < 候选锁存 ≤ 100'
    );
  }
  if (policy.pullbackStabilizationSeconds >= policy.pullbackLookbackSeconds) {
    errors.push('低点稳定时长必须小于回撤回看窗口');
  }
  if (
    policy.pullbackVolumeShortWindowSeconds >
    policy.pullbackVolumeBaselineWindowSeconds
  ) {
    errors.push('回撤量能短窗不能长于量能基线窗口');
  }
  if (policy.momentumWindowSeconds > policy.momentumBaselineSeconds) {
    errors.push('动量短窗不能长于成交基线窗口');
  }
  if (policy.momentumMinMoveSeconds > policy.momentumWindowSeconds) {
    errors.push('动量最短持续时间不能长于动量窗口');
  }
  if (
    policy.momentumBaselineCoverageRatio < 0 ||
    policy.momentumBaselineCoverageRatio > 1
  ) {
    errors.push('动量基线覆盖率必须在 [0, 1]');
  }
  for (const [label, multiplier] of [
    ['回撤形成倍率', policy.pullbackFormationThresholdMultiplier],
    ['动量形成倍率', policy.momentumFormationThresholdMultiplier],
  ] as const) {
    if (!(multiplier > 0 && multiplier <= 1)) {
      errors.push(`${label}必须在 (0, 1]`);
    }
  }
  if (policy.momentumMaxVwapPremiumPct <= policy.momentumMinVwapPremiumPct) {
    errors.push('动量 VWAP 上限必须高于下限');
  }
  if (
    policy.maxSamples <
    Math.max(policy.pullbackMinSamples, policy.momentumMinSamples)
  ) {
    errors.push('窗口样本上限必须覆盖两条路径的最少样本数');
  }
  if (policy.pullbackMinCoverageSeconds < policy.pullbackStabilizationSeconds) {
    errors.push('回撤 READY 覆盖必须覆盖低点稳定时长');
  }
  if (
    policy.momentumMinCoverageSeconds <
    Math.max(
      policy.momentumMinMoveSeconds,
      policy.momentumBaselineSeconds * policy.momentumBaselineCoverageRatio
    )
  ) {
    errors.push('动量 READY 覆盖必须覆盖持续时长与成交基线要求');
  }

  const supportedFields = new Set<string>(supportedRequiredFields);
  for (const [label, fields] of [
    ['回撤必需字段', policy.pullbackRequiredFields],
    ['动量必需字段', policy.momentumRequiredFields],
  ] as const) {
    if (
      fields.length === 0 ||
      new Set(fields).size !== fields.length ||
      fields.some(field => !supportedFields.has(field))
    ) {
      errors.push(`${label}必须非空、无重复且只包含受支持字段`);
    }
  }
  const supportedSessions = new Set<string>(supportedSessionCodes);
  if (
    policy.allowedSessionCodes.length === 0 ||
    new Set(policy.allowedSessionCodes).size !==
      policy.allowedSessionCodes.length ||
    policy.allowedSessionCodes.some(code => !supportedSessions.has(code))
  ) {
    errors.push('允许时段必须非空、无重复且只包含连续竞价时段');
  }

  const amStart = parseTimeSeconds(policy.continuousAmStartTime);
  const amEnd = parseTimeSeconds(policy.continuousAmEndTime);
  const pmStart = parseTimeSeconds(policy.continuousPmStartTime);
  const pmEnd = parseTimeSeconds(policy.continuousPmEndTime);
  if ([amStart, amEnd, pmStart, pmEnd].some(value => value == null)) {
    errors.push('交易时段必须使用 HH:mm 或 HH:mm:ss 24 小时格式');
  } else if (
    amStart != null &&
    amEnd != null &&
    pmStart != null &&
    pmEnd != null
  ) {
    if (!(amStart < amEnd && amEnd <= pmStart && pmStart < pmEnd)) {
      errors.push('上午、下午连续竞价窗口必须有序且不能重叠');
    }
    const lifecycle = Math.max(
      policy.candidateTtlSeconds + policy.candidateConfirmSeconds,
      policy.rearmSeconds
    );
    const windows: Record<string, [number, number]> = {
      CONTINUOUS_AM: [amStart, amEnd],
      CONTINUOUS_PM: [pmStart, pmEnd],
    };
    if (
      policy.allowedSessionCodes.some(code => {
        const window = windows[code];
        if (!window) return true;
        return (
          window[1] - window[0] - policy.closeProtectionSeconds < lifecycle
        );
      })
    ) {
      errors.push('启用的交易窗口无法容纳确认、TTL、再武装与收盘保护');
    }
  }

  const pullbackWeight =
    policy.pullbackDepthWeight +
    policy.pullbackReboundWeight +
    policy.pullbackStabilizationWeight +
    policy.pullbackTurnSlopeWeight +
    policy.pullbackVwapWeight +
    policy.pullbackLiquidityWeight +
    policy.pullbackVolumeWeight;
  const momentumWeight =
    policy.momentumRiseWeight +
    policy.momentumTurnoverWeight +
    policy.momentumSlopeWeight +
    policy.momentumPersistenceWeight +
    policy.momentumVwapWeight +
    policy.momentumLiquidityWeight +
    policy.momentumBookImbalanceWeight;
  if (Math.abs(pullbackWeight - 100) > 1e-8) {
    errors.push(`回撤正向权重合计必须为 100（当前 ${pullbackWeight}）`);
  }
  if (Math.abs(momentumWeight - 100) > 1e-8) {
    errors.push(`动量正向权重合计必须为 100（当前 ${momentumWeight}）`);
  }
  const weights = [
    policy.pullbackDepthWeight,
    policy.pullbackReboundWeight,
    policy.pullbackStabilizationWeight,
    policy.pullbackTurnSlopeWeight,
    policy.pullbackVwapWeight,
    policy.pullbackLiquidityWeight,
    policy.pullbackVolumeWeight,
    policy.momentumRiseWeight,
    policy.momentumTurnoverWeight,
    policy.momentumSlopeWeight,
    policy.momentumPersistenceWeight,
    policy.momentumVwapWeight,
    policy.momentumLiquidityWeight,
    policy.momentumBookImbalanceWeight,
  ];
  if (weights.some(weight => weight < 0)) {
    errors.push('正向贡献权重不能小于 0');
  }

  for (const [lower, upper, message] of [
    [
      policy.profilePullbackThresholdMinMultiplier,
      policy.profilePullbackThresholdMaxMultiplier,
      '画像回撤夹取下界必须小于上界',
    ],
    [
      policy.profileMomentumRiseMinMultiplier,
      policy.profileMomentumRiseMaxMultiplier,
      '画像动量涨幅夹取下界必须小于上界',
    ],
    [
      policy.profileMomentumVelocityMinRatio,
      policy.profileMomentumVelocityMaxRatio,
      '画像成交速度夹取下界必须小于上界',
    ],
    [
      policy.pullbackReboundScoreMinPct,
      policy.pullbackReboundScoreMaxPct,
      '回撤反弹归一化下界必须小于上界',
    ],
    [
      policy.pullbackStabilizationScoreMinSeconds,
      policy.pullbackStabilizationScoreMaxSeconds,
      '稳定时长归一化下界必须小于上界',
    ],
    [
      policy.pullbackTurnSlopeScoreMinPctPerSecond,
      policy.pullbackTurnSlopeScoreMaxPctPerSecond,
      '转折斜率归一化下界必须小于上界',
    ],
    [
      policy.pullbackVwapFullScoreMaxPremiumPct,
      policy.pullbackVwapZeroScorePremiumPct,
      '回撤 VWAP 满分边界必须小于零分边界',
    ],
    [
      policy.pullbackLiquidityFullScoreSpreadTicks,
      policy.pullbackLiquidityZeroScoreSpreadTicks,
      '回撤流动性满分边界必须小于零分边界',
    ],
    [
      policy.pullbackVolumeScoreMinRatio,
      policy.pullbackVolumeScoreMaxRatio,
      '回撤量能归一化下界必须小于上界',
    ],
    [
      policy.momentumPersistenceScoreMinRatio,
      policy.momentumPersistenceScoreMaxRatio,
      '动量持续性归一化下界必须小于上界',
    ],
    [
      policy.momentumVwapZeroScoreMinPremiumPct,
      policy.momentumMinVwapPremiumPct,
      '动量 VWAP 低位零分边界必须小于甜蜜区下界',
    ],
    [
      policy.momentumMaxVwapPremiumPct,
      policy.momentumVwapZeroScoreMaxPremiumPct,
      '动量 VWAP 甜蜜区上界必须小于高位零分边界',
    ],
    [
      policy.momentumLiquidityFullScoreSpreadTicks,
      policy.momentumLiquidityZeroScoreSpreadTicks,
      '动量流动性满分边界必须小于零分边界',
    ],
    [
      policy.momentumBookImbalanceScoreMinRatio,
      policy.momentumBookImbalanceScoreMaxRatio,
      '盘口失衡归一化下界必须小于上界',
    ],
    [
      policy.pullbackChasePenaltyStartPremiumPct,
      policy.pullbackChasePenaltyFullPremiumPct,
      '回撤追涨惩罚起点必须小于满额边界',
    ],
    [
      policy.momentumOverextensionPenaltyStartPremiumPct,
      policy.momentumOverextensionPenaltyFullPremiumPct,
      '动量过度延伸惩罚起点必须小于满额边界',
    ],
  ] as const) {
    below(errors, lower, upper, message);
  }
  if (
    Math.abs(
      policy.momentumOverextensionPenaltyStartPremiumPct -
        policy.momentumMaxVwapPremiumPct
    ) > 1e-8
  ) {
    errors.push('动量过度延伸惩罚起点必须等于 VWAP 追涨上限');
  }

  const derivedTargets = [
    [
      policy.pullbackDepthScoreMinPct,
      policy.pullbackThresholdPct *
        policy.profilePullbackThresholdMinMultiplier *
        policy.pullbackDepthScoreTargetMultiplier,
      '回撤深度归一化下界必须低于解析后的目标',
    ],
    [
      policy.momentumRiseScoreMinPct,
      policy.momentumMinRisePct *
        policy.profileMomentumRiseMinMultiplier *
        policy.momentumRiseScoreTargetMultiplier,
      '动量涨幅归一化下界必须低于解析后的目标',
    ],
    [
      policy.momentumTurnoverScoreMinRatio,
      policy.profileMomentumVelocityMinRatio *
        policy.momentumTurnoverScoreTargetMultiplier,
      '动量成交速度归一化下界必须低于解析后的目标',
    ],
    [
      policy.momentumSlopeScoreMinPctPerSecond,
      (policy.momentumMinRisePct *
        policy.profileMomentumRiseMinMultiplier *
        policy.momentumSlopeScoreTargetMultiplier) /
        policy.momentumWindowSeconds,
      '动量斜率归一化下界必须低于解析后的目标',
    ],
  ] as const;
  for (const [lower, upper, message] of derivedTargets) {
    below(errors, lower, upper, message);
  }
  return errors;
}
