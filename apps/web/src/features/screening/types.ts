export type StockScreenUniverse = 'STOCK' | 'ETF' | 'STOCK_AND_ETF';
export type ScreeningMode = 'DAILY' | 'INTRADAY';
export type RoeQualityStatus =
  'VALID' | 'STALE' | 'SUSPICIOUS' | 'INVALID' | 'UNVERIFIED';

export interface ScreeningCriteria {
  // --- Universe ---
  screeningMode?: ScreeningMode;
  universe?: StockScreenUniverse;
  excludeST?: boolean;
  includeIndustries?: string[];
  excludeIndustries?: string[];

  // --- Fundamentals (screening.py lines 153-163) ---
  minROE?: number; // default 5
  minNetProfitGrowth?: number; // default 5
  minYoYGrowth?: number; // default 0

  // --- Strategies (Toggles) ---
  enableOversoldRebound?: boolean; // 超跌反弹
  enableStrongTrend?: boolean; // 强势股
  enableKDJGoldenCross?: boolean; // KDJ金叉
  enableVolumeBreakout?: boolean; // 放量上涨
  enableMACrossover?: boolean; // MA金叉
  enableBollingerLowerRebound?: boolean; // 布林下轨反弹
  enableBollingerUpperBreakout?: boolean; // 布林上轨突破
  enableRSIOversold?: boolean; // RSI超卖
  enableRSIStrong?: boolean; // RSI强势

  // --- Technical Parameters ---
  priceDropMin?: number; // 跌幅最小值
  rsiOversoldThreshold?: number; // Default 30
  rsiStrongThreshold?: number; // Default 70
  volumeRatioMin?: number;
  volumeRatioMax?: number;
  volumeRatio5Min?: number;
  amountRatioMin?: number;
  turnoverRateMin?: number;
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
  currentPrice: number;
  openPrice: number;
  changePct: number;
  volume: number;
  volumeRatio: number; // Current / Avg20
  avgVolume20: number;
  avgVolume5?: number;
  volumeRatio5?: number;
  avgAmount20?: number;
  amountRatio20?: number;
  turnoverRatePct?: number | null;
  volumePercentile60?: number;
  amountPercentile60?: number;
  isBullish: boolean; // Close > Open
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
  peakPrice: number;
  daysSincePeak: number;
  priceDropPct: number;
  lowPrice: number;
  daysSinceLow: number;
  priceRisePct: number;

  // Consecutive Stats
  consecutiveDownDays: number;
  consecutiveDownPct: number;

  // Technical Indicators
  k: number;
  d: number;
  j: number;

  rsi6: number;
  rsi12: number;
  rsi24: number;

  upperBand: number;
  middleBand: number;
  lowerBand: number;

  ma5: number;
  ma10: number;
  ma20: number;
  ma5Prev?: number;
  ma10Prev?: number;

  // Signals
  matchedStrategies: string[];
  score: number;
  scoreVersion?: string;
  signalVersion?: string;
  calculatedAt?: string | null;
  hasStaleData?: boolean;
  signalMissing?: boolean;
  missingSignals?: string[];
}

export interface StockScreeningMeta {
  total: number;
  loadedCount?: number;
  snapshotDate?: string | null;
  expectedSnapshotDate?: string | null;
  missingSnapshotDates: string[];
  latestRunStatus?: string | null;
  scoreVersion?: string;
  signalVersion?: string;
  calculatedAt?: string | null;
  hasStaleData: boolean;
  isComplete: boolean;
  warnings: string[];
  financialHealth?: StockScreenFinancialHealth | null;
  intradayScannerRunning?: boolean;
  intradayUpdatedAt?: string | null;
  intradayStaleRowCount?: number;
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
  | 'CODE'
  | 'NAME'
  | 'CURRENT_PRICE'
  | 'CHANGE_PCT'
  | 'SIGNAL_COUNT'
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
