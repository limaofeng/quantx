export type StockScreenUniverse = 'STOCK' | 'ETF' | 'STOCK_AND_ETF';
export type ScreeningMode = 'INDICATOR' | 'PROBABILITY' | 'INTRADAY';
export type CandidateLevel = 'A' | 'B';
export type RoeQualityStatus =
  'VALID' | 'STALE' | 'SUSPICIOUS' | 'INVALID' | 'UNVERIFIED';

export type IndicatorOperator = 'eq' | 'gte' | 'lte' | 'gt' | 'lt' | 'between';
export interface IndicatorCondition {
  indicatorId: string;
  operator: IndicatorOperator;
  value: number | null;
  valueTo?: number | null;
}

export interface IndicatorDefinition {
  id: string;
  label: string;
  category: string;
  description: string;
  unit: string;
  lookback: number;
  kind: string;
  operators: string[];
  researchSupported: boolean;
  unsupportedReason?: string | null;
  version: string;
}

export interface ScreeningCriteria {
  // --- Universe ---
  screeningMode?: ScreeningMode;
  universe?: StockScreenUniverse;
  excludeST?: boolean;
  includeIndustries?: string[];
  excludeIndustries?: string[];

  indicatorConditions?: IndicatorCondition[];
  probabilityMinimum?: number;
  probabilityLevels?: CandidateLevel[];
  probabilityModelVersion?: string | null;
  probabilitySearch?: string;
  intradayVolumePaceMin?: number;
  intradayAmountPaceMin?: number;
  intradayLast5mVolumeRatioMin?: number;
  intradayTurnoverRateMin?: number;
  intradayDepthImbalanceMin?: number;
  requireFresh?: boolean;
}

export interface StockScreeningResult {
  code: string;
  name: string;
  industry?: string;
  instrumentType: string;

  // Market Data
  currentPrice: number | null;
  openPrice: number | null;
  changePct: number | null;
  volume: number | null;
  volumeRatio: number | null;
  avgVolume20: number | null;
  avgVolume5?: number | null;
  volumeRatio5?: number | null;
  avgAmount20?: number | null;
  amountRatio20?: number | null;
  turnoverRatePct?: number | null;
  volumePercentile60?: number | null;
  amountPercentile60?: number | null;
  isBullish: boolean | null;
  amount?: number;

  // Intraday volume scan
  volumePaceRatio?: number;
  amountPaceRatio?: number;
  last5mVolumeRatio?: number;
  intradayTurnoverRatePct?: number | null;
  depthImbalance5?: number;
  avgTradeAmountProxy?: number | null;
  updatedAt?: string | null;
  isStale?: boolean;

  // Fundamentals
  roe?: number;
  roeQualityStatus?: RoeQualityStatus;
  netProfitGrowth?: number;
  yoyGrowth?: number;
  netProfitAccumGrowth?: number;
  revenueAccumGrowth?: number;
  financialReportDate?: string | null;
  financialAnnounceDate?: string | null;
  financialAsOfDate?: string | null;
  financialVerifiedAt?: string | null;
  financialQualityFlags?: string[];

  // Peak/Trough Stats
  peakPrice: number | null;
  daysSincePeak: number | null;
  priceDropPct: number | null;
  lowPrice: number | null;
  daysSinceLow: number | null;
  priceRisePct: number | null;

  // Consecutive Stats
  consecutiveDownDays: number | null;
  consecutiveDownPct: number | null;

  // Technical Indicators
  k: number | null;
  d: number | null;
  j: number | null;

  rsi6: number | null;
  rsi12: number | null;
  rsi24: number | null;

  upperBand: number | null;
  middleBand: number | null;
  lowerBand: number | null;

  ma5: number | null;
  ma10: number | null;
  ma20: number | null;
  ma5Prev?: number;
  ma10Prev?: number;

  intradaySignals?: string[];
  calculationVersion?: string;
  indicatorValues?: Array<{ indicatorId: string; value?: number | null }>;
  calculatedAt?: string | null;
  hasStaleData?: boolean;

  // Next-day probability research candidate (never a trade signal)
  calibratedProbability?: number;
  rawScore?: number;
  logisticProbability?: number;
  lightgbmProbability?: number;
  probabilityRank?: number;
  confidence?: number;
  candidateLevel?: CandidateLevel;
  probabilityModelVersion?: string;
  probabilityRunKey?: string;
  probabilityFactorSetHash?: string;
  probabilityRuleVersion?: string;
  probabilityFactorSnapshotSha256?: string | null;
  probabilityStage?:
    'CANDIDATE' | 'SHADOW' | 'ACTIVE' | 'SUSPENDED' | 'RETIRED';
  isShadowCandidate?: boolean;
  probabilityAsOf?: string;
  probabilityTargetDate?: string;
  factorCompleteness?: number;
  oodFit?: number;
  probabilityReasons?: string[];
  probabilityRisks?: string[];
  calibrationBucketSamples?: number;
  calibrationBucketRealizedRate?: number | null;
}

export interface StockScreeningMeta {
  total: number;
  loadedCount?: number;
  snapshotDate?: string | null;
  expectedSnapshotDate?: string | null;
  missingSnapshotDates: string[];
  latestRunStatus?: string | null;
  calculationVersion?: string;
  calculatedAt?: string | null;
  hasStaleData: boolean;
  isComplete: boolean;
  warnings: string[];
  financialHealth?: StockScreenFinancialHealth | null;
  intradayScannerRunning?: boolean;
  intradayUpdatedAt?: string | null;
  intradayStaleRowCount?: number;
  probabilityShowingShadow?: boolean;
  probabilityModelVersion?: string | null;
  probabilityTargetDate?: string | null;
}

export interface StockScreenFinancialHealth {
  status:
    | 'NEVER_RUN'
    | 'RUNNING'
    | 'SUCCESS'
    | 'PARTIAL_FAILURE'
    | 'FAILED'
    | 'STALE';
  lastSuccessAt?: string | null;
  verifiedCount: number;
  selectableCount: number;
  excludedStaleCount: number;
  excludedSuspiciousCount: number;
  excludedInvalidCount: number;
  excludedUnverifiedCount: number;
}

export interface StockScreenSnapshotStatus {
  latestSnapshotDate?: string | null;
  expectedSnapshotDate: string;
  missingSnapshotDates: string[];
  isComplete: boolean;
  latestRunStatus?: string | null;
  latestCalculatedAt?: string | null;
  warnings: string[];
}

export interface FilterOption {
  value: string;
  label: string;
}

export type StockScreenSortField =
  | (string & {})
  | 'CODE'
  | 'NAME'
  | 'CURRENT_PRICE'
  | 'CHANGE_PCT'
  | 'KDJ_J'
  | 'RSI12'
  | 'VOLUME_RATIO'
  | 'VOLUME_RATIO_5'
  | 'AMOUNT_RATIO_20'
  | 'TURNOVER_RATE'
  | 'VOLUME_PERCENTILE_60'
  | 'AMOUNT_PERCENTILE_60'
  | 'PRICE_DROP_PCT'
  | 'DAYS_SINCE_PEAK'
  | 'ROE'
  | 'NET_PROFIT_GROWTH'
  | 'YOY_GROWTH';

export type StockScreenSortDirection = 'ASC' | 'DESC';

export interface StockScreenSortState {
  field: StockScreenSortField;
  direction: StockScreenSortDirection;
}
