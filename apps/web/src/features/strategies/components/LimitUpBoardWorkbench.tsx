import {
  AlertTriangle,
  Check,
  Clock3,
  Gauge,
  History,
  RefreshCw,
  ShieldCheck,
  ShieldX,
  Target,
  TrendingDown,
  TrendingUp,
  X,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  ApproveStrategyTradeIntentDocument,
  LimitUpBoardWorkbenchDocument,
  RejectStrategyTradeIntentDocument,
  StrategyRunMode,
  StrategyRunStatus,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import type {
  ExecutionTraceView,
  StrategyDecision,
  StrategyJsonValue,
} from '../domain';

interface LimitUpBoardWorkbenchProps {
  runId?: string;
  instrumentCode?: string;
  runMode?: StrategyRunMode;
  runStatus?: StrategyRunStatus;
  parameters?: Record<string, StrategyJsonValue>;
  decisions: StrategyDecision[];
  executions: ExecutionTraceView[];
  backtestId?: string | null;
  active?: boolean;
}

type JsonRecord = Record<string, unknown>;

const REASON_LABELS: Record<string, string> = {
  LIMIT_UP_BREAK: '破板退出',
  TRAILING_DRAWDOWN: '高点回撤退出',
  MAX_HOLDING_DAYS: '持有期退出',
  USER_REJECTED: '用户拒绝',
  APPROVAL_TTL_EXPIRED: '确认已过期',
  T1_WAIT: '等待 T+1 可卖',
};

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as JsonRecord)
    : {};
}

function numeric(record: JsonRecord, key: string) {
  const value = Number(record[key] ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function formatNumber(value: number | null | undefined, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '--';
  }
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatPercent(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '--';
  }
  return `${(value * 100).toFixed(2)}%`;
}

function formatPercentPoints(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '--';
  }
  return `${value.toFixed(2)}%`;
}

function formatTime(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString('zh-CN', { hour12: false });
}

function useCountdown(expiresAt?: string | null) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!expiresAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expiresAt]);

  if (!expiresAt) return { label: '--', expired: false };
  const remaining = new Date(expiresAt).getTime() - now;
  if (!Number.isFinite(remaining) || remaining <= 0) {
    return { label: '已过期', expired: true };
  }
  return {
    label: `${Math.ceil(remaining / 1000)} 秒`,
    expired: false,
  };
}

function ApprovalCard({
  intent,
  busy,
  onApprove,
  onReject,
}: {
  intent: {
    id: string;
    instrumentCode: string;
    reason: string;
    confidence?: number | null;
    signalPrice?: number | null;
    limitUpPrice?: number | null;
    limitPriceHint?: number | null;
    distanceToLimitTicks?: number | null;
    targetPositionPct?: number | null;
    approvalExpiresAt?: string | null;
    createdAt?: string | null;
  };
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const countdown = useCountdown(intent.approvalExpiresAt);

  return (
    <Card className="overflow-hidden rounded-2xl border-amber-500/25 bg-amber-500/[0.06] shadow-lg">
      <div className="flex flex-col gap-4 border-b border-amber-500/15 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Badge className="border-amber-500/30 bg-amber-500/10 text-amber-600 hover:bg-amber-500/10 dark:text-amber-300">
              待人工确认
            </Badge>
            <span className="font-mono text-xs font-bold text-slate-700 dark:text-slate-200">
              {intent.instrumentCode}
            </span>
          </div>
          <p className="mt-2 text-xs font-medium leading-relaxed text-slate-600 dark:text-slate-300">
            {intent.reason}
          </p>
        </div>
        <div
          className={cn(
            'flex items-center gap-2 rounded-xl border px-3 py-2 font-mono text-xs font-bold',
            countdown.expired
              ? 'border-rose-500/25 bg-rose-500/10 text-rose-500'
              : 'border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-300'
          )}
        >
          <Clock3 className="h-4 w-4" />
          {countdown.label}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px bg-slate-200/70 dark:bg-white/10 sm:grid-cols-5">
        {[
          ['信号价', formatNumber(intent.signalPrice)],
          ['涨停价', formatNumber(intent.limitUpPrice)],
          ['委托上限', formatNumber(intent.limitPriceHint)],
          ['距涨停', `${formatNumber(intent.distanceToLimitTicks, 0)} 档`],
          ['目标仓位', formatPercent(intent.targetPositionPct)],
        ].map(([label, value]) => (
          <div key={label} className="bg-white px-4 py-3 dark:bg-[#0d1425]">
            <div className="text-[9px] font-black uppercase tracking-wider text-slate-400">
              {label}
            </div>
            <div className="mt-1 font-mono text-xs font-bold text-slate-800 dark:text-slate-100">
              {value}
            </div>
          </div>
        ))}
      </div>

      <div className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-[10px] text-slate-500">
          置信度 {formatPercent(intent.confidence)} · 产生于{' '}
          {formatTime(intent.createdAt)}
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={busy || countdown.expired}
            onClick={onReject}
            className="rounded-lg border-rose-500/25 text-rose-600 hover:bg-rose-500/10"
          >
            <X className="mr-1.5 h-3.5 w-3.5" />
            拒绝
          </Button>
          <Button
            size="sm"
            disabled={busy || countdown.expired}
            onClick={onApprove}
            className="rounded-lg bg-red-600 text-white hover:bg-red-500"
          >
            {busy ? (
              <RefreshCw className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Check className="mr-1.5 h-3.5 w-3.5" />
            )}
            确认买入
          </Button>
        </div>
      </div>
    </Card>
  );
}

function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 px-6 py-10 text-center dark:border-white/10">
      <ShieldCheck className="mx-auto h-7 w-7 text-emerald-500" />
      <div className="mt-3 text-sm font-bold text-slate-800 dark:text-slate-100">
        {title}
      </div>
      <p className="mx-auto mt-1 max-w-md text-[11px] leading-relaxed text-slate-500">
        {description}
      </p>
    </div>
  );
}

export default function LimitUpBoardWorkbench({
  runId,
  instrumentCode,
  runMode,
  runStatus,
  parameters = {},
  decisions,
  executions,
  backtestId,
  active = true,
}: LimitUpBoardWorkbenchProps) {
  const isBacktest = runMode === StrategyRunMode.Backtest;
  const [{ data, fetching, error }, refresh] = useQuery({
    query: LimitUpBoardWorkbenchDocument,
    variables: {
      runId: runId || '',
      backtestId: backtestId || null,
      includePerformance: isBacktest,
    },
    pause: !runId || !active,
    requestPolicy: 'cache-and-network',
  });
  const [, approve] = useMutation(ApproveStrategyTradeIntentDocument);
  const [, reject] = useMutation(RejectStrategyTradeIntentDocument);
  const [busyIntentId, setBusyIntentId] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [actionFailed, setActionFailed] = useState(false);

  useEffect(() => {
    const shouldPoll =
      active &&
      !!runId &&
      runMode !== StrategyRunMode.Backtest &&
      runStatus === StrategyRunStatus.Running;
    if (!shouldPoll) return;
    const poll = () => {
      if (document.visibilityState === 'visible') {
        refresh({ requestPolicy: 'network-only' });
      }
    };
    const timer = window.setInterval(poll, 2000);
    document.addEventListener('visibilitychange', poll);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', poll);
    };
  }, [active, refresh, runId, runMode, runStatus]);

  const executionByIntent = useMemo(
    () => new Map(executions.map(item => [item.intentId, item])),
    [executions]
  );
  const latestSignals = useMemo(
    () =>
      decisions
        .flatMap(decision =>
          decision.tradeIntents.map(intent => ({
            ...intent,
            decidedAt: decision.decidedAt,
            execution: executionByIntent.get(intent.id),
          }))
        )
        .sort((a, b) => b.decidedAt.localeCompare(a.decidedAt))
        .slice(0, 6),
    [decisions, executionByIntent]
  );

  const executionQuality = asRecord(
    data?.strategyPerformance?.executionQuality
  );
  const summary = asRecord(data?.strategyPerformance?.summary);
  const autoApproval = Boolean(parameters.auto_approve_manual_intents);
  const entryMode = String(parameters.entry_execution_mode || 'MANUAL_CONFIRM');
  const strictData =
    Boolean(parameters.strict_market_data) &&
    Boolean(parameters.strict_limit_data);

  const handleApproval = async (intentId: string, accepted: boolean) => {
    if (!runId || busyIntentId) return;
    setBusyIntentId(intentId);
    setActionMessage(null);
    setActionFailed(false);
    try {
      if (accepted) {
        const result = await approve({ runId, intentId });
        const response = result.data?.approveStrategyTradeIntent;
        const failed = Boolean(result.error) || response?.success === false;
        setActionFailed(failed);
        setActionMessage(
          result.error?.message ||
            response?.message ||
            (failed ? '确认失败，请等待新信号' : '确认指令已提交')
        );
      } else {
        const result = await reject({
          runId,
          intentId,
          reason: 'USER_REJECTED',
        });
        const response = result.data?.rejectStrategyTradeIntent;
        const failed = Boolean(result.error) || response?.success === false;
        setActionFailed(failed);
        setActionMessage(
          result.error?.message ||
            response?.message ||
            (failed ? '拒绝失败，请刷新后重试' : '拒绝指令已提交')
        );
      }
    } catch (actionError) {
      setActionFailed(true);
      setActionMessage(
        actionError instanceof Error
          ? actionError.message
          : '交易信号处理失败，请刷新后重试'
      );
    } finally {
      setBusyIntentId(null);
      refresh({ requestPolicy: 'network-only' });
    }
  };

  if (!runId) {
    return (
      <EmptyState
        title="尚未选择策略实例"
        description="创建并选择一个单标的打板实例后，这里会展示信号确认、卖出计划与回测约束。"
      />
    );
  }

  return (
    <div className="space-y-5 pb-12">
      <Card className="overflow-hidden rounded-2xl border-slate-200 bg-slate-950 text-white shadow-xl dark:border-white/10">
        <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div>
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.24em] text-red-400">
              <Gauge className="h-4 w-4" />
              Limit-up board workbench
            </div>
            <h2 className="mt-2 text-xl font-black">打板执行工作台</h2>
            <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-slate-400">
              {instrumentCode || '--'} ·
              信号只负责表达交易意图，风控、整手、T+1、委托与成交回报由统一交易域处理。
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {[
              ['入场', entryMode === 'AUTO' ? '自动' : '人工'],
              [
                '委托 TTL',
                `${Number(parameters.entry_order_ttl_ms || 15000) / 1000}s`,
              ],
              ['退出', parameters.auto_exit_authorized ? '自动' : '人工'],
            ].map(([label, value]) => (
              <div
                key={label}
                className="min-w-20 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2"
              >
                <div className="text-[9px] text-slate-500">{label}</div>
                <div className="mt-1 font-mono text-xs font-bold">{value}</div>
              </div>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-px bg-white/10 lg:grid-cols-4">
          {[
            {
              label: '一字板保护',
              value:
                parameters.exclude_one_word_limit_up === false
                  ? '关闭'
                  : '启用',
              healthy: parameters.exclude_one_word_limit_up !== false,
            },
            {
              label: '回测人工信号',
              value: isBacktest && autoApproval ? '自动确认' : '按实例授权',
              healthy: !isBacktest || autoApproval,
            },
            {
              label: '严格数据门禁',
              value: isBacktest
                ? strictData
                  ? '启用'
                  : '未完全启用'
                : '实时校验',
              healthy: !isBacktest || strictData,
            },
            {
              label: '运行状态',
              value: runStatus || '--',
              healthy: runStatus !== StrategyRunStatus.Error,
            },
          ].map(item => (
            <div key={item.label} className="bg-slate-950 px-4 py-3">
              <div className="flex items-center gap-1.5 text-[9px] font-bold text-slate-500">
                {item.healthy ? (
                  <ShieldCheck className="h-3 w-3 text-emerald-400" />
                ) : (
                  <ShieldX className="h-3 w-3 text-amber-400" />
                )}
                {item.label}
              </div>
              <div
                className={cn(
                  'mt-1 text-xs font-bold',
                  item.healthy ? 'text-slate-200' : 'text-amber-300'
                )}
              >
                {item.value}
              </div>
            </div>
          ))}
        </div>
      </Card>

      {(error || actionMessage) && (
        <div
          role="status"
          className={cn(
            'rounded-xl border px-4 py-3 text-xs',
            error || actionFailed
              ? 'border-rose-500/25 bg-rose-500/10 text-rose-600 dark:text-rose-300'
              : 'border-emerald-500/25 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
          )}
        >
          {error?.message || actionMessage}
        </div>
      )}

      <section aria-labelledby="board-approvals-title">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3
              id="board-approvals-title"
              className="text-sm font-black text-slate-900 dark:text-white"
            >
              待确认入场
            </h3>
            <p className="mt-1 text-[10px] text-slate-500">
              超过确认窗口的信号会自动失效，不能补确认。
            </p>
          </div>
          {fetching && (
            <RefreshCw className="h-4 w-4 animate-spin text-slate-400" />
          )}
        </div>
        <div className="space-y-3">
          {data?.strategyPendingTradeIntents.length ? (
            data.strategyPendingTradeIntents.map(intent => (
              <ApprovalCard
                key={intent.id}
                intent={intent}
                busy={busyIntentId === intent.id}
                onApprove={() => void handleApproval(intent.id, true)}
                onReject={() => void handleApproval(intent.id, false)}
              />
            ))
          ) : (
            <EmptyState
              title="当前没有待确认信号"
              description="策略尚未满足接近涨停、盘口质量、市场环境与仓位门禁，或信号已进入执行链路。"
            />
          )}
        </div>
      </section>

      <section aria-labelledby="board-exit-title">
        <div className="mb-3">
          <h3
            id="board-exit-title"
            className="text-sm font-black text-slate-900 dark:text-white"
          >
            打板卖出计划
          </h3>
          <p className="mt-1 text-[10px] text-slate-500">
            只有真实成交后才创建；可卖量不足时按 T+1 规则等待。
          </p>
        </div>
        {data?.strategyExitPlans.length ? (
          <div className="grid gap-3 lg:grid-cols-2">
            {data.strategyExitPlans.map(plan => (
              <Card
                key={plan.id}
                className="rounded-2xl border-slate-200 bg-white p-5 shadow-lg dark:border-white/10 dark:bg-[#0d1425]"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="font-mono text-sm font-black text-slate-900 dark:text-white">
                      {plan.instrumentCode}
                    </div>
                    <div className="mt-1 text-[10px] text-slate-500">
                      {plan.sourceType} · {plan.bucket}
                    </div>
                  </div>
                  <Badge variant="outline">{plan.status}</Badge>
                </div>
                <div className="mt-4 grid grid-cols-3 gap-2">
                  {[
                    ['剩余', `${formatNumber(plan.remainingVolume, 0)} 股`],
                    ['持有', `${plan.holdingTradingDays} 日`],
                    ['净收益', formatPercentPoints(plan.lastNetProfitPct)],
                    ['入场均价', formatNumber(plan.entryAvgPrice)],
                    ['当前价', formatNumber(plan.lastPrice)],
                    ['峰值收益', formatPercentPoints(plan.peakNetProfitPct)],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="rounded-lg bg-slate-50 px-3 py-2 dark:bg-white/[0.04]"
                    >
                      <div className="text-[9px] text-slate-400">{label}</div>
                      <div className="mt-1 font-mono text-[11px] font-bold text-slate-700 dark:text-slate-200">
                        {value}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="mt-4 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
                  <Badge
                    variant="outline"
                    className="border-slate-200 dark:border-white/10"
                  >
                    {plan.t1Policy}
                  </Badge>
                  {plan.ruleTypes.map(rule => (
                    <Badge key={rule} variant="outline">
                      {REASON_LABELS[rule] || rule}
                    </Badge>
                  ))}
                  {plan.lastExitReason && (
                    <span className="text-amber-600 dark:text-amber-300">
                      {REASON_LABELS[plan.lastExitReason] ||
                        plan.lastExitReason}
                    </span>
                  )}
                </div>
              </Card>
            ))}
          </div>
        ) : (
          <EmptyState
            title="当前没有活跃卖出计划"
            description="尚未发生有效入场成交，或对应仓位已经完成退出。回测结果仍可在下方查看成交约束。"
          />
        )}
      </section>

      {isBacktest && (
        <section aria-labelledby="board-backtest-title">
          <div className="mb-3">
            <h3
              id="board-backtest-title"
              className="text-sm font-black text-slate-900 dark:text-white"
            >
              回测成交可信度
            </h3>
            <p className="mt-1 text-[10px] text-slate-500">
              这些数字用于识别被涨跌停、盘口深度和订单时效挡掉的理想化成交。
            </p>
          </div>
          <Card className="rounded-2xl border-slate-200 bg-white p-5 shadow-lg dark:border-white/10 dark:bg-[#0d1425]">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                {
                  label: '涨停买入阻塞',
                  value: numeric(executionQuality, 'limit_up_buy_blocked'),
                  icon: TrendingUp,
                },
                {
                  label: '跌停卖出阻塞',
                  value: numeric(executionQuality, 'limit_down_sell_blocked'),
                  icon: TrendingDown,
                },
                {
                  label: '盘口深度限量',
                  value: numeric(executionQuality, 'book_depth_capped_orders'),
                  icon: Target,
                },
                {
                  label: '委托时效过期',
                  value: numeric(executionQuality, 'expired_orders'),
                  icon: Clock3,
                },
                {
                  label: '停牌阻塞',
                  value: numeric(executionQuality, 'suspended_blocked'),
                  icon: AlertTriangle,
                },
                {
                  label: '部分成交',
                  value: numeric(executionQuality, 'partial_fills'),
                  icon: Gauge,
                },
                {
                  label: '完整成交',
                  value: numeric(executionQuality, 'full_fills'),
                  icon: Check,
                },
                {
                  label: '未成交股数',
                  value: numeric(executionQuality, 'unfilled_volume'),
                  icon: ShieldX,
                },
              ].map(item => {
                const Icon = item.icon;
                return (
                  <div
                    key={item.label}
                    className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-white/10 dark:bg-white/[0.03]"
                  >
                    <div className="flex items-center gap-2 text-[9px] font-bold text-slate-500">
                      <Icon className="h-3.5 w-3.5 text-red-500" />
                      {item.label}
                    </div>
                    <div className="mt-2 font-mono text-lg font-black text-slate-900 dark:text-white">
                      {item.value.toLocaleString('zh-CN')}
                    </div>
                  </div>
                );
              })}
            </div>
            <div className="mt-4 flex flex-col gap-2 border-t border-slate-100 pt-4 text-[10px] text-slate-500 dark:border-white/5 sm:flex-row sm:items-center sm:justify-between">
              <span>
                数据质量：
                {data?.strategyPerformance?.dataQuality.status || '--'}
                {data?.strategyPerformance?.dataQuality.warning
                  ? ` · ${data.strategyPerformance.dataQuality.warning}`
                  : ''}
              </span>
              <span className="flex items-center gap-1.5 font-mono">
                <History className="h-3.5 w-3.5" />
                交易数{' '}
                {numeric(summary, 'total_trades').toLocaleString('zh-CN')}
              </span>
            </div>
          </Card>
        </section>
      )}

      <section aria-labelledby="board-signals-title">
        <div className="mb-3">
          <h3
            id="board-signals-title"
            className="text-sm font-black text-slate-900 dark:text-white"
          >
            最近信号链路
          </h3>
        </div>
        <Card className="overflow-hidden rounded-2xl border-slate-200 bg-white shadow-lg dark:border-white/10 dark:bg-[#0d1425]">
          {latestSignals.length ? (
            latestSignals.map((signal, index) => (
              <div
                key={`${signal.id}-${index}`}
                className="grid gap-2 border-b border-slate-100 px-5 py-3 last:border-b-0 dark:border-white/5 sm:grid-cols-[150px_90px_minmax(0,1fr)_120px]"
              >
                <span className="font-mono text-[10px] text-slate-500">
                  {formatTime(signal.decidedAt)}
                </span>
                <span className="text-[10px] font-bold text-slate-700 dark:text-slate-200">
                  {signal.side} {signal.instrumentCode}
                </span>
                <span className="truncate text-[10px] text-slate-500">
                  {signal.reason || '--'}
                </span>
                <span className="text-right font-mono text-[10px] font-bold text-slate-700 dark:text-slate-200">
                  {signal.execution?.fillStatus ||
                    signal.execution?.orderStatus ||
                    signal.status ||
                    '已产生'}
                </span>
              </div>
            ))
          ) : (
            <div className="px-6 py-10 text-center text-xs text-slate-500">
              暂无已产生的交易意图。
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}
