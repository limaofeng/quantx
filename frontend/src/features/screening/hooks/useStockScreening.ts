import { useMemo, useState } from 'react';
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

function normalizeIndustryName(name: string): string {
  return name.trim().replace(/\s+/g, '').replace(/加权$/, '');
}

const DEFAULT_CRITERIA: ScreeningCriteria = {
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

const SORT_FIELD_INPUT: Record<
  StockScreenSortField,
  GqlStockScreenSortField
> = {
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
  VOLUME_RATIO: GqlStockScreenSortField.VolumeRatio,
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

function buildStockScreenInput(
  criteria: ScreeningCriteria,
  sort: StockScreenSortState | null
) {
  const universe = criteria.universe ?? 'STOCK';
  const supportsStockOnlyFilters = universe === 'STOCK';
  const activeFundamentalThreshold = (value?: number) =>
    typeof value === 'number' && Number.isFinite(value) && value > 0
      ? value
      : null;
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
    includeIndustries: supportsStockOnlyFilters && criteria.includeIndustries?.length
      ? criteria.includeIndustries
      : null,
    excludeIndustries: supportsStockOnlyFilters && criteria.excludeIndustries?.length
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

export function useStockScreening() {
  const [screeningCriteria, setScreeningCriteria] =
    useState<ScreeningCriteria>(DEFAULT_CRITERIA);
  const [sort, setSort] = useState<StockScreenSortState | null>(null);
  const [queryInput, setQueryInput] = useState(() =>
    buildStockScreenInput(DEFAULT_CRITERIA, null)
  );

  const [stockScreenResult] = useQuery({
    query: STOCK_SCREEN_QUERY,
    variables: { input: queryInput },
    requestPolicy: 'cache-and-network',
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
    }));
  }, [stockScreenResult.data?.stockScreen?.items]);

  const meta = useMemo<StockScreeningMeta>(() => {
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
  }, [stockScreenResult.data?.stockScreen]);

  const runScreening = (criteria: ScreeningCriteria = screeningCriteria) => {
    setScreeningCriteria(criteria);
    setQueryInput(buildStockScreenInput(criteria, sort));
  };

  const applySort = (nextSort: StockScreenSortState | null) => {
    setSort(nextSort);
    setQueryInput(buildStockScreenInput(screeningCriteria, nextSort));
  };

  const resetCriteria = () => {
    setScreeningCriteria(DEFAULT_CRITERIA);
    setSort(null);
    setQueryInput(buildStockScreenInput(DEFAULT_CRITERIA, null));
  };

  return {
    screeningCriteria,
    setScreeningCriteria,
    results,
    meta,
    sort,
    applySort,
    error: stockScreenResult.error,
    isLoading: stockScreenResult.fetching,
    runScreening,
    resetCriteria,
    availableIndustries,
  };
}
