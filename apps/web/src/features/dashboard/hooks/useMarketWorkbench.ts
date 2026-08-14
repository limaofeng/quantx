import { useEffect, useMemo } from 'react';
import { useQuery } from 'urql';

import { useLatestMarketQuotes } from '@/features/portfolio/hooks/useRealTimeHoldings';
import { gql } from '@/generated/gql';

import {
  CORE_MARKET_INDICES,
  isMarketQuoteFreshForSession,
  isMarketQuoteFromTradingDate,
  selectLatestMarketQuote,
  selectMarketQuoteForTradingDate,
  summarizeCoreMarket,
  type AMarketSessionPhase,
  type MarketIndexDefinition,
  type MarketQuoteSnapshot,
} from '../marketWorkbench';

const indexCodes = CORE_MARKET_INDICES.map(index => index.code);

const MARKET_SNAPSHOT_REFRESH_INTERVAL_MS = 15_000;

const MarketIndexSnapshotsQuery = gql(`
  query Dashboard_MarketIndexSnapshots {
    shanghaiTick: ticks(
      stockCode: "000001.SH"
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
    }
    shenzhenTick: ticks(
      stockCode: "399001.SZ"
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
    }
    chinextTick: ticks(
      stockCode: "399006.SZ"
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
    }
    csi300Tick: ticks(stockCode: "000300.SH", limit: 1, order: "desc") {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
    }
    csi500Tick: ticks(stockCode: "000905.SH", limit: 1, order: "desc") {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
    }
    csi1000Tick: ticks(stockCode: "000852.SH", limit: 1, order: "desc") {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
    }
    shanghaiIndex: klines(
      stockCode: "000001.SH"
      period: DAY_1
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      open
      high
      low
      close
      preClose
      volume
    }
    shenzhenComponent: klines(
      stockCode: "399001.SZ"
      period: DAY_1
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      open
      high
      low
      close
      preClose
      volume
    }
    chinextIndex: klines(
      stockCode: "399006.SZ"
      period: DAY_1
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      open
      high
      low
      close
      preClose
      volume
    }
    csi300: klines(
      stockCode: "000300.SH"
      period: DAY_1
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      open
      high
      low
      close
      preClose
      volume
    }
    csi500: klines(
      stockCode: "000905.SH"
      period: DAY_1
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      open
      high
      low
      close
      preClose
      volume
    }
    csi1000: klines(
      stockCode: "000852.SH"
      period: DAY_1
      limit: 1
      order: "desc"
    ) {
      stockCode
      time
      open
      high
      low
      close
      preClose
      volume
    }
  }
`);

interface DailyCloseRow {
  close: number;
  high: number;
  low: number;
  open: number;
  preClose: number;
  stockCode: string;
  time: unknown;
  volume: number;
}

interface PersistedTickRow {
  high: number;
  lastPrice: number;
  low: number;
  open: number;
  preClose: number;
  stockCode: string;
  time: unknown;
  volume: number;
}

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
  now,
  phase,
  targetTradingDate,
}: {
  now: Date;
  phase: AMarketSessionPhase;
  targetTradingDate: string | null;
}) {
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
      data?.csi300[0],
      data?.csi500[0],
      data?.csi1000[0],
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
      data?.csi300Tick[0],
      data?.csi500Tick[0],
      data?.csi1000Tick[0],
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
    CORE_MARKET_INDICES.forEach(definition => {
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
    persistedTickQuotes,
    quoteState.quotes,
    targetTradingDate,
  ]);
  const indices = useMemo(
    () =>
      CORE_MARKET_INDICES.map(definition => ({
        definition,
        quote: effectiveQuotes.get(definition.code),
      })),
    [effectiveQuotes]
  );
  const summary = useMemo(
    () => summarizeCoreMarket(CORE_MARKET_INDICES, effectiveQuotes),
    [effectiveQuotes]
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
