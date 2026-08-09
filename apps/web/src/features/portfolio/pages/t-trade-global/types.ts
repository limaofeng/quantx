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
  hardStopEnabled: boolean;
  hardStopPct: string;
  signalLookbackSeconds: string;
  stabilizationSeconds: string;
  pullbackThresholdPct: string;
  reboundThresholdPct: string;
  maxSpreadTicks: string;
  approvalTtlSeconds: string;
  maxPriceDeviationPct: string;
  timeExitMode: TTradeTimeExitMode;
  timeExitTime: string;
  maxHoldingTradingDays: string;
  cooldownSeconds: string;
};

export type TTradeStudioMode =
  'MONITOR' | 'SIGNALS' | 'POSITIONS' | 'EVENTS' | 'SETTINGS';

export type SignalPanelMode = 'PENDING' | 'HISTORY';

export type SignalHistoryFilter = 'ALL' | 'EXPIRED' | 'IGNORED' | 'CONFIRMED';
