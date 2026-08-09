import {
  Activity,
  AlertCircle,
  BarChart3,
  CheckCircle2,
  LineChart,
  RotateCcw,
  Shield,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  Cell,
  Line,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useQuery } from 'urql';

import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { cn } from '@/utils/cn';

import { StrategyPerformanceQuery } from '../hooks/strategyInstanceOperations';

type PerformancePoint = {
  sequence: number;
  timestamp: string;
  equity: number;
  value: number;
  benchmarkValue?: number | null;
  eventType?: string | null;
};

type MonthlyReturn = {
  month: string;
  returnPct: number;
};

type BacktestVersionRef = {
  id: string;
  version: number;
  status?: string | null;
};

interface PerformanceTabProps {
  runId?: string | null;
  runMode?: string | null;
  selectedBacktestId?: string | null;
  currentBacktestVersion?: BacktestVersionRef | null;
  benchmarkCode?: string | null;
  runStatus?: string | null;
  active?: boolean;
}

type TimeRange = {
  startIndex: number;
  endIndex: number;
};

type TooltipPayloadItem = {
  dataKey?: string;
  name?: string;
  value?: unknown;
  payload?: PerformancePoint;
};

type StrategyPerformanceResult = {
  strategyPerformance?: Record<string, unknown> | null;
};

function num(value: unknown, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function fmtNumber(value: unknown, digits = 2): string {
  return num(value).toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function fmtPct(value: unknown, digits = 2): string {
  const n = num(value);
  return `${n > 0 ? '+' : ''}${fmtNumber(n, digits)}%`;
}

function fmtMoney(value: unknown): string {
  const n = num(value);
  return `${n < 0 ? '-' : ''}¥${Math.abs(n).toLocaleString('zh-CN', {
    maximumFractionDigits: 2,
  })}`;
}

function formatDate(value: string): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1}/${date.getDate()} ${date
    .getHours()
    .toString()
    .padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
}

function formatExactDate(value: string | number | undefined): string {
  if (!value) return '-';
  const raw = String(value);
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, '0');
  const day = `${date.getDate()}`.padStart(2, '0');
  const hours = `${date.getHours()}`.padStart(2, '0');
  const minutes = `${date.getMinutes()}`.padStart(2, '0');
  const seconds = `${date.getSeconds()}`.padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

function downsample<T>(items: T[], max = 1200): T[] {
  if (items.length <= max) return items;
  const step = Math.ceil(items.length / max);
  return items.filter(
    (_, index) => index % step === 0 || index === items.length - 1
  );
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function snakeCase(key: string): string {
  return key.replace(/[A-Z]/g, char => `_${char.toLowerCase()}`);
}

function metric(record: Record<string, unknown>, key: string): unknown {
  return record[key] ?? record[snakeCase(key)];
}

function mapPoints(value: unknown): PerformancePoint[] {
  if (!Array.isArray(value)) return [];
  return value.map(raw => {
    const item = asRecord(raw);
    return {
      sequence: num(item.sequence, 0),
      timestamp: String(item.timestamp || ''),
      equity: num(item.equity, 0),
      value: num(item.value, 0),
      benchmarkValue:
        item.benchmarkValue === null || item.benchmarkValue === undefined
          ? null
          : num(item.benchmarkValue, 0),
      eventType: item.eventType ? String(item.eventType) : null,
    };
  });
}

function mapMonthly(value: unknown): MonthlyReturn[] {
  if (!Array.isArray(value)) return [];
  return value.map(raw => {
    const item = asRecord(raw);
    return {
      month: String(item.month || ''),
      returnPct: num(item.returnPct, 0),
    };
  });
}

function clampRange(range: TimeRange | null, length: number): TimeRange | null {
  if (length <= 0) return null;
  if (!range) return { startIndex: 0, endIndex: length - 1 };
  const startIndex = Math.max(0, Math.min(range.startIndex, length - 1));
  const endIndex = Math.max(startIndex, Math.min(range.endIndex, length - 1));
  return { startIndex, endIndex };
}

function PerformanceTooltip({
  active,
  payload,
  label,
  kind,
}: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string | number;
  kind: 'return' | 'drawdown';
}) {
  if (!active || !payload?.length) return null;
  const point = payload.find(item => item.payload)?.payload;
  if (!point) return null;
  const title = kind === 'drawdown' ? '回撤' : '收益';
  const value =
    kind === 'drawdown' ? fmtPct(-Math.abs(point.value)) : fmtPct(point.value);

  return (
    <div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-xl dark:border-white/10 dark:bg-slate-950">
      <div className="mb-2 font-bold text-slate-900 dark:text-white">
        {formatExactDate(label)}
      </div>
      <div className="space-y-1 text-slate-600 dark:text-slate-300">
        <div className="flex min-w-[180px] justify-between gap-4">
          <span>{title}</span>
          <span className="font-mono font-bold text-slate-900 dark:text-white">
            {value}
          </span>
        </div>
        <div className="flex min-w-[180px] justify-between gap-4">
          <span>权益</span>
          <span className="font-mono font-bold text-slate-900 dark:text-white">
            {fmtMoney(point.equity)}
          </span>
        </div>
        {point.eventType && (
          <div className="flex min-w-[180px] justify-between gap-4">
            <span>事件</span>
            <span className="font-mono font-bold uppercase text-slate-900 dark:text-white">
              {point.eventType}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  tone = 'neutral',
  icon: Icon,
}: {
  label: string;
  value: string | number;
  tone?: 'positive' | 'negative' | 'neutral' | 'warning';
  icon: typeof TrendingUp;
}) {
  const toneClass = {
    positive: 'text-emerald-500',
    negative: 'text-rose-500',
    neutral: 'text-slate-900 dark:text-white',
    warning: 'text-amber-500',
  }[tone];

  return (
    <Card className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-white/10 dark:bg-slate-900/70">
      <div className="mb-3 flex items-center justify-between gap-3">
        <span className="text-[11px] font-bold text-slate-500">{label}</span>
        <Icon className="h-4 w-4 text-slate-400" />
      </div>
      <div className={cn('text-xl font-black tabular-nums', toneClass)}>
        {value}
      </div>
    </Card>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <Card className="rounded-lg border border-dashed border-slate-300 bg-white p-10 text-center dark:border-white/10 dark:bg-slate-900/60">
      <BarChart3 className="mx-auto mb-4 h-8 w-8 text-slate-400" />
      <div className="text-sm font-bold text-slate-700 dark:text-slate-200">
        {message}
      </div>
    </Card>
  );
}

export default function PerformanceTab({
  runId,
  runMode,
  selectedBacktestId,
  currentBacktestVersion,
  benchmarkCode,
  runStatus,
  active = true,
}: PerformanceTabProps) {
  const [timeRange, setTimeRange] = useState<TimeRange | null>(null);
  const backtestId =
    runMode === 'BACKTEST' || runMode === 'backtest'
      ? selectedBacktestId || currentBacktestVersion?.id || null
      : null;
  const [{ data, fetching, error }, reexecutePerformance] = useQuery({
    query: StrategyPerformanceQuery,
    variables: {
      runId: runId || '',
      backtestId,
      benchmarkCode: benchmarkCode || null,
      cursor: null,
      limit: 2000,
    },
    pause: !runId || !active,
    requestPolicy: 'cache-and-network',
  });

  const performance =
    (data as StrategyPerformanceResult | undefined)?.strategyPerformance ||
    null;
  const summary = asRecord(performance?.summary);
  const risk = asRecord(performance?.risk);
  const tradeStats = asRecord(performance?.tradeStats);
  const executionQuality = asRecord(performance?.executionQuality);
  const dataQuality = asRecord(performance?.dataQuality);
  const equityCurve = useMemo(
    () => downsample(mapPoints(performance?.equityCurve)),
    [performance?.equityCurve]
  );
  const drawdownCurve = useMemo(
    () => downsample(mapPoints(performance?.drawdownCurve)),
    [performance?.drawdownCurve]
  );
  const monthlyReturns = useMemo(
    () => mapMonthly(performance?.monthlyReturns),
    [performance?.monthlyReturns]
  );
  const normalizedRunStatus = String(runStatus || '').toUpperCase();
  const shouldPoll =
    Boolean(runId) &&
    active &&
    !backtestId &&
    normalizedRunStatus === 'RUNNING';

  useEffect(() => {
    if (!shouldPoll) return;
    const refresh = () => {
      if (document.visibilityState === 'visible') {
        reexecutePerformance({ requestPolicy: 'network-only' });
      }
    };
    const intervalId = window.setInterval(refresh, 5000);
    const handleVisibilityChange = () => refresh();
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [reexecutePerformance, shouldPoll]);

  useEffect(() => {
    setTimeRange(clampRange(null, equityCurve.length));
  }, [backtestId, equityCurve.length, runId]);

  const activeRange = clampRange(timeRange, equityCurve.length);
  const visibleEquityCurve = useMemo(() => {
    if (!activeRange) return [];
    return equityCurve.slice(activeRange.startIndex, activeRange.endIndex + 1);
  }, [activeRange, equityCurve]);
  const visibleSequenceStart = visibleEquityCurve[0]?.sequence ?? null;
  const visibleSequenceEnd =
    visibleEquityCurve[visibleEquityCurve.length - 1]?.sequence ?? null;
  const visibleDrawdownCurve = useMemo(() => {
    if (visibleSequenceStart === null || visibleSequenceEnd === null) return [];
    return drawdownCurve.filter(
      point =>
        point.sequence >= visibleSequenceStart &&
        point.sequence <= visibleSequenceEnd
    );
  }, [drawdownCurve, visibleSequenceEnd, visibleSequenceStart]);
  const isRangeFiltered =
    Boolean(activeRange) &&
    (activeRange!.startIndex > 0 ||
      activeRange!.endIndex < equityCurve.length - 1);

  if (!runId) {
    return <EmptyState message="请选择一个策略实例查看绩效。" />;
  }

  if (fetching && !performance) {
    return <EmptyState message="正在加载策略绩效..." />;
  }

  if (error) {
    return <EmptyState message={`绩效数据加载失败：${error.message}`} />;
  }

  if (!performance) {
    return <EmptyState message="暂无策略绩效数据。" />;
  }

  const totalReturn = num(metric(summary, 'totalReturnPct'));
  const totalPnl = num(metric(summary, 'totalPnl'));
  const versionLabel = backtestId
    ? `回测版本 v${currentBacktestVersion?.version || '-'}`
    : runMode === 'LIVE'
      ? '实盘绩效'
      : runMode === 'PAPER'
        ? '模拟盘绩效'
        : '策略绩效';
  const qualityWarning = dataQuality.warning
    ? String(dataQuality.warning)
    : null;
  const hasEquityCurve = equityCurve.length > 0;
  const summaryOnly = Boolean(performance.summaryOnly);
  const benchmarkCodeValue = performance.benchmarkCode
    ? String(performance.benchmarkCode)
    : null;
  const sourceLabel = String(performance.source || '-');
  const returnedSampleCount = num(
    metric(dataQuality, 'returnedSampleCount'),
    0
  );
  const sampleCount = num(metric(dataQuality, 'sampleCount'), 0);
  const rawSampleCount = num(
    metric(dataQuality, 'rawSampleCount'),
    sampleCount
  );
  const compressedSampleCount = num(
    metric(dataQuality, 'compressedSampleCount'),
    sampleCount
  );
  const rangeLabel =
    activeRange && equityCurve.length
      ? `${formatExactDate(equityCurve[activeRange.startIndex]?.timestamp)} - ${formatExactDate(
          equityCurve[activeRange.endIndex]?.timestamp
        )}`
      : '全量';

  const resetRange = () => {
    setTimeRange(clampRange(null, equityCurve.length));
  };

  const handleBrushChange = (range: {
    startIndex?: number;
    endIndex?: number;
  }) => {
    if (!equityCurve.length) return;
    const nextRange = clampRange(
      {
        startIndex: num(range.startIndex, 0),
        endIndex: num(range.endIndex, equityCurve.length - 1),
      },
      equityCurve.length
    );
    setTimeRange(nextRange);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white px-5 py-4 dark:border-white/10 dark:bg-slate-900/70 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-black text-slate-900 dark:text-white">
              策略绩效
            </h2>
            <Badge className="rounded-md bg-blue-500/10 text-blue-500 hover:bg-blue-500/10">
              {versionLabel}
            </Badge>
            {summaryOnly && (
              <Badge
                variant="outline"
                className="rounded-md border-amber-500/30 text-amber-500"
              >
                摘要数据
              </Badge>
            )}
            {shouldPoll && (
              <Badge
                variant="outline"
                className="rounded-md border-emerald-500/30 text-emerald-500"
              >
                实时刷新
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs font-medium text-slate-500">
            样本 {returnedSampleCount.toLocaleString('zh-CN')} /{' '}
            {sampleCount.toLocaleString('zh-CN')}
            {rawSampleCount > compressedSampleCount && (
              <>
                {' '}
                · 压缩 {compressedSampleCount.toLocaleString('zh-CN')} / 原始{' '}
                {rawSampleCount.toLocaleString('zh-CN')}
              </>
            )}{' '}
            · 来源 {sourceLabel}
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs font-bold text-slate-500">
          {qualityWarning ? (
            <AlertCircle className="h-4 w-4 text-amber-500" />
          ) : (
            <CheckCircle2 className="h-4 w-4 text-emerald-500" />
          )}
          {qualityWarning || '绩效数据正常'}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
        <MetricCard
          label="累计收益"
          value={fmtPct(metric(summary, 'totalReturnPct'))}
          tone={totalReturn >= 0 ? 'positive' : 'negative'}
          icon={TrendingUp}
        />
        <MetricCard
          label="总盈亏"
          value={fmtMoney(totalPnl)}
          tone={totalPnl >= 0 ? 'positive' : 'negative'}
          icon={LineChart}
        />
        <MetricCard
          label="最大回撤"
          value={fmtPct(-Math.abs(num(metric(summary, 'maxDrawdownPct'))))}
          tone="negative"
          icon={TrendingDown}
        />
        <MetricCard
          label="夏普比率"
          value={fmtNumber(metric(summary, 'sharpeRatio'))}
          icon={Shield}
        />
        <MetricCard
          label="胜率"
          value={fmtPct(metric(summary, 'winRatePct'))}
          tone="positive"
          icon={Target}
        />
        <MetricCard
          label="成交数"
          value={`${num(metric(summary, 'totalTrades'), 0).toLocaleString('zh-CN')} 笔`}
          icon={Activity}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
        <Card className="rounded-lg border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900/70 xl:col-span-2">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h3 className="text-sm font-black text-slate-900 dark:text-white">
                收益曲线
              </h3>
              <p className="text-xs text-slate-500">与回撤曲线共享时间范围</p>
            </div>
            {benchmarkCodeValue && (
              <Badge variant="outline" className="rounded-md">
                基准 {benchmarkCodeValue}
              </Badge>
            )}
          </div>
          <div className="h-[320px]">
            {hasEquityCurve ? (
              <ResponsiveContainer width="100%" height="100%">
                <RechartsLineChart data={visibleEquityCurve}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(148,163,184,0.18)"
                  />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={formatDate}
                    tick={{ fontSize: 11 }}
                    minTickGap={36}
                  />
                  <YAxis
                    tickFormatter={value => `${value}%`}
                    tick={{ fontSize: 11 }}
                  />
                  <Tooltip content={<PerformanceTooltip kind="return" />} />
                  <Line
                    type="monotone"
                    dataKey="value"
                    stroke="#2563eb"
                    strokeWidth={2}
                    dot={false}
                    name="策略"
                  />
                  {benchmarkCodeValue && (
                    <Line
                      type="monotone"
                      dataKey="benchmarkValue"
                      stroke="#94a3b8"
                      strokeDasharray="4 4"
                      strokeWidth={1.5}
                      dot={false}
                      name="基准"
                    />
                  )}
                </RechartsLineChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState message="暂无收益曲线样本。" />
            )}
          </div>
        </Card>

        <Card className="rounded-lg border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900/70">
          <h3 className="mb-5 text-sm font-black text-slate-900 dark:text-white">
            回撤曲线
          </h3>
          <div className="h-[320px]">
            {visibleDrawdownCurve.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={visibleDrawdownCurve}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(148,163,184,0.18)"
                  />
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={formatDate}
                    tick={{ fontSize: 11 }}
                    minTickGap={40}
                  />
                  <YAxis
                    tickFormatter={value => `-${value}%`}
                    tick={{ fontSize: 11 }}
                  />
                  <Tooltip content={<PerformanceTooltip kind="drawdown" />} />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#e11d48"
                    fill="#e11d48"
                    fillOpacity={0.18}
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState message="暂无回撤曲线样本。" />
            )}
          </div>
        </Card>

        <Card className="rounded-lg border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900/70 xl:col-span-3">
          <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <h3 className="text-sm font-black text-slate-900 dark:text-white">
                时间范围
              </h3>
              <p className="mt-1 text-xs font-medium text-slate-500">
                {rangeLabel}
              </p>
            </div>
            <button
              type="button"
              onClick={resetRange}
              disabled={!isRangeFiltered}
              className="inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border border-slate-200 px-3 text-xs font-bold text-slate-600 transition-colors hover:border-blue-300 hover:text-blue-600 disabled:cursor-not-allowed disabled:opacity-50 dark:border-white/10 dark:text-slate-300 dark:hover:border-blue-500/40 dark:hover:text-blue-300"
            >
              <RotateCcw className="h-3.5 w-3.5" />
              全量
            </button>
          </div>
          <div className="h-[96px]">
            {hasEquityCurve && activeRange ? (
              <ResponsiveContainer width="100%" height="100%">
                <RechartsLineChart
                  data={equityCurve}
                  margin={{ top: 4, right: 12, bottom: 0, left: 12 }}
                >
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={formatDate}
                    tick={{ fontSize: 10 }}
                    minTickGap={42}
                  />
                  <YAxis hide domain={['dataMin', 'dataMax']} />
                  <Line
                    type="monotone"
                    dataKey="value"
                    stroke="#2563eb"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                  <Brush
                    dataKey="timestamp"
                    height={24}
                    travellerWidth={10}
                    startIndex={activeRange.startIndex}
                    endIndex={activeRange.endIndex}
                    tickFormatter={formatDate}
                    stroke="#2563eb"
                    fill="rgba(37, 99, 235, 0.08)"
                    onChange={handleBrushChange}
                  />
                </RechartsLineChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState message="暂无可选择的时间范围。" />
            )}
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Card className="rounded-lg border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900/70">
          <h3 className="mb-5 text-sm font-black text-slate-900 dark:text-white">
            月度收益
          </h3>
          <div className="h-[260px]">
            {monthlyReturns.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={monthlyReturns}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(148,163,184,0.18)"
                  />
                  <XAxis dataKey="month" tick={{ fontSize: 11 }} />
                  <YAxis
                    tickFormatter={value => `${value}%`}
                    tick={{ fontSize: 11 }}
                  />
                  <Tooltip
                    formatter={(value: number) => [fmtPct(value), '收益']}
                  />
                  <Bar dataKey="returnPct" radius={[4, 4, 0, 0]}>
                    {monthlyReturns.map(item => (
                      <Cell
                        key={item.month}
                        fill={item.returnPct >= 0 ? '#10b981' : '#ef4444'}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <EmptyState message="暂无月度收益数据。" />
            )}
          </div>
        </Card>

        <Card className="rounded-lg border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900/70">
          <h3 className="mb-5 text-sm font-black text-slate-900 dark:text-white">
            成交与执行质量
          </h3>
          <div className="grid grid-cols-2 gap-3">
            {[
              [
                '成交笔数',
                `${num(metric(tradeStats, 'totalTrades')).toLocaleString('zh-CN')} 笔`,
              ],
              [
                '盈利/亏损',
                `${num(metric(tradeStats, 'winningTrades'))} / ${num(metric(tradeStats, 'losingTrades'))}`,
              ],
              ['利润因子', fmtNumber(metric(tradeStats, 'profitFactor'))],
              ['期望收益', fmtMoney(metric(tradeStats, 'expectancy'))],
              [
                '意图数',
                `${num(metric(executionQuality, 'intentCount')).toLocaleString('zh-CN')} 个`,
              ],
              [
                '下单数',
                `${num(metric(executionQuality, 'ordersPlaced')).toLocaleString('zh-CN')} 笔`,
              ],
              ['成交率', fmtPct(metric(executionQuality, 'fillRatePct'))],
              ['拒单率', fmtPct(metric(executionQuality, 'rejectionRatePct'))],
            ].map(([label, value]) => (
              <div
                key={label}
                className="rounded-lg border border-slate-100 bg-slate-50 p-4 dark:border-white/5 dark:bg-white/[0.03]"
              >
                <div className="text-[11px] font-bold text-slate-500">
                  {label}
                </div>
                <div className="mt-1 text-base font-black text-slate-900 dark:text-white">
                  {value}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <Card className="rounded-lg border border-slate-200 bg-white p-5 dark:border-white/10 dark:bg-slate-900/70">
        <h3 className="mb-5 text-sm font-black text-slate-900 dark:text-white">
          风险指标
        </h3>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
          {[
            ['年化收益', fmtPct(metric(risk, 'annualReturnPct'))],
            ['年化波动', fmtPct(metric(risk, 'annualVolatilityPct'))],
            ['Sortino', fmtNumber(metric(risk, 'sortinoRatio'))],
            ['Calmar', fmtNumber(metric(risk, 'calmarRatio'))],
            [
              '最大回撤',
              fmtPct(-Math.abs(num(metric(risk, 'maxDrawdownPct')))),
            ],
            [
              '回撤时长',
              `${num(metric(risk, 'maxDrawdownDurationDays'), 0)} 天`,
            ],
          ].map(([label, value]) => (
            <div
              key={label}
              className="rounded-lg bg-slate-50 p-4 dark:bg-white/[0.03]"
            >
              <div className="text-[11px] font-bold text-slate-500">
                {label}
              </div>
              <div className="mt-1 text-base font-black text-slate-900 dark:text-white">
                {value}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
