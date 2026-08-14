import type { TTradeTimeExitMode } from '@/generated/gql/graphql';

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
  signalLookbackSeconds: string;
  stabilizationSeconds: string;
  pullbackThresholdPct: string;
  reboundThresholdPct: string;
  maxSpreadTicks: string;
  momentumEnabled: boolean;
  momentumWindowSeconds: string;
  momentumMinRisePct: string;
  momentumMinMoveSeconds: string;
  momentumBaselineSeconds: string;
  momentumMinAmountVelocityRatio: string;
  momentumMinVwapPremiumPct: string;
  momentumMaxVwapPremiumPct: string;
  momentumHighToleranceTicks: string;
  momentumMaxSpreadTicks: string;
  momentumMaxSpreadPct: string;
  approvalTtlSeconds: string;
  maxPriceDeviationPct: string;
  limitUpTouchExitEnabled: boolean;
  limitUpTouchToleranceTicks: string;
  timeExitMode: TTradeTimeExitMode;
  timeExitTime: string;
  maxHoldingTradingDays: string;
  cooldownSeconds: string;
};

export type TTradeStudioMode =
  'MONITOR' | 'SIGNALS' | 'POSITIONS' | 'EVENTS' | 'SETTINGS';

export type SignalPanelMode = 'PENDING' | 'HISTORY';

export type SignalHistoryFilter = 'ALL' | 'EXPIRED' | 'IGNORED' | 'CONFIRMED';
