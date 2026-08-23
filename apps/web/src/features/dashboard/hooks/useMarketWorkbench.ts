import { useEffect, useMemo } from 'react';
import { useQuery } from 'urql';

import { useLatestMarketQuotes } from '@/features/portfolio/hooks/useRealTimeHoldings';

import {
  Dashboard_MarketIndexSnapshotsDocument,
  type Dashboard_MarketIndexSnapshotsQuery,
} from '../graphql/__generated__/graphql';
import {
  CORE_MARKET_INDICES,
  MAX_MARKET_INDEXES,
  isMarketQuoteFreshForSession,
  isMarketQuoteFromTradingDate,
  selectLatestMarketQuote,
  selectMarketQuoteForTradingDate,
  summarizeCoreMarket,
  type AMarketSessionPhase,
  type MarketIndexDefinition,
  type MarketQuoteSnapshot,
} from '../marketWorkbench';

const MARKET_SNAPSHOT_REFRESH_INTERVAL_MS = 15_000;

const MarketIndexSnapshotsQuery = Dashboard_MarketIndexSnapshotsDocument;

type DailyCloseRow =
  Dashboard_MarketIndexSnapshotsQuery['shanghaiIndex'][number];
type PersistedTickRow =
  Dashboard_MarketIndexSnapshotsQuery['shanghaiTick'][number];

const toClosingQuote = (
  definition: MarketIndexDefinition,
  row: DailyCloseRow | null | undefined
): MarketQuoteSnapshot | null => {
  if (!row || !Number.isFinite(row.close) || row.close <= 0) return null;
  const change = row.close - row.preClose;
  const changePercent = row.preClose > 0 ? (change / row.preClose) * 100 : null;

  return {
    change,
    changePercent,
    currentPrice: row.close,
    high: row.high,
    low: row.low,
    open: row.open,
    preClose: row.preClose,
    source: 'daily-close',
    stockCode: definition.code,
    time: String(row.time),
    volume: row.volume,
  };
};

const toPersistedTickQuote = (
  definition: MarketIndexDefinition,
  row: PersistedTickRow | null | undefined
): MarketQuoteSnapshot | null => {
  if (!row || !Number.isFinite(row.lastPrice) || row.lastPrice <= 0) {
    return null;
  }
  const change = row.lastPrice - row.preClose;
  const changePercent = row.preClose > 0 ? (change / row.preClose) * 100 : null;

  return {
    change,
    changePercent,
    currentPrice: row.lastPrice,
    high: row.high,
    low: row.low,
    open: row.open,
    preClose: row.preClose,
    source: 'persisted-tick',
    stockCode: definition.code,
    time: String(row.time),
    volume: row.volume,
  };
};

export function useMarketWorkbench({
  indexDefinitions = CORE_MARKET_INDICES,
  now,
  phase,
  targetTradingDate,
}: {
  indexDefinitions?: readonly MarketIndexDefinition[];
  now: Date;
  phase: AMarketSessionPhase;
  targetTradingDate: string | null;
}) {
  const indexCodes = useMemo(
    () =>
      Array.from(new Set(indexDefinitions.map(index => index.code))).slice(
        0,
        MAX_MARKET_INDEXES
      ),
    [indexDefinitions]
  );
  const quoteState = useLatestMarketQuotes({ stockCodes: indexCodes });
  const [snapshotResult, refreshSnapshots] = useQuery({
    query: MarketIndexSnapshotsQuery,
    requestPolicy: 'cache-and-network',
  });
  const closingQuotes = useMemo(() => {
    const data = snapshotResult.data;
    const candidates: Array<DailyCloseRow | null | undefined> = [
      data?.shanghaiIndex[0],
      data?.shenzhenComponent[0],
      data?.chinextIndex[0],
      data?.kechuangComposite[0],
      data?.kechuang50[0],
      data?.csiA500[0],
      data?.csi300[0],
      data?.csi1000[0],
      data?.shanghai50[0],
      data?.shenzhen100[0],
      data?.csi500[0],
      data?.chinext50[0],
      data?.kechuang100[0],
    ];
    const quotes = new Map<string, MarketQuoteSnapshot>();
    CORE_MARKET_INDICES.forEach((definition, index) => {
      const quote = toClosingQuote(definition, candidates[index]);
      if (quote) quotes.set(definition.code, quote);
    });
    return quotes;
  }, [snapshotResult.data]);
  const persistedTickQuotes = useMemo(() => {
    const data = snapshotResult.data;
    const candidates: Array<PersistedTickRow | null | undefined> = [
      data?.shanghaiTick[0],
      data?.shenzhenTick[0],
      data?.chinextTick[0],
      data?.kechuangCompositeTick[0],
      data?.kechuang50Tick[0],
      data?.csiA500Tick[0],
      data?.csi300Tick[0],
      data?.csi1000Tick[0],
      data?.shanghai50Tick[0],
      data?.shenzhen100Tick[0],
      data?.csi500Tick[0],
      data?.chinext50Tick[0],
      data?.kechuang100Tick[0],
    ];
    const quotes = new Map<string, MarketQuoteSnapshot>();
    CORE_MARKET_INDICES.forEach((definition, index) => {
      const quote = toPersistedTickQuote(definition, candidates[index]);
      if (quote) quotes.set(definition.code, quote);
    });
    return quotes;
  }, [snapshotResult.data]);
  const effectiveQuotes = useMemo(() => {
    const quotes = new Map<string, MarketQuoteSnapshot>();
    indexDefinitions.forEach(definition => {
      const liveQuote = quoteState.quotes.get(definition.code);
      const quote = selectMarketQuoteForTradingDate(
        targetTradingDate,
        closingQuotes.get(definition.code),
        persistedTickQuotes.get(definition.code),
        liveQuote ? { ...liveQuote, source: 'live' } : undefined
      );
      if (quote) quotes.set(definition.code, quote);
    });
    return quotes;
  }, [
    closingQuotes,
    indexDefinitions,
    persistedTickQuotes,
    quoteState.quotes,
    targetTradingDate,
  ]);
  const indices = useMemo(
    () =>
      indexDefinitions.map(definition => ({
        definition,
        quote: effectiveQuotes.get(definition.code),
      })),
    [effectiveQuotes, indexDefinitions]
  );
  const summary = useMemo(
    () => summarizeCoreMarket(indexDefinitions, effectiveQuotes),
    [effectiveQuotes, indexDefinitions]
  );
  const latestQuote = selectLatestMarketQuote(
    ...Array.from(effectiveQuotes.values())
  );
  const latestDataAt = latestQuote?.time;
  const freshCoverage = Array.from(effectiveQuotes.values()).filter(quote =>
    isMarketQuoteFreshForSession(quote.time, now, phase)
  ).length;
  const targetDateCoverage = Array.from(effectiveQuotes.values()).filter(
    quote => isMarketQuoteFromTradingDate(quote.time, targetTradingDate)
  ).length;
  const liveCoverage = Array.from(effectiveQuotes.values()).filter(
    quote => quote.source === 'live'
  ).length;
  const persistedTickCoverage = Array.from(effectiveQuotes.values()).filter(
    quote => quote.source === 'persisted-tick'
  ).length;
  const closingCoverage = closingQuotes.size;
  const dataMode = latestQuote
    ? latestQuote.source === 'live'
      ? 'live'
      : latestQuote.source === 'persisted-tick'
        ? 'intraday'
        : 'close'
    : 'waiting';

  useEffect(() => {
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        refreshSnapshots({ requestPolicy: 'network-only' });
      }
    }, MARKET_SNAPSHOT_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [refreshSnapshots]);

  return {
    ...quoteState,
    closingCoverage,
    dataMode,
    error: quoteState.error || snapshotResult.error,
    freshCoverage,
    indices,
    latestQuoteAt: latestDataAt,
    liveCoverage,
    persistedTickCoverage,
    refreshLatestQuotes: () => {
      quoteState.refreshLatestQuotes();
      refreshSnapshots({ requestPolicy: 'network-only' });
    },
    summary,
    targetDateCoverage,
  };
}
