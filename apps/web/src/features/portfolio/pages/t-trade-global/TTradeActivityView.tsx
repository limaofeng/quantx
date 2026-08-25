import {
  Activity,
  AlertTriangle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clipboard,
  Copy,
  Database,
  ExternalLink,
  Filter,
  ListFilter,
  Loader2,
  Radio,
  Search,
  ShieldAlert,
  Terminal,
  XCircle,
} from 'lucide-react';
import * as React from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { GraphqlWsStatus } from '@/core/graphql/ws-status';
import StrategyLogsTab from '@/features/strategies/components/StrategyLogsTab';
import { cn } from '@/utils/cn';

import {
  buildTTradeActivityItems,
  filterTTradeActivityItems,
  type ActivityBatch,
  type ActivityBatchEvent,
  type ActivityKind,
  type ActivitySignalEvaluation,
  type ActivityTone,
  type TTradeActivityItem,
} from './activity';
import { isKnownSignalSnapshot, type SignalSnapshot } from './monitoring';
import { formatNumber, formatTime } from './utils';

type ActivitySurface = 'BUSINESS' | 'TECHNICAL';
type ActivityKindFilter = 'ALL' | ActivityKind;

const healthLabels: Readonly<Record<string, string>> = {
  WARMING: '重热中',
  READY: '数据 READY',
  DEGRADED: '数据降级',
  STALE: '数据陈旧',
  CONTINUITY_LOST: '连续性中断',
  INSUFFICIENT: '数据不足',
};

const pathLabels: Readonly<Record<string, string>> = {
  PULLBACK_REBOUND: '回撤反弹',
  MOMENTUM_ACCELERATION: '早期动量',
};

const phaseLabels: Readonly<Record<string, string>> = {
  NONE: '无',
  OBSERVING: '观察',
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

const pullbackPhases = [
  'OBSERVING',
  'PULLBACK_FORMING',
  'LOW_STABILIZING',
  'REBOUND_CONFIRMING',
  'CANDIDATE_LATCHED',
  'SUPPRESSED',
] as const;

const momentumPhases = [
  'OBSERVING',
  'BASELINING',
  'MOMENTUM_BUILDING',
  'ACCELERATING',
  'OVEREXTENDED',
  'CANDIDATE_LATCHED',
  'SUPPRESSED',
] as const;

const kindLabels: Readonly<Record<ActivityKindFilter, string>> = {
  ALL: '全部类型',
  SIGNAL: '信号状态',
  CANDIDATE: '候选与意图',
  DIAGNOSTIC: '诊断观测',
  ORDER: '委托事件',
  TRADE: '真实成交',
  ERROR: '执行异常',
};

const toneStyles: Readonly<
  Record<
    ActivityTone,
    { dot: string; icon: string; title: string; border: string }
  >
> = {
  blue: {
    dot: 'bg-blue-400',
    icon: 'border-blue-400/25 bg-blue-400/10 text-blue-200',
    title: 'text-blue-100',
    border: 'border-blue-400/25',
  },
  emerald: {
    dot: 'bg-emerald-400',
    icon: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200',
    title: 'text-emerald-100',
    border: 'border-emerald-400/25',
  },
  amber: {
    dot: 'bg-amber-400',
    icon: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
    title: 'text-amber-100',
    border: 'border-amber-400/25',
  },
  rose: {
    dot: 'bg-rose-400',
    icon: 'border-rose-400/25 bg-rose-400/10 text-rose-200',
    title: 'text-rose-100',
    border: 'border-rose-400/25',
  },
  slate: {
    dot: 'bg-slate-500',
    icon: 'border-white/10 bg-white/[0.04] text-slate-300',
    title: 'text-slate-200',
    border: 'border-white/10',
  },
  marketBuy: {
    dot: 'bg-market-up',
    icon: 'border-market-up/25 bg-market-up/10 text-market-up',
    title: 'text-market-up',
    border: 'border-market-up/25',
  },
  marketSell: {
    dot: 'bg-market-down',
    icon: 'border-market-down/25 bg-market-down/10 text-market-down',
    title: 'text-market-down',
    border: 'border-market-down/25',
  },
};

function activityIcon(kind: ActivityKind) {
  if (kind === 'TRADE') return CheckCircle2;
  if (kind === 'ORDER') return Clipboard;
  if (kind === 'ERROR') return XCircle;
  if (kind === 'CANDIDATE') return CircleDot;
  if (kind === 'DIAGNOSTIC') return Database;
  return Activity;
}

function formatEventTime(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  const date = new Date(timestamp);
  const milliseconds = String(date.getMilliseconds()).padStart(3, '0');
  return `${date.toLocaleTimeString('zh-CN', { hour12: false })}.${milliseconds}`;
}

function nullableNumber(
  value: number | null | undefined,
  digits = 2,
  suffix = ''
) {
  return value == null || !Number.isFinite(value)
    ? '不可计算'
    : `${formatNumber(value, digits)}${suffix}`;
}

function compactId(value?: string | null, visible = 18) {
  if (!value) return '--';
  return value.length > visible ? `${value.slice(0, visible)}…` : value;
}

function copyText(value?: string | null) {
  if (!value) return;
  void navigator.clipboard?.writeText(value);
}

function SnapshotMetric({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="min-w-0 border border-white/[0.06] bg-white/[0.018] px-2.5 py-2">
      <div className="text-[9px] font-bold text-slate-600">{label}</div>
      <div className="mt-1 truncate font-mono text-[11px] font-black text-slate-200">
        {value}
      </div>
    </div>
  );
}

function ThresholdRail({ snapshot }: { snapshot: SignalSnapshot }) {
  const points = [
    { label: '再武装', value: snapshot.rearmThreshold, tone: 'bg-violet-400' },
    { label: '预览', value: snapshot.previewThreshold, tone: 'bg-amber-300' },
    {
      label: '重验',
      value: snapshot.revalidateThreshold,
      tone: 'bg-pink-400',
    },
    {
      label: '候选',
      value: snapshot.candidateThreshold,
      tone: 'bg-orange-400',
    },
  ];
  const maximum = Math.max(
    100,
    snapshot.candidateThreshold + 5,
    snapshot.opportunityScore || 0
  );
  const position = (value: number) =>
    `${Math.min(100, Math.max(0, (value / maximum) * 100))}%`;

  return (
    <div className="border border-white/[0.06] bg-[#081321] p-3">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-[9px] font-black uppercase tracking-[0.1em] text-slate-500">
          机会分阈值轨道
        </h4>
        <span className="font-mono text-[10px] font-black text-slate-200">
          {nullableNumber(snapshot.opportunityScore, 1)} /{' '}
          {nullableNumber(snapshot.candidateThreshold, 0)}
        </span>
      </div>
      <div className="relative mt-8 h-1.5 bg-slate-800">
        <div className="absolute inset-y-0 left-0 w-1/3 bg-slate-600/60" />
        <div className="absolute inset-y-0 left-1/3 w-1/3 bg-amber-400/45" />
        <div className="absolute inset-y-0 right-0 w-1/3 bg-orange-400/55" />
        {points.map(point => (
          <div
            key={point.label}
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ left: position(point.value) }}
          >
            <span
              className={cn('block h-3 w-0.5 -translate-y-0.5', point.tone)}
            />
            <span className="absolute bottom-3 left-1/2 -translate-x-1/2 whitespace-nowrap text-center text-[8px] text-slate-500">
              {point.label} {formatNumber(point.value, 0)}
            </span>
          </div>
        ))}
        {snapshot.opportunityScore != null && (
          <div
            className="absolute top-1/2 z-10 -translate-x-1/2 -translate-y-1/2"
            style={{ left: position(snapshot.opportunityScore) }}
          >
            <span className="block h-4 w-4 rounded-full border-2 border-blue-200 bg-blue-500 shadow-[0_0_0_3px_rgba(59,130,246,0.18)]" />
            <span className="absolute left-1/2 top-5 -translate-x-1/2 whitespace-nowrap bg-blue-500 px-1.5 py-0.5 font-mono text-[8px] font-black text-white">
              {formatNumber(snapshot.opportunityScore, 1)}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function FsmTrack({
  current,
  previous,
  title,
  values,
}: {
  current: string;
  previous?: string | null;
  title: string;
  values: readonly string[];
}) {
  return (
    <div className="border border-white/[0.06] bg-[#081321] p-3">
      <div className="flex items-center justify-between gap-3">
        <h4 className="text-[9px] font-black uppercase tracking-[0.1em] text-slate-500">
          {title}
        </h4>
        <span className="text-[9px] text-slate-500">
          {previous && previous !== current
            ? `${phaseLabels[previous] || previous} → `
            : ''}
          <span className="font-bold text-blue-200">
            {phaseLabels[current] || current}
          </span>
        </span>
      </div>
      <div className="mt-3 flex items-start">
        {values.map((value, index) => {
          const active = value === current;
          const reached = values.indexOf(current) >= index;
          return (
            <React.Fragment key={value}>
              {index > 0 && (
                <span
                  className={cn(
                    'mt-2 h-px min-w-2 flex-1',
                    reached ? 'bg-blue-400/60' : 'bg-white/10'
                  )}
                />
              )}
              <div className="w-16 shrink-0 text-center">
                <span
                  className={cn(
                    'mx-auto flex h-4 w-4 items-center justify-center rounded-full border text-[8px]',
                    active
                      ? 'border-blue-200 bg-blue-500 text-white ring-2 ring-blue-400/20'
                      : reached
                        ? 'border-emerald-400/40 bg-emerald-400/10 text-emerald-300'
                        : 'border-white/10 text-slate-700'
                  )}
                >
                  {reached ? <Check className="h-2.5 w-2.5" /> : index + 1}
                </span>
                <span
                  className={cn(
                    'mt-1 block text-[8px] leading-3',
                    active ? 'font-bold text-blue-100' : 'text-slate-600'
                  )}
                >
                  {phaseLabels[value] || value}
                </span>
              </div>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

function HistoricalSignalSnapshot({
  item,
  onViewCurrent,
}: {
  item: TTradeActivityItem;
  onViewCurrent: (stockCode: string) => void;
}) {
  const evaluation = item.signalEvaluation;
  const snapshot = evaluation?.signalSnapshot;
  const previous = item.previousSignalSnapshot;
  if (!evaluation || !snapshot) {
    return (
      <div
        id={`${item.id}-snapshot`}
        role="status"
        className="border-t border-white/[0.06] p-4 text-xs text-slate-500"
      >
        该事件没有可展示的历史信号快照。
      </div>
    );
  }
  if (!isKnownSignalSnapshot(snapshot)) {
    return (
      <div
        id={`${item.id}-snapshot`}
        role="alert"
        className="border-t border-rose-400/20 bg-rose-400/[0.04] p-4 text-xs text-rose-100"
      >
        历史快照 schema
        与当前客户端不兼容，已阻止展示；请使用原始技术日志排查版本。
      </div>
    );
  }

  const blocker = snapshot.topBlockers[0];
  const passedGates = snapshot.hardGates.filter(gate => gate.passed).length;
  const failedGates = snapshot.hardGates.filter(gate => !gate.passed);

  return (
    <div
      id={`${item.id}-snapshot`}
      className="border-t border-blue-400/20 bg-[#07111f] p-3 sm:p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-black text-slate-100">
            事件时刻快照
          </span>
          <Badge className="rounded-sm border border-blue-400/20 bg-blue-400/10 px-2 py-0.5 text-[8px] text-blue-100">
            历史事实
          </Badge>
          <span className="font-mono text-[9px] text-slate-500">
            源时间 {formatEventTime(snapshot.sourceAt)}
          </span>
        </div>
        <span className="font-mono text-[8px] text-slate-600">
          identity {snapshot.continuityGeneration}/{snapshot.sourceTimeMs}/
          {snapshot.tickOrdinal}
        </span>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <SnapshotMetric
          label="主导路径"
          value={pathLabels[snapshot.selectedPath || ''] || '尚未选择'}
        />
        <SnapshotMetric
          label="规则机会分 / 候选阈值"
          value={`${nullableNumber(snapshot.opportunityScore, 1)} / ${nullableNumber(snapshot.candidateThreshold, 0)}`}
        />
        <SnapshotMetric
          label="首要阻断"
          value={blocker?.label || '当前无 blocker'}
        />
        <SnapshotMetric
          label="数据健康"
          value={healthLabels[snapshot.dataHealth] || snapshot.dataHealth}
        />
      </div>

      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
        <SnapshotMetric
          label="价格"
          value={nullableNumber(snapshot.features.price, 3)}
        />
        <SnapshotMetric
          label="VWAP"
          value={nullableNumber(snapshot.features.sessionVwap, 3)}
        />
        <SnapshotMetric
          label="窗口高点"
          value={nullableNumber(snapshot.features.windowHigh, 3)}
        />
        <SnapshotMetric
          label="窗口低点"
          value={nullableNumber(snapshot.features.windowLow, 3)}
        />
        <SnapshotMetric
          label="价差"
          value={nullableNumber(snapshot.features.spreadTicks, 0, ' tick')}
        />
      </div>

      <div className="mt-2 grid gap-2 xl:grid-cols-[0.9fr_1.1fr]">
        <ThresholdRail snapshot={snapshot} />
        <div className="space-y-2">
          <FsmTrack
            title="回撤 FSM"
            values={pullbackPhases}
            current={snapshot.pullbackPhase}
            previous={previous?.pullbackPhase}
          />
          <FsmTrack
            title="动量 FSM"
            values={momentumPhases}
            current={snapshot.momentumPhase}
            previous={previous?.momentumPhase}
          />
        </div>
      </div>

      <div className="mt-2 grid gap-2 xl:grid-cols-2">
        <section className="border border-white/[0.06] bg-[#081321] p-3">
          <div className="flex items-center justify-between gap-3">
            <h4 className="text-[9px] font-black uppercase tracking-[0.1em] text-slate-500">
              门禁与阻断
            </h4>
            <span
              className={cn(
                'font-mono text-[9px] font-black',
                failedGates.length ? 'text-amber-200' : 'text-emerald-300'
              )}
            >
              {passedGates} / {snapshot.hardGates.length} 通过
            </span>
          </div>
          <div className="mt-2 space-y-1.5">
            {(failedGates.length
              ? failedGates
              : snapshot.hardGates.slice(0, 4)
            ).map(gate => (
              <div
                key={gate.code}
                className={cn(
                  'grid grid-cols-[16px_minmax(100px,0.6fr)_1fr_auto] items-center gap-2 border px-2 py-1.5 text-[9px]',
                  gate.passed
                    ? 'border-emerald-400/10 text-slate-400'
                    : 'border-rose-400/20 bg-rose-400/[0.035] text-rose-100'
                )}
              >
                {gate.passed ? (
                  <CheckCircle2 className="h-3 w-3 text-emerald-400" />
                ) : (
                  <XCircle className="h-3 w-3 text-rose-300" />
                )}
                <span className="font-bold">{gate.label}</span>
                <span className="truncate text-slate-500">{gate.detail}</span>
                <span className="font-mono text-slate-400">
                  {nullableNumber(gate.observedValue)} /{' '}
                  {nullableNumber(gate.requiredValue)}
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="border border-white/[0.06] bg-[#081321] p-3">
          <div className="flex items-center justify-between gap-3">
            <h4 className="text-[9px] font-black uppercase tracking-[0.1em] text-slate-500">
              机会分贡献
            </h4>
            <span className="text-[8px] text-slate-600">服务端计算</span>
          </div>
          <div className="mt-2 space-y-2">
            {snapshot.scoreContributions.slice(0, 7).map(contribution => {
              const width =
                contribution.maxPoints > 0
                  ? Math.min(
                      100,
                      Math.max(
                        0,
                        (contribution.points / contribution.maxPoints) * 100
                      )
                    )
                  : 0;
              return (
                <div
                  key={contribution.code}
                  className="grid grid-cols-[minmax(110px,0.7fr)_1fr_64px] items-center gap-2 text-[9px]"
                >
                  <span className="truncate text-slate-400">
                    {contribution.label}
                  </span>
                  <span className="h-1 bg-white/[0.06]">
                    <span
                      className="block h-full bg-emerald-400"
                      style={{ width: `${width}%` }}
                    />
                  </span>
                  <span className="text-right font-mono text-slate-300">
                    {formatNumber(contribution.points, 1)} /{' '}
                    {formatNumber(contribution.maxPoints, 1)}
                  </span>
                </div>
              );
            })}
          </div>
        </section>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-3">
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="sm"
            className="h-8 rounded-sm bg-blue-600 px-3 text-[9px] hover:bg-blue-500"
            onClick={() => onViewCurrent(item.stockCode)}
          >
            查看当前状态
            <ExternalLink className="ml-1.5 h-3 w-3" />
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 rounded-sm border-white/10 px-3 text-[9px]"
            onClick={() => copyText(evaluation.id)}
          >
            复制事件 ID
            <Copy className="ml-1.5 h-3 w-3" />
          </Button>
        </div>
        <span className="font-mono text-[8px] text-slate-600">
          state {snapshot.stateSchemaVersion} · feature{' '}
          {snapshot.featureSchemaVersion} · policy {snapshot.policyVersion} ·
          config {snapshot.configVersion}
          {!previous && ' · 前态未载入'}
        </span>
      </div>
    </div>
  );
}

function FactRow({
  copyable,
  label,
  value,
}: {
  copyable?: boolean;
  label: string;
  value: React.ReactNode;
}) {
  const textValue = typeof value === 'string' ? value : null;
  return (
    <div className="grid grid-cols-[92px_minmax(0,1fr)_24px] items-center gap-2 text-[10px]">
      <span className="text-slate-600">{label}</span>
      <span className="min-w-0 break-all font-mono text-slate-300">
        {value}
      </span>
      {copyable && textValue ? (
        <button
          type="button"
          aria-label={`复制${label}`}
          className="flex h-6 w-6 items-center justify-center text-slate-600 hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          onClick={() => copyText(textValue)}
        >
          <Copy className="h-3 w-3" />
        </button>
      ) : (
        <span />
      )}
    </div>
  );
}

function ExecutionEventSnapshot({
  item,
  onViewBatch,
}: {
  item: TTradeActivityItem;
  onViewBatch: (batchId: string) => void;
}) {
  const event = item.batchEvent;
  const snapshot = item.executionSnapshot;
  if (!event || !snapshot) return null;
  const batch = item.batch;
  const isTrade = item.kind === 'TRADE';
  const directionLabel =
    snapshot.direction === 'BUY'
      ? '买入'
      : snapshot.direction === 'SELL'
        ? '卖出'
        : '方向未知';
  const directionClass =
    snapshot.direction === 'BUY'
      ? 'text-market-up'
      : snapshot.direction === 'SELL'
        ? 'text-market-down'
        : 'text-slate-300';
  const applied = event.status.toUpperCase() === 'APPLIED';
  const reportTime = snapshot.reportTime || event.createdAt;

  return (
    <div
      id={`${item.id}-snapshot`}
      className="border-t border-blue-400/20 bg-[#07111f] p-3 sm:p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/[0.06] pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-black text-slate-100">
            {isTrade ? '成交时刻快照' : '委托事件快照'}
          </span>
          <Badge
            className={cn(
              'rounded-sm border px-2 py-0.5 text-[8px]',
              applied
                ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-200'
                : 'border-amber-400/20 bg-amber-400/10 text-amber-200'
            )}
          >
            {applied ? '已持久化' : event.status}
          </Badge>
          {isTrade && (
            <Badge className="rounded-sm border border-white/10 bg-white/[0.035] px-2 py-0.5 text-[8px] text-slate-300">
              真实成交真源
            </Badge>
          )}
          <span className="font-mono text-[9px] text-slate-500">
            回报时间 {formatEventTime(reportTime)}
          </span>
        </div>
        <span className="font-mono text-[8px] text-slate-600">
          persisted {formatEventTime(event.appliedAt || event.createdAt)}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
        <SnapshotMetric
          label="A 股方向"
          value={<span className={directionClass}>{directionLabel}</span>}
        />
        <SnapshotMetric
          label={isTrade ? '成交量' : '委托量'}
          value={`${nullableNumber(
            isTrade ? snapshot.tradedVolume : snapshot.orderVolume,
            0
          )} 股`}
        />
        <SnapshotMetric
          label={isTrade ? '成交均价' : '委托价格'}
          value={nullableNumber(
            isTrade ? snapshot.tradedPrice : snapshot.price,
            3
          )}
        />
        <SnapshotMetric
          label="应用状态"
          value={
            <span className={applied ? 'text-emerald-300' : 'text-amber-200'}>
              {event.status}
            </span>
          }
        />
      </div>

      <div className="mt-2 grid gap-2 lg:grid-cols-2 xl:grid-cols-3">
        <section className="border border-white/[0.06] bg-[#081321] p-3">
          <h4 className="text-[10px] font-black text-slate-200">
            券商{isTrade ? '成交' : '委托'}事实
          </h4>
          <div className="mt-3 space-y-2">
            <FactRow label="证券" value={snapshot.stockCode || '--'} />
            <FactRow
              label={isTrade ? '成交编号' : '事件编号'}
              value={snapshot.executionId || event.eventId}
              copyable
            />
            <FactRow
              label="券商委托"
              value={event.brokerOrderId || '--'}
              copyable={Boolean(event.brokerOrderId)}
            />
            <FactRow
              label="Client Order"
              value={event.clientOrderId}
              copyable
            />
            <FactRow label="回报时间" value={formatEventTime(reportTime)} />
            <FactRow label="回报序列" value={snapshot.reportSequence || '--'} />
          </div>
        </section>

        <section className="border border-white/[0.06] bg-[#081321] p-3">
          <h4 className="text-[10px] font-black text-slate-200">
            批次收敛快照
          </h4>
          <div className="mt-3 space-y-2">
            <FactRow label="批次状态" value={batch?.status || '批次尚未投影'} />
            <FactRow
              label="入场成交"
              value={`${nullableNumber(batch?.entryFilledVolume, 0)} 股`}
            />
            <FactRow
              label="活跃仓"
              value={`${nullableNumber(batch?.activeVolume, 0)} 股`}
            />
            <FactRow
              label="入场均价"
              value={nullableNumber(batch?.entryAvgPrice, 3)}
            />
            <FactRow
              label="退出状态"
              value={
                batch?.exitReason ||
                (batch?.activeVolume ? '等待退出条件' : '--')
              }
            />
            <FactRow
              label="批次版本"
              value={batch ? `v${batch.version}` : '--'}
            />
          </div>
          <div className="mt-4">
            <div className="mb-2 text-[8px] font-black uppercase tracking-[0.1em] text-slate-600">
              生命周期
            </div>
            <div className="flex items-center gap-1 text-[8px] text-slate-500">
              {['候选', '意图', '委托', '成交', '活跃批次'].map(
                (label, index, values) => (
                  <React.Fragment key={label}>
                    <span className="inline-flex flex-col items-center gap-1">
                      <span
                        className={cn(
                          'flex h-4 w-4 items-center justify-center rounded-full border',
                          index <= (isTrade ? 4 : 2)
                            ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
                            : 'border-white/10 text-slate-700'
                        )}
                      >
                        {index <= (isTrade ? 4 : 2) ? (
                          <Check className="h-2.5 w-2.5" />
                        ) : (
                          index + 1
                        )}
                      </span>
                      {label}
                    </span>
                    {index < values.length - 1 && (
                      <ArrowRight className="mb-3 h-3 w-3 shrink-0 text-slate-700" />
                    )}
                  </React.Fragment>
                )
              )}
            </div>
          </div>
        </section>

        <section className="border border-white/[0.06] bg-[#081321] p-3 lg:col-span-2 xl:col-span-1">
          <h4 className="text-[10px] font-black text-slate-200">关联与审计</h4>
          <div className="mt-3 space-y-2">
            <FactRow
              label="策略运行"
              value={batch?.strategyRunId || compactId(event.eventId)}
              copyable={Boolean(batch?.strategyRunId)}
            />
            <FactRow
              label="批次"
              value={event.batchId || '--'}
              copyable={Boolean(event.batchId)}
            />
            <FactRow label="角色" value={snapshot.role || '--'} />
            <FactRow
              label="仓位归因"
              value={
                snapshot.role === 'ENTRY'
                  ? '活跃仓'
                  : snapshot.role === 'EXIT'
                    ? '活跃仓退出'
                    : '--'
              }
            />
            <FactRow
              label="持久化时间"
              value={formatEventTime(event.appliedAt || event.createdAt)}
            />
          </div>
          <div className="mt-4 flex items-start gap-2 border border-blue-400/15 bg-blue-400/[0.04] p-2.5 text-[9px] leading-4 text-blue-100">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            命令确认不代表成交；本记录来自已持久化的券商
            {isTrade ? '成交' : '委托'}回报。
          </div>
          {event.error && (
            <div
              role="alert"
              className="mt-2 border border-rose-400/20 bg-rose-400/[0.05] p-2 text-[9px] text-rose-100"
            >
              {event.error}
            </div>
          )}
        </section>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 border-t border-white/[0.06] pt-3">
        {event.batchId && (
          <Button
            type="button"
            size="sm"
            className="h-8 rounded-sm bg-blue-600 px-3 text-[9px] hover:bg-blue-500"
            onClick={() => onViewBatch(event.batchId)}
          >
            查看批次
            <ExternalLink className="ml-1.5 h-3 w-3" />
          </Button>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-8 rounded-sm border-white/10 px-3 text-[9px]"
          onClick={() => copyText(event.eventId)}
        >
          复制事件 ID
          <Copy className="ml-1.5 h-3 w-3" />
        </Button>
      </div>
    </div>
  );
}

function ActivityRow({
  expanded,
  item,
  instrumentName,
  onToggle,
  onViewBatch,
  onViewCurrent,
}: {
  expanded: boolean;
  item: TTradeActivityItem;
  instrumentName?: string;
  onToggle: () => void;
  onViewBatch: (batchId: string) => void;
  onViewCurrent: (stockCode: string) => void;
}) {
  const Icon = activityIcon(item.kind);
  const tone = toneStyles[item.tone];
  const snapshotId = `${item.id}-snapshot`;
  return (
    <article
      className={cn(
        'relative border-b border-white/[0.05] bg-[#091422]',
        expanded && `border ${tone.border} bg-[#0a1727]`
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'absolute bottom-0 left-[19px] top-0 w-px bg-white/[0.07]',
          expanded && 'bg-blue-400/25'
        )}
      />
      <button
        type="button"
        aria-expanded={expanded}
        aria-controls={snapshotId}
        className="relative grid min-h-11 w-full cursor-pointer grid-cols-[40px_84px_minmax(110px,0.7fr)_minmax(135px,0.85fr)_minmax(200px,1.6fr)_24px] items-center gap-2 px-2 text-left transition-colors duration-200 hover:bg-blue-400/[0.035] focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70 max-lg:grid-cols-[40px_78px_minmax(100px,0.6fr)_minmax(120px,0.7fr)_minmax(160px,1fr)_24px]"
        onClick={onToggle}
      >
        <span
          className={cn(
            'z-10 mx-auto flex h-6 w-6 items-center justify-center rounded-full border',
            tone.icon
          )}
        >
          <Icon className="h-3.5 w-3.5" aria-hidden="true" />
        </span>
        <span className="font-mono text-[10px] text-slate-400">
          {formatTime(item.occurredAt)}
        </span>
        <span className="min-w-0">
          <span className="block truncate font-mono text-[10px] font-black text-slate-200">
            {item.stockCode || '--'}
          </span>
          {instrumentName && (
            <span className="block truncate text-[8px] text-slate-600">
              {instrumentName}
            </span>
          )}
        </span>
        <span className={cn('truncate text-[10px] font-black', tone.title)}>
          {item.title}
        </span>
        <span className="truncate text-[10px] text-slate-400">
          {item.summary}
        </span>
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-blue-300" />
        ) : (
          <ChevronRight className="h-4 w-4 text-slate-600" />
        )}
      </button>
      {expanded && item.signalEvaluation && (
        <HistoricalSignalSnapshot item={item} onViewCurrent={onViewCurrent} />
      )}
      {expanded && item.batchEvent && (
        <ExecutionEventSnapshot item={item} onViewBatch={onViewBatch} />
      )}
    </article>
  );
}

export function TTradeActivityView({
  batchError,
  batches,
  eventError,
  events,
  evaluations,
  hasMoreEvents,
  hasMoreSignals,
  includeDiagnostics,
  instrumentNames,
  isRunning,
  loading,
  loadingMore,
  onIncludeDiagnosticsChange,
  onLoadMore,
  onRefresh,
  onViewBatch,
  onViewCurrent,
  runId,
  runMode,
  signalError,
  wsStatus,
}: {
  batchError?: string | null;
  batches: readonly ActivityBatch[];
  eventError?: string | null;
  events: readonly ActivityBatchEvent[];
  evaluations: readonly ActivitySignalEvaluation[];
  hasMoreEvents: boolean;
  hasMoreSignals: boolean;
  includeDiagnostics: boolean;
  instrumentNames: ReadonlyMap<string, string>;
  isRunning: boolean;
  loading: boolean;
  loadingMore: boolean;
  onIncludeDiagnosticsChange: (value: boolean) => void;
  onLoadMore: () => void;
  onRefresh: () => void;
  onViewBatch: (batchId: string) => void;
  onViewCurrent: (stockCode: string) => void;
  runId?: string | null;
  runMode?: string | null;
  signalError?: string | null;
  wsStatus: GraphqlWsStatus;
}) {
  const [surface, setSurface] = React.useState<ActivitySurface>('BUSINESS');
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const [stockCode, setStockCode] = React.useState('ALL');
  const [kind, setKind] = React.useState<ActivityKindFilter>('ALL');
  const [search, setSearch] = React.useState('');
  const [newEventCount, setNewEventCount] = React.useState(0);
  const listRef = React.useRef<HTMLDivElement>(null);
  const atTopRef = React.useRef(true);
  const previousNewestIdRef = React.useRef<string | null>(null);

  const items = React.useMemo(
    () => buildTTradeActivityItems(evaluations, events, batches),
    [batches, evaluations, events]
  );
  const filteredItems = React.useMemo(
    () =>
      filterTTradeActivityItems(items, {
        includeDiagnostics,
        kind,
        stockCode,
        search,
      }),
    [includeDiagnostics, items, kind, search, stockCode]
  );
  const stockCodes = React.useMemo(
    () =>
      Array.from(
        new Set(items.map(item => item.stockCode).filter(Boolean))
      ).sort(),
    [items]
  );

  React.useEffect(() => {
    const newestId = items[0]?.id || null;
    const previousId = previousNewestIdRef.current;
    previousNewestIdRef.current = newestId;
    if (
      previousId &&
      newestId &&
      previousId !== newestId &&
      !atTopRef.current
    ) {
      setNewEventCount(count => count + 1);
    }
  }, [items]);

  React.useEffect(() => {
    if (expandedId && !items.some(item => item.id === expandedId)) {
      setExpandedId(null);
    }
  }, [expandedId, items]);

  React.useEffect(() => {
    if (!expandedId) return;
    const collapseOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpandedId(null);
    };
    window.addEventListener('keydown', collapseOnEscape);
    return () => window.removeEventListener('keydown', collapseOnEscape);
  }, [expandedId]);

  const errorMessages = [signalError, eventError, batchError].filter(
    (value): value is string => Boolean(value)
  );
  const connected = wsStatus === 'connected';

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col text-slate-200">
      <header className="shrink-0 border-b border-white/[0.06] bg-[#091422] px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-base font-black text-slate-100">运行动态</h1>
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 text-[9px] font-bold',
                  connected ? 'text-emerald-300' : 'text-amber-200'
                )}
              >
                <Radio className="h-3 w-3" aria-hidden="true" />
                {connected ? '实时连接' : '等待重连'}
              </span>
            </div>
            <p className="mt-1 text-[9px] text-slate-600">
              关键业务事件可原位展开事件时刻快照；历史事实不代表当前状态。
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 rounded-sm border-white/10 px-3 text-[9px]"
            onClick={onRefresh}
          >
            <Radio className="mr-1.5 h-3 w-3" />
            刷新真源
          </Button>
        </div>
        <div className="mt-3 inline-flex border border-white/10 bg-[#07111f] p-0.5">
          {(
            [
              ['BUSINESS', '业务动态', Activity],
              ['TECHNICAL', '原始技术日志', Terminal],
            ] as const
          ).map(([value, label, Icon]) => (
            <button
              key={value}
              type="button"
              className={cn(
                'flex h-8 cursor-pointer items-center gap-1.5 px-4 text-[10px] font-black transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                surface === value
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-500 hover:bg-blue-400/[0.05] hover:text-slate-200'
              )}
              onClick={() => setSurface(value)}
            >
              <Icon className="h-3.5 w-3.5" />
              {label}
            </button>
          ))}
        </div>
      </header>

      {surface === 'TECHNICAL' ? (
        <div className="flex min-h-0 flex-1 flex-col p-3">
          <div className="mb-2 flex shrink-0 items-start gap-2 border border-blue-400/15 bg-blue-400/[0.04] px-3 py-2 text-[9px] leading-4 text-blue-100">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            原始技术日志用于专业排障；委托与成交事实以运行动态中的持久化券商回报为准。
          </div>
          <div className="min-h-0 flex-1">
            <StrategyLogsTab
              fillAvailable
              isRunning={isRunning}
              runId={runId}
              runMode={runMode}
              showAdvancedFilters
              status={isRunning ? 'RUNNING' : 'STOPPED'}
              strategyName="做 T 助手 · 原始技术日志"
            />
          </div>
        </div>
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/[0.06] bg-[#081321] px-4 py-2.5">
            <Button
              type="button"
              size="sm"
              variant={includeDiagnostics ? 'default' : 'outline'}
              className={cn(
                'h-8 rounded-sm px-3 text-[9px]',
                includeDiagnostics
                  ? 'bg-blue-600 hover:bg-blue-500'
                  : 'border-white/10 text-slate-400'
              )}
              onClick={() => onIncludeDiagnosticsChange(!includeDiagnostics)}
            >
              <Filter className="mr-1.5 h-3 w-3" />
              {includeDiagnostics ? '诊断观测已显示' : '仅关键事件'}
            </Button>
            <Select value={stockCode} onValueChange={setStockCode}>
              <SelectTrigger className="h-8 w-40 rounded-sm border-white/10 bg-[#07111f] text-[9px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部标的</SelectItem>
                {stockCodes.map(code => (
                  <SelectItem key={code} value={code}>
                    {code} {instrumentNames.get(code) || ''}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={kind}
              onValueChange={value => setKind(value as ActivityKindFilter)}
            >
              <SelectTrigger className="h-8 w-40 rounded-sm border-white/10 bg-[#07111f] text-[9px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.keys(kindLabels) as ActivityKindFilter[])
                  .filter(value => includeDiagnostics || value !== 'DIAGNOSTIC')
                  .map(value => (
                    <SelectItem key={value} value={value}>
                      {kindLabels[value]}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>
            <div className="relative min-w-48 flex-1">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
              <Input
                aria-label="搜索运行动态"
                value={search}
                onChange={event => setSearch(event.target.value)}
                placeholder="搜索消息、标的或 ID"
                className="h-8 rounded-sm border-white/10 bg-[#07111f] pl-8 text-[9px] focus-visible:ring-blue-400/70"
              />
            </div>
            <span className="inline-flex items-center gap-1.5 text-[9px] text-slate-600">
              <ListFilter className="h-3 w-3" />
              {filteredItems.length} 条
            </span>
          </div>

          {errorMessages.length > 0 && (
            <div
              role="alert"
              className="flex shrink-0 items-start gap-2 border-b border-amber-400/20 bg-amber-400/[0.05] px-4 py-2 text-[9px] leading-4 text-amber-100"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              部分真源刷新失败，仍保留上次可信记录：
              {errorMessages.join('；')}
            </div>
          )}

          <div
            ref={listRef}
            className="relative min-h-0 flex-1 overflow-auto bg-[#07111f] custom-scrollbar"
            onScroll={event => {
              atTopRef.current = event.currentTarget.scrollTop < 48;
              if (atTopRef.current) setNewEventCount(0);
            }}
          >
            {newEventCount > 0 && (
              <div className="sticky top-2 z-20 flex justify-center">
                <button
                  type="button"
                  className="rounded-full border border-blue-400/30 bg-blue-600 px-3 py-1 text-[9px] font-bold text-white shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300"
                  onClick={() => {
                    const reducedMotion = window.matchMedia(
                      '(prefers-reduced-motion: reduce)'
                    ).matches;
                    listRef.current?.scrollTo({
                      top: 0,
                      behavior: reducedMotion ? 'auto' : 'smooth',
                    });
                    setNewEventCount(0);
                  }}
                >
                  有 {newEventCount} 条新动态
                </button>
              </div>
            )}

            {loading && items.length === 0 ? (
              <div
                role="status"
                className="flex h-full min-h-64 items-center justify-center text-xs text-slate-600"
              >
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
                正在读取持久化运行动态…
              </div>
            ) : filteredItems.length === 0 ? (
              <div className="flex h-full min-h-64 flex-col items-center justify-center text-center">
                <Activity className="h-9 w-9 text-slate-800" />
                <div className="mt-3 text-sm font-bold text-slate-500">
                  暂无匹配的运行动态
                </div>
                <div className="mt-1 text-[9px] text-slate-700">
                  调整标的、类型或诊断观测筛选
                </div>
              </div>
            ) : (
              <div className="min-w-[820px]">
                <div className="sticky top-0 z-10 grid h-8 grid-cols-[40px_84px_minmax(110px,0.7fr)_minmax(135px,0.85fr)_minmax(200px,1.6fr)_24px] items-center gap-2 border-b border-white/[0.06] bg-[#0b1628] px-2 text-[8px] font-black uppercase tracking-[0.08em] text-slate-600 max-lg:grid-cols-[40px_78px_minmax(100px,0.6fr)_minmax(120px,0.7fr)_minmax(160px,1fr)_24px]">
                  <span />
                  <span>时间</span>
                  <span>标的</span>
                  <span>事件</span>
                  <span>摘要</span>
                  <span />
                </div>
                {filteredItems.map(item => (
                  <ActivityRow
                    key={item.id}
                    expanded={expandedId === item.id}
                    instrumentName={instrumentNames.get(item.stockCode)}
                    item={item}
                    onToggle={() =>
                      setExpandedId(current =>
                        current === item.id ? null : item.id
                      )
                    }
                    onViewBatch={onViewBatch}
                    onViewCurrent={onViewCurrent}
                  />
                ))}
              </div>
            )}

            {(hasMoreEvents || hasMoreSignals) && (
              <div className="border-t border-white/[0.06] p-3 text-center">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={loadingMore}
                  className="h-8 rounded-sm border-white/10 px-4 text-[9px]"
                  onClick={onLoadMore}
                >
                  {loadingMore ? (
                    <Loader2 className="mr-1.5 h-3 w-3 animate-spin motion-reduce:animate-none" />
                  ) : (
                    <ChevronDown className="mr-1.5 h-3 w-3" />
                  )}
                  加载更早动态
                </Button>
              </div>
            )}
          </div>
          <div className="sr-only" aria-live="polite">
            运行动态已更新，共 {filteredItems.length} 条
          </div>
        </>
      )}
    </div>
  );
}
