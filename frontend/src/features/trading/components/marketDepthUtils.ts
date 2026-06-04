export interface DepthLevel {
  price?: number | null;
  volume?: number | null;
}

export interface MarketDepthStockLike {
  currentPrice?: unknown;
  id?: unknown;
  instrumentName?: unknown;
  name?: unknown;
  quote?: {
    lastPrice?: unknown;
  } | null;
  stockCode?: unknown;
}

export interface MarketDepthTickLike {
  lastPrice?: unknown;
  preClose?: unknown;
  stockCode?: unknown;
}

export const toFiniteNumber = (value: unknown): number | null => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

export const formatDepthVolume = (value: number | null | undefined) => {
  if (!value || value <= 0) return '--';
  return Math.round(value).toString();
};

export const resolveStockCode = (
  stock: MarketDepthStockLike | string | null
) =>
  typeof stock === 'string'
    ? stock
    : String(stock?.stockCode || stock?.id || '');

export const resolveStockName = (
  stock: MarketDepthStockLike | string | null,
  stockCode: string
) =>
  typeof stock === 'string'
    ? stock
    : String(stock?.name || stock?.instrumentName || stockCode);

export const resolveQuotePrice = (
  stock: MarketDepthStockLike | string | null
) => {
  if (typeof stock === 'string') return null;
  return (
    toFiniteNumber(stock?.quote?.lastPrice) ??
    toFiniteNumber(stock?.currentPrice)
  );
};

export const resolveMarketSnapshot = ({
  bestAsk,
  bestBid,
  selectedStock,
  tick,
}: {
  bestAsk: number | null;
  bestBid: number | null;
  selectedStock: MarketDepthStockLike | string | null;
  tick?: MarketDepthTickLike | null;
}) => {
  const tickPrice = toFiniteNumber(tick?.lastPrice);
  const quotePrice = resolveQuotePrice(selectedStock);
  const depthMidPrice =
    bestAsk && bestBid ? (bestAsk + bestBid) / 2 : bestAsk || bestBid || null;
  const price = tickPrice ?? quotePrice ?? depthMidPrice;
  const preClose = toFiniteNumber(tick?.preClose);
  const changePercent =
    tickPrice !== null && preClose !== null && preClose > 0
      ? ((tickPrice - preClose) / preClose) * 100
      : null;

  return {
    changePercent,
    price,
  };
};
