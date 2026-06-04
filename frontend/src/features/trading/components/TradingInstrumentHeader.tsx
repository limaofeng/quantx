import { useEffect } from 'react';
import { gql as urqlGql, useQuery, useSubscription } from 'urql';

import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import type { Stock } from '@/shared/types';
import { cn } from '@/utils/cn';

interface TradingInstrumentHeaderProps {
  accountCash?: number | null;
  onInstrumentNameChange?: (name: string) => void;
  selectedStock: Stock | null;
  stockCode?: string;
}

interface MarketSnapshotTick {
  amount?: number | null;
  high?: number | null;
  lastPrice?: number | null;
  low?: number | null;
  open?: number | null;
  preClose?: number | null;
  stockCode?: string | null;
  time?: string | null;
  volume?: number | null;
}

interface MarketSnapshotTickData {
  marketTicks?: MarketSnapshotTick | null;
}

interface HeaderInstrumentInfo {
  id: string;
  name?: string | null;
  market?: string | null;
  type?: string | null;
  totalVolume?: number | null;
  floatVolume?: number | null;
  quote?: {
    amount?: number | null;
    change?: number | null;
    changePercent?: number | null;
    high?: number | null;
    lastPrice?: number | null;
    low?: number | null;
    open?: number | null;
    preClose?: number | null;
    time?: string | null;
    turnoverRate?: number | null;
    volume?: number | null;
  } | null;
}

interface HeaderFinancialSummary {
  circulatingCapital?: number | null;
  epsBasic?: number | null;
  totalCapital?: number | null;
}

interface HeaderInfoData {
  financialSummary?: HeaderFinancialSummary | null;
  instrument?: HeaderInstrumentInfo | null;
}

type HeaderStock = Stock & {
  floatVolume?: number | null;
  instrumentName?: string | null;
  profitRate?: number | null;
  totalVolume?: number | null;
  volume?: number | null;
};

const HeaderInfoQuery = urqlGql`
  query TradingInstrumentHeader_Info($stockCode: String!) {
    instrument(stockCode: $stockCode) {
      id
      name
      market
      type
      totalVolume
      floatVolume
      quote {
        lastPrice
        open
        high
        low
        preClose
        change
        changePercent
        volume
        amount
        turnoverRate
        time
      }
    }
    financialSummary(stockCode: $stockCode) {
      totalCapital
      circulatingCapital
      epsBasic
    }
  }
`;

const MarketSnapshotTickSubscription = urqlGql`
  subscription TradingInstrumentHeader_MarketSnapshotTick($stockList: [String!]!) {
    marketTicks(stockList: $stockList) {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
      amount
    }
  }
`;

const normalizeStockCode = (value: unknown) =>
  typeof value === 'string' ? value.trim().toUpperCase() : '';

const toFiniteNumber = (value: unknown): number | null => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const firstNumber = (...values: unknown[]) => {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null) return parsed;
  }
  return null;
};

const firstPositiveNumber = (...values: unknown[]) => {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null && parsed > 0) return parsed;
  }
  return null;
};

const getQuoteExtra = (stock: Stock | null, key: string) => {
  if (!stock?.quote || typeof stock.quote !== 'object') return null;
  return (stock.quote as Record<string, unknown>)[key];
};

const formatPrice = (value: unknown) => {
  const price = firstPositiveNumber(value);
  if (price === null) return '--';
  return price.toFixed(price >= 10 ? 2 : 3);
};

const formatSignedPrice = (value: unknown) => {
  const parsed = firstNumber(value);
  if (parsed === null) return '--';
  const prefix = parsed > 0 ? '+' : '';
  return `${prefix}${parsed.toFixed(Math.abs(parsed) >= 10 ? 2 : 3)}`;
};

const formatSignedPercent = (value: unknown) => {
  const parsed = firstNumber(value);
  if (parsed === null) return '--';
  const prefix = parsed > 0 ? '+' : '';
  return `${prefix}${parsed.toFixed(2)}%`;
};

const formatPlainPercent = (value: unknown) => {
  const parsed = firstNumber(value);
  if (parsed === null) return '--';
  return `${parsed.toFixed(2)}%`;
};

const formatLargeMetric = (value: unknown) => {
  const parsed = firstPositiveNumber(value);
  if (parsed === null) return '--';
  if (parsed >= 1e8) return `${(parsed / 1e8).toFixed(2)}亿`;
  if (parsed >= 1e4) return `${(parsed / 1e4).toFixed(1)}万`;
  return Math.round(parsed).toLocaleString();
};

const formatRatio = (value: unknown) => {
  const parsed = firstPositiveNumber(value);
  if (parsed === null) return '--';
  return parsed.toFixed(parsed >= 100 ? 0 : 2);
};

const formatTime = (value: unknown) => {
  if (typeof value !== 'string' || !value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(11, 19) || '--';
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Asia/Shanghai',
  });
};

function HeaderMetric({
  label,
  title,
  tone,
  value,
}: {
  label: string;
  title?: string;
  tone?: string;
  value: string;
}) {
  return (
    <div className="flex min-w-[68px] items-baseline gap-2" title={title}>
      <span className="shrink-0 text-[12px] font-black text-slate-500">
        {label}
      </span>
      <span
        className={cn(
          'min-w-0 truncate font-mono text-[15px] font-black text-slate-200 tabular-nums',
          tone
        )}
      >
        {value}
      </span>
    </div>
  );
}

export function TradingInstrumentHeader({
  accountCash,
  onInstrumentNameChange,
  selectedStock,
  stockCode,
}: TradingInstrumentHeaderProps) {
  const normalizedStockCode = normalizeStockCode(
    stockCode || selectedStock?.stockCode || selectedStock?.id
  );
  const { holdings, portfolioSummary } = useHoldings();
  const [headerInfoResult] = useQuery<HeaderInfoData>({
    query: HeaderInfoQuery,
    variables: {
      stockCode: normalizedStockCode,
    },
    pause: !normalizedStockCode,
  });
  const holding = holdings.find(
    item => normalizeStockCode(item?.stockCode) === normalizedStockCode
  );
  const instrument = headerInfoResult.data?.instrument || null;
  const financialSummary = headerInfoResult.data?.financialSummary || null;
  const stock = (selectedStock ||
    holding ||
    instrument ||
    null) as HeaderStock | null;
  const [snapshotTickResult] = useSubscription<MarketSnapshotTickData>({
    query: MarketSnapshotTickSubscription,
    variables: {
      stockList: normalizedStockCode ? [normalizedStockCode] : [],
    },
    pause: !normalizedStockCode,
  });
  const tick =
    snapshotTickResult.data?.marketTicks?.stockCode === normalizedStockCode
      ? snapshotTickResult.data.marketTicks
      : null;

  const lastPrice = firstPositiveNumber(
    tick?.lastPrice,
    instrument?.quote?.lastPrice,
    stock?.quote?.lastPrice,
    stock?.currentPrice,
    holding?.lastPrice
  );
  const preClose = firstPositiveNumber(
    tick?.preClose,
    instrument?.quote?.preClose,
    stock?.quote?.preClose
  );
  const tickChange =
    lastPrice !== null && preClose !== null ? lastPrice - preClose : null;
  const change =
    tickChange ?? firstNumber(instrument?.quote?.change, stock?.quote?.change);
  const changePercent =
    tickChange !== null && preClose !== null && preClose > 0
      ? (tickChange / preClose) * 100
      : firstNumber(
          instrument?.quote?.changePercent,
          stock?.quote?.changePercent,
          stock?.profitRate
        );
  const tone =
    changePercent === null
      ? 'text-slate-300'
      : changePercent >= 0
        ? 'text-red-400'
        : 'text-emerald-400';
  const cash = firstNumber(accountCash, portfolioSummary?.cash);
  const availableToBuy =
    cash !== null && lastPrice !== null && lastPrice > 0
      ? Math.floor(cash / lastPrice)
      : null;
  const availableToSell = firstNumber(holding?.volume);
  const stockName =
    instrument?.name ||
    stock?.name ||
    stock?.instrumentName ||
    holding?.instrumentName ||
    normalizedStockCode ||
    '未选择标的';

  useEffect(() => {
    onInstrumentNameChange?.(stockName);
  }, [onInstrumentNameChange, stockName]);

  const marketType =
    instrument?.type ||
    stock?.type ||
    (normalizedStockCode.endsWith('.SH') || normalizedStockCode.endsWith('.SZ')
      ? 'A股'
      : '--');
  const limitDown = preClose !== null ? formatPrice(preClose * 0.9) : '--';
  const limitUp = preClose !== null ? formatPrice(preClose * 1.1) : '--';
  const totalShares = firstPositiveNumber(
    financialSummary?.totalCapital,
    instrument?.totalVolume,
    stock?.totalVolume
  );
  const floatShares = firstPositiveNumber(
    financialSummary?.circulatingCapital,
    instrument?.floatVolume,
    stock?.floatVolume
  );
  const marketCap =
    lastPrice !== null && totalShares !== null ? lastPrice * totalShares : null;
  const floatMarketCap =
    lastPrice !== null && floatShares !== null ? lastPrice * floatShares : null;
  const epsBasic = firstPositiveNumber(financialSummary?.epsBasic);
  const peRatio =
    lastPrice !== null && epsBasic !== null ? lastPrice / epsBasic : null;
  const sessionVolume = firstPositiveNumber(
    tick?.volume,
    instrument?.quote?.volume,
    stock?.quote?.volume,
    stock?.volume
  );
  const sessionAmount = firstPositiveNumber(
    tick?.amount,
    instrument?.quote?.amount,
    stock?.quote?.amount
  );
  const quotedTurnoverRate = firstNumber(
    instrument?.quote?.turnoverRate,
    getQuoteExtra(stock as Stock | null, 'turnoverRate')
  );
  const turnoverRate =
    quotedTurnoverRate ??
    (sessionVolume !== null && floatShares !== null
      ? (sessionVolume / floatShares) * 100
      : null);

  return (
    <div className="shrink-0 border-b border-white/5 bg-[#0b1120]/95 px-3 py-2">
      <div className="flex min-h-[86px] min-w-0 items-center gap-4">
        <div className="min-w-[190px] shrink-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-black text-slate-100">
              {stockName}
            </span>
            <span className="rounded border border-red-500/25 bg-red-500/10 px-1.5 py-0.5 text-[9px] font-black text-red-300">
              {marketType}
            </span>
          </div>
          <div className="mt-1 truncate font-mono text-[10px] font-bold text-slate-500">
            {normalizedStockCode || '--'} · {formatTime(tick?.time)}
          </div>
        </div>

        <div className="w-[160px] shrink-0">
          <div
            className={cn(
              'font-mono text-[34px] font-black leading-none',
              tone
            )}
          >
            {formatPrice(lastPrice)}
          </div>
          <div className={cn('mt-1 font-mono text-sm font-black', tone)}>
            {formatSignedPrice(change)} {formatSignedPercent(changePercent)}
          </div>
        </div>

        <div className="grid min-w-0 flex-1 grid-cols-[repeat(auto-fit,minmax(74px,1fr))] gap-x-4 gap-y-1.5">
          <HeaderMetric
            label="高"
            tone="text-red-400"
            value={formatPrice(
              firstPositiveNumber(
                tick?.high,
                instrument?.quote?.high,
                stock?.quote?.high
              )
            )}
          />
          <HeaderMetric
            label="低"
            tone="text-emerald-400"
            value={formatPrice(
              firstPositiveNumber(
                tick?.low,
                instrument?.quote?.low,
                stock?.quote?.low
              )
            )}
          />
          <HeaderMetric
            label="开"
            value={formatPrice(
              firstPositiveNumber(
                tick?.open,
                instrument?.quote?.open,
                stock?.quote?.open
              )
            )}
          />
          <HeaderMetric label="量" value={formatLargeMetric(sessionVolume)} />
          <HeaderMetric label="额" value={formatLargeMetric(sessionAmount)} />
          <HeaderMetric label="昨" value={formatPrice(preClose)} />
          <HeaderMetric label="市值" value={formatLargeMetric(marketCap)} />
          <HeaderMetric
            label="流通"
            value={formatLargeMetric(floatMarketCap)}
          />
          <HeaderMetric
            label="市盈"
            title="基于最新财报 EPS 估算"
            value={formatRatio(peRatio)}
          />
          <HeaderMetric label="换" value={formatPlainPercent(turnoverRate)} />
        </div>

        <div className="hidden min-w-[180px] shrink-0 grid-cols-2 gap-x-3 gap-y-1.5 border-l border-white/5 pl-4 2xl:grid">
          <HeaderMetric
            label="跌停"
            tone="text-emerald-400"
            value={limitDown}
          />
          <HeaderMetric label="涨停" tone="text-red-400" value={limitUp} />
          <HeaderMetric
            label="可买"
            value={
              availableToBuy === null ? '--' : availableToBuy.toLocaleString()
            }
          />
          <HeaderMetric
            label="可卖"
            value={
              availableToSell === null ? '--' : availableToSell.toLocaleString()
            }
          />
        </div>
      </div>
    </div>
  );
}
