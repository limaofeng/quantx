import { useEffect, useMemo, useState } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';
import {
  GetSectorsDocument,
  type GetSectorsQuery,
  type GetSectorsQueryVariables,
  StockScreenSortDirection as GqlStockScreenSortDirection,
  StockScreenSortField as GqlStockScreenSortField,
  StockScreenUniverse as GqlStockScreenUniverse,
} from '@/generated/gql/graphql';

import {
  type ScreeningCriteria,
  type ScreeningMode,
  type StockScreenSortDirection,
  type StockScreenSortField,
  type StockScreenSortState,
  type StockScreenUniverse,
  type StockScreeningMeta,
  type StockScreeningResult,
} from '../types';

const STOCK_SCREEN_QUERY = gql(`
  query StockScreen($input: StockScreenInput!) {
    stockScreen(input: $input) {
      total
      limit
      offset
      snapshotDate
      scoreVersion
      signalVersion
      calculatedAt
      hasStaleData
      isComplete
      warnings
      items {
        code
        name
        industry
        instrumentType
        currentPrice
        openPrice
        changePct
        volume
        volumeRatio
        avgVolume20
        avgVolume5
        volumeRatio5
        avgAmount20
        amountRatio20
        turnoverRatePct
        volumePercentile60
        amountPercentile60
        isBullish
        peakPrice
        daysSincePeak
        priceDropPct
        lowPrice
        daysSinceLow
        priceRisePct
        consecutiveDownDays
        consecutiveDownPct
        k
        d
        j
        rsi6
        rsi12
        rsi24
        upperBand
        middleBand
        lowerBand
        ma5
        ma10
        ma20
        ma5Prev
        ma10Prev
        roe
        netProfitGrowth
        yoyGrowth
        netProfitAccumGrowth
        revenueAccumGrowth
        financialReportDate
        financialAnnounceDate
        financialQualityFlags
        matchedStrategies
        score
        scoreVersion
        signalVersion
        calculatedAt
        hasStaleData
        signalMissing
        missingSignals
      }
    }
  }
`);

const INTRADAY_VOLUME_SCREEN_QUERY = gql(`
  query IntradayVolumeScreen($input: IntradayVolumeScreenInput!) {
    intradayVolumeScreen(input: $input) {
      total
      limit
      offset
      updatedAt
      isScannerRunning
      warnings
      items {
        code
        name
        industry
        instrumentType
        currentPrice
        changePct
        volume
        amount
        volumeRatio
        amountRatio
        volumePaceRatio
        amountPaceRatio
        last5mVolumeRatio
        intradayTurnoverRatePct
        depthImbalance5
        avgTradeAmountProxy
        matchedSignals
        updatedAt
        isStale
      }
    }
  }
`);

function normalizeIndustryName(name: string): string {
  return name.trim().replace(/\s+/g, '').replace(/加权$/, '');
}

const DEFAULT_CRITERIA: ScreeningCriteria = {
  screeningMode: 'DAILY',
  universe: 'STOCK',
  excludeST: true,
  minROE: 5,
  minNetProfitGrowth: 5,
  minYoYGrowth: 0,

  enableOversoldRebound: false,
  enableStrongTrend: false,
  enableKDJGoldenCross: false,
  enableVolumeBreakout: false,
  enableMACrossover: false,
  enableBollingerLowerRebound: false,
  enableBollingerUpperBreakout: false,
  enableRSIOversold: false,
  enableRSIStrong: false,

  rsiPeriod: 12,
  rsiOversoldThreshold: 30,
  rsiStrongThreshold: 70,
  maShort: 5,
  maLong: 10,
  bollingerUpperProximity: 0.98,
  bollingerLowerProximity: 1.02,
  requireFresh: false,
};

const FALLBACK_INDUSTRIES = [
  '银行',
  '房地产',
  '医药生物',
  '食品饮料',
  '电子',
  '计算机',
  '新能源',
  '国防军工',
  '非银金融',
  '有色金属',
];

const SIGNAL_BY_FLAG: Array<[keyof ScreeningCriteria, string, number]> = [
  ['enableOversoldRebound', '超跌反弹', 2],
  ['enableStrongTrend', '强势股', 2],
  ['enableKDJGoldenCross', 'KDJ 金叉', 1.5],
  ['enableVolumeBreakout', '放量突破', 1],
  ['enableMACrossover', '均线金叉', 1.5],
  ['enableBollingerLowerRebound', '布林下轨反弹', 1],
  ['enableBollingerUpperBreakout', '布林上轨突破', 1],
  ['enableRSIOversold', 'RSI 超卖', 1],
  ['enableRSIStrong', 'RSI 强势', 1],
];

const SORT_FIELD_INPUT: Record<StockScreenSortField, GqlStockScreenSortField> =
  {
    AMOUNT_PERCENTILE_60: GqlStockScreenSortField.AmountPercentile_60,
    AMOUNT_RATIO_20: GqlStockScreenSortField.AmountRatio_20,
    CHANGE_PCT: GqlStockScreenSortField.ChangePct,
    CODE: GqlStockScreenSortField.Code,
    CURRENT_PRICE: GqlStockScreenSortField.CurrentPrice,
    DAYS_SINCE_PEAK: GqlStockScreenSortField.DaysSincePeak,
    KDJ_J: GqlStockScreenSortField.KdjJ,
    NAME: GqlStockScreenSortField.Name,
    NET_PROFIT_GROWTH: GqlStockScreenSortField.NetProfitGrowth,
    PRICE_DROP_PCT: GqlStockScreenSortField.PriceDropPct,
    ROE: GqlStockScreenSortField.Roe,
    RSI12: GqlStockScreenSortField.Rsi12,
    SIGNAL_COUNT: GqlStockScreenSortField.SignalCount,
    TURNOVER_RATE: GqlStockScreenSortField.TurnoverRate,
    VOLUME_PERCENTILE_60: GqlStockScreenSortField.VolumePercentile_60,
    VOLUME_RATIO: GqlStockScreenSortField.VolumeRatio,
    VOLUME_RATIO_5: GqlStockScreenSortField.VolumeRatio_5,
    YOY_GROWTH: GqlStockScreenSortField.YoyGrowth,
  };

const SORT_DIRECTION_INPUT: Record<
  StockScreenSortDirection,
  GqlStockScreenSortDirection
> = {
  ASC: GqlStockScreenSortDirection.Asc,
  DESC: GqlStockScreenSortDirection.Desc,
};

const UNIVERSE_INPUT: Record<StockScreenUniverse, GqlStockScreenUniverse> = {
  ETF: GqlStockScreenUniverse.Etf,
  STOCK: GqlStockScreenUniverse.Stock,
  STOCK_AND_ETF: GqlStockScreenUniverse.StockAndEtf,
};

interface IntradayVolumeQueryItem {
  amount: number;
  amountPaceRatio: number;
  amountRatio: number;
  avgTradeAmountProxy?: number | null;
  changePct: number;
  code: string;
  currentPrice: number;
  depthImbalance5: number;
  industry?: string | null;
  instrumentType: string;
  intradayTurnoverRatePct?: number | null;
  isStale: boolean;
  last5mVolumeRatio: number;
  matchedSignals: string[];
  name: string;
  updatedAt?: string | null;
  volume: number;
  volumePaceRatio: number;
  volumeRatio: number;
}

function activePositiveThreshold(value?: number) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0
    ? value
    : null;
}

function buildStockScreenInput(
  criteria: ScreeningCriteria,
  sort: StockScreenSortState | null
) {
  const universe = criteria.universe ?? 'STOCK';
  const supportsStockOnlyFilters = universe === 'STOCK';
  const activeFundamentalThreshold = activePositiveThreshold;
  const signalConditions = SIGNAL_BY_FLAG.filter(
    ([flag]) => criteria[flag]
  ).map(([, signalCode]) => ({
    signalCode,
    required: true,
  }));
  const scoreRules = SIGNAL_BY_FLAG.map(([, signalCode, weight]) => ({
    signalCode,
    weight,
  }));
  const fieldConditions = [];

  if (criteria.priceDropMin && criteria.priceDropMin > 0) {
    fieldConditions.push({
      field: 'price_drop_pct',
      operator: 'lte',
      value: -Math.abs(criteria.priceDropMin),
    });
  }
  if (criteria.volumeRatioMin && criteria.volumeRatioMin > 0) {
    fieldConditions.push({
      field: 'volume_ratio',
      operator: 'gte',
      value: criteria.volumeRatioMin,
    });
  }
  if (criteria.volumeRatioMax && criteria.volumeRatioMax > 0) {
    fieldConditions.push({
      field: 'volume_ratio',
      operator: 'lte',
      value: criteria.volumeRatioMax,
    });
  }
  if (criteria.volumeRatio5Min && criteria.volumeRatio5Min > 0) {
    fieldConditions.push({
      field: 'volume_ratio_5',
      operator: 'gte',
      value: criteria.volumeRatio5Min,
    });
  }
  if (criteria.amountRatioMin && criteria.amountRatioMin > 0) {
    fieldConditions.push({
      field: 'amount_ratio_20',
      operator: 'gte',
      value: criteria.amountRatioMin,
    });
  }
  if (criteria.turnoverRateMin && criteria.turnoverRateMin > 0) {
    fieldConditions.push({
      field: 'turnover_rate_pct',
      operator: 'gte',
      value: criteria.turnoverRateMin,
    });
  }
  if (criteria.rsiOversoldThreshold && criteria.enableRSIOversold) {
    fieldConditions.push({
      field: 'rsi12',
      operator: 'lte',
      value: criteria.rsiOversoldThreshold,
    });
  }
  if (criteria.rsiStrongThreshold && criteria.enableRSIStrong) {
    fieldConditions.push({
      field: 'rsi12',
      operator: 'gte',
      value: criteria.rsiStrongThreshold,
    });
  }

  return {
    includeIndustries:
      supportsStockOnlyFilters && criteria.includeIndustries?.length
        ? criteria.includeIndustries
        : null,
    excludeIndustries:
      supportsStockOnlyFilters && criteria.excludeIndustries?.length
        ? criteria.excludeIndustries
        : null,
    signalConditions,
    scoreRules,
    fieldConditions,
    universe: UNIVERSE_INPUT[universe],
    excludeSt: criteria.excludeST !== false,
    requireFresh: Boolean(criteria.requireFresh),
    sort: sort
      ? {
          field: SORT_FIELD_INPUT[sort.field],
          direction: SORT_DIRECTION_INPUT[sort.direction],
        }
      : null,
    limit: 200,
    offset: 0,
    minRoe: supportsStockOnlyFilters
      ? activeFundamentalThreshold(criteria.minROE)
      : null,
    minNetProfitGrowth: supportsStockOnlyFilters
      ? activeFundamentalThreshold(criteria.minNetProfitGrowth)
      : null,
    minYoyGrowth: supportsStockOnlyFilters
      ? activeFundamentalThreshold(criteria.minYoYGrowth)
      : null,
  };
}

function buildIntradayVolumeScreenInput(criteria: ScreeningCriteria) {
  const universe = criteria.universe ?? 'STOCK';
  const supportsStockOnlyFilters = universe === 'STOCK';

  return {
    universe: UNIVERSE_INPUT[universe],
    includeIndustries:
      supportsStockOnlyFilters && criteria.includeIndustries?.length
        ? criteria.includeIndustries
        : null,
    excludeIndustries:
      supportsStockOnlyFilters && criteria.excludeIndustries?.length
        ? criteria.excludeIndustries
        : null,
    excludeSt: criteria.excludeST !== false,
    minVolumePaceRatio: activePositiveThreshold(criteria.intradayVolumePaceMin),
    minAmountPaceRatio: activePositiveThreshold(criteria.intradayAmountPaceMin),
    minLast5mVolumeRatio: activePositiveThreshold(
      criteria.intradayLast5mVolumeRatioMin
    ),
    minIntradayTurnoverRate: activePositiveThreshold(
      criteria.intradayTurnoverRateMin
    ),
    minDepthImbalance5: activePositiveThreshold(
      criteria.intradayDepthImbalanceMin
    ),
    staleAfterSeconds: 15,
    limit: 200,
    offset: 0,
  };
}

function getSortValue(
  stock: StockScreeningResult,
  field: StockScreenSortField
) {
  switch (field) {
    case 'AMOUNT_PERCENTILE_60':
      return stock.amountPercentile60 ?? 0;
    case 'AMOUNT_RATIO_20':
      return stock.amountRatio20 ?? 0;
    case 'CHANGE_PCT':
      return stock.changePct;
    case 'CODE':
      return stock.code;
    case 'CURRENT_PRICE':
      return stock.currentPrice;
    case 'DAYS_SINCE_PEAK':
      return stock.daysSincePeak;
    case 'KDJ_J':
      return stock.j;
    case 'NAME':
      return stock.name;
    case 'NET_PROFIT_GROWTH':
      return stock.netProfitGrowth ?? 0;
    case 'PRICE_DROP_PCT':
      return stock.priceDropPct;
    case 'ROE':
      return stock.roe ?? 0;
    case 'RSI12':
      return stock.rsi12;
    case 'SIGNAL_COUNT':
      return stock.matchedStrategies.length;
    case 'TURNOVER_RATE':
      return stock.turnoverRatePct ?? stock.intradayTurnoverRatePct ?? 0;
    case 'VOLUME_PERCENTILE_60':
      return stock.volumePercentile60 ?? 0;
    case 'VOLUME_RATIO':
      return stock.volumePaceRatio ?? stock.volumeRatio;
    case 'VOLUME_RATIO_5':
      return stock.volumeRatio5 ?? stock.last5mVolumeRatio ?? 0;
    case 'YOY_GROWTH':
      return stock.yoyGrowth ?? 0;
    default:
      return 0;
  }
}

function sortResultsLocally(
  results: StockScreeningResult[],
  sort: StockScreenSortState | null
) {
  if (!sort) return results;
  const direction = sort.direction === 'ASC' ? 1 : -1;

  return [...results].sort((left, right) => {
    const leftValue = getSortValue(left, sort.field);
    const rightValue = getSortValue(right, sort.field);
    if (typeof leftValue === 'string' || typeof rightValue === 'string') {
      return (
        String(leftValue).localeCompare(String(rightValue), 'zh-CN') * direction
      );
    }
    return ((leftValue as number) - (rightValue as number)) * direction;
  });
}

function mapIntradayItemToResult(
  item: IntradayVolumeQueryItem
): StockScreeningResult {
  return {
    amount: item.amount,
    amountPaceRatio: item.amountPaceRatio,
    amountRatio20: item.amountRatio,
    avgAmount20: item.amountRatio > 0 ? item.amount / item.amountRatio : 0,
    avgTradeAmountProxy: item.avgTradeAmountProxy ?? null,
    avgVolume20: item.volumeRatio > 0 ? item.volume / item.volumeRatio : 0,
    changePct: item.changePct,
    code: item.code,
    consecutiveDownDays: 0,
    consecutiveDownPct: 0,
    currentPrice: item.currentPrice,
    d: 0,
    daysSinceLow: 0,
    daysSincePeak: 0,
    depthImbalance5: item.depthImbalance5,
    hasStaleData: item.isStale,
    industry: item.industry ?? undefined,
    instrumentType: item.instrumentType || 'stock',
    intradayTurnoverRatePct: item.intradayTurnoverRatePct ?? null,
    isBullish: item.changePct >= 0,
    isStale: item.isStale,
    j: 0,
    k: 0,
    last5mVolumeRatio: item.last5mVolumeRatio,
    lowPrice: item.currentPrice,
    lowerBand: item.currentPrice,
    ma5: item.currentPrice,
    ma10: item.currentPrice,
    ma20: item.currentPrice,
    matchedStrategies: item.matchedSignals,
    middleBand: item.currentPrice,
    name: item.name,
    openPrice: item.currentPrice,
    peakPrice: item.currentPrice,
    priceDropPct: 0,
    priceRisePct: 0,
    rsi6: 0,
    rsi12: 0,
    rsi24: 0,
    score: item.volumePaceRatio,
    signalMissing: false,
    upperBand: item.currentPrice,
    updatedAt: item.updatedAt ?? null,
    volume: item.volume,
    volumePaceRatio: item.volumePaceRatio,
    volumeRatio: item.volumeRatio,
  };
}

export function useStockScreening() {
  const [screeningCriteria, setScreeningCriteria] =
    useState<ScreeningCriteria>(DEFAULT_CRITERIA);
  const [activeMode, setActiveMode] = useState<ScreeningMode>('DAILY');
  const [sort, setSort] = useState<StockScreenSortState | null>(null);
  const [queryInput, setQueryInput] = useState(() =>
    buildStockScreenInput(DEFAULT_CRITERIA, null)
  );
  const [intradayInput, setIntradayInput] = useState(() =>
    buildIntradayVolumeScreenInput(DEFAULT_CRITERIA)
  );
  const isIntradayMode = activeMode === 'INTRADAY';

  const [stockScreenResult] = useQuery({
    query: STOCK_SCREEN_QUERY,
    variables: { input: queryInput },
    pause: isIntradayMode,
    requestPolicy: 'cache-and-network',
  });

  const [intradayVolumeResult, reexecuteIntradayVolume] = useQuery({
    query: INTRADAY_VOLUME_SCREEN_QUERY,
    variables: { input: intradayInput },
    pause: !isIntradayMode,
    requestPolicy: 'network-only',
  });

  const [gnSectorsResult] = useQuery<GetSectorsQuery, GetSectorsQueryVariables>(
    {
      query: GetSectorsDocument,
      variables: {
        classification: 'SW1',
        search: null,
        limit: 1000,
        offset: 0,
      },
    }
  );

  useEffect(() => {
    if (!isIntradayMode) return;
    const intervalId = window.setInterval(() => {
      reexecuteIntradayVolume({ requestPolicy: 'network-only' });
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, [isIntradayMode, reexecuteIntradayVolume]);

  const availableIndustries = useMemo(() => {
    const sectorNames =
      gnSectorsResult.data?.sectors?.items
        ?.map(item => item?.name?.trim())
        .filter((name): name is string => Boolean(name)) || [];

    if (sectorNames.length === 0) {
      return FALLBACK_INDUSTRIES;
    }

    const deduped = new Map<string, string>();
    for (const rawName of sectorNames) {
      const normalized = normalizeIndustryName(rawName);
      if (!normalized) continue;
      const current = deduped.get(normalized);
      if (!current || current.endsWith('加权')) {
        deduped.set(normalized, normalized);
      }
    }

    return Array.from(deduped.values());
  }, [gnSectorsResult.data?.sectors?.items]);

  const results = useMemo<StockScreeningResult[]>(() => {
    if (isIntradayMode) {
      const items =
        intradayVolumeResult.data?.intradayVolumeScreen?.items ?? [];
      return sortResultsLocally(items.map(mapIntradayItemToResult), sort);
    }

    const items = stockScreenResult.data?.stockScreen?.items ?? [];
    return items.map(item => ({
      ...item,
      industry: item.industry ?? undefined,
      instrumentType: item.instrumentType || 'stock',
      ma5Prev: item.ma5Prev ?? undefined,
      ma10Prev: item.ma10Prev ?? undefined,
      roe: item.roe ?? undefined,
      netProfitGrowth: item.netProfitGrowth ?? undefined,
      yoyGrowth: item.yoyGrowth ?? undefined,
      netProfitAccumGrowth: item.netProfitAccumGrowth ?? undefined,
      revenueAccumGrowth: item.revenueAccumGrowth ?? undefined,
      financialReportDate: item.financialReportDate ?? undefined,
      financialAnnounceDate: item.financialAnnounceDate ?? undefined,
      financialQualityFlags: item.financialQualityFlags ?? [],
      turnoverRatePct: item.turnoverRatePct ?? null,
    }));
  }, [
    intradayVolumeResult.data?.intradayVolumeScreen?.items,
    isIntradayMode,
    sort,
    stockScreenResult.data?.stockScreen?.items,
  ]);

  const meta = useMemo<StockScreeningMeta>(() => {
    if (isIntradayMode) {
      const page = intradayVolumeResult.data?.intradayVolumeScreen;
      return {
        total: page?.total ?? 0,
        snapshotDate: null,
        scoreVersion: 'intraday-volume',
        signalVersion: 'xtquant-whole-quote',
        calculatedAt: page?.updatedAt ?? null,
        hasStaleData: false,
        isComplete: Boolean(page?.isScannerRunning),
        warnings: page?.warnings ?? [],
      };
    }

    const page = stockScreenResult.data?.stockScreen;
    return {
      total: page?.total ?? 0,
      snapshotDate: page?.snapshotDate ?? null,
      scoreVersion: page?.scoreVersion,
      signalVersion: page?.signalVersion,
      calculatedAt: page?.calculatedAt ?? null,
      hasStaleData: Boolean(page?.hasStaleData),
      isComplete: Boolean(page?.isComplete),
      warnings: page?.warnings ?? [],
    };
  }, [
    intradayVolumeResult.data?.intradayVolumeScreen,
    isIntradayMode,
    stockScreenResult.data?.stockScreen,
  ]);

  const runScreening = (criteria: ScreeningCriteria = screeningCriteria) => {
    const nextMode = criteria.screeningMode ?? 'DAILY';
    setScreeningCriteria(criteria);
    setActiveMode(nextMode);
    setQueryInput(buildStockScreenInput(criteria, sort));
    setIntradayInput(buildIntradayVolumeScreenInput(criteria));
  };

  const applySort = (nextSort: StockScreenSortState | null) => {
    setSort(nextSort);
    setQueryInput(buildStockScreenInput(screeningCriteria, nextSort));
  };

  const resetCriteria = () => {
    setScreeningCriteria(DEFAULT_CRITERIA);
    setActiveMode('DAILY');
    setSort(null);
    setQueryInput(buildStockScreenInput(DEFAULT_CRITERIA, null));
    setIntradayInput(buildIntradayVolumeScreenInput(DEFAULT_CRITERIA));
  };

  return {
    screeningCriteria,
    setScreeningCriteria,
    results,
    meta,
    sort,
    applySort,
    error: isIntradayMode
      ? intradayVolumeResult.error
      : stockScreenResult.error,
    isLoading: isIntradayMode
      ? intradayVolumeResult.fetching && !intradayVolumeResult.data
      : stockScreenResult.fetching,
    runScreening,
    resetCriteria,
    availableIndustries,
  };
}
