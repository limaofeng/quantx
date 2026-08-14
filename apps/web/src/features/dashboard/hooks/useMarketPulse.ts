import { useEffect, useMemo } from 'react';
import { useQuery } from 'urql';

import {
  getShanghaiDateKey,
  parseMarketDate,
} from '@/components/trading-chart/utils/time-utils';
import { gql } from '@/generated/gql';

import {
  isMarketQuoteFreshForSession,
  type AMarketSessionPhase,
} from '../marketWorkbench';

const MarketPulseQuery = gql(`
  query Dashboard_MarketPulseV2 {
    gainers: stockScreen(
      input: {
        universe: STOCK
        excludeSt: true
        requireFresh: false
        fieldConditions: [
          {
            field: "change_pct"
            operator: "between"
            value: 0.000001
            valueTo: 100
          }
        ]
        sort: { field: CHANGE_PCT, direction: DESC }
        limit: 10
        offset: 0
      }
    ) {
      total
      snapshotDate
      calculatedAt
      warnings
      items {
        code
        name
        industry
        currentPrice
        changePct
        volumeRatio
        turnoverRatePct
      }
    }
    losers: stockScreen(
      input: {
        universe: STOCK
        excludeSt: true
        requireFresh: false
        fieldConditions: [
          {
            field: "change_pct"
            operator: "between"
            value: -100
            valueTo: -0.000001
          }
        ]
        sort: { field: CHANGE_PCT, direction: ASC }
        limit: 10
        offset: 0
      }
    ) {
      total
      snapshotDate
      calculatedAt
      warnings
      items {
        code
        name
        industry
        currentPrice
        changePct
        volumeRatio
        turnoverRatePct
      }
    }
    flats: stockScreen(
      input: {
        universe: STOCK
        excludeSt: true
        requireFresh: false
        fieldConditions: [
          { field: "change_pct", operator: "eq", value: 0 }
        ]
        limit: 1
        offset: 0
      }
    ) {
      total
    }
  }
`);

const MarketIntradayPulseQuery = gql(`
  query Dashboard_MarketIntradayPulse {
    intraday: intradayVolumeScreen(
      input: { universe: STOCK, excludeSt: true, limit: 8, offset: 0 }
    ) {
      total
      updatedAt
      isScannerRunning
      advancers
      decliners
      flats
      warnings
      items {
        code
        name
        industry
        currentPrice
        changePct
        amount
        volumePaceRatio
        last5mVolumeRatio
        intradayTurnoverRatePct
        isStale
      }
      topGainers {
        code
        name
        currentPrice
        changePct
        volumeRatio
      }
      topLosers {
        code
        name
        currentPrice
        changePct
        volumeRatio
      }
    }
  }
`);

export type MarketPulseSnapshotMode = 'daily' | 'intraday' | 'unavailable';

const snapshotDateKey = (value: string | null | undefined) => {
  if (!value) return null;
  const directDate = /^(\d{4}-\d{2}-\d{2})/.exec(value)?.[1];
  if (directDate) return directDate;
  const parsed = parseMarketDate(value);
  return parsed ? getShanghaiDateKey(parsed) : null;
};

export function selectMarketPulseSnapshot({
  dailySnapshotDate,
  intradayTotal,
  intradayUpdatedAt,
  phase,
  targetTradingDate,
}: {
  dailySnapshotDate: string | null | undefined;
  intradayTotal: number;
  intradayUpdatedAt: string | null | undefined;
  phase: AMarketSessionPhase;
  targetTradingDate: string | null;
}): MarketPulseSnapshotMode {
  const dailyDate = snapshotDateKey(dailySnapshotDate);
  const intradayDate =
    intradayTotal > 0 ? snapshotDateKey(intradayUpdatedAt) : null;

  if (targetTradingDate) {
    const dailyMatches = dailyDate === targetTradingDate;
    const intradayMatches = intradayDate === targetTradingDate;
    const activeSession = [
      'call-auction',
      'opening-wait',
      'morning',
      'lunch-break',
      'afternoon',
    ].includes(phase);
    if (activeSession && intradayMatches) return 'intraday';
    if (dailyMatches) return 'daily';
    if (intradayMatches) return 'intraday';
    return 'unavailable';
  }

  if (dailyDate && intradayDate) {
    if (intradayDate > dailyDate) return 'intraday';
    return 'daily';
  }
  if (intradayDate) return 'intraday';
  if (dailyDate) return 'daily';
  return 'unavailable';
}

export function useMarketPulse({
  now,
  phase,
  targetTradingDate,
}: {
  now: Date;
  phase: AMarketSessionPhase;
  targetTradingDate: string | null;
}) {
  const [dailyResult, refreshDaily] = useQuery({
    query: MarketPulseQuery,
    requestPolicy: 'network-only',
  });
  const [intradayResult, refreshIntraday] = useQuery({
    query: MarketIntradayPulseQuery,
    requestPolicy: 'cache-and-network',
  });

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        refreshIntraday({ requestPolicy: 'network-only' });
      }
    }, 15_000);
    return () => window.clearInterval(intervalId);
  }, [refreshIntraday]);

  const dailyBreadth = useMemo(() => {
    const advancers = dailyResult.data?.gainers.total ?? 0;
    const decliners = dailyResult.data?.losers.total ?? 0;
    const flats = dailyResult.data?.flats.total ?? 0;
    return {
      advancers,
      decliners,
      flats,
      total: advancers + decliners + flats,
    };
  }, [dailyResult.data]);
  const intraday = intradayResult.data?.intraday;
  const snapshotMode = selectMarketPulseSnapshot({
    dailySnapshotDate: dailyResult.data?.gainers.snapshotDate,
    intradayTotal: intraday?.total ?? 0,
    intradayUpdatedAt: intraday?.updatedAt,
    phase,
    targetTradingDate,
  });
  const useIntradaySnapshot = snapshotMode === 'intraday';
  const useDailySnapshot = snapshotMode === 'daily';
  const breadth = useIntradaySnapshot
    ? {
        advancers: intraday?.advancers ?? 0,
        decliners: intraday?.decliners ?? 0,
        flats: intraday?.flats ?? 0,
        total: intraday?.total ?? 0,
      }
    : useDailySnapshot
      ? dailyBreadth
      : { advancers: 0, decliners: 0, flats: 0, total: 0 };
  const snapshotAt = useIntradaySnapshot
    ? (intraday?.updatedAt ?? null)
    : useDailySnapshot
      ? (dailyResult.data?.gainers.snapshotDate ?? null)
      : null;

  return {
    breadth,
    calculatedAt: dailyResult.data?.gainers.calculatedAt ?? null,
    error: dailyResult.error || intradayResult.error,
    fetching: dailyResult.fetching || intradayResult.fetching,
    gainers: useIntradaySnapshot
      ? (intraday?.topGainers ?? [])
      : useDailySnapshot
        ? (dailyResult.data?.gainers.items ?? [])
        : [],
    intraday: intraday?.items ?? [],
    intradayIsFresh: isMarketQuoteFreshForSession(
      intraday?.updatedAt,
      now,
      phase
    ),
    intradayRunning: Boolean(intraday?.isScannerRunning),
    intradayUpdatedAt: intraday?.updatedAt ?? null,
    losers: useIntradaySnapshot
      ? (intraday?.topLosers ?? [])
      : useDailySnapshot
        ? (dailyResult.data?.losers.items ?? [])
        : [],
    refresh: () => {
      refreshDaily({ requestPolicy: 'network-only' });
      refreshIntraday({ requestPolicy: 'network-only' });
    },
    snapshotAt,
    snapshotDate: dailyResult.data?.gainers.snapshotDate ?? null,
    snapshotMode,
    warnings: [
      ...(dailyResult.data?.gainers.warnings ?? []),
      ...(intradayResult.data?.intraday.warnings ?? []),
    ],
  };
}
