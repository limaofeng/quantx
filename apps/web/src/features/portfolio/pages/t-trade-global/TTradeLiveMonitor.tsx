import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  DatabaseBackup,
  Gauge,
  HeartPulse,
  Loader2,
  Play,
  Radio,
  RefreshCw,
  RotateCw,
  Server,
  ShieldAlert,
  ShieldCheck,
  Square,
  Waves,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import type { GraphqlWsStatus } from '@/core/graphql/ws-status';
import { cn } from '@/utils/cn';

import type { LiveMarketQuote } from '../../hooks/useRealTimeHoldings';

import {
  buildAttentionRows,
  conditionProgress,
  evaluationReasonLabel,
  type Freshness,
  type MonitorConfig,
  type MonitorHolding,
  type MonitorSession,
} from './monitoring';
import type {
  QuoteHistoryByCode,
  QuoteHistoryPoint,
} from './useLiveQuoteHistory';
import { useMarketDataHealth } from './useMarketDataHealth';
import {
  formatNumber,
  formatSignedPercent,
  formatTime,
  quoteTone,
} from './utils';

type ReadinessLike = {
  automationReady: boolean;
  preparationReady: boolean;
  status: string;
  stage: string;
  engineStatus: string;
  agentStatus: string;
  agentMode: string;
  protocolVersion: string;
  reconcileStatus: string;
  killSwitch: boolean;
  blockedReasons: readonly string[];
  preparationBlockedReasons: readonly string[];
  snapshotAt?: string | null;
  reconciliationAgeSeconds?: number | null;
  queuedCommandCount: number;
  queueDelaySeconds: number;
  deadLetterCount: number;
  unresolvedCriticalAlertCount: number;
  journalIntegrity: string;
  journalPendingReports: number;
  lastBackupAt?: string | null;
  checkedAt: string;
};

export type TTradeMonitorLike = MonitorConfig & {
  enabled: boolean;
  mode: string;
  holdingCount: number;
  eligibleCount: number;
  monitoredCount: number;
  pendingSignalCount: number;
  activeBatchCount: number;
  drainingCount: number;
  ignoredCount: number;
  lastReconciledAt?: string | null;
  lastError?: string | null;
  updatedAt?: string | null;
  projectionGeneratedAt?: string | null;
  positionSnapshotReportedAt?: string | null;
  positionSnapshotReceivedAt?: string | null;
  positionSnapshotComplete: boolean;
  positionSnapshotError?: string | null;
  rolloutStage: string;
  engineStatus: string;
  agentStatus: string;
  reconcileStatus: string;
  killSwitch: boolean;
  canActivateLive: boolean;
  blockedReasons: readonly string[];
  holdings: readonly MonitorHolding[];
  sessions: readonly MonitorSession[];
  readiness?: ReadinessLike | null;
};

function useNow() {
  const [now, setNow] = React.useState(() => new Date());
  React.useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function formatAge(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return '--';
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

function freshnessTone(freshness: Freshness) {
  if (freshness.level === 'LIVE') return 'text-emerald-300';
  if (freshness.level === 'DELAYED') return 'text-amber-300';
  if (freshness.level === 'STALE' || freshness.level === 'MISSING') {
    return 'text-rose-300';
  }
  return 'text-slate-500';
}

function FreshnessLabel({
  freshness,
  source,
}: {
  freshness: Freshness;
  source?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5',
        freshnessTone(freshness)
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          freshness.level === 'LIVE'
            ? 'bg-emerald-400'
            : freshness.level === 'DELAYED'
              ? 'bg-amber-400'
              : freshness.level === 'CLOSED'
                ? 'bg-slate-600'
                : 'bg-rose-400'
        )}
      />
      {source && <span className="text-slate-500">{source}</span>}
      {freshness.label}
      {freshness.ageSeconds != null && (
        <span className="font-mono text-slate-600">
          {Math.round(freshness.ageSeconds)}s
        </span>
      )}
    </span>
  );
}

function StatusCell({
  icon: Icon,
  label,
  tone,
  value,
}: {
  icon: React.ElementType;
  label: string;
  tone: 'amber' | 'emerald' | 'rose' | 'sky' | 'slate';
  value: React.ReactNode;
}) {
  const tones = {
    amber: 'border-amber-400/15 bg-amber-400/[0.055] text-amber-200',
    emerald: 'border-emerald-400/15 bg-emerald-400/[0.055] text-emerald-200',
    rose: 'border-rose-400/15 bg-rose-400/[0.055] text-rose-200',
    sky: 'border-sky-300/15 bg-sky-300/[0.055] text-sky-200',
    slate: 'border-white/[0.07] bg-white/[0.025] text-slate-300',
  };
  return (
    <div className={cn('min-w-0 border px-2.5 py-2', tones[tone])}>
      <div className="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-[0.1em] opacity-65">
        <Icon className="h-3 w-3 shrink-0" aria-hidden="true" />
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-1 truncate text-[11px] font-black">{value}</div>
    </div>
  );
}

export function TTradeHealthConsole({
  accountId,
  actionLoading,
  monitor,
  isCurrentTradingDay,
  onRefresh,
  onReconcile,
  onToggleMonitoring,
  quotes,
  quoteConnected,
  quoteError,
  refreshing,
  toggleDisabled,
  wsStatus,
}: {
  accountId: string;
  actionLoading: boolean;
  monitor?: TTradeMonitorLike;
  isCurrentTradingDay?: boolean;
  onRefresh: () => void;
  onReconcile: () => void;
  onToggleMonitoring: () => void;
  quotes: ReadonlyMap<string, LiveMarketQuote>;
  quoteConnected: boolean;
  quoteError?: { message?: string } | null;
  refreshing: boolean;
  toggleDisabled: boolean;
  wsStatus: GraphqlWsStatus;
}) {
  const now = useNow();
  const marketData = useMarketDataHealth();
  const readiness = monitor?.readiness;
  const rows = monitor
    ? buildAttentionRows(
        monitor.holdings,
        monitor.sessions,
        quotes,
        monitor,
        now,
        isCurrentTradingDay
      )
    : [];
  const telemetryCoverage = rows.filter(
    row => row.session?.latestEvaluation
  ).length;
  const staleCount = rows.filter(
    row =>
      ['STALE', 'MISSING'].includes(row.quoteFreshness.level) ||
      ['STALE', 'MISSING'].includes(row.heartbeatFreshness.level)
  ).length;
  const exceptionCount =
    Number(
      Boolean(
        monitor?.lastError || monitor?.positionSnapshotError || quoteError
      )
    ) +
    Number(readiness?.deadLetterCount || 0) +
    Number(readiness?.unresolvedCriticalAlertCount || 0);
  const blockedReason =
    readiness?.blockedReasons[0] ||
    readiness?.preparationBlockedReasons[0] ||
    monitor?.blockedReasons[0] ||
    monitor?.positionSnapshotError ||
    monitor?.lastError;
  const wsHealthy = wsStatus === 'connected';
  const marketDataReady = marketData.status.toLowerCase() === 'ready';
  const marketDataPending = ['checking', 'starting', 'syncing'].includes(
    marketData.status.toLowerCase()
  );
  const automaticReady = Boolean(
    (readiness?.automationReady ?? monitor?.canActivateLive) &&
    !(readiness?.killSwitch ?? monitor?.killSwitch)
  );

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[#081423] text-slate-200">
      <div className="shrink-0 border-b border-white/[0.06] px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[9px] font-black uppercase tracking-[0.24em] text-red-300">
              Live operations
            </div>
            <h1 className="mt-1 text-base font-black">健康控制台</h1>
            <div className="mt-1 font-mono text-[9px] text-slate-600">
              {accountId || '未配置账户'}
            </div>
          </div>
          <button
            type="button"
            aria-label="刷新健康控制台"
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-sm border border-white/[0.08] text-slate-500 hover:border-red-400/25 hover:text-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/60 disabled:opacity-40"
            disabled={!accountId || actionLoading}
            onClick={onRefresh}
          >
            <RefreshCw
              className={cn(
                'h-4 w-4',
                refreshing && 'animate-spin motion-reduce:animate-none'
              )}
            />
          </button>
        </div>
      </div>

      <div className="grid shrink-0 grid-cols-2 gap-2 border-b border-white/[0.06] p-3">
        <StatusCell
          icon={Radio}
          label="监控运行"
          tone={monitor?.enabled ? 'emerald' : 'slate'}
          value={monitor?.enabled ? '运行中' : '已停止'}
        />
        <StatusCell
          icon={ShieldCheck}
          label="自动交易"
          tone={automaticReady ? 'emerald' : 'amber'}
          value={automaticReady ? '已就绪' : '关闭 / 门禁中'}
        />
        <StatusCell
          icon={Gauge}
          label="执行模式"
          tone={monitor?.mode === 'live' ? 'rose' : 'slate'}
          value={monitor?.mode === 'live' ? 'LIVE 实盘' : 'PAPER 模拟'}
        />
        <StatusCell
          icon={Server}
          label="页面 GraphQL WS"
          tone={
            wsHealthy
              ? 'emerald'
              : wsStatus === 'reconnecting'
                ? 'amber'
                : 'rose'
          }
          value={wsStatus === 'connected' ? '已连接' : wsStatus}
        />
        <StatusCell
          icon={Waves}
          label="上游行情链路"
          tone={
            marketDataReady ? 'emerald' : marketDataPending ? 'amber' : 'rose'
          }
          value={
            marketDataReady
              ? `READY · #${marketData.engineSequence ?? marketData.sequence ?? 0}`
              : marketData.status.toUpperCase()
          }
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <section className="border-b border-white/[0.06] p-3">
          <div className="mb-2 flex items-center justify-between text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            <span>双流覆盖</span>
            <span>{monitor?.monitoredCount || 0} 只监控</span>
          </div>
          <div className="space-y-2 text-[10px]">
            <div className="flex items-center justify-between gap-3">
              <span className="inline-flex items-center gap-2 text-slate-400">
                <Waves className="h-3.5 w-3.5 text-cyan-400" />
                页面行情推送
              </span>
              <span
                className={
                  quoteConnected ? 'text-emerald-300' : 'text-amber-300'
                }
              >
                {quotes.size} / {monitor?.holdings.length || 0}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="inline-flex items-center gap-2 text-slate-400">
                <Server className="h-3.5 w-3.5 text-violet-400" />
                Agent → Engine
              </span>
              <span
                className={
                  marketDataReady
                    ? 'text-emerald-300'
                    : marketDataPending
                      ? 'text-amber-300'
                      : 'text-rose-300'
                }
              >
                {marketData.status.toUpperCase()} ·{' '}
                {formatAge(marketData.engineAgeSeconds)}
              </span>
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-white/[0.06]">
              <div
                className="h-full bg-cyan-400 transition-[width] duration-300 motion-reduce:transition-none"
                style={{
                  width: `${monitor?.holdings.length ? Math.min(100, (quotes.size / monitor.holdings.length) * 100) : 0}%`,
                }}
              />
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="inline-flex items-center gap-2 text-slate-400">
                <HeartPulse className="h-3.5 w-3.5 text-red-400" />
                策略心跳
              </span>
              <span
                className={
                  telemetryCoverage ? 'text-emerald-300' : 'text-amber-300'
                }
              >
                {telemetryCoverage} / {monitor?.holdings.length || 0}
              </span>
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-white/[0.06]">
              <div
                className="h-full bg-red-400 transition-[width] duration-300 motion-reduce:transition-none"
                style={{
                  width: `${monitor?.holdings.length ? Math.min(100, (telemetryCoverage / monitor.holdings.length) * 100) : 0}%`,
                }}
              />
            </div>
            <p className="pt-1 leading-4 text-slate-600">
              页面由 GraphQL WS 推送；行情年龄取 miniQMT 源 Tick
              时间，上游链路状态独立显示。
            </p>
          </div>
        </section>

        <section className="border-b border-white/[0.06] p-3">
          <div className="mb-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            账户事实
          </div>
          <div className="space-y-2 text-[10px]">
            <div className="flex items-center justify-between gap-3">
              <span className="text-slate-500">持仓快照</span>
              <span className="text-right text-slate-300">
                {formatTime(
                  monitor?.positionSnapshotReceivedAt ||
                    monitor?.positionSnapshotReportedAt
                )}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-slate-500">监控投影</span>
              <span className="text-right text-slate-300">
                {formatTime(monitor?.projectionGeneratedAt)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-slate-500">对账年龄</span>
              <span className="font-mono text-slate-300">
                {formatAge(readiness?.reconciliationAgeSeconds)}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="text-slate-500">最近备份</span>
              <span className="text-right text-slate-300">
                {formatTime(readiness?.lastBackupAt)}
              </span>
            </div>
          </div>
        </section>

        <section className="border-b border-white/[0.06] p-3">
          <div className="mb-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            需要关注
          </div>
          <div className="grid grid-cols-2 gap-2">
            <StatusCell
              icon={Activity}
              label="待确认"
              tone={monitor?.pendingSignalCount ? 'amber' : 'slate'}
              value={monitor?.pendingSignalCount || 0}
            />
            <StatusCell
              icon={Radio}
              label="活跃批次"
              tone={monitor?.activeBatchCount ? 'emerald' : 'slate'}
              value={monitor?.activeBatchCount || 0}
            />
            <StatusCell
              icon={Clock3}
              label="陈旧数据"
              tone={staleCount ? 'rose' : 'slate'}
              value={staleCount}
            />
            <StatusCell
              icon={AlertTriangle}
              label="异常"
              tone={exceptionCount ? 'rose' : 'slate'}
              value={exceptionCount}
            />
          </div>
        </section>

        <section className="p-3">
          <div className="mb-2 flex items-center gap-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            {blockedReason ? (
              <ShieldAlert className="h-3.5 w-3.5 text-amber-300" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
            )}
            首要门禁
          </div>
          <p
            className={cn(
              'text-[10px] leading-4',
              blockedReason ? 'text-amber-100' : 'text-emerald-200'
            )}
          >
            {blockedReason || '当前没有阻断项'}
          </p>
          {readiness && (
            <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-white/[0.05] pt-3 font-mono text-[9px] text-slate-600">
              <span>队列 {readiness.queuedCommandCount}</span>
              <span>延迟 {formatAge(readiness.queueDelaySeconds)}</span>
              <span>死信 {readiness.deadLetterCount}</span>
              <span>待报 {readiness.journalPendingReports}</span>
              <span>Engine {readiness.engineStatus}</span>
              <span>Agent {readiness.agentStatus}</span>
            </div>
          )}
          {!readiness && monitor && (
            <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-white/[0.05] pt-3 font-mono text-[9px] text-slate-600">
              <span>Stage {monitor.rolloutStage}</span>
              <span>对账 {monitor.reconcileStatus}</span>
              <span>Engine {monitor.engineStatus}</span>
              <span>Agent {monitor.agentStatus}</span>
            </div>
          )}
        </section>
      </div>

      <div className="shrink-0 space-y-2 border-t border-white/[0.06] bg-[#07111f] p-3">
        <div className="grid grid-cols-2 gap-2">
          <Button
            type="button"
            variant="outline"
            className="h-8 cursor-pointer rounded-sm border-white/10 text-[10px] text-slate-300"
            disabled={!accountId || actionLoading}
            onClick={onRefresh}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            刷新
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 cursor-pointer rounded-sm border-white/10 text-[10px] text-slate-300"
            disabled={!accountId || actionLoading}
            onClick={onReconcile}
          >
            <RotateCw className="mr-1.5 h-3.5 w-3.5" />
            同步持仓
          </Button>
        </div>
        <Button
          type="button"
          className={cn(
            'h-9 w-full cursor-pointer rounded-sm text-[10px] font-black',
            monitor?.enabled
              ? 'bg-slate-700 text-white hover:bg-slate-600'
              : 'bg-red-500 text-white hover:bg-red-400'
          )}
          disabled={!accountId || actionLoading || toggleDisabled}
          onClick={onToggleMonitoring}
        >
          {actionLoading ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
          ) : monitor?.enabled ? (
            <Square className="mr-1.5 h-3 w-3" />
          ) : (
            <Play className="mr-1.5 h-3.5 w-3.5" />
          )}
          {monitor?.enabled ? '停止监控' : '启动监控'}
        </Button>
      </div>
    </aside>
  );
}

function Sparkline({
  className,
  label,
  points,
}: {
  className?: string;
  label: string;
  points: readonly QuoteHistoryPoint[];
}) {
  const width = 240;
  const height = 64;
  if (points.length < 2) {
    return (
      <div
        className={cn(
          'flex items-center justify-center text-[9px] text-slate-700',
          className
        )}
        role="img"
        aria-label={`${label}：等待实时行情样本`}
      >
        等待走势
      </div>
    );
  }
  const prices = points.map(point => point.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = Math.max(max - min, Math.abs(max) * 0.0005, 0.001);
  const path = points
    .map((point, index) => {
      const x = (index / Math.max(1, points.length - 1)) * width;
      const y = height - ((point.price - min) / range) * (height - 8) - 4;
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const positive = points.at(-1)!.price >= points[0].price;
  return (
    <svg
      className={cn('overflow-visible', className)}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`${label}：2 分钟价格走势，共 ${points.length} 个样本`}
    >
      <title>{label} 2 分钟实时走势</title>
      <line
        x1="0"
        y1={height / 2}
        x2={width}
        y2={height / 2}
        stroke="rgba(148,163,184,0.12)"
        strokeDasharray="3 4"
      />
      <path
        d={path}
        fill="none"
        stroke={positive ? '#f87171' : '#34d399'}
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function InspectorMetric({
  label,
  threshold,
  value,
}: {
  label: string;
  threshold?: string;
  value: string;
}) {
  return (
    <div className="border-b border-white/[0.05] py-2.5 last:border-b-0">
      <div className="flex items-center justify-between gap-4">
        <span className="text-[10px] text-slate-500">{label}</span>
        <span className="font-mono text-xs font-bold text-slate-100">
          {value}
        </span>
      </div>
      {threshold && (
        <div className="mt-1 text-right text-[9px] text-slate-600">
          阈值 {threshold}
        </div>
      )}
    </div>
  );
}

function TTradeInspector({
  config,
  holding,
  history,
  onIgnore,
  quote,
  session,
}: {
  config: MonitorConfig;
  holding: MonitorHolding;
  history: readonly QuoteHistoryPoint[];
  onIgnore?: (stockCode: string, ignored: boolean) => void;
  quote?: LiveMarketQuote;
  session?: MonitorSession | null;
}) {
  const evaluation = session?.latestEvaluation;
  const progress = conditionProgress(evaluation, config);
  const metric = (value?: number | null, digits = 2, suffix = '') =>
    value == null ? '--' : `${formatNumber(value, digits)}${suffix}`;
  return (
    <div className="mt-5 space-y-5">
      <section className="border border-white/[0.07] bg-white/[0.02] p-3">
        <div className="flex items-end justify-between gap-3">
          <div>
            <div className="text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
              实时行情流
            </div>
            <div
              className={cn(
                'mt-1 font-mono text-2xl font-black',
                quoteTone(quote?.changePercent)
              )}
            >
              {quote ? formatNumber(quote.currentPrice, 3) : '--'}
            </div>
          </div>
          <div
            className={cn(
              'font-mono text-sm font-bold',
              quoteTone(quote?.changePercent)
            )}
          >
            {formatSignedPercent(quote?.changePercent)}
          </div>
        </div>
        <Sparkline
          className="mt-4 h-28 w-full"
          label={holding.stockCode}
          points={history}
        />
        <div className="mt-2 flex justify-between font-mono text-[9px] text-slate-600">
          <span>2 分钟</span>
          <span>{formatTime(quote?.time)}</span>
        </div>
      </section>

      <section>
        <div className="flex items-center justify-between">
          <h3 className="text-xs font-black text-slate-200">策略评估</h3>
          <span className="border border-red-400/20 bg-red-400/[0.07] px-2 py-1 font-mono text-[9px] text-red-200">
            {evaluation?.phase || 'WAITING_FIRST_TICK'}
          </span>
        </div>
        <p className="mt-2 text-[11px] text-slate-400">
          {evaluationReasonLabel(evaluation?.reason)}
        </p>
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
          <div
            className="h-full bg-red-400 transition-[width] motion-reduce:transition-none"
            style={{ width: `${Math.round(progress * 100)}%` }}
          />
        </div>
        <div className="mt-1 text-right font-mono text-[9px] text-slate-600">
          条件进度 {Math.round(progress * 100)}%
        </div>
        <div className="mt-3 border-y border-white/[0.06]">
          <InspectorMetric
            label="回撤"
            value={metric(evaluation?.pullbackPct, 2, '%')}
            threshold={`≥ ${formatNumber(config.pullbackThresholdPct)}%`}
          />
          <InspectorMetric
            label="反弹"
            value={metric(evaluation?.reboundPct, 2, '%')}
            threshold={`≥ ${formatNumber(config.reboundThresholdPct)}%`}
          />
          <InspectorMetric
            label="动量涨幅"
            value={metric(evaluation?.momentumRisePct, 2, '%')}
            threshold={`≥ ${formatNumber(config.momentumMinRisePct)}%`}
          />
          <InspectorMetric
            label="动量持续"
            value={metric(evaluation?.momentumMoveSeconds, 0, ' 秒')}
            threshold={`≥ ${config.momentumMinMoveSeconds} 秒`}
          />
          <InspectorMetric
            label="成交加速比"
            value={metric(evaluation?.momentumAmountVelocityRatio, 2, 'x')}
            threshold={`≥ ${formatNumber(config.momentumMinAmountVelocityRatio)}x`}
          />
          <InspectorMetric
            label="基线覆盖"
            value={metric(
              evaluation?.momentumBaselineCoverageSeconds,
              0,
              ' 秒'
            )}
            threshold={`${config.momentumBaselineSeconds} 秒`}
          />
          <InspectorMetric
            label="VWAP / 溢价"
            value={`${metric(evaluation?.vwap, 3)} / ${metric(evaluation?.vwapPremiumPct, 2, '%')}`}
            threshold={`${formatNumber(config.momentumMinVwapPremiumPct)}%–${formatNumber(config.momentumMaxVwapPremiumPct)}%`}
          />
          <InspectorMetric
            label="价差"
            value={`${metric(evaluation?.spreadTicks, 1, ' Tick')} / ${metric(evaluation?.spreadPct, 3, '%')}`}
            threshold={`回撤 ≤ ${config.maxSpreadTicks} Tick`}
          />
        </div>
      </section>

      <section>
        <h3 className="text-xs font-black text-slate-200">持仓与批次</h3>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <StatusCell
            icon={DatabaseBackup}
            label="持仓 / 可用"
            tone="slate"
            value={`${holding.volume.toLocaleString()} / ${holding.availableVolume.toLocaleString()}`}
          />
          <StatusCell
            icon={Activity}
            label="活跃仓"
            tone={session?.activeVolume ? 'emerald' : 'slate'}
            value={session?.activeVolume?.toLocaleString() || 0}
          />
          <StatusCell
            icon={Gauge}
            label="批次盈亏"
            tone={(session?.lastNetProfitPct || 0) >= 0 ? 'rose' : 'sky'}
            value={
              session?.activeVolume
                ? `${formatNumber(session.lastNetProfitPct)}%`
                : '--'
            }
          />
          <StatusCell
            icon={CheckCircle2}
            label="完成次数"
            tone="slate"
            value={session?.completedCycles || 0}
          />
        </div>
        <div className="mt-3 border-y border-white/[0.06]">
          <InspectorMetric
            label="策略状态"
            value={session?.status || holding.status}
          />
          <InspectorMetric
            label="买入委托"
            value={session?.entryOrderStatus || '--'}
          />
          <InspectorMetric
            label="卖出委托"
            value={session?.exitOrderStatus || '--'}
          />
          <InspectorMetric
            label="买入均价 / 成交"
            value={`${metric(session?.entryAvgPrice, 3)} / ${(session?.entryFilledVolume || 0).toLocaleString()} 股`}
          />
          <InspectorMetric
            label="卖出均价 / 成交"
            value={`${metric(session?.exitAvgPrice, 3)} / ${(session?.exitFilledVolume || 0).toLocaleString()} 股`}
          />
        </div>
      </section>

      {onIgnore && (
        <Button
          type="button"
          variant="outline"
          className="h-9 w-full cursor-pointer rounded-sm border-white/10 text-[10px] text-slate-300"
          onClick={() => onIgnore(holding.stockCode, !holding.ignored)}
        >
          {holding.ignored ? '恢复该标的监控' : '忽略该标的'}
        </Button>
      )}
    </div>
  );
}

export function TTradeLiveBoard({
  historyByCode,
  loading,
  monitor,
  isCurrentTradingDay,
  onIgnore,
  quotes,
}: {
  historyByCode: QuoteHistoryByCode;
  loading: boolean;
  monitor?: TTradeMonitorLike;
  isCurrentTradingDay?: boolean;
  onIgnore?: (stockCode: string, ignored: boolean) => void;
  quotes: ReadonlyMap<string, LiveMarketQuote>;
}) {
  const now = useNow();
  const [selectedCode, setSelectedCode] = React.useState<string | null>(null);
  const lastSelectedCodeRef = React.useRef<string | null>(null);
  const rowButtonRefs = React.useRef(new Map<string, HTMLButtonElement>());
  const rows = React.useMemo(
    () =>
      monitor
        ? buildAttentionRows(
            monitor.holdings,
            monitor.sessions,
            quotes,
            monitor,
            now,
            isCurrentTradingDay
          )
        : [],
    [isCurrentTradingDay, monitor, now, quotes]
  );
  const selected = rows.find(row => row.holding.stockCode === selectedCode);

  return (
    <>
      <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
        <table className="w-full min-w-[1120px] text-left text-xs">
          <thead className="sticky top-0 z-10 bg-[#0b1628] text-[9px] font-black uppercase tracking-[0.1em] text-slate-600 shadow-[0_1px_0_rgba(255,255,255,0.05)]">
            <tr>
              <th className="px-4 py-2.5">标的 / 持仓</th>
              <th className="px-3 py-2.5 text-right">实时行情</th>
              <th className="px-3 py-2.5">2 分钟走势</th>
              <th className="px-3 py-2.5">策略心跳</th>
              <th className="px-3 py-2.5">最近判断</th>
              <th className="px-4 py-2.5">T 批次状态</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => {
              const evaluation = row.session?.latestEvaluation;
              const history = historyByCode.get(row.holding.stockCode) || [];
              return (
                <tr
                  key={row.holding.stockCode}
                  className="border-b border-white/[0.045] transition-colors hover:bg-white/[0.03] focus-within:bg-white/[0.03] motion-reduce:transition-none"
                >
                  <td className="p-0">
                    <button
                      type="button"
                      className="block w-full cursor-pointer px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-red-500/60"
                      onClick={() => {
                        lastSelectedCodeRef.current = row.holding.stockCode;
                        setSelectedCode(row.holding.stockCode);
                      }}
                      ref={element => {
                        if (element) {
                          rowButtonRefs.current.set(
                            row.holding.stockCode,
                            element
                          );
                        } else {
                          rowButtonRefs.current.delete(row.holding.stockCode);
                        }
                      }}
                      aria-label={`检查 ${row.holding.instrumentName || row.holding.stockCode}`}
                    >
                      <div className="font-black text-slate-100">
                        {row.holding.instrumentName || row.holding.stockCode}
                      </div>
                      <div className="mt-0.5 flex items-center gap-2 font-mono text-[9px] text-slate-600">
                        <span>{row.holding.stockCode}</span>
                        <span>
                          {row.holding.volume.toLocaleString()} /{' '}
                          {row.holding.availableVolume.toLocaleString()} 股
                        </span>
                      </div>
                    </button>
                  </td>
                  <td className="px-3 py-3 text-right">
                    <div
                      className={cn(
                        'font-mono text-sm font-black tabular-nums',
                        quoteTone(row.quote?.changePercent)
                      )}
                    >
                      {row.quote
                        ? formatNumber(row.quote.currentPrice, 3)
                        : '--'}
                    </div>
                    <div
                      className={cn(
                        'mt-0.5 font-mono text-[10px]',
                        quoteTone(row.quote?.changePercent)
                      )}
                    >
                      {formatSignedPercent(row.quote?.changePercent)}
                    </div>
                    <div className="mt-1 text-[9px]">
                      <FreshnessLabel
                        freshness={row.quoteFreshness}
                        source="源 Tick"
                      />
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <Sparkline
                      className="h-10 w-36"
                      label={row.holding.stockCode}
                      points={history}
                    />
                    <div className="mt-1 font-mono text-[9px] text-slate-700">
                      {history.length} / 120 点
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <div className="text-[10px] font-bold text-slate-300">
                      {evaluation?.phase || '等待首个有效 Tick'}
                    </div>
                    <div className="mt-1 text-[9px]">
                      <FreshnessLabel freshness={row.heartbeatFreshness} />
                    </div>
                    <div className="mt-1 font-mono text-[9px] text-slate-700">
                      Tick #
                      {evaluation?.processedTickCount?.toLocaleString() || '--'}{' '}
                      · 窗口 {evaluation?.windowSampleCount || 0}
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <div
                      className={cn(
                        'max-w-[220px] truncate text-[10px] font-bold',
                        evaluation?.triggered
                          ? 'text-amber-200'
                          : 'text-slate-300'
                      )}
                      title={evaluationReasonLabel(evaluation?.reason)}
                    >
                      {evaluationReasonLabel(evaluation?.reason)}
                    </div>
                    <div className="mt-2 flex items-center gap-2">
                      <div className="h-1 w-24 overflow-hidden rounded-full bg-white/[0.06]">
                        <div
                          className="h-full bg-red-400"
                          style={{
                            width: `${Math.round(row.conditionProgress * 100)}%`,
                          }}
                        />
                      </div>
                      <span className="font-mono text-[9px] text-slate-600">
                        {Math.round(row.conditionProgress * 100)}%
                      </span>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="text-[10px] font-black text-slate-300">
                      {row.session?.status || row.holding.status}
                    </div>
                    <div className="mt-1 font-mono text-[9px] text-slate-600">
                      活跃 {row.session?.activeVolume?.toLocaleString() || 0} ·
                      完成 {row.session?.completedCycles || 0}
                    </div>
                    {(row.session?.pendingEntryIntentId ||
                      row.session?.pendingExitIntentId) && (
                      <div className="mt-1 text-[9px] font-bold text-amber-300">
                        等待订单确认 / 回报
                      </div>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {!loading && rows.length === 0 && (
          <div className="flex min-h-64 flex-col items-center justify-center text-center">
            <Radio className="h-10 w-10 text-slate-800" />
            <div className="mt-3 text-sm font-bold text-slate-500">
              暂无作战台标的
            </div>
            <div className="mt-1 text-[10px] text-slate-700">
              同步账户持仓后自动生成监控范围
            </div>
          </div>
        )}
        {loading && rows.length === 0 && (
          <div className="flex min-h-64 items-center justify-center gap-2 text-xs text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            加载监控投影
          </div>
        )}
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1 border-t border-white/[0.05] bg-[#07111f] px-4 py-2 text-[9px] text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <Waves className="h-3 w-3 text-cyan-400" />
          页面 GraphQL WS 推送
        </span>
        <span>行情年龄为 miniQMT 源 Tick 时间，不等同于链路传输延迟</span>
        <span className="inline-flex items-center gap-1.5">
          <HeartPulse className="h-3 w-3 text-red-400" />
          策略投影约 10 秒更新
        </span>
        <span>排序：异常 → 待确认 → 陈旧 → 活跃批次 → 条件接近</span>
      </div>

      <Sheet
        open={Boolean(selected)}
        onOpenChange={open => !open && setSelectedCode(null)}
      >
        <SheetContent
          side="right"
          className="w-[92vw] overflow-y-auto border-white/[0.08] bg-[#081321] p-5 text-slate-100 motion-reduce:animate-none motion-reduce:transition-none sm:max-w-[540px] custom-scrollbar"
          onCloseAutoFocus={event => {
            event.preventDefault();
            const code = lastSelectedCodeRef.current;
            if (code) rowButtonRefs.current.get(code)?.focus();
          }}
        >
          {selected && monitor && (
            <>
              <SheetHeader className="border-b border-white/[0.06] pb-4 pr-8 text-left">
                <SheetTitle className="text-lg font-black text-slate-100">
                  {selected.holding.instrumentName ||
                    selected.holding.stockCode}
                </SheetTitle>
                <SheetDescription className="font-mono text-[10px] text-slate-500">
                  {selected.holding.stockCode} · 策略与批次检查器
                </SheetDescription>
              </SheetHeader>
              <TTradeInspector
                config={monitor}
                holding={selected.holding}
                history={historyByCode.get(selected.holding.stockCode) || []}
                onIgnore={onIgnore}
                quote={selected.quote}
                session={selected.session}
              />
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
