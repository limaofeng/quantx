import {
  ArrowRight,
  ExternalLink,
  Loader2,
  Search,
  WalletCards,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import { isCurrentTTradeBatch } from './batchWorkspace';
import { batchStatusLabels, formatNumber, formatSignedPercent } from './utils';

export type TTradeExecutionMode = 'LIVE' | 'PAPER' | 'BACKTEST';
export type TTradeMetricBasis =
  'RULE_ESTIMATE' | 'BACKTEST_MODEL' | 'LEGACY_BACKFILL' | 'INCOMPLETE';

export type TTradeBatchMetrics = {
  metricBasis: TTradeMetricBasis;
  entryCapitalCny?: number | null;
  totalFeesCny?: number | null;
  realizedNetProfitCny?: number | null;
  markToMarketNetProfitCny?: number | null;
  netReturnPct?: number | null;
  holdingHours?: number | null;
  capitalUtilizationPct?: number | null;
};

export type TTradePositionBatch = {
  batchId: string;
  stockCode: string;
  strategyRunId: string;
  status: string;
  version?: number;
  executionMode?: TTradeExecutionMode | null;
  entryIntentId?: string | null;
  exitIntentId?: string | null;
  entryClientOrderId?: string | null;
  exitClientOrderId?: string | null;
  entryBrokerOrderId?: string | null;
  exitBrokerOrderId?: string | null;
  targetVolume: number;
  entryFilledVolume: number;
  entryAvgPrice: number;
  exitFilledVolume: number;
  exitAvgPrice: number;
  activeVolume: number;
  lastPrice: number | null;
  priceAsOf?: string | null;
  priceQuality?: string | null;
  netProfit?: number | null;
  lastNetProfitPct: number;
  peakNetProfitPct: number | null;
  trailingFloorPct?: number | null;
  exitReason?: string | null;
  exceptionReason?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
  entryFilledAt?: string | null;
  terminalAt?: string | null;
  closedAt?: string | null;
  metrics?: TTradeBatchMetrics | null;
};

export type TTradePositionEvent = {
  eventId: string;
  batchId: string;
  eventType: string;
  status: string;
  clientOrderId?: string | null;
  brokerOrderId?: string | null;
  createdAt: string;
  appliedAt?: string | null;
  error?: string | null;
};

export type TTradeBatchSummary = {
  total: number;
  completed: number;
  completionRate: number | null;
  winning: number;
  winRate: number | null;
  feesCny: number | null;
  netProfitCny: number | null;
  averageHoldingHours: number | null;
  capitalUtilizationPct: number | null;
  coverage: number;
};

type WorkspaceSection = 'CURRENT' | 'HISTORY';
type CurrentFilter = 'ALL' | 'ACTIVE' | 'EXITING' | 'EXCEPTION';
type ResultFilter = 'ALL' | 'COMPLETED' | 'WIN' | 'LOSS' | 'EXCEPTION';

const EXITING_STATUSES = new Set([
  'EXIT_TRIGGERED',
  'EXIT_SUBMITTED',
  'EXIT_PARTIAL',
]);

const EXCEPTION_STATUSES = new Set([
  'ENTRY_REJECTED',
  'EXIT_REJECTED',
  'RECONCILE_REQUIRED',
  'KILL_SWITCHED',
  'LIQUIDATION_FAILED',
  'FAILED',
  'ERROR',
]);

const CANCELLABLE_STATUSES = new Set([
  'ENTRY_QUEUED',
  'ENTRY_SUBMITTED',
  'ENTRY_PARTIAL',
  'EXIT_TRIGGERED',
  'EXIT_SUBMITTED',
  'EXIT_PARTIAL',
]);

const executionModeLabels: Record<TTradeExecutionMode, string> = {
  LIVE: '实盘',
  PAPER: '模拟',
  BACKTEST: '回测',
};

const metricBasisMeta: Record<
  TTradeMetricBasis,
  { label: string; detail: string; className: string }
> = {
  RULE_ESTIMATE: {
    label: '估算',
    detail: '按批次成交与规则费率快照估算，最终以券商结算为准。',
    className: 'border-blue-400/20 bg-blue-400/[0.06] text-blue-200',
  },
  BACKTEST_MODEL: {
    label: '回测模型',
    detail: '由 BACKTEST_BROKER 的确定性成本模型计算。',
    className: 'border-cyan-400/20 bg-cyan-400/[0.06] text-cyan-200',
  },
  LEGACY_BACKFILL: {
    label: '历史回填',
    detail: '由批次历史事实回填；无法恢复的字段保持空值。',
    className: 'border-amber-400/20 bg-amber-400/[0.06] text-amber-200',
  },
  INCOMPLETE: {
    label: '指标不完整',
    detail: '该批次缺少稳定费率或时间事实，未套用当前配置补算。',
    className: 'border-white/10 bg-white/[0.04] text-slate-400',
  },
};

function upper(value: string | null | undefined) {
  return String(value || '').toUpperCase();
}

function isExceptionBatch(batch: TTradePositionBatch) {
  return (
    Boolean(batch.exceptionReason) ||
    EXCEPTION_STATUSES.has(upper(batch.status))
  );
}

function isExitingBatch(batch: TTradePositionBatch) {
  return EXITING_STATUSES.has(upper(batch.status));
}

function resolvedExecutionMode(
  batch: TTradePositionBatch,
  mode: 'LIVE' | 'REPLAY',
  defaultExecutionMode?: TTradeExecutionMode
) {
  return (
    batch.executionMode ||
    defaultExecutionMode ||
    (mode === 'REPLAY' ? 'BACKTEST' : 'LIVE')
  );
}

function inferredMetrics(
  batch: TTradePositionBatch,
  mode: 'LIVE' | 'REPLAY'
): TTradeBatchMetrics {
  if (batch.metrics) return batch.metrics;
  if (mode === 'REPLAY') {
    if (isCurrentTTradeBatch(batch)) {
      return {
        metricBasis: 'INCOMPLETE',
        entryCapitalCny: batch.entryAvgPrice * batch.entryFilledVolume,
        totalFeesCny: null,
        realizedNetProfitCny: null,
        markToMarketNetProfitCny: null,
        netReturnPct: null,
        holdingHours: null,
        capitalUtilizationPct: null,
      };
    }
    return {
      metricBasis: 'BACKTEST_MODEL',
      entryCapitalCny: batch.entryAvgPrice * batch.entryFilledVolume,
      totalFeesCny: null,
      realizedNetProfitCny: batch.netProfit ?? null,
      markToMarketNetProfitCny: batch.netProfit ?? null,
      netReturnPct: batch.lastNetProfitPct,
      holdingHours: null,
      capitalUtilizationPct: null,
    };
  }
  if (!isCurrentTTradeBatch(batch)) {
    return {
      metricBasis: 'INCOMPLETE',
      entryCapitalCny: batch.entryAvgPrice * batch.entryFilledVolume,
      totalFeesCny: null,
      realizedNetProfitCny: batch.netProfit ?? null,
      markToMarketNetProfitCny: null,
      netReturnPct: batch.lastNetProfitPct,
      holdingHours: null,
      capitalUtilizationPct: null,
    };
  }
  return {
    metricBasis: 'INCOMPLETE',
    entryCapitalCny: batch.entryAvgPrice * batch.entryFilledVolume,
    totalFeesCny: null,
    realizedNetProfitCny: null,
    markToMarketNetProfitCny: null,
    netReturnPct: batch.lastNetProfitPct,
    holdingHours: null,
    capitalUtilizationPct: null,
  };
}

function hasTrustedPrice(
  batch: TTradePositionBatch,
  mode: 'LIVE' | 'REPLAY'
): batch is TTradePositionBatch & { lastPrice: number } {
  if (batch.lastPrice == null || !Number.isFinite(batch.lastPrice))
    return false;
  if (mode === 'REPLAY') return true;
  return upper(batch.priceQuality) === 'FRESH' && Boolean(batch.priceAsOf);
}

function batchTimestamp(batch: TTradePositionBatch) {
  return (
    batch.terminalAt ||
    batch.closedAt ||
    batch.entryFilledAt ||
    batch.updatedAt ||
    batch.createdAt ||
    null
  );
}

function dateInput(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function clampDateInput(value: string, min: string, max: string) {
  if (!value || value < min) return min;
  if (value > max) return max;
  return value;
}

function defaultHistoryRange(
  mode: 'LIVE' | 'REPLAY',
  batches: readonly TTradePositionBatch[]
) {
  if (mode === 'REPLAY') {
    const timestamps = batches
      .flatMap(batch => [batch.entryFilledAt, batch.terminalAt, batch.closedAt])
      .filter((value): value is string => Boolean(value))
      .map(value => Date.parse(value))
      .filter(Number.isFinite);
    if (timestamps.length > 0) {
      return {
        start: dateInput(new Date(Math.min(...timestamps))),
        end: dateInput(new Date(Math.max(...timestamps))),
      };
    }
  }
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 30);
  return { start: dateInput(start), end: dateInput(end) };
}

function formattedDateTime(value?: string | null) {
  if (!value) return '--';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  return new Date(timestamp).toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function cny(value: number | null | undefined, signed = false) {
  if (value == null || !Number.isFinite(value)) return '--';
  return `${signed && value >= 0 ? '+' : ''}¥${formatNumber(value, 2)}`;
}

function percent(value: number | null | undefined) {
  return value == null || !Number.isFinite(value)
    ? '--'
    : formatSignedPercent(value);
}

function summaryFor(
  rows: readonly TTradePositionBatch[],
  mode: 'LIVE' | 'REPLAY'
) {
  const metrics = rows.map(batch => inferredMetrics(batch, mode));
  const covered = metrics.filter(
    item =>
      item.metricBasis !== 'INCOMPLETE' && item.realizedNetProfitCny != null
  );
  const completed = rows.filter(
    batch => upper(batch.status) === 'CLOSED'
  ).length;
  const winning = covered.filter(
    item => (item.realizedNetProfitCny || 0) > 0
  ).length;
  const sum = (values: Array<number | null | undefined>) =>
    values.reduce<number>((total, value) => total + (value || 0), 0);
  const capitalWeightedAverage = (
    field: 'holdingHours' | 'capitalUtilizationPct'
  ) => {
    const available = covered.flatMap(item => {
      const value = item[field];
      const weight = item.entryCapitalCny;
      return value != null &&
        Number.isFinite(value) &&
        weight != null &&
        Number.isFinite(weight) &&
        weight > 0
        ? [{ value, weight }]
        : [];
    });
    const totalWeight = sum(available.map(item => item.weight));
    return totalWeight > 0
      ? sum(available.map(item => item.value * item.weight)) / totalWeight
      : null;
  };
  return {
    total: rows.length,
    completed,
    completionRate: rows.length ? (completed / rows.length) * 100 : null,
    winning,
    winRate: covered.length ? (winning / covered.length) * 100 : null,
    feesCny:
      covered.length > 0 && covered.every(item => item.totalFeesCny != null)
        ? sum(covered.map(item => item.totalFeesCny))
        : null,
    netProfitCny: covered.length
      ? sum(covered.map(item => item.realizedNetProfitCny))
      : null,
    averageHoldingHours: capitalWeightedAverage('holdingHours'),
    capitalUtilizationPct: capitalWeightedAverage('capitalUtilizationPct'),
    coverage: covered.length,
  } satisfies TTradeBatchSummary;
}

function coverageSummary(summary: TTradeBatchSummary, hasMore: boolean) {
  const prefix = hasMore ? '当前已加载 · ' : '';
  if (summary.total === 0) return `${prefix}暂无指标批次`;
  if (summary.coverage === 0) {
    return `${prefix}0/${summary.total} 批指标完整 · 收益与税费暂无可比口径`;
  }
  if (summary.coverage < summary.total) {
    return `${prefix}仅合计已覆盖批次 · ${summary.coverage}/${summary.total} 批指标完整`;
  }
  return `${prefix}${summary.coverage}/${summary.total} 批指标完整`;
}

function MetricBasisBadge({ basis }: { basis: TTradeMetricBasis }) {
  const meta = metricBasisMeta[basis];
  return (
    <span
      className={cn(
        'inline-flex border px-1.5 py-0.5 text-ui-micro font-bold',
        meta.className
      )}
    >
      {meta.label}
    </span>
  );
}

function Kpi({
  label,
  tone,
  value,
}: {
  label: string;
  tone?: string;
  value: React.ReactNode;
}) {
  return (
    <div className="min-w-0 border-r border-white/[0.05] px-3 py-2.5 last:border-r-0">
      <div className="text-ui-caption text-slate-600">{label}</div>
      <div
        className={cn(
          'mt-1 truncate font-mono text-ui-title font-black tabular-nums text-slate-200',
          tone
        )}
      >
        {value}
      </div>
    </div>
  );
}

function StatusBadge({ batch }: { batch: TTradePositionBatch }) {
  const exception = isExceptionBatch(batch);
  const exiting = isExitingBatch(batch);
  return (
    <span
      className={cn(
        'inline-flex border px-2 py-1 text-ui-caption font-bold',
        exception
          ? 'border-rose-400/20 bg-rose-400/[0.06] text-rose-200'
          : exiting
            ? 'border-amber-400/20 bg-amber-400/[0.06] text-amber-200'
            : 'border-white/10 bg-white/[0.035] text-slate-300'
      )}
    >
      {batchStatusLabels[batch.status] || batch.status}
    </span>
  );
}

function BatchDetailDrawer({
  batch,
  events,
  mode,
  onOpenChange,
  onViewActivity,
  open,
  returnFocusRef,
}: {
  batch: TTradePositionBatch | null;
  events: readonly TTradePositionEvent[];
  mode: 'LIVE' | 'REPLAY';
  onOpenChange: (open: boolean) => void;
  onViewActivity?: (batchId: string) => void;
  open: boolean;
  returnFocusRef: React.RefObject<HTMLButtonElement | null>;
}) {
  const metrics = batch ? inferredMetrics(batch, mode) : null;
  const batchEvents = React.useMemo(
    () =>
      batch
        ? events
            .filter(event => event.batchId === batch.batchId)
            .sort(
              (left, right) =>
                Date.parse(right.createdAt) - Date.parse(left.createdAt)
            )
        : [],
    [batch, events]
  );
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        closeLabel="关闭批次详情"
        className="border-white/10 bg-[#07111f] p-0 text-slate-200"
        style={{ width: 'min(720px, 56vw)', maxWidth: 'none' }}
        onCloseAutoFocus={event => {
          event.preventDefault();
          returnFocusRef.current?.focus();
        }}
      >
        {batch && metrics && (
          <div className="flex h-full min-h-0 flex-col">
            <SheetHeader className="shrink-0 border-b border-white/[0.07] bg-[#091422] px-ui-section py-3 pr-12 text-left">
              <div className="flex items-center gap-2">
                <SheetTitle className="text-ui-heading font-black text-slate-100">
                  {batch.stockCode} · 批次详情
                </SheetTitle>
                <StatusBadge batch={batch} />
                <MetricBasisBadge basis={metrics.metricBasis} />
              </div>
              <SheetDescription className="font-mono text-ui-caption text-slate-500">
                {batch.batchId} · {batch.strategyRunId}
              </SheetDescription>
            </SheetHeader>

            <div className="min-h-0 flex-1 overflow-y-auto p-ui-section custom-scrollbar">
              <section className="grid grid-cols-4 border border-white/[0.06] bg-[#091422]">
                <Kpi label="入场资本" value={cny(metrics.entryCapitalCny)} />
                <Kpi label="税费" value={cny(metrics.totalFeesCny)} />
                <Kpi
                  label={
                    isCurrentTTradeBatch(batch)
                      ? mode === 'REPLAY'
                        ? '模型浮动净收益'
                        : '规则估算浮盈'
                      : mode === 'REPLAY'
                        ? '模型净收益'
                        : '规则估算净收益'
                  }
                  value={cny(
                    isCurrentTTradeBatch(batch)
                      ? hasTrustedPrice(batch, mode)
                        ? metrics.markToMarketNetProfitCny
                        : null
                      : metrics.realizedNetProfitCny,
                    true
                  )}
                  tone={financialToneClass(
                    isCurrentTTradeBatch(batch)
                      ? hasTrustedPrice(batch, mode)
                        ? metrics.markToMarketNetProfitCny
                        : null
                      : metrics.realizedNetProfitCny,
                    isCurrentTTradeBatch(batch) ? 'holding' : 'market'
                  )}
                />
                <Kpi label="净收益率" value={percent(metrics.netReturnPct)} />
              </section>

              <div className="mt-3 grid grid-cols-2 gap-3">
                <section className="border border-white/[0.06] bg-[#091422] p-3">
                  <h3 className="text-ui-label font-black text-slate-200">
                    成交与仓位
                  </h3>
                  <dl className="mt-3 space-y-2 text-ui-caption">
                    {[
                      [
                        '买入成交',
                        `${batch.entryFilledVolume.toLocaleString()} 股 @ ${formatNumber(batch.entryAvgPrice, 3)}`,
                      ],
                      [
                        '卖出成交',
                        `${batch.exitFilledVolume.toLocaleString()} 股 @ ${formatNumber(batch.exitAvgPrice, 3)}`,
                      ],
                      [
                        '当前活跃仓',
                        `${batch.activeVolume.toLocaleString()} 股`,
                      ],
                      ['首次成交', formattedDateTime(batch.entryFilledAt)],
                      [
                        '批次创建 / 投影时间',
                        formattedDateTime(batch.createdAt),
                      ],
                      ['成交关闭', formattedDateTime(batch.closedAt)],
                      ['终态时间', formattedDateTime(batch.terminalAt)],
                      [
                        '退出原因',
                        batch.exceptionReason || batch.exitReason || '--',
                      ],
                    ].map(([label, value]) => (
                      <div
                        key={label}
                        className="flex justify-between gap-3 border-b border-white/[0.04] pb-2 last:border-b-0"
                      >
                        <dt className="text-slate-600">{label}</dt>
                        <dd className="text-right font-mono text-slate-300">
                          {value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </section>

                <section className="border border-white/[0.06] bg-[#091422] p-3">
                  <h3 className="text-ui-label font-black text-slate-200">
                    关联标识
                  </h3>
                  <dl className="mt-3 space-y-2 text-ui-caption">
                    {[
                      ['入场 Client Order', batch.entryClientOrderId || '--'],
                      ['入场券商委托', batch.entryBrokerOrderId || '--'],
                      ['退出 Client Order', batch.exitClientOrderId || '--'],
                      ['退出券商委托', batch.exitBrokerOrderId || '--'],
                      ['入场 Intent', batch.entryIntentId || '--'],
                      ['退出 Intent', batch.exitIntentId || '--'],
                    ].map(([label, value]) => (
                      <div
                        key={label}
                        className="border-b border-white/[0.04] pb-2 last:border-b-0"
                      >
                        <dt className="text-slate-600">{label}</dt>
                        <dd className="mt-1 break-all font-mono text-slate-300">
                          {value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </section>
              </div>

              <section className="mt-3 border border-white/[0.06] bg-[#091422] p-3">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-ui-label font-black text-slate-200">
                    指标口径
                  </h3>
                  <MetricBasisBadge basis={metrics.metricBasis} />
                </div>
                <p className="mt-2 text-ui-caption leading-5 text-slate-500">
                  {metricBasisMeta[metrics.metricBasis].detail}
                </p>
                <div className="mt-3 grid grid-cols-3 gap-2">
                  <Kpi
                    label="持有时间"
                    value={
                      metrics.holdingHours == null
                        ? '--'
                        : `${formatNumber(metrics.holdingHours, 1)}h`
                    }
                  />
                  <Kpi
                    label="资金利用率"
                    value={
                      metrics.capitalUtilizationPct == null
                        ? '--'
                        : `${formatNumber(metrics.capitalUtilizationPct, 1)}%`
                    }
                  />
                  <Kpi
                    label="数据来源"
                    value={
                      metrics.metricBasis === 'BACKTEST_MODEL'
                        ? 'BACKTEST_BROKER'
                        : '批次投影'
                    }
                  />
                </div>
              </section>

              <section className="mt-3 border border-white/[0.06] bg-[#091422]">
                <div className="flex items-center justify-between border-b border-white/[0.05] px-3 py-2.5">
                  <h3 className="text-ui-label font-black text-slate-200">
                    真实生命周期事件
                  </h3>
                  <span className="font-mono text-ui-caption text-slate-600">
                    {batchEvents.length} 条
                  </span>
                </div>
                {batchEvents.length ? (
                  <ol className="divide-y divide-white/[0.05]">
                    {batchEvents.map(event => (
                      <li
                        key={event.eventId}
                        className="grid gap-3 px-3 py-2.5 text-ui-caption"
                        style={{ gridTemplateColumns: '112px 1fr 108px' }}
                      >
                        <time className="font-mono text-slate-500">
                          {formattedDateTime(event.createdAt)}
                        </time>
                        <div className="min-w-0">
                          <div className="font-bold text-slate-200">
                            {event.eventType}
                          </div>
                          <div className="mt-1 break-all font-mono text-ui-micro text-slate-600">
                            {event.brokerOrderId ||
                              event.clientOrderId ||
                              event.eventId}
                          </div>
                          {event.error && (
                            <div className="mt-1 text-rose-200">
                              {event.error}
                            </div>
                          )}
                        </div>
                        <span className="text-right font-mono text-slate-400">
                          {event.status}
                        </span>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="px-3 py-ui-panel text-center text-ui-caption text-slate-600">
                    当前查询结果中没有该批次的持久化事件；未生成推测时间点。
                  </div>
                )}
              </section>
            </div>

            {onViewActivity && (
              <div className="shrink-0 border-t border-white/[0.07] bg-[#091422] p-3 text-right">
                <Button
                  type="button"
                  size="sm"
                  className="h-control-compact rounded-sm bg-blue-600 px-3 text-ui-caption hover:bg-blue-500"
                  onClick={() => onViewActivity(batch.batchId)}
                >
                  在运行动态中查看
                  <ExternalLink className="ml-1.5 h-3.5 w-3.5" />
                </Button>
              </div>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function CurrentTable({
  actionLoading,
  instrumentNames,
  loading,
  mode,
  onCancelOrder,
  onOpenBatch,
  rows,
}: {
  actionLoading: boolean;
  instrumentNames: ReadonlyMap<string, string>;
  loading: boolean;
  mode: 'LIVE' | 'REPLAY';
  onCancelOrder?: (clientOrderId: string) => void;
  onOpenBatch: (batch: TTradePositionBatch, trigger: HTMLButtonElement) => void;
  rows: readonly TTradePositionBatch[];
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
      <table
        className="w-full text-left text-ui-label"
        style={{ minWidth: 1120 }}
      >
        <thead className="sticky top-0 z-10 bg-[#0b1628] text-ui-caption font-bold text-slate-600">
          <tr>
            <th className="px-ui-section py-2.5">标的 / 批次</th>
            <th className="px-3 py-2.5">生命周期</th>
            <th className="px-3 py-2.5 text-right">买入成交</th>
            <th className="px-3 py-2.5 text-right">活跃仓</th>
            <th className="px-3 py-2.5 text-right">均价 / 最新价</th>
            <th className="px-3 py-2.5 text-right">浮动净收益 / 峰值</th>
            <th className="px-3 py-2.5 text-right">保护线</th>
            <th className="px-ui-section py-2.5 text-right">委托 / 操作</th>
          </tr>
        </thead>
        <tbody>
          {loading && rows.length === 0 ? (
            <tr>
              <td
                colSpan={8}
                className="px-ui-section py-ui-empty text-center text-slate-600"
                role="status"
              >
                <Loader2 className="mr-2 inline-block h-4 w-4 animate-spin motion-reduce:animate-none" />
                正在读取当前仓位…
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={8}
                className="px-ui-section py-ui-empty text-center text-slate-600"
              >
                当前筛选下没有仓位批次
              </td>
            </tr>
          ) : (
            rows.map(batch => {
              const metrics = inferredMetrics(batch, mode);
              const exiting = isExitingBatch(batch);
              const clientOrderId = exiting
                ? batch.exitClientOrderId
                : batch.entryClientOrderId;
              const brokerOrderId = exiting
                ? batch.exitBrokerOrderId
                : batch.entryBrokerOrderId;
              const canCancel =
                mode === 'LIVE' &&
                Boolean(clientOrderId) &&
                CANCELLABLE_STATUSES.has(upper(batch.status));
              return (
                <tr
                  key={batch.batchId}
                  className="border-b border-white/[0.04] hover:bg-blue-400/[0.035]"
                >
                  <td className="px-ui-section py-2.5">
                    <div className="font-bold text-slate-100">
                      {instrumentNames.get(batch.stockCode.toUpperCase()) ||
                        batch.stockCode}
                    </div>
                    <div className="mt-1 font-mono text-ui-micro text-slate-600">
                      {batch.stockCode} · {batch.batchId.slice(0, 12)}
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusBadge batch={batch} />
                    {(batch.exceptionReason || batch.exitReason) && (
                      <div
                        className="mt-1 truncate text-ui-micro text-amber-200/80"
                        style={{ maxWidth: '13rem' }}
                      >
                        {batch.exceptionReason || batch.exitReason}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-300">
                    {batch.entryFilledVolume.toLocaleString()} /{' '}
                    {batch.targetVolume.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono font-bold text-cyan-200">
                    {batch.activeVolume.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-300">
                    {formatNumber(batch.entryAvgPrice, 3)}
                    <span className="mx-1 text-slate-700">/</span>
                    {hasTrustedPrice(batch, mode) ? (
                      formatNumber(batch.lastPrice, 3)
                    ) : (
                      <span className="text-slate-600">暂无可信行情</span>
                    )}
                  </td>
                  <td
                    className={cn(
                      'px-3 py-2.5 text-right font-mono',
                      financialToneClass(
                        metrics.markToMarketNetProfitCny,
                        'holding'
                      )
                    )}
                  >
                    {hasTrustedPrice(batch, mode)
                      ? cny(metrics.markToMarketNetProfitCny, true)
                      : '暂无可信行情'}
                    <div className="mt-1 flex items-center justify-end gap-1.5 text-ui-micro text-slate-600">
                      <span>峰值 {percent(batch.peakNetProfitPct)}</span>
                      {metrics.metricBasis === 'RULE_ESTIMATE' && (
                        <MetricBasisBadge basis={metrics.metricBasis} />
                      )}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-400">
                    {batch.trailingFloorPct == null
                      ? '未武装'
                      : formatSignedPercent(batch.trailingFloorPct)}
                  </td>
                  <td className="px-ui-section py-2.5 text-right">
                    <div className="font-mono text-ui-micro text-slate-600">
                      {mode === 'REPLAY'
                        ? 'BACKTEST_BROKER'
                        : brokerOrderId ||
                          clientOrderId?.slice(0, 12) ||
                          '尚未委托'}
                    </div>
                    <div className="mt-1.5 flex justify-end gap-1.5">
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        className="h-control-compact rounded-sm px-2 text-ui-micro text-blue-200"
                        onClick={event =>
                          onOpenBatch(batch, event.currentTarget)
                        }
                      >
                        详情
                      </Button>
                      {canCancel && clientOrderId && onCancelOrder && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={actionLoading}
                          onClick={() => onCancelOrder(clientOrderId)}
                          className="h-control-compact rounded-sm border-white/10 px-2 text-ui-micro"
                        >
                          申请撤单
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

function HistoryTable({
  defaultExecutionMode,
  instrumentNames,
  loading,
  mode,
  onOpenBatch,
  rows,
}: {
  defaultExecutionMode?: TTradeExecutionMode;
  instrumentNames: ReadonlyMap<string, string>;
  loading: boolean;
  mode: 'LIVE' | 'REPLAY';
  onOpenBatch: (batch: TTradePositionBatch, trigger: HTMLButtonElement) => void;
  rows: readonly TTradePositionBatch[];
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
      <table
        className="w-full text-left text-ui-label"
        style={{ minWidth: 1260 }}
      >
        <thead className="sticky top-0 z-10 bg-[#0b1628] text-ui-caption font-bold text-slate-600">
          <tr>
            <th className="px-ui-section py-2.5">标的 / 批次</th>
            <th className="px-3 py-2.5">来源 / 终态</th>
            <th className="px-3 py-2.5">入场 / 终态</th>
            <th className="px-3 py-2.5 text-right">买入</th>
            <th className="px-3 py-2.5 text-right">卖出</th>
            <th className="px-3 py-2.5 text-right">
              {mode === 'REPLAY' ? '模型税费' : '规则估算费用'}
            </th>
            <th className="px-3 py-2.5 text-right">持有 / 利用率</th>
            <th className="px-3 py-2.5 text-right">
              {mode === 'REPLAY'
                ? '模型净收益 / 收益率'
                : '规则估算净收益 / 收益率'}
            </th>
            <th className="px-ui-section py-2.5">退出原因</th>
          </tr>
        </thead>
        <tbody>
          {loading && rows.length === 0 ? (
            <tr>
              <td
                colSpan={9}
                className="px-ui-section py-ui-empty text-center text-slate-600"
                role="status"
              >
                <Loader2 className="mr-2 inline-block h-4 w-4 animate-spin motion-reduce:animate-none" />
                正在读取历史批次…
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={9}
                className="px-ui-section py-ui-empty text-center text-slate-600"
              >
                当前筛选下没有历史批次
              </td>
            </tr>
          ) : (
            rows.map(batch => {
              const metrics = inferredMetrics(batch, mode);
              const profit = metrics.realizedNetProfitCny;
              const executionMode = resolvedExecutionMode(
                batch,
                mode,
                defaultExecutionMode
              );
              return (
                <tr
                  key={batch.batchId}
                  className="border-b border-white/[0.04] hover:bg-blue-400/[0.035]"
                >
                  <td className="px-ui-section py-2.5">
                    <button
                      type="button"
                      className="cursor-pointer text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                      onClick={event => onOpenBatch(batch, event.currentTarget)}
                    >
                      <span className="block font-bold text-slate-100">
                        {instrumentNames.get(batch.stockCode.toUpperCase()) ||
                          batch.stockCode}
                      </span>
                      <span className="mt-1 block font-mono text-ui-micro text-blue-300">
                        {batch.stockCode} · {batch.batchId.slice(0, 12)}{' '}
                        <ArrowRight className="inline h-3 w-3" />
                      </span>
                    </button>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <span className="border border-white/10 px-1.5 py-0.5 text-ui-micro text-slate-400">
                        {executionModeLabels[executionMode]}
                      </span>
                      <StatusBadge batch={batch} />
                    </div>
                    <div className="mt-1">
                      <MetricBasisBadge basis={metrics.metricBasis} />
                    </div>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-ui-caption text-slate-400">
                    <div>
                      {formattedDateTime(
                        batch.entryFilledAt || batch.createdAt
                      )}
                    </div>
                    <div className="mt-1 text-slate-600">
                      {formattedDateTime(batch.terminalAt || batch.closedAt)}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-300">
                    {batch.entryFilledVolume.toLocaleString()} @{' '}
                    {formatNumber(batch.entryAvgPrice, 3)}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-300">
                    {batch.exitFilledVolume.toLocaleString()} @{' '}
                    {formatNumber(batch.exitAvgPrice, 3)}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-400">
                    {cny(metrics.totalFeesCny)}
                  </td>
                  <td className="px-3 py-2.5 text-right font-mono text-slate-400">
                    {metrics.holdingHours == null
                      ? '--'
                      : `${formatNumber(metrics.holdingHours, 1)}h`}
                    <div className="mt-1 text-ui-micro text-slate-600">
                      {metrics.capitalUtilizationPct == null
                        ? '--'
                        : `${formatNumber(metrics.capitalUtilizationPct, 1)}%`}
                    </div>
                  </td>
                  <td
                    className={cn(
                      'px-3 py-2.5 text-right font-mono',
                      financialToneClass(profit)
                    )}
                  >
                    {cny(profit, true)}
                    <div className="mt-1 text-ui-micro text-slate-600">
                      {percent(metrics.netReturnPct)}
                    </div>
                  </td>
                  <td
                    className="px-ui-section py-2.5 text-slate-400"
                    style={{ maxWidth: '13rem' }}
                  >
                    <div className="truncate">
                      {batch.exceptionReason || batch.exitReason || '--'}
                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

export function TTradePositionsView({
  actionLoading = false,
  batches,
  defaultExecutionMode,
  error,
  events = [],
  focusBatchId,
  hasMore = false,
  historyScopeKey,
  instrumentNames,
  loading,
  loadingMore = false,
  mode,
  onCancelOrder,
  onFocusBatchHandled,
  onInspectBatch,
  onLoadMore,
  onRefresh,
  onViewActivity,
}: {
  actionLoading?: boolean;
  batches: readonly TTradePositionBatch[];
  defaultExecutionMode?: TTradeExecutionMode;
  error?: string | null;
  events?: readonly TTradePositionEvent[];
  focusBatchId?: string | null;
  hasMore?: boolean;
  historyScopeKey?: string | null;
  instrumentNames: ReadonlyMap<string, string>;
  loading: boolean;
  loadingMore?: boolean;
  mode: 'LIVE' | 'REPLAY';
  onCancelOrder?: (clientOrderId: string) => void;
  onFocusBatchHandled?: () => void;
  onInspectBatch?: (batchId: string | null) => void;
  onLoadMore?: () => void;
  onRefresh: () => void;
  onViewActivity?: (batchId: string) => void;
  summary?: TTradeBatchSummary | null;
}) {
  const replay = mode === 'REPLAY';
  const [section, setSection] = React.useState<WorkspaceSection>('CURRENT');
  const [currentFilter, setCurrentFilter] =
    React.useState<CurrentFilter>('ALL');
  const initialRange = React.useMemo(
    () => defaultHistoryRange(mode, batches),
    [batches, mode]
  );
  const [startDate, setStartDate] = React.useState(initialRange.start);
  const [endDate, setEndDate] = React.useState(initialRange.end);
  const [source, setSource] = React.useState<'ALL' | TTradeExecutionMode>(
    replay ? 'BACKTEST' : 'ALL'
  );
  const [result, setResult] = React.useState<ResultFilter>('ALL');
  const [keyword, setKeyword] = React.useState('');
  const [selectedBatch, setSelectedBatch] =
    React.useState<TTradePositionBatch | null>(null);
  const returnFocusRef = React.useRef<HTMLButtonElement | null>(null);
  const historyRangeEditedRef = React.useRef(false);
  const historyDatasetKey = React.useMemo(() => {
    if (!replay) return `LIVE:${historyScopeKey || 'default'}`;
    const runIds = Array.from(
      new Set(batches.map(batch => batch.strategyRunId))
    ).sort();
    return `REPLAY:${historyScopeKey || 'default'}:${runIds.join(',') || 'empty'}`;
  }, [batches, historyScopeKey, replay]);
  const lastHistoryDatasetKeyRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    if (lastHistoryDatasetKeyRef.current !== historyDatasetKey) {
      lastHistoryDatasetKeyRef.current = historyDatasetKey;
      historyRangeEditedRef.current = false;
      setStartDate(initialRange.start);
      setEndDate(initialRange.end);
      setSource(replay ? 'BACKTEST' : 'ALL');
      setResult('ALL');
      setKeyword('');
      return;
    }
    if (replay && !historyRangeEditedRef.current) {
      setStartDate(initialRange.start);
      setEndDate(initialRange.end);
    }
  }, [historyDatasetKey, initialRange.end, initialRange.start, replay]);

  const startDateMax = replay
    ? endDate
    : endDate && endDate < initialRange.end
      ? endDate
      : initialRange.end;
  const endDateMin = replay
    ? startDate
    : startDate && startDate > initialRange.start
      ? startDate
      : initialRange.start;

  const currentBatches = React.useMemo(
    () => batches.filter(isCurrentTTradeBatch),
    [batches]
  );
  const historyBatches = React.useMemo(
    () => batches.filter(batch => !isCurrentTTradeBatch(batch)),
    [batches]
  );
  const currentRows = React.useMemo(
    () =>
      currentBatches.filter(batch => {
        if (currentFilter === 'EXCEPTION') return isExceptionBatch(batch);
        if (currentFilter === 'EXITING') return isExitingBatch(batch);
        if (currentFilter === 'ACTIVE') {
          return !isExceptionBatch(batch) && !isExitingBatch(batch);
        }
        return true;
      }),
    [currentBatches, currentFilter]
  );
  const historyRows = React.useMemo(() => {
    const query = keyword.trim().toLowerCase();
    const start = startDate
      ? Date.parse(`${startDate}T00:00:00`)
      : Number.NEGATIVE_INFINITY;
    const end = endDate
      ? Date.parse(`${endDate}T23:59:59.999`)
      : Number.POSITIVE_INFINITY;
    return historyBatches
      .filter(batch => {
        const timestamp = batchTimestamp(batch);
        const epoch = timestamp ? Date.parse(timestamp) : null;
        if (
          epoch != null &&
          Number.isFinite(epoch) &&
          (epoch < start || epoch > end)
        ) {
          return false;
        }
        const executionMode = resolvedExecutionMode(
          batch,
          mode,
          defaultExecutionMode
        );
        if (source !== 'ALL' && executionMode !== source) return false;
        const metrics = inferredMetrics(batch, mode);
        if (result === 'COMPLETED' && upper(batch.status) !== 'CLOSED') {
          return false;
        }
        if (result === 'EXCEPTION' && !isExceptionBatch(batch)) return false;
        if (
          result === 'WIN' &&
          (metrics.realizedNetProfitCny == null ||
            metrics.realizedNetProfitCny <= 0)
        ) {
          return false;
        }
        if (
          result === 'LOSS' &&
          (metrics.realizedNetProfitCny == null ||
            metrics.realizedNetProfitCny >= 0)
        ) {
          return false;
        }
        if (!query) return true;
        return [
          batch.stockCode,
          batch.batchId,
          batch.strategyRunId,
          batch.entryClientOrderId,
          batch.exitClientOrderId,
          batch.entryBrokerOrderId,
          batch.exitBrokerOrderId,
          batch.exitReason,
          batch.exceptionReason,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase()
          .includes(query);
      })
      .sort((left, right) => {
        const leftTimestamp = batchTimestamp(left);
        const rightTimestamp = batchTimestamp(right);
        return (
          (rightTimestamp ? Date.parse(rightTimestamp) : 0) -
          (leftTimestamp ? Date.parse(leftTimestamp) : 0)
        );
      });
  }, [
    defaultExecutionMode,
    endDate,
    historyBatches,
    keyword,
    mode,
    result,
    source,
    startDate,
  ]);

  const computedSummary = React.useMemo(
    () => summaryFor(historyRows, mode),
    [historyRows, mode]
  );
  const visibleSummary = computedSummary;
  const currentMetrics = currentBatches.map(batch =>
    inferredMetrics(batch, mode)
  );
  const activeVolume = currentBatches.reduce(
    (total, batch) => total + batch.activeVolume,
    0
  );
  const occupiedCapital = currentMetrics.reduce(
    (total, item) => total + (item.entryCapitalCny || 0),
    0
  );
  const floatingProfitValues = currentMetrics
    .map((item, index) =>
      hasTrustedPrice(currentBatches[index], mode)
        ? item.markToMarketNetProfitCny
        : null
    )
    .filter((value): value is number => value != null);
  const floatingProfit = floatingProfitValues.length
    ? floatingProfitValues.reduce((total, value) => total + value, 0)
    : null;

  const openBatch = React.useCallback(
    (batch: TTradePositionBatch, trigger: HTMLButtonElement) => {
      returnFocusRef.current = trigger;
      setSelectedBatch(batch);
      onInspectBatch?.(batch.batchId);
    },
    [onInspectBatch]
  );

  React.useEffect(() => {
    if (!focusBatchId) return;
    const batch = batches.find(item => item.batchId === focusBatchId);
    if (batch) {
      setSection(isCurrentTTradeBatch(batch) ? 'CURRENT' : 'HISTORY');
      setSelectedBatch(batch);
      onInspectBatch?.(batch.batchId);
    }
    onFocusBatchHandled?.();
  }, [batches, focusBatchId, onFocusBatchHandled, onInspectBatch]);

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col text-slate-200">
      {error && (
        <div
          role="alert"
          className="flex shrink-0 items-start justify-between gap-3 border-b border-rose-400/20 bg-rose-400/[0.06] px-ui-section py-2.5 text-ui-caption text-rose-100"
        >
          <span>
            {replay
              ? '回放批次读取失败；未使用实盘批次回退。'
              : '批次读取失败；仍显示上次成功读取的结果。'}
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-control-compact px-2 text-ui-micro text-rose-100"
            onClick={onRefresh}
          >
            重试
          </Button>
        </div>
      )}
      {!error && loading && batches.length > 0 && (
        <div
          role="status"
          aria-busy="true"
          className="flex shrink-0 items-center gap-2 border-b border-blue-400/15 bg-blue-400/[0.04] px-ui-section py-2 text-ui-micro text-blue-100"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
          正在刷新批次，暂保留上次结果…
        </div>
      )}
      {replay && (
        <div className="shrink-0 border-b border-blue-400/15 bg-blue-400/[0.04] px-ui-section py-2 text-ui-micro text-blue-100">
          隔离回放 · 仓位和批次来自 BACKTEST_BROKER，不读取当前实盘账户
        </div>
      )}

      <header className="shrink-0 border-b border-white/[0.06] bg-[#091422] px-ui-section pt-3">
        <div className="flex items-start justify-between gap-ui-section">
          <div>
            <div className="flex items-center gap-2">
              <WalletCards className="h-4 w-4 text-blue-300" />
              <h1 className="text-ui-title font-black text-slate-100">
                仓位与批次
              </h1>
            </div>
            <p className="mt-1 text-ui-caption text-slate-600">
              当前仓位用于执行处置，历史批次用于复盘收益、成本与真实生命周期。
            </p>
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-control-compact rounded-sm border-white/10 px-3 text-ui-caption"
            onClick={onRefresh}
          >
            刷新批次
          </Button>
        </div>
        <div
          className="mt-3 flex gap-ui-section"
          role="tablist"
          aria-label="仓位与批次视图"
        >
          {(
            [
              [
                'CURRENT',
                replay ? '期末仓位' : '当前仓位',
                currentBatches.length,
              ],
              ['HISTORY', '历史批次', historyBatches.length],
            ] as const
          ).map(([value, label, count]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={section === value}
              onClick={() => setSection(value)}
              className={cn(
                'relative h-control-default cursor-pointer px-1 text-ui-label font-bold transition-colors after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                section === value
                  ? 'text-blue-200 after:bg-blue-400'
                  : 'text-slate-500 hover:text-slate-200'
              )}
            >
              <span>{label}</span>
              <span className="ml-1.5 font-mono text-ui-micro text-slate-600">
                {count}
              </span>
            </button>
          ))}
        </div>
      </header>

      {section === 'CURRENT' ? (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="grid shrink-0 grid-cols-5 border-b border-white/[0.05] bg-[#081321]">
            <Kpi
              label={replay ? '期末未平批次' : '当前批次'}
              value={currentBatches.length}
            />
            <Kpi label="活跃股数" value={activeVolume.toLocaleString()} />
            <Kpi label="占用资金" value={cny(occupiedCapital)} />
            <Kpi
              label={replay ? '模型浮动净收益' : '规则估算浮盈'}
              value={cny(floatingProfit, true)}
              tone={financialToneClass(floatingProfit, 'holding')}
            />
            <Kpi
              label="退出中 / 异常"
              value={`${currentBatches.filter(isExitingBatch).length} / ${currentBatches.filter(isExceptionBatch).length}`}
            />
          </div>
          <div className="flex shrink-0 items-center justify-between gap-3 border-b border-white/[0.05] px-ui-section py-2">
            <div
              className="flex items-center gap-1.5"
              role="group"
              aria-label="当前仓位状态筛选"
            >
              {(
                [
                  ['ALL', '全部'],
                  ['ACTIVE', '持有中'],
                  ['EXITING', '退出中'],
                  ['EXCEPTION', '异常'],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setCurrentFilter(value)}
                  className={cn(
                    'h-control-compact cursor-pointer border px-2.5 text-ui-caption font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                    currentFilter === value
                      ? 'border-blue-400/30 bg-blue-400/10 text-blue-200'
                      : 'border-white/10 text-slate-500 hover:bg-blue-400/[0.04] hover:text-slate-200'
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            <span className="text-ui-caption text-slate-600">
              显示 {currentRows.length} 个批次
            </span>
          </div>
          <CurrentTable
            actionLoading={actionLoading}
            instrumentNames={instrumentNames}
            loading={loading}
            mode={mode}
            onCancelOrder={onCancelOrder}
            onOpenBatch={openBatch}
            rows={currentRows}
          />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="grid shrink-0 grid-cols-6 border-b border-white/[0.05] bg-[#081321]">
            <Kpi label="历史批次" value={visibleSummary.total} />
            <Kpi
              label="完成率"
              value={
                visibleSummary.completionRate == null
                  ? '--'
                  : `${formatNumber(visibleSummary.completionRate, 1)}%`
              }
            />
            <Kpi
              label={replay ? '税费后模型净增量' : '规则估算净收益'}
              value={cny(visibleSummary.netProfitCny, true)}
              tone={financialToneClass(visibleSummary.netProfitCny)}
            />
            <Kpi
              label="胜率"
              value={
                visibleSummary.winRate == null
                  ? '--'
                  : `${formatNumber(visibleSummary.winRate, 1)}%`
              }
            />
            <Kpi
              label="平均持有"
              value={
                visibleSummary.averageHoldingHours == null
                  ? '--'
                  : `${formatNumber(visibleSummary.averageHoldingHours, 1)}h`
              }
            />
            <Kpi
              label="资金利用率"
              value={
                visibleSummary.capitalUtilizationPct == null
                  ? '--'
                  : `${formatNumber(visibleSummary.capitalUtilizationPct, 1)}%`
              }
            />
          </div>
          <div className="flex shrink-0 items-center gap-2 border-b border-white/[0.05] px-ui-section py-2">
            <label className="flex items-center gap-1.5 text-ui-caption text-slate-500">
              <span>从</span>
              <Input
                aria-label="历史开始日期"
                type="date"
                value={startDate}
                min={replay ? undefined : initialRange.start}
                max={startDateMax}
                onChange={event => {
                  historyRangeEditedRef.current = true;
                  setStartDate(
                    replay
                      ? event.target.value
                      : clampDateInput(
                          event.target.value,
                          initialRange.start,
                          startDateMax
                        )
                  );
                }}
                className="h-control-compact w-36 rounded-sm border-white/10 bg-[#07111f] text-ui-caption"
              />
            </label>
            <label className="flex items-center gap-1.5 text-ui-caption text-slate-500">
              <span>至</span>
              <Input
                aria-label="历史结束日期"
                type="date"
                value={endDate}
                min={endDateMin}
                max={replay ? undefined : initialRange.end}
                onChange={event => {
                  historyRangeEditedRef.current = true;
                  setEndDate(
                    replay
                      ? event.target.value
                      : clampDateInput(
                          event.target.value,
                          endDateMin,
                          initialRange.end
                        )
                  );
                }}
                className="h-control-compact w-36 rounded-sm border-white/10 bg-[#07111f] text-ui-caption"
              />
            </label>
            <Select
              value={source}
              onValueChange={value =>
                setSource(value as 'ALL' | TTradeExecutionMode)
              }
            >
              <SelectTrigger
                aria-label="批次来源"
                className="h-control-compact w-28 rounded-sm border-white/10 bg-[#07111f] text-ui-caption"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部来源</SelectItem>
                {!replay && <SelectItem value="LIVE">实盘</SelectItem>}
                {!replay && <SelectItem value="PAPER">模拟</SelectItem>}
                <SelectItem value="BACKTEST">回测</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={result}
              onValueChange={value => setResult(value as ResultFilter)}
            >
              <SelectTrigger
                aria-label="批次结果"
                className="h-control-compact w-28 rounded-sm border-white/10 bg-[#07111f] text-ui-caption"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL">全部终态</SelectItem>
                <SelectItem value="COMPLETED">已完成</SelectItem>
                <SelectItem value="WIN">盈利</SelectItem>
                <SelectItem value="LOSS">亏损</SelectItem>
                <SelectItem value="EXCEPTION">异常</SelectItem>
              </SelectContent>
            </Select>
            <label className="relative min-w-48 flex-1">
              <span className="sr-only">搜索历史批次</span>
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
              <Input
                value={keyword}
                onChange={event => setKeyword(event.target.value)}
                placeholder="标的、批次、运行或委托编号"
                className="h-control-compact rounded-sm border-white/10 bg-[#07111f] pl-8 text-ui-caption"
              />
            </label>
            <span className="shrink-0 text-ui-caption text-slate-600">
              {coverageSummary(visibleSummary, hasMore)}
            </span>
          </div>
          <HistoryTable
            defaultExecutionMode={defaultExecutionMode}
            instrumentNames={instrumentNames}
            loading={loading}
            mode={mode}
            onOpenBatch={openBatch}
            rows={historyRows}
          />
        </div>
      )}

      {hasMore && onLoadMore && (
        <div className="shrink-0 border-t border-white/[0.05] p-2 text-center">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loadingMore}
            onClick={onLoadMore}
            className="h-control-compact text-ui-caption text-slate-400"
          >
            {loadingMore ? '加载中…' : '加载更多批次'}
          </Button>
        </div>
      )}

      <BatchDetailDrawer
        batch={selectedBatch}
        events={events}
        mode={mode}
        open={Boolean(selectedBatch)}
        onOpenChange={open => {
          if (!open) {
            setSelectedBatch(null);
            onInspectBatch?.(null);
          }
        }}
        onViewActivity={onViewActivity}
        returnFocusRef={returnFocusRef}
      />
    </div>
  );
}
