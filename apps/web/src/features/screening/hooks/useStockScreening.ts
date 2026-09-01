import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';
import {
  GetSectorsDocument,
  type GetSectorsQuery,
  type GetSectorsQueryVariables,
  StockScreenSortDirection as GqlStockScreenSortDirection,
  StockScreenUniverse as GqlStockScreenUniverse,
} from '@/generated/gql/graphql';

import { validateFactorConditions } from '../factorModel';
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

import { useStockScreenSnapshotStatus } from './useStockScreenSnapshotStatus';

const STOCK_SCREEN_QUERY = gql(`
  query StockScreen($input: StockScreenInput!) {
    stockScreen(input: $input) {
      total
      limit
      offset
      snapshotDate
      calculationVersion
      calculatedAt
      hasStaleData
      isComplete
      warnings
      financialHealth {
        status
        lastSuccessAt
        verifiedCount
        selectableCount
        excludedStaleCount
        excludedSuspiciousCount
        excludedInvalidCount
        excludedUnverifiedCount
      }
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
        roeQualityStatus
        netProfitGrowth
        yoyGrowth
        netProfitAccumGrowth
        revenueAccumGrowth
        financialReportDate
        financialAnnounceDate
        financialAsOfDate
        financialVerifiedAt
        financialQualityFlags
        calculationVersion
        factorValues { factorId value }
        calculatedAt
        hasStaleData
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

export const DEFAULT_CRITERIA: ScreeningCriteria = {
  screeningMode: 'DAILY',
  universe: 'STOCK',
  excludeST: true,
  factorConditions: [],
  requireFresh: false,
};
export const DEFAULT_SORT: StockScreenSortState = {
  field: 'CHANGE_PCT',
  direction: 'DESC',
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

const SORT_FIELD_INPUT: Record<string, string> = {
  AMOUNT_PERCENTILE_60: 'amount_percentile_60',
  AMOUNT_RATIO_20: 'amount_ratio_20',
  CHANGE_PCT: 'change_pct',
  CODE: 'code',
  CURRENT_PRICE: 'current_price',
  DAYS_SINCE_PEAK: 'days_since_peak',
  KDJ_J: 'kdj_j',
  NAME: 'name',
  NET_PROFIT_GROWTH: 'net_profit_growth_pct',
  PRICE_DROP_PCT: 'price_drop_pct',
  ROE: 'roe_ttm',
  RSI12: 'rsi12',
  TURNOVER_RATE: 'turnover_rate_pct',
  VOLUME_PERCENTILE_60: 'volume_percentile_60',
  VOLUME_RATIO: 'volume_ratio',
  VOLUME_RATIO_5: 'volume_ratio_5',
  YOY_GROWTH: 'revenue_growth_pct',
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

export function buildStockScreenInput(
  criteria: ScreeningCriteria,
  sort: StockScreenSortState | null
) {
  const universe = criteria.universe ?? 'STOCK';
  const effectiveSort = sort ?? DEFAULT_SORT;
  const validationError = validateFactorConditions(
    criteria.factorConditions ?? []
  );
  if (validationError) throw new Error(validationError);
  return {
    includeIndustries:
      universe === 'STOCK' ? (criteria.includeIndustries ?? []) : [],
    excludeIndustries:
      universe === 'STOCK' ? (criteria.excludeIndustries ?? []) : [],
    factorConditions: (criteria.factorConditions ?? []).map(condition => ({
      factorId: condition.factorId,
      operator: condition.operator,
      value: condition.value!,
      valueTo: condition.operator === 'between' ? condition.valueTo : null,
    })),
    universe: UNIVERSE_INPUT[universe],
    excludeSt: criteria.excludeST !== false,
    requireFresh: Boolean(criteria.requireFresh),
    sort: {
      field: SORT_FIELD_INPUT[effectiveSort.field] ?? effectiveSort.field,
      direction: SORT_DIRECTION_INPUT[effectiveSort.direction],
    },
    limit: 200,
    offset: 0,
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
    case 'TURNOVER_RATE':
      return stock.turnoverRatePct ?? stock.intradayTurnoverRatePct ?? 0;
    case 'VOLUME_PERCENTILE_60':
      return stock.volumePercentile60 ?? 0;
    case 'VOLUME_RATIO':
      return stock.volumeRatio;
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
    if (leftValue == null && rightValue == null)
      return left.code.localeCompare(right.code);
    if (leftValue == null) return 1;
    if (rightValue == null) return -1;
    if (typeof leftValue === 'string' || typeof rightValue === 'string') {
      return (
        String(leftValue).localeCompare(String(rightValue), 'zh-CN') *
          direction || left.code.localeCompare(right.code)
      );
    }
    return (
      (leftValue - rightValue) * direction ||
      left.code.localeCompare(right.code)
    );
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
    intradaySignals: item.matchedSignals,
    middleBand: item.currentPrice,
    name: item.name,
    openPrice: item.currentPrice,
    peakPrice: item.currentPrice,
    priceDropPct: 0,
    priceRisePct: 0,
    rsi6: 0,
    rsi12: 0,
    rsi24: 0,
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
  const [sort, setSort] = useState<StockScreenSortState | null>(DEFAULT_SORT);
  const [queryInput, setQueryInput] = useState(() =>
    buildStockScreenInput(DEFAULT_CRITERIA, null)
  );
  const [intradayInput, setIntradayInput] = useState(() =>
    buildIntradayVolumeScreenInput(DEFAULT_CRITERIA)
  );
  const isIntradayMode = activeMode === 'INTRADAY';

  const [stockScreenResult, reexecuteStockScreen] = useQuery({
    query: STOCK_SCREEN_QUERY,
    variables: { input: queryInput },
    pause: isIntradayMode,
    requestPolicy: 'cache-and-network',
  });
  const snapshotStatusResult = useStockScreenSnapshotStatus({
    pause: isIntradayMode,
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

  useEffect(() => {
    if (
      isIntradayMode ||
      snapshotStatusResult.status?.latestRunStatus !== 'running'
    ) {
      return;
    }
    const intervalId = window.setInterval(() => {
      snapshotStatusResult.refresh();
    }, 3000);
    return () => window.clearInterval(intervalId);
  }, [
    isIntradayMode,
    snapshotStatusResult,
    snapshotStatusResult.refresh,
    snapshotStatusResult.status?.latestRunStatus,
  ]);

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
      currentPrice: item.currentPrice ?? null,
      openPrice: item.openPrice ?? null,
      changePct: item.changePct ?? null,
      volume: item.volume ?? null,
      volumeRatio: item.volumeRatio ?? null,
      avgVolume20: item.avgVolume20 ?? null,
      isBullish: item.isBullish ?? null,
      peakPrice: item.peakPrice ?? null,
      daysSincePeak: item.daysSincePeak ?? null,
      priceDropPct: item.priceDropPct ?? null,
      lowPrice: item.lowPrice ?? null,
      daysSinceLow: item.daysSinceLow ?? null,
      priceRisePct: item.priceRisePct ?? null,
      consecutiveDownDays: item.consecutiveDownDays ?? null,
      consecutiveDownPct: item.consecutiveDownPct ?? null,
      k: item.k ?? null,
      d: item.d ?? null,
      j: item.j ?? null,
      rsi6: item.rsi6 ?? null,
      rsi12: item.rsi12 ?? null,
      rsi24: item.rsi24 ?? null,
      upperBand: item.upperBand ?? null,
      middleBand: item.middleBand ?? null,
      lowerBand: item.lowerBand ?? null,
      ma5: item.ma5 ?? null,
      ma10: item.ma10 ?? null,
      ma20: item.ma20 ?? null,
      industry: item.industry ?? undefined,
      instrumentType: item.instrumentType || 'stock',
      ma5Prev: item.ma5Prev ?? undefined,
      ma10Prev: item.ma10Prev ?? undefined,
      roe: item.roe ?? undefined,
      roeQualityStatus: item.roeQualityStatus,
      netProfitGrowth: item.netProfitGrowth ?? undefined,
      yoyGrowth: item.yoyGrowth ?? undefined,
      netProfitAccumGrowth: item.netProfitAccumGrowth ?? undefined,
      revenueAccumGrowth: item.revenueAccumGrowth ?? undefined,
      financialReportDate: item.financialReportDate ?? undefined,
      financialAnnounceDate: item.financialAnnounceDate ?? undefined,
      financialAsOfDate: item.financialAsOfDate ?? undefined,
      financialVerifiedAt: item.financialVerifiedAt ?? undefined,
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
      const intradayItems = page?.items ?? [];
      return {
        total: page?.total ?? 0,
        loadedCount: intradayItems.length,
        snapshotDate: null,
        expectedSnapshotDate: null,
        missingSnapshotDates: [],
        latestRunStatus: null,
        calculationVersion: 'intraday-volume',
        calculatedAt: page?.updatedAt ?? null,
        hasStaleData: false,
        // Scanner availability is intentionally exposed separately; a running
        // scanner does not mean the result set is complete.
        isComplete: false,
        warnings: page?.warnings ?? [],
        financialHealth: null,
        intradayScannerRunning: Boolean(page?.isScannerRunning),
        intradayUpdatedAt: page?.updatedAt ?? null,
        intradayStaleRowCount: intradayItems.filter(item => item.isStale)
          .length,
      };
    }

    const page = stockScreenResult.data?.stockScreen;
    const status = snapshotStatusResult.status;
    const completeSnapshotIdentity = Boolean(
      page?.snapshotDate &&
      page.snapshotDate === status?.expectedSnapshotDate &&
      page.snapshotDate === status?.latestSnapshotDate
    );
    return {
      total: page?.total ?? 0,
      loadedCount: page?.items?.length ?? 0,
      snapshotDate: page?.snapshotDate ?? null,
      expectedSnapshotDate: status?.expectedSnapshotDate ?? null,
      missingSnapshotDates: status?.missingSnapshotDates ?? [],
      latestRunStatus: status?.latestRunStatus ?? null,
      calculationVersion: page?.calculationVersion,
      calculatedAt: page?.calculatedAt ?? null,
      hasStaleData: Boolean(page?.hasStaleData),
      isComplete: Boolean(
        status?.isComplete && page?.isComplete && completeSnapshotIdentity
      ),
      warnings: Array.from(
        new Set([
          ...(snapshotStatusResult.error
            ? [`快照状态查询失败：${snapshotStatusResult.error.message}`]
            : []),
          ...(status?.warnings ?? []),
          ...(page?.warnings ?? []),
        ])
      ),
      financialHealth: page?.financialHealth ?? null,
    };
  }, [
    intradayVolumeResult.data?.intradayVolumeScreen,
    isIntradayMode,
    snapshotStatusResult.error,
    snapshotStatusResult.status,
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
    setSort(nextSort ?? DEFAULT_SORT);
    setQueryInput(buildStockScreenInput(screeningCriteria, nextSort));
  };

  const resetCriteria = () => {
    setScreeningCriteria(DEFAULT_CRITERIA);
    setActiveMode('DAILY');
    setSort(DEFAULT_SORT);
    setQueryInput(buildStockScreenInput(DEFAULT_CRITERIA, null));
    setIntradayInput(buildIntradayVolumeScreenInput(DEFAULT_CRITERIA));
  };

  const refreshDailyData = useCallback(() => {
    snapshotStatusResult.refresh();
    reexecuteStockScreen({ requestPolicy: 'network-only' });
  }, [reexecuteStockScreen, snapshotStatusResult]);

  const retry = useCallback(() => {
    if (isIntradayMode) {
      reexecuteIntradayVolume({ requestPolicy: 'network-only' });
      return;
    }
    reexecuteStockScreen({ requestPolicy: 'network-only' });
  }, [isIntradayMode, reexecuteIntradayVolume, reexecuteStockScreen]);

  return {
    screeningCriteria,
    setScreeningCriteria,
    activeMode,
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
    refreshDailyData,
    isSnapshotStatusLoading: snapshotStatusResult.fetching,
    retry,
  };
}
