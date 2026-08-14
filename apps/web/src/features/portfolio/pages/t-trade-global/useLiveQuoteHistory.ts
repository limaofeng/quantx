import * as React from 'react';

import type { LiveMarketQuote } from '../../hooks/useRealTimeHoldings';

export type QuoteHistoryPoint = {
  at: number;
  price: number;
  sourceAt: number;
};

export type QuoteHistoryByCode = Map<string, QuoteHistoryPoint[]>;

export function sampleQuoteHistory(
  previous: QuoteHistoryByCode,
  quotes: ReadonlyMap<string, LiveMarketQuote>,
  nowMs: number
): QuoteHistoryByCode {
  const cutoff = nowMs - 120_000;
  const next = new Map<string, QuoteHistoryPoint[]>();
  for (const [stockCode, quote] of quotes) {
    const sourceAt = new Date(quote.time).getTime();
    const existing = (previous.get(stockCode) || []).filter(
      point => point.at >= cutoff
    );
    const last = existing.at(-1);
    if (
      Number.isFinite(sourceAt) &&
      quote.currentPrice > 0 &&
      last?.sourceAt !== sourceAt
    ) {
      existing.push({ at: nowMs, price: quote.currentPrice, sourceAt });
    }
    next.set(stockCode, existing.slice(-120));
  }
  return next;
}

export function useLiveQuoteHistory(
  quotes: ReadonlyMap<string, LiveMarketQuote>,
  enabled: boolean
) {
  const [history, setHistory] = React.useState<QuoteHistoryByCode>(new Map());

  React.useEffect(() => {
    if (!enabled) return;
    const sample = () =>
      setHistory(previous => sampleQuoteHistory(previous, quotes, Date.now()));
    sample();
    const timer = window.setInterval(sample, 1000);
    return () => window.clearInterval(timer);
  }, [enabled, quotes]);

  React.useEffect(() => {
    const wanted = new Set(quotes.keys());
    setHistory(previous => {
      const next = new Map(
        Array.from(previous.entries()).filter(([code]) => wanted.has(code))
      );
      return next.size === previous.size ? previous : next;
    });
  }, [quotes]);

  return history;
}
