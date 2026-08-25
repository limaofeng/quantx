import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Gauge,
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
  XCircle,
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
  CANDIDATE_STATUS_VALUES,
  DATA_HEALTH_VALUES,
  isKnownSignalSnapshot,
  MOMENTUM_PHASE_VALUES,
  PULLBACK_PHASE_VALUES,
  type MonitorHolding,
  type MonitorSession,
  type SignalSnapshot,
} from './monitoring';
import type {
  QuoteHistoryByCode,
  QuoteHistoryPoint,
} from './useLiveQuoteHistory';
import { useMarketDataHealth } from './useMarketDataHealth';
import { formatNumber, formatTime } from './utils';

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

export type TTradeMonitorLike = {
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

export type SignalEvaluationLike = {
  id: string;
  accountId: string;
  runId: string;
  stockCode: string;
  eventKind: string;
  eventType: string;
  evaluatedAt: string;
  coalescedCount: number;
  policyVersion: string;
  signalSnapshot?: SignalSnapshot | null;
};

const healthLabels: Record<string, string> = {
  WARMING: '重热中',
  READY: '数据就绪',
  DEGRADED: '数据降级',
  STALE: '数据陈旧',
  CONTINUITY_LOST: '连续性中断',
  INSUFFICIENT: '数据不足',
};

const candidateLabels: Record<string, string> = {
  NONE: '无候选',
  LATCHED: '候选已锁存',
  AWAITING_APPROVAL: '等待人工确认',
  SUPPRESSED: '候选已抑制',
  REARMING: '等待再武装',
};

const pathLabels: Record<string, string> = {
  PULLBACK_REBOUND: '回撤反弹',
  MOMENTUM_ACCELERATION: '早期动量',
};

const phaseLabels: Record<string, string> = {
  NONE: '暂无主导形态',
  OBSERVING: '观察中',
  PULLBACK_FORMING: '回撤形成',
  LOW_STABILIZING: '低点企稳',
  REBOUND_CONFIRMING: '反弹确认',
  BASELINING: '建立基线',
  MOMENTUM_BUILDING: '动量形成',
  ACCELERATING: '加速确认',
  OVEREXTENDED: '过度延伸',
  CANDIDATE_LATCHED: '候选锁存',
  SUPPRESSED: '已抑制',
  PULLBACK_OBSERVING: '回撤 · 观察',
  PULLBACK_LOW_STABILIZING: '回撤 · 低点企稳',
  PULLBACK_REBOUND_CONFIRMING: '回撤 · 反弹确认',
  PULLBACK_CANDIDATE_LATCHED: '回撤 · 候选锁存',
  PULLBACK_SUPPRESSED: '回撤 · 已抑制',
  MOMENTUM_OBSERVING: '动量 · 观察',
  MOMENTUM_BASELINING: '动量 · 建立基线',
  MOMENTUM_ACCELERATING: '动量 · 加速确认',
  MOMENTUM_OVEREXTENDED: '动量 · 过度延伸',
  MOMENTUM_CANDIDATE_LATCHED: '动量 · 候选锁存',
  MOMENTUM_SUPPRESSED: '动量 · 已抑制',
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
  if (seconds == null || !Number.isFinite(seconds)) return '不可计算';
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

function nullableNumber(value?: number | null, digits = 2, suffix = '') {
  return value == null || !Number.isFinite(value)
    ? '不可计算'
    : `${formatNumber(value, digits)}${suffix}`;
}

function thresholdNumber(value?: number | null) {
  return value == null || !Number.isFinite(value)
    ? '不可用'
    : formatNumber(value, 1);
}

function scoreLabel(snapshot?: SignalSnapshot | null) {
  return snapshot?.opportunityScore == null
    ? `不可计算 / ${nullableNumber(snapshot?.candidateThreshold, 0)}`
    : `${formatNumber(snapshot.opportunityScore, 1)} / ${formatNumber(snapshot.candidateThreshold, 1)}`;
}

function scoreTone(snapshot?: SignalSnapshot | null) {
  if (snapshot?.opportunityScore == null) return 'text-slate-500';
  if (snapshot.opportunityScore >= snapshot.candidateThreshold)
    return 'text-emerald-300';
  if (snapshot.opportunityScore >= snapshot.previewThreshold)
    return 'text-amber-300';
  return 'text-slate-300';
}

function healthTone(health?: string) {
  if (health === 'READY')
    return 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200';
  if (health === 'WARMING' || health === 'DEGRADED')
    return 'border-amber-400/25 bg-amber-400/10 text-amber-200';
  return 'border-rose-400/25 bg-rose-400/10 text-rose-200';
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
  loading = false,
  monitor,
  onRefresh,
  onReconcile,
  onToggleMonitoring,
  quotes,
  quoteConnected,
  quoteError,
  refreshing,
  snapshotTrusted = false,
  toggleDisabled,
  wsStatus,
}: {
  accountId: string;
  actionLoading: boolean;
  loading?: boolean;
  monitor?: TTradeMonitorLike;
  isCurrentTradingDay?: boolean;
  onRefresh: () => void;
  onReconcile: () => void;
  onToggleMonitoring: () => void;
  quotes: ReadonlyMap<string, LiveMarketQuote>;
  quoteConnected: boolean;
  quoteError?: { message?: string } | null;
  refreshing: boolean;
  snapshotTrusted?: boolean;
  toggleDisabled: boolean;
  wsStatus: GraphqlWsStatus;
}) {
  const marketData = useMarketDataHealth();
  const readiness = monitor?.readiness;
  const snapshots = (monitor?.sessions || [])
    .map(session => session.signalSnapshot)
    .filter((snapshot): snapshot is SignalSnapshot => Boolean(snapshot));
  const healthCounts = new Map<string, number>();
  for (const snapshot of snapshots) {
    healthCounts.set(
      snapshot.dataHealth,
      (healthCounts.get(snapshot.dataHealth) || 0) + 1
    );
  }
  const readyCount = healthCounts.get('READY') || 0;
  const hasSignalSnapshots = snapshots.length > 0;
  const failClosedCount = snapshots.length - readyCount;
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
  const automaticReady = Boolean(
    (readiness?.automationReady ?? monitor?.canActivateLive) &&
    !(readiness?.killSwitch ?? monitor?.killSwitch)
  );

  return (
    <aside className="studio-workspace-surface flex h-full min-h-0 flex-col text-slate-200">
      <div className="shrink-0 border-b border-white/[0.06] px-4 py-3.5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[9px] font-black uppercase tracking-[0.24em] text-blue-300">
              Stateful opportunity V3
            </div>
            <h1 className="mt-1 text-base font-black">健康控制台</h1>
            <div className="mt-1 font-mono text-[9px] text-slate-600">
              {accountId || '未配置账户'}
            </div>
          </div>
          <button
            type="button"
            aria-label="刷新健康控制台"
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-sm border border-white/[0.08] text-slate-500 hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 disabled:opacity-40"
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

      <div
        aria-atomic="true"
        aria-live="polite"
        className="grid shrink-0 grid-cols-2 gap-2 border-b border-white/[0.06] p-3"
      >
        <StatusCell
          icon={Radio}
          label="监控运行"
          tone={monitor?.enabled ? 'emerald' : 'slate'}
          value={monitor?.enabled ? '运行中' : '已停止'}
        />
        <StatusCell
          icon={ShieldCheck}
          label="自动执行"
          tone={automaticReady ? 'emerald' : 'sky'}
          value={automaticReady ? '门禁已通过' : '安全关闭'}
        />
        <StatusCell
          icon={Gauge}
          label="信号快照 READY"
          tone={
            !hasSignalSnapshots
              ? 'slate'
              : failClosedCount
                ? 'amber'
                : 'emerald'
          }
          value={
            hasSignalSnapshots
              ? `${readyCount} / ${snapshots.length}`
              : '等待快照'
          }
        />
        <StatusCell
          icon={Server}
          label="订阅 / 真源"
          tone={
            wsStatus === 'connected' && snapshotTrusted ? 'emerald' : 'amber'
          }
          value={
            <span className="block leading-4">
              订阅{wsStatus === 'connected' ? '已连接' : wsStatus} · 查询
              {snapshotTrusted ? '已复核' : '待复核'}
            </span>
          }
        />
      </div>

      <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
        {loading && !monitor && (
          <div
            role="status"
            aria-busy="true"
            className="flex items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-3 py-3 text-[10px] text-cyan-100"
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            正在读取账户健康快照…
          </div>
        )}
        {!loading && !monitor && !accountId && (
          <div
            role="status"
            className="border-b border-amber-400/15 bg-amber-400/[0.04] px-3 py-3 text-[10px] leading-4 text-amber-100"
          >
            尚未配置交易账户，健康控制台处于只读等待状态。
          </div>
        )}
        {!loading && !monitor && accountId && (
          <div
            role="status"
            className="border-b border-amber-400/15 bg-amber-400/[0.04] px-3 py-3 text-[10px] leading-4 text-amber-100"
          >
            当前账户尚未返回健康快照；控制台保持安全关闭，请刷新或先完成持仓同步。
          </div>
        )}
        <section className="border-b border-white/[0.06] p-3">
          <div className="mb-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            服务端数据健康
          </div>
          <div className="grid grid-cols-2 gap-2">
            {DATA_HEALTH_VALUES.map(health => (
              <div
                key={health}
                className="flex items-center justify-between border border-white/[0.06] px-2 py-1.5 text-[9px]"
              >
                <span
                  className={
                    health === 'READY' ? 'text-emerald-300' : 'text-slate-500'
                  }
                >
                  {healthLabels[health]}
                </span>
                <span className="font-mono text-slate-300">
                  {healthCounts.get(health) || 0}
                </span>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[9px] leading-4 text-slate-600">
            READY 与阻断原因完全来自服务端信号快照；页面行情仅用于价格展示。
          </p>
        </section>

        <section className="border-b border-white/[0.06] p-3">
          <div className="mb-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            链路与投影
          </div>
          <div className="space-y-2 text-[10px]">
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center gap-2 text-slate-500">
                <Waves className="h-3.5 w-3.5 text-cyan-400" />
                页面行情
              </span>
              <span
                className={
                  quoteConnected ? 'text-emerald-300' : 'text-amber-300'
                }
              >
                {quotes.size} 只
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center gap-2 text-slate-500">
                <Server className="h-3.5 w-3.5 text-violet-400" />
                上游行情
              </span>
              <span className="text-slate-300">
                {marketData.status.toUpperCase()}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-500">持仓快照</span>
              <span className="text-slate-300">
                {formatTime(
                  monitor?.positionSnapshotReceivedAt ||
                    monitor?.positionSnapshotReportedAt
                )}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-500">监控投影</span>
              <span className="text-slate-300">
                {formatTime(monitor?.projectionGeneratedAt)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-slate-500">对账年龄</span>
              <span className="font-mono text-slate-300">
                {formatAge(readiness?.reconciliationAgeSeconds)}
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
              icon={ShieldAlert}
              label="非 READY"
              tone={failClosedCount ? 'rose' : 'slate'}
              value={failClosedCount}
            />
            <StatusCell
              icon={AlertTriangle}
              label="运行异常"
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
            外部发意图门禁
          </div>
          <p
            className={cn(
              'text-[10px] leading-4',
              blockedReason ? 'text-amber-100' : 'text-emerald-200'
            )}
          >
            {blockedReason || '当前没有账户级阻断项'}
          </p>
        </section>
      </div>

      <div className="shrink-0 space-y-2 border-t border-white/[0.06] bg-[#07111f] p-3">
        <div className="grid grid-cols-2 gap-2">
          <Button
            type="button"
            variant="outline"
            className="h-8 rounded-sm border-white/10 text-[10px]"
            disabled={!accountId || actionLoading}
            onClick={onRefresh}
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            刷新
          </Button>
          <Button
            type="button"
            variant="outline"
            className="h-8 rounded-sm border-white/10 text-[10px]"
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
            'h-9 w-full rounded-sm text-[10px] font-black',
            monitor?.enabled
              ? 'bg-slate-700 text-white hover:bg-slate-600'
              : 'bg-primary text-primary-foreground hover:bg-primary/90'
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

function PriceSparkline({ points }: { points: readonly QuoteHistoryPoint[] }) {
  if (points.length < 2) {
    return (
      <div
        className="flex h-16 items-center justify-center text-[9px] text-slate-600"
        role="img"
        aria-label="价格走势：样本不足"
      >
        等待价格样本
      </div>
    );
  }
  const prices = points.map(point => point.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = max - min || 1;
  const polyline = points
    .map(
      (point, index) =>
        `${(index / (points.length - 1)) * 100},${40 - ((point.price - min) / span) * 36}`
    )
    .join(' ');
  return (
    <div>
      <svg
        className="h-16 w-full"
        viewBox="0 0 100 44"
        preserveAspectRatio="none"
        role="img"
        aria-label={`价格走势，最低 ${formatNumber(min, 3)}，最高 ${formatNumber(max, 3)}`}
      >
        <polyline
          fill="none"
          stroke="rgb(34 211 238)"
          strokeWidth="1.2"
          vectorEffect="non-scaling-stroke"
          points={polyline}
        />
      </svg>
      <div className="flex justify-between font-mono text-[9px] text-slate-600">
        <span>低 {formatNumber(min, 3)}</span>
        <span>高 {formatNumber(max, 3)}</span>
      </div>
    </div>
  );
}

type SignalTrendMetric = {
  key: 'score' | 'preview' | 'candidate' | 'revalidate' | 'rearm';
  label: string;
  color: string;
  dash?: string;
  value: (snapshot: SignalSnapshot) => number | null | undefined;
};

type SignalTrendSegment = {
  id: string;
  generation: string;
  metric: SignalTrendMetric;
  points: Array<{ generation: string; index: number; value: number }>;
};

const signalTrendMetrics: readonly SignalTrendMetric[] = [
  {
    key: 'score',
    label: '机会分',
    color: 'rgb(34 211 238)',
    value: snapshot => snapshot.opportunityScore,
  },
  {
    key: 'preview',
    label: '重点观察',
    color: 'rgb(250 204 21)',
    dash: '2 2',
    value: snapshot => snapshot.previewThreshold,
  },
  {
    key: 'candidate',
    label: '候选锁存',
    color: 'rgb(251 146 60)',
    dash: '4 2',
    value: snapshot => snapshot.candidateThreshold,
  },
  {
    key: 'revalidate',
    label: '确认重验',
    color: 'rgb(244 114 182)',
    dash: '6 2',
    value: snapshot => snapshot.revalidateThreshold,
  },
  {
    key: 'rearm',
    label: '再武装',
    color: 'rgb(167 139 250)',
    dash: '1 2',
    value: snapshot => snapshot.rearmThreshold,
  },
];

function compareSignalIdentity(left: SignalSnapshot, right: SignalSnapshot) {
  for (const [leftValue, rightValue] of [
    [left.continuityGeneration, right.continuityGeneration],
    [left.sourceTimeMs, right.sourceTimeMs],
    [left.tickOrdinal, right.tickOrdinal],
  ]) {
    const leftNumber = Number(leftValue);
    const rightNumber = Number(rightValue);
    const comparison =
      Number.isFinite(leftNumber) && Number.isFinite(rightNumber)
        ? leftNumber - rightNumber
        : leftValue.localeCompare(rightValue);
    if (comparison !== 0) return comparison;
  }
  return 0;
}

function signalIdentity(snapshot: SignalSnapshot) {
  return `${snapshot.continuityGeneration}:${snapshot.sourceTimeMs}:${snapshot.tickOrdinal}`;
}

function SignalScoreTrend({
  evaluations,
  snapshot,
}: {
  evaluations: readonly SignalEvaluationLike[];
  snapshot: SignalSnapshot;
}) {
  const byIdentity = new Map<string, SignalSnapshot>();
  for (const evaluation of evaluations) {
    if (evaluation.signalSnapshot) {
      byIdentity.set(
        signalIdentity(evaluation.signalSnapshot),
        evaluation.signalSnapshot
      );
    }
  }
  byIdentity.set(signalIdentity(snapshot), snapshot);
  const snapshots = [...byIdentity.values()].sort(compareSignalIdentity);
  const segments = signalTrendMetrics.flatMap(metric => {
    const result: SignalTrendSegment[] = [];
    let current: (typeof result)[number] | undefined;
    let serial = 0;
    snapshots.forEach((item, index) => {
      const value = metric.value(item);
      if (value == null || !Number.isFinite(value)) {
        current = undefined;
        return;
      }
      if (!current || current.generation !== item.continuityGeneration) {
        current = {
          id: `${metric.key}:${item.continuityGeneration}:${serial++}`,
          generation: item.continuityGeneration,
          metric,
          points: [],
        };
        result.push(current);
      }
      current.points.push({
        generation: item.continuityGeneration,
        index,
        value,
      });
    });
    return result;
  });
  const x = (index: number) =>
    snapshots.length === 1 ? 50 : 2 + (index / (snapshots.length - 1)) * 96;
  const y = (value: number) => 64 - (value / 100) * 58;

  return (
    <div className="border border-white/[0.06] p-3">
      <svg
        className="h-44 w-full"
        viewBox="0 0 100 68"
        preserveAspectRatio="none"
        role="img"
        aria-label="机会分与四条服务端阈值趋势；缺失值和连续代际切换均断线"
      >
        {[0, 50, 100].map(value => (
          <line
            key={value}
            x1="2"
            x2="98"
            y1={y(value)}
            y2={y(value)}
            stroke="rgb(71 85 105)"
            strokeOpacity="0.3"
            strokeWidth="0.35"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        {segments.map(segment => {
          const points = segment.points
            .map(point => `${x(point.index)},${y(point.value)}`)
            .join(' ');
          return (
            <React.Fragment key={segment.id}>
              <polyline
                data-testid="signal-trend-segment"
                data-series={segment.metric.key}
                data-generation={segment.generation}
                data-point-count={segment.points.length}
                data-values={segment.points.map(point => point.value).join(',')}
                fill="none"
                stroke={segment.metric.color}
                strokeDasharray={segment.metric.dash}
                strokeOpacity={
                  segment.metric.key === 'revalidate' &&
                  snapshot.candidateStatus !== 'AWAITING_APPROVAL'
                    ? '0.45'
                    : '1'
                }
                strokeWidth={
                  segment.metric.key === 'score'
                    ? '1.5'
                    : segment.metric.key === 'revalidate' &&
                        snapshot.candidateStatus === 'AWAITING_APPROVAL'
                      ? '1.35'
                      : '1'
                }
                vectorEffect="non-scaling-stroke"
                points={points}
              />
              {segment.points.map(point => (
                <circle
                  key={`${segment.id}:${point.index}`}
                  cx={x(point.index)}
                  cy={y(point.value)}
                  r={segment.metric.key === 'score' ? '0.8' : '0.55'}
                  fill={segment.metric.color}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
            </React.Fragment>
          );
        })}
      </svg>
      <div className="mt-2 grid grid-cols-2 gap-2 text-[9px] sm:grid-cols-5">
        {signalTrendMetrics.map(metric => {
          const value = metric.value(snapshot);
          return (
            <div key={metric.key} className="flex min-w-0 items-center gap-1.5">
              <span
                className="h-0.5 w-4 shrink-0"
                style={{ backgroundColor: metric.color }}
                aria-hidden="true"
              />
              <span className="truncate text-slate-500">{metric.label}</span>
              <span className="font-mono text-slate-200">
                {thresholdNumber(value)}
              </span>
            </div>
          );
        })}
      </div>
      <div className="mt-2 text-[9px] text-slate-600">
        仅连接同一 continuity generation 内的服务端值；代际变化或缺值处断线。
      </div>
      <div className="mt-1 flex justify-between gap-2 text-[8px] text-slate-700">
        <span>机会分（0–100）</span>
        <span>
          源时间：{formatTime(snapshots[0]?.sourceAt)} →{' '}
          {formatTime(snapshots[snapshots.length - 1]?.sourceAt)}
        </span>
      </div>
    </div>
  );
}

function FsmTrack({
  title,
  values,
  current,
}: {
  title: string;
  values: readonly string[];
  current: string;
}) {
  return (
    <section aria-label={`${title}状态机`}>
      <div className="mb-2 flex items-center justify-between text-[10px]">
        <span className="font-black text-slate-300">{title}</span>
        <span className="text-cyan-200">{phaseLabels[current] || current}</span>
      </div>
      <ol className="grid gap-1 sm:grid-cols-3 xl:grid-cols-2 2xl:grid-cols-3">
        {values.map(value => (
          <li
            key={value}
            aria-current={value === current ? 'step' : undefined}
            className={cn(
              'border px-2 py-1.5 text-[9px]',
              value === current
                ? 'border-cyan-400/30 bg-cyan-400/10 text-cyan-100'
                : 'border-white/[0.05] text-slate-600'
            )}
          >
            {phaseLabels[value] || value}
          </li>
        ))}
      </ol>
    </section>
  );
}

function SnapshotInspector({
  evaluations,
  history,
  holding,
  snapshot,
}: {
  evaluations: readonly SignalEvaluationLike[];
  history: readonly QuoteHistoryPoint[];
  holding: MonitorHolding;
  snapshot?: SignalSnapshot | null;
}) {
  const now = useNow();
  if (!snapshot) {
    return (
      <div className="mt-6 border border-amber-400/15 bg-amber-400/[0.05] p-4 text-xs text-amber-100">
        服务端尚未生成 V3 信号快照。当前不可判断，也不能确认买入。
      </div>
    );
  }
  const compatible = isKnownSignalSnapshot(snapshot);
  const expiresAt = snapshot.candidateExpiresAt
    ? Date.parse(snapshot.candidateExpiresAt)
    : Number.NaN;
  const ttlSeconds = Number.isFinite(expiresAt)
    ? Math.max(0, Math.ceil((expiresAt - now.getTime()) / 1000))
    : null;
  const blocker = snapshot.topBlockers[0];
  const instrumentEvaluations = evaluations.filter(
    item => item.stockCode === holding.stockCode
  );

  return (
    <div className="space-y-5 py-5 text-slate-300">
      {!compatible && (
        <div
          role="alert"
          className="flex items-start gap-2 border border-rose-400/25 bg-rose-400/[0.08] p-3 text-[10px] leading-4 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
          版本不兼容或出现未知状态。页面已进入只读失败态，禁止确认。
        </div>
      )}

      <section className="border border-white/[0.07] bg-white/[0.025] p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[9px] font-black uppercase tracking-[0.16em] text-slate-600">
              服务端结论
            </div>
            <div className="mt-1 text-sm font-black text-slate-100">
              {candidateLabels[snapshot.candidateStatus] ||
                `未知状态 · ${snapshot.candidateStatus}`}
            </div>
            <div className="mt-1 text-[10px] text-slate-500">
              {pathLabels[snapshot.selectedPath || ''] || '尚未选择路径'} ·{' '}
              {phaseLabels[snapshot.dominantPhase] || snapshot.dominantPhase}
            </div>
          </div>
          <div className="text-right">
            <div
              className={cn(
                'font-mono text-xl font-black',
                scoreTone(snapshot)
              )}
            >
              {scoreLabel(snapshot)}
            </div>
            <div className="text-[9px] text-slate-600">
              规则机会分 / 候选阈值，不是概率
            </div>
          </div>
        </div>
        <div className="mt-3 flex items-start gap-2 border-t border-white/[0.05] pt-3 text-[10px] leading-4">
          {blocker ? (
            <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-300" />
          ) : (
            <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
          )}
          <span className={blocker ? 'text-amber-100' : 'text-emerald-200'}>
            {blocker
              ? `${blocker.label}：${blocker.detail}`
              : '当前无服务端 blocker'}
          </span>
        </div>
        <div className="mt-2 font-mono text-[9px] text-slate-600">
          source {formatTime(snapshot.sourceAt)} · identity{' '}
          {snapshot.continuityGeneration}/{snapshot.sourceTimeMs}/
          {snapshot.tickOrdinal}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
          价格上下文
        </h3>
        <PriceSparkline points={history} />
        <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4">
          {[
            ['服务端价格', nullableNumber(snapshot.features.price, 3)],
            ['VWAP', nullableNumber(snapshot.features.sessionVwap, 3)],
            ['窗口高点', nullableNumber(snapshot.features.windowHigh, 3)],
            ['窗口低点', nullableNumber(snapshot.features.windowLow, 3)],
            ['回撤', nullableNumber(snapshot.features.pullbackPct, 2, '%')],
            ['反弹', nullableNumber(snapshot.features.reboundPct, 2, '%')],
            [
              'VWAP 偏离',
              nullableNumber(snapshot.features.vwapPremiumPct, 2, '%'),
            ],
            ['价差', nullableNumber(snapshot.features.spreadTicks, 2, ' tick')],
          ].map(([label, value]) => (
            <div key={label} className="border border-white/[0.06] p-2">
              <div className="text-slate-600">{label}</div>
              <div className="mt-1 font-mono text-slate-200">{value}</div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
          分数趋势与四条阈值
        </h3>
        <SignalScoreTrend
          evaluations={instrumentEvaluations}
          snapshot={snapshot}
        />
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            ['重点观察', snapshot.previewThreshold],
            ['候选锁存', snapshot.candidateThreshold],
            ['确认重验', snapshot.revalidateThreshold],
            ['再武装', snapshot.rearmThreshold],
          ].map(([label, value]) => (
            <div
              key={String(label)}
              className="border border-white/[0.06] p-2 text-[9px]"
            >
              <div className="text-slate-600">{label}</div>
              <div className="mt-1 font-mono text-slate-200">
                {thresholdNumber(Number(value))}
              </div>
            </div>
          ))}
        </div>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[420px] text-left text-[9px]">
            <caption className="sr-only">
              最近服务端机会分历史；缺失值保持不可计算
            </caption>
            <thead className="text-slate-600">
              <tr>
                <th className="py-1">源时间</th>
                <th>事件</th>
                <th>路径</th>
                <th>机会分</th>
                <th>数据健康</th>
              </tr>
            </thead>
            <tbody>
              {instrumentEvaluations.slice(0, 12).map(item => (
                <tr key={item.id} className="border-t border-white/[0.05]">
                  <td className="py-1.5 font-mono">
                    {formatTime(
                      item.signalSnapshot?.sourceAt || item.evaluatedAt
                    )}
                  </td>
                  <td>{item.eventKind}</td>
                  <td>
                    {pathLabels[item.signalSnapshot?.selectedPath || ''] ||
                      '未选择'}
                  </td>
                  <td>
                    {nullableNumber(item.signalSnapshot?.opportunityScore, 1)}
                  </td>
                  <td>
                    {healthLabels[item.signalSnapshot?.dataHealth || ''] ||
                      '不可计算'}
                  </td>
                </tr>
              ))}
              {instrumentEvaluations.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-4 text-center text-slate-600">
                    暂无持久化评估历史
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <FsmTrack
          title="回撤反弹 FSM"
          values={PULLBACK_PHASE_VALUES}
          current={snapshot.pullbackPhase}
        />
        <FsmTrack
          title="早期动量 FSM"
          values={MOMENTUM_PHASE_VALUES}
          current={snapshot.momentumPhase}
        />
      </div>

      <section>
        <h3 className="mb-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
          硬门禁
        </h3>
        <div className="space-y-1.5">
          {snapshot.hardGates.map(gate => (
            <div
              key={gate.code}
              className={cn(
                'grid gap-2 border px-3 py-2 text-[10px] sm:grid-cols-[18px_minmax(120px,0.7fr)_1fr_auto]',
                gate.passed
                  ? 'border-emerald-400/10 bg-emerald-400/[0.025]'
                  : 'border-rose-400/20 bg-rose-400/[0.05]'
              )}
            >
              {gate.passed ? (
                <CheckCircle2 className="h-4 w-4 text-emerald-400" />
              ) : (
                <XCircle className="h-4 w-4 text-rose-300" />
              )}
              <span className="font-bold text-slate-200">{gate.label}</span>
              <span className="text-slate-500">{gate.detail}</span>
              <span className="font-mono text-slate-400">
                {nullableNumber(gate.observedValue)} /{' '}
                {nullableNumber(gate.requiredValue)}
              </span>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
          分数贡献
        </h3>
        <div className="space-y-2">
          {snapshot.scoreContributions.map(item => {
            const width =
              item.maxPoints > 0
                ? Math.min(
                    100,
                    Math.max(0, (item.points / item.maxPoints) * 100)
                  )
                : 0;
            return (
              <div
                key={item.code}
                className="border border-white/[0.06] p-2.5 text-[10px]"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-bold text-slate-300">{item.label}</span>
                  <span className="font-mono text-slate-200">
                    {formatNumber(item.points, 1)} /{' '}
                    {formatNumber(item.maxPoints, 1)}
                  </span>
                </div>
                <div className="mt-2 h-1 bg-white/[0.06]">
                  <div
                    className="h-full bg-cyan-400 motion-reduce:transition-none"
                    style={{ width: `${width}%` }}
                  />
                </div>
                <div className="mt-1.5 flex flex-wrap justify-between gap-2 text-[9px] text-slate-600">
                  <span>{item.detail}</span>
                  <span>
                    观测 {nullableNumber(item.observedValue)} · 目标{' '}
                    {nullableNumber(item.targetValue)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <div className="border border-white/[0.07] p-3">
          <h3 className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
            数据健康
          </h3>
          <div className="mt-3 grid grid-cols-2 gap-2 text-[9px]">
            <div>
              <span className="text-slate-600">状态</span>
              <div className="mt-1 text-slate-200">
                {healthLabels[snapshot.dataHealth] || snapshot.dataHealth}
              </div>
            </div>
            <div>
              <span className="text-slate-600">数据年龄</span>
              <div className="mt-1 font-mono text-slate-200">
                {snapshot.dataAgeMs == null
                  ? '不可计算'
                  : `${snapshot.dataAgeMs} ms`}
              </div>
            </div>
            <div>
              <span className="text-slate-600">窗口覆盖</span>
              <div className="mt-1 font-mono text-slate-200">
                {snapshot.windowCoverageSeconds == null
                  ? '不可计算'
                  : `${snapshot.windowCoverageSeconds} 秒`}
              </div>
            </div>
            <div>
              <span className="text-slate-600">样本数</span>
              <div className="mt-1 font-mono text-slate-200">
                {snapshot.sampleCount}
              </div>
            </div>
            <div>
              <span className="text-slate-600">画像版本</span>
              <div className="mt-1 break-all font-mono text-slate-200">
                {snapshot.profileVersion || '不可用'}
              </div>
            </div>
            <div>
              <span className="text-slate-600">连续代际</span>
              <div className="mt-1 font-mono text-slate-200">
                {snapshot.continuityGeneration}
              </div>
            </div>
          </div>
          <ul className="mt-3 space-y-1 border-t border-white/[0.05] pt-2 text-[9px] text-slate-500">
            {snapshot.dataHealthReasons.map(reason => (
              <li key={reason.code}>
                <span className="text-slate-300">{reason.label}</span> ·{' '}
                {reason.detail}
              </li>
            ))}
            {snapshot.dataHealthReasons.length === 0 && (
              <li>服务端未报告数据健康异常</li>
            )}
          </ul>
        </div>
        <div className="border border-white/[0.07] p-3">
          <h3 className="text-[10px] font-black uppercase tracking-[0.12em] text-slate-500">
            候选与执行审计
          </h3>
          <div className="mt-3 space-y-2 text-[9px]">
            <div className="flex justify-between gap-3">
              <span className="text-slate-600">候选状态</span>
              <span>
                {candidateLabels[snapshot.candidateStatus] ||
                  snapshot.candidateStatus}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-600">候选 TTL</span>
              <span
                className={
                  ttlSeconds === 0 ? 'text-rose-300' : 'text-slate-200'
                }
              >
                {ttlSeconds == null ? '不可计算' : `${ttlSeconds} 秒`}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-600">Episode</span>
              <span className="max-w-[65%] break-all font-mono">
                {snapshot.episodeId || '无'}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-600">Candidate</span>
              <span className="max-w-[65%] break-all font-mono">
                {snapshot.candidateId || '无'}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-600">Fingerprint</span>
              <span className="max-w-[65%] break-all font-mono">
                {snapshot.candidateFingerprint || '无'}
              </span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-600">TradeIntent</span>
              <span className="max-w-[65%] break-all font-mono">
                {snapshot.pendingEntryIntentId || '无'}
              </span>
            </div>
            <div className="flex justify-between gap-3 border-t border-white/[0.05] pt-2">
              <span className="text-slate-600">版本</span>
              <span className="max-w-[65%] break-all text-right font-mono">
                state {snapshot.stateSchemaVersion} · feature{' '}
                {snapshot.featureSchemaVersion} · policy{' '}
                {snapshot.policyVersion} · config {snapshot.configVersion}
              </span>
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

export function TTradeLiveBoard({
  evaluations = [],
  focusStockCode,
  historyByCode = new Map(),
  loading,
  monitor,
  onFocusHandled,
  onIgnore,
  quotes,
}: {
  evaluations?: readonly SignalEvaluationLike[];
  focusStockCode?: string | null;
  historyByCode?: QuoteHistoryByCode;
  loading: boolean;
  monitor?: TTradeMonitorLike;
  onFocusHandled?: () => void;
  onIgnore?: (stockCode: string, ignored: boolean) => void;
  quotes: ReadonlyMap<string, LiveMarketQuote>;
}) {
  const [selectedCode, setSelectedCode] = React.useState<string | null>(null);
  const rowButtonRefs = React.useRef(new Map<string, HTMLButtonElement>());
  const lastSelectedCodeRef = React.useRef<string | null>(null);
  const handledFocusCodeRef = React.useRef<string | null>(null);
  const rows = monitor
    ? buildAttentionRows(monitor.holdings, monitor.sessions, quotes)
    : [];
  const selected = rows.find(row => row.holding.stockCode === selectedCode);

  React.useEffect(() => {
    if (!focusStockCode) {
      handledFocusCodeRef.current = null;
      return;
    }
    if (handledFocusCodeRef.current === focusStockCode || !monitor) return;
    if (
      !monitor.holdings.some(holding => holding.stockCode === focusStockCode)
    ) {
      return;
    }
    handledFocusCodeRef.current = focusStockCode;
    lastSelectedCodeRef.current = focusStockCode;
    setSelectedCode(focusStockCode);
    onFocusHandled?.();
  }, [focusStockCode, monitor, onFocusHandled]);

  if (loading && !monitor) {
    return (
      <div
        role="status"
        aria-busy="true"
        className="flex h-full items-center justify-center text-xs text-slate-600"
      >
        <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
        读取服务端信号快照…
      </div>
    );
  }
  if (!monitor || rows.length === 0) {
    return (
      <div className="flex h-full min-h-64 flex-col items-center justify-center text-center">
        <Database className="h-10 w-10 text-slate-800" />
        <div className="mt-3 text-sm font-bold text-slate-500">
          暂无可展示持仓
        </div>
        <div className="mt-1 text-[10px] text-slate-700">
          持仓同步并生成 V3 快照后显示
        </div>
      </div>
    );
  }

  return (
    <>
      <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
        {loading && monitor && (
          <div
            role="status"
            aria-busy="true"
            className="flex items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-4 py-2 text-[9px] text-cyan-100"
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
            正在刷新服务端快照，暂保留上次可信结果…
          </div>
        )}
        <div className="hidden min-w-[880px] grid-cols-[minmax(170px,1.3fr)_100px_130px_minmax(135px,1fr)_100px_minmax(150px,1.2fr)_120px] border-b border-white/[0.06] px-4 py-2 text-[9px] font-black uppercase tracking-[0.1em] text-slate-600 lg:grid">
          <span>持仓标的</span>
          <span>服务端价格</span>
          <span>数据健康</span>
          <span>形态 / 路径</span>
          <span>机会分</span>
          <span>第一 blocker</span>
          <span>候选状态</span>
        </div>
        {rows.map(row => {
          const snapshot = row.snapshot;
          const compatible = !snapshot || isKnownSignalSnapshot(snapshot);
          const blocker = snapshot?.topBlockers[0];
          const HealthIcon =
            snapshot?.dataHealth === 'READY'
              ? CheckCircle2
              : snapshot?.dataHealth
                ? AlertTriangle
                : Database;
          const CandidateIcon =
            snapshot?.candidateStatus === 'AWAITING_APPROVAL'
              ? Activity
              : snapshot?.candidateStatus === 'LATCHED'
                ? CheckCircle2
                : ShieldAlert;
          return (
            <button
              key={row.holding.stockCode}
              ref={element => {
                if (element)
                  rowButtonRefs.current.set(row.holding.stockCode, element);
                else rowButtonRefs.current.delete(row.holding.stockCode);
              }}
              type="button"
              aria-label={`检查 ${row.holding.instrumentName || row.holding.stockCode}`}
              onClick={() => {
                lastSelectedCodeRef.current = row.holding.stockCode;
                setSelectedCode(row.holding.stockCode);
              }}
              className="grid w-full cursor-pointer gap-3 border-b border-white/[0.05] px-4 py-3 text-left transition-colors hover:bg-white/[0.025] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/60 lg:min-w-[880px] lg:grid-cols-[minmax(170px,1.3fr)_100px_130px_minmax(135px,1fr)_100px_minmax(150px,1.2fr)_120px] lg:items-center"
            >
              <span className="min-w-0">
                <span className="block truncate text-xs font-black text-slate-100">
                  {row.holding.instrumentName || row.holding.stockCode}
                </span>
                <span className="mt-0.5 block font-mono text-[9px] text-slate-600">
                  {row.holding.stockCode} · 持仓{' '}
                  {row.holding.volume.toLocaleString()}
                </span>
              </span>
              <span className="font-mono text-xs text-slate-200">
                <span className="mr-2 text-[9px] text-slate-600 lg:hidden">
                  价格
                </span>
                {nullableNumber(snapshot?.features.price, 3)}
                <span className="mt-0.5 block text-[8px] font-normal text-slate-600">
                  {snapshot
                    ? `源时间 ${formatTime(snapshot.sourceAt)}`
                    : '等待源时间'}
                </span>
              </span>
              <span>
                <span
                  className={cn(
                    'inline-flex items-center gap-1 border px-2 py-0.5 text-[9px] font-black',
                    healthTone(snapshot?.dataHealth)
                  )}
                >
                  <HealthIcon className="h-3 w-3 shrink-0" aria-hidden="true" />
                  {compatible
                    ? healthLabels[snapshot?.dataHealth || ''] || '等待快照'
                    : '版本不兼容'}
                </span>
                <span className="mt-1 block text-[8px] text-slate-600">
                  {snapshot?.dataHealthReasons[0]?.label ||
                    (snapshot?.dataHealth === 'READY'
                      ? '无健康异常'
                      : '等待原因')}
                </span>
              </span>
              <span className="text-[10px] text-slate-300">
                <span className="mr-2 text-slate-600 lg:hidden">形态</span>
                {phaseLabels[snapshot?.dominantPhase || ''] || '等待服务端评估'}
                <span className="mt-0.5 block text-[9px] text-slate-600">
                  {pathLabels[snapshot?.selectedPath || ''] || '尚未选择路径'}
                </span>
              </span>
              <span
                className={cn(
                  'font-mono text-xs font-black',
                  scoreTone(snapshot)
                )}
              >
                <span className="mr-2 text-[9px] font-normal text-slate-600 lg:hidden">
                  机会分
                </span>
                {scoreLabel(snapshot)}
              </span>
              <span className="truncate text-[10px] text-amber-100">
                <span className="mr-2 text-slate-600 lg:hidden">阻断</span>
                {blocker?.label ||
                  (snapshot?.dataHealth === 'READY'
                    ? '无'
                    : snapshot?.dataHealthReasons[0]?.label || '等待快照')}
              </span>
              <span className="text-[10px] font-bold text-slate-300">
                <span className="mr-2 text-slate-600 lg:hidden">候选</span>
                <CandidateIcon className="mr-1 inline-block h-3 w-3" aria-hidden="true" />
                {candidateLabels[snapshot?.candidateStatus || ''] || '无候选'}
                <span className="mt-0.5 block text-[8px] font-normal text-slate-600">
                  批次 {row.session?.status || '无活动批次'}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <Sheet
        open={Boolean(selected)}
        onOpenChange={open => {
          if (open) return;
          setSelectedCode(null);
          window.setTimeout(() => {
            const code = lastSelectedCodeRef.current;
            if (code) rowButtonRefs.current.get(code)?.focus();
          }, 0);
        }}
      >
        <SheetContent
          side="right"
          className="w-full overflow-y-auto border-white/[0.08] bg-[#081322] p-5 custom-scrollbar sm:max-w-2xl xl:max-w-4xl"
        >
          {selected && (
            <>
              <SheetHeader className="border-b border-white/[0.06] pb-4 pr-8 text-left">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <SheetTitle className="text-lg font-black text-slate-100">
                      {selected.holding.instrumentName ||
                        selected.holding.stockCode}
                    </SheetTitle>
                    <SheetDescription className="font-mono text-[10px] text-slate-500">
                      {selected.holding.stockCode} · V3 服务端信号检查器
                    </SheetDescription>
                  </div>
                  {onIgnore && (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="h-8 rounded-sm border-white/10 text-[10px]"
                      onClick={() =>
                        onIgnore(
                          selected.holding.stockCode,
                          !selected.holding.ignored
                        )
                      }
                    >
                      {selected.holding.ignored ? '恢复监控' : '加入忽略'}
                    </Button>
                  )}
                </div>
              </SheetHeader>
              <SnapshotInspector
                evaluations={evaluations}
                history={historyByCode.get(selected.holding.stockCode) || []}
                holding={selected.holding}
                snapshot={selected.snapshot}
              />
            </>
          )}
        </SheetContent>
      </Sheet>
      <div className="sr-only" aria-live="polite">
        信号列表已更新，共 {rows.length} 只持仓
      </div>
    </>
  );
}

export { CANDIDATE_STATUS_VALUES };
