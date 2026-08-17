import { useMemo } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import {
  getShanghaiDateKey,
  parseMarketDate,
} from '@/components/trading-chart/utils/time-utils';
import { useIntradayTrendData } from '@/hooks/useIntradayTrendData';

import {
  formatMarketSessionMinute,
  MARKET_SESSION_MINUTES,
  MARKET_SESSION_TICKS,
  toMarketSessionMinute,
} from '../marketIntradayAxis';
import { selectShanghaiMarketBarsForTradingDate } from '../marketIntradayData';
import { formatMarketPrice } from '../marketWorkbench';

interface MarketIntradayChartProps {
  changePercent?: number | null;
  preClose?: number | null;
  stockCode: string;
  targetTradingDate: string | null;
}

const readPrice = (...values: unknown[]) => {
  for (const value of values) {
    const parsed = Number(value);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return null;
};

const formatChartTime = (value: number) =>
  new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Shanghai',
  }).format(new Date(value));

export function MarketIntradayChart({
  changePercent,
  preClose,
  stockCode,
  targetTradingDate,
}: MarketIntradayChartProps) {
  const { anchorDate, bars, loading, error } = useIntradayTrendData(
    stockCode,
    '1d'
  );
  const resolvedBars = useMemo(
    () => selectShanghaiMarketBarsForTradingDate(bars, targetTradingDate),
    [bars, targetTradingDate]
  );
  const data = useMemo(
    () =>
      resolvedBars
        .map(bar => {
          const date = parseMarketDate(bar.time);
          const sessionMinute = date ? toMarketSessionMinute(date) : null;
          const price = readPrice(
            bar.close,
            bar.lastPrice,
            bar.currentPrice,
            bar.open
          );
          return date && price && sessionMinute !== null
            ? {
                price,
                sessionMinute,
                timeLabel: formatChartTime(date.getTime()),
                timestamp: date.getTime(),
              }
            : null;
        })
        .filter(
          (
            point
          ): point is {
            price: number;
            sessionMinute: number;
            timeLabel: string;
            timestamp: number;
          } => point !== null
        )
        .sort((left, right) => left.timestamp - right.timestamp),
    [resolvedBars]
  );
  const isUp = typeof changePercent !== 'number' || changePercent >= 0;
  const stroke = isUp ? '#fb7185' : '#34d399';
  const gradientId = `market-trend-${stockCode.replace('.', '-')}`;
  const resolvedAnchorDate = useMemo(() => {
    const latest = resolvedBars
      .map(bar => parseMarketDate(bar.time))
      .filter((value): value is Date => value !== null)
      .sort((left, right) => right.getTime() - left.getTime())[0];
    return (
      targetTradingDate || (latest ? getShanghaiDateKey(latest) : anchorDate)
    );
  }, [anchorDate, resolvedBars, targetTradingDate]);
  const dateLabel = resolvedAnchorDate?.slice(5).replace('-', '/');

  if (loading && data.length === 0) {
    return (
      <div className="flex h-[210px] items-center justify-center xl:h-[150px]">
        <div className="flex items-center gap-2 text-xs font-medium text-slate-500">
          <span className="h-3 w-3 animate-spin rounded-full border-2 border-slate-700 border-t-red-400" />
          正在加载分钟行情…
        </div>
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className="flex h-[210px] items-center justify-center px-6 text-center xl:h-[150px]">
        <div>
          <div className="text-sm font-bold text-slate-300">
            QMT {dateLabel ? `${dateLabel} ` : ''}分钟行情暂不可用
          </div>
          <div className="mt-1 text-xs leading-5 text-slate-600">
            {error
              ? 'QMT 行情连接或查询异常，顶部实时快照不受影响。'
              : dateLabel
                ? `等待 QMT 推送 ${dateLabel} 的 1 分钟 K 线。`
                : '等待 QMT 推送目标交易日的 1 分钟 K 线。'}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      className="relative h-[210px] w-full xl:h-[150px]"
      data-testid="market-intraday-chart"
    >
      {dateLabel ? (
        <span className="pointer-events-none absolute left-2 top-1 z-10 rounded border border-cyan-400/20 bg-cyan-400/10 px-1.5 py-0.5 text-[8px] font-bold text-cyan-300">
          {dateLabel} · 分时
        </span>
      ) : null}
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ left: 0, right: 4, top: 10, bottom: 0 }}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={stroke} stopOpacity={0.28} />
              <stop offset="100%" stopColor={stroke} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid
            stroke="rgba(148, 163, 184, 0.08)"
            strokeDasharray="3 3"
            vertical={false}
          />
          <XAxis
            axisLine={false}
            dataKey="sessionMinute"
            domain={[0, MARKET_SESSION_MINUTES]}
            interval={0}
            scale="linear"
            tick={{ fill: '#64748b', fontSize: 10 }}
            tickFormatter={value => formatMarketSessionMinute(Number(value))}
            tickLine={false}
            tickMargin={6}
            ticks={[...MARKET_SESSION_TICKS]}
            type="number"
          />
          <YAxis
            axisLine={false}
            domain={['auto', 'auto']}
            orientation="right"
            tick={{ fill: '#64748b', fontSize: 10 }}
            tickFormatter={value => Number(value).toFixed(0)}
            tickLine={false}
            width={44}
          />
          <Tooltip
            contentStyle={{
              background: '#0b1120',
              border: '1px solid rgba(148, 163, 184, 0.18)',
              borderRadius: '8px',
              color: '#e2e8f0',
              fontSize: '12px',
            }}
            formatter={value => [formatMarketPrice(Number(value)), '指数']}
            labelFormatter={(value, payload) => {
              const point = payload?.[0]?.payload as
                { timeLabel?: string } | undefined;
              return (
                point?.timeLabel || formatMarketSessionMinute(Number(value))
              );
            }}
          />
          {typeof preClose === 'number' && preClose > 0 ? (
            <ReferenceLine
              y={preClose}
              stroke="rgba(148, 163, 184, 0.5)"
              strokeDasharray="4 4"
            />
          ) : null}
          <Area
            dataKey="price"
            fill={`url(#${gradientId})`}
            isAnimationActive={false}
            stroke={stroke}
            strokeWidth={1.8}
            type="linear"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
