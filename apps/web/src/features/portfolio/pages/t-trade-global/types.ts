import type { TTradeTimeExitMode } from '@/generated/gql/graphql';

export type SignalPolicyInput = {
  maxSamples: number;
  maxQuoteAgeMs: number;
  pullbackMinSamples: number;
  pullbackMinCoverageSeconds: number;
  momentumMinSamples: number;
  momentumMinCoverageSeconds: number;
  sparseDegradedGapSeconds: number;
  pullbackRequiredFields: string[];
  momentumRequiredFields: string[];
  allowedSessionCodes: string[];
  continuousAmStartTime: string;
  continuousAmEndTime: string;
  continuousPmStartTime: string;
  continuousPmEndTime: string;
  closeProtectionSeconds: number;
  pullbackLookbackSeconds: number;
  pullbackStabilizationSeconds: number;
  pullbackThresholdPct: number;
  pullbackFormationThresholdMultiplier: number;
  pullbackReboundThresholdPct: number;
  pullbackMaxSpreadTicks: number;
  pullbackVolumeShortWindowSeconds: number;
  pullbackVolumeBaselineWindowSeconds: number;
  momentumEnabled: boolean;
  momentumWindowSeconds: number;
  momentumMinRisePct: number;
  momentumFormationThresholdMultiplier: number;
  momentumMinMoveSeconds: number;
  momentumBaselineSeconds: number;
  momentumBaselineCoverageRatio: number;
  momentumMinAmountVelocityRatio: number;
  momentumMinVwapPremiumPct: number;
  momentumMaxVwapPremiumPct: number;
  momentumHighToleranceTicks: number;
  momentumMaxSpreadTicks: number;
  momentumMaxSpreadPct: number;
  profilePullbackThresholdMinMultiplier: number;
  profilePullbackThresholdMaxMultiplier: number;
  profileMomentumRiseMinMultiplier: number;
  profileMomentumRiseMaxMultiplier: number;
  profileMomentumVelocityMinRatio: number;
  profileMomentumVelocityMaxRatio: number;
  pullbackDepthWeight: number;
  pullbackReboundWeight: number;
  pullbackStabilizationWeight: number;
  pullbackTurnSlopeWeight: number;
  pullbackVwapWeight: number;
  pullbackLiquidityWeight: number;
  pullbackVolumeWeight: number;
  momentumRiseWeight: number;
  momentumTurnoverWeight: number;
  momentumSlopeWeight: number;
  momentumPersistenceWeight: number;
  momentumVwapWeight: number;
  momentumLiquidityWeight: number;
  momentumBookImbalanceWeight: number;
  pullbackDepthScoreMinPct: number;
  pullbackDepthScoreTargetMultiplier: number;
  pullbackReboundScoreMinPct: number;
  pullbackReboundScoreMaxPct: number;
  pullbackStabilizationScoreMinSeconds: number;
  pullbackStabilizationScoreMaxSeconds: number;
  pullbackTurnSlopeScoreMinPctPerSecond: number;
  pullbackTurnSlopeScoreMaxPctPerSecond: number;
  pullbackVwapFullScoreMaxPremiumPct: number;
  pullbackVwapZeroScorePremiumPct: number;
  pullbackLiquidityFullScoreSpreadTicks: number;
  pullbackLiquidityZeroScoreSpreadTicks: number;
  pullbackVolumeScoreMinRatio: number;
  pullbackVolumeScoreMaxRatio: number;
  momentumRiseScoreMinPct: number;
  momentumRiseScoreTargetMultiplier: number;
  momentumTurnoverScoreMinRatio: number;
  momentumTurnoverScoreTargetMultiplier: number;
  momentumSlopeScoreMinPctPerSecond: number;
  momentumSlopeScoreTargetMultiplier: number;
  momentumPersistenceScoreMinRatio: number;
  momentumPersistenceScoreMaxRatio: number;
  momentumVwapZeroScoreMinPremiumPct: number;
  momentumVwapZeroScoreMaxPremiumPct: number;
  momentumLiquidityFullScoreSpreadTicks: number;
  momentumLiquidityZeroScoreSpreadTicks: number;
  momentumBookImbalanceScoreMinRatio: number;
  momentumBookImbalanceScoreMaxRatio: number;
  pullbackDataQualityPenaltyPoints: number;
  pullbackChasePenaltyStartPremiumPct: number;
  pullbackChasePenaltyFullPremiumPct: number;
  pullbackChasePenaltyPoints: number;
  momentumDataQualityPenaltyPoints: number;
  momentumOverextensionPenaltyStartPremiumPct: number;
  momentumOverextensionPenaltyFullPremiumPct: number;
  momentumOverextensionPenaltyPoints: number;
  previewScore: number;
  candidateScore: number;
  revalidateScore: number;
  rearmScore: number;
  candidateConfirmSeconds: number;
  candidateConfirmTicks: number;
  candidateTtlSeconds: number;
  rearmSeconds: number;
};

export type SignalPolicyForm = {
  [Key in keyof SignalPolicyInput]: SignalPolicyInput[Key] extends boolean
    ? boolean
    : SignalPolicyInput[Key] extends string[]
      ? string[]
      : string;
};

export type SignalPolicyFormValue = SignalPolicyForm[keyof SignalPolicyForm];

export type SettingsForm = {
  mode: 'paper' | 'live';
  acknowledged: boolean;
  targetTradeAmount: string;
  maxTradeAmount: string;
  maxConcurrentBatches: string;
  maxTotalTExposurePct: string;
  targetProfitPct: string;
  baseFloorPct: string;
  initialGapPct: string;
  trailingGapSlope: string;
  maxGapPct: string;
  highProfitLockEnabled: boolean;
  highProfitArmPct: string;
  highProfitMaxDrawdownPct: string;
  rapidReversalEnabled: boolean;
  rapidReversalWindowSeconds: string;
  rapidReversalDrawdownPct: string;
  rapidReversalConfirmTicks: string;
  hardStopEnabled: boolean;
  hardStopPct: string;
  signalPolicy: SignalPolicyForm;
  maxPriceDeviationPct: string;
  limitUpTouchExitEnabled: boolean;
  limitUpTouchToleranceTicks: string;
  timeExitMode: TTradeTimeExitMode;
  timeExitTime: string;
  maxHoldingTradingDays: string;
  cooldownSeconds: string;
};

export type TTradeStudioMode =
  'MONITOR' | 'SIGNALS' | 'DIAGNOSTICS' | 'POSITIONS' | 'EVENTS' | 'SETTINGS';

export type SignalPanelMode = 'PENDING' | 'HISTORY';

export type SignalHistoryFilter = 'ALL' | 'EXPIRED' | 'IGNORED' | 'CONFIRMED';
