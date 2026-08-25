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

type MarketIndexSnapshotRow =
  Dashboard_MarketIndexSnapshotsQuery['marketIndexSnapshots'][number];
type DailyCloseRow = MarketIndexSnapshotRow['dailyKline'];
type PersistedTickRow = MarketIndexSnapshotRow['quote'];

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
    variables: { stockList: indexCodes },
    pause: indexCodes.length === 0,
    requestPolicy: 'cache-and-network',
  });
  const closingQuotes = useMemo(() => {
    const quotes = new Map<string, MarketQuoteSnapshot>();
    const definitions = new Map(
      indexDefinitions.map(definition => [definition.code, definition])
    );
    snapshotResult.data?.marketIndexSnapshots.forEach(row => {
      const definition = definitions.get(row.stockCode);
      if (!definition) return;
      const quote = toClosingQuote(definition, row.dailyKline);
      if (quote) quotes.set(definition.code, quote);
    });
    return quotes;
  }, [indexDefinitions, snapshotResult.data]);
  const persistedTickQuotes = useMemo(() => {
    const quotes = new Map<string, MarketQuoteSnapshot>();
    const definitions = new Map(
      indexDefinitions.map(definition => [definition.code, definition])
    );
    snapshotResult.data?.marketIndexSnapshots.forEach(row => {
      const definition = definitions.get(row.stockCode);
      if (!definition) return;
      const quote = toPersistedTickQuote(definition, row.quote);
      if (quote) quotes.set(definition.code, quote);
    });
    return quotes;
  }, [indexDefinitions, snapshotResult.data]);
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
