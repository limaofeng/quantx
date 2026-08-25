import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock3,
  Database,
  FileJson2,
  FlaskConical,
  Loader2,
  Play,
  RotateCcw,
  ShieldCheck,
  Square,
} from 'lucide-react';
import * as React from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useMutation, useQuery, useSubscription } from 'urql';

import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { createClientId } from '@/utils/clientId';
import { cn } from '@/utils/cn';

import {
  CancelExitPlanReplayMutation,
  ExitPlanReplayHistoryQuery,
  ExitPlanReplayPreparationQuery,
  ExitPlanReplayQuery,
  ExitPlanReplayUpdatesSubscription,
  ExitPlansQuery,
  StartExitPlanReplayMutation,
} from '../hooks/usePortfolio';

const terminalStatuses = new Set([
  'CANCELLED',
  'COMPLETED',
  'ERROR',
  'FAILED',
  'STOPPED',
]);

type ReplayLocation = { planId?: string; runId?: string };

function dateInputValue(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function shiftWeekdays(value: Date, amount: number) {
  const next = new Date(value);
  const direction = amount >= 0 ? 1 : -1;
  let remaining = Math.abs(amount);
  while (remaining > 0) {
    next.setDate(next.getDate() + direction);
    if (next.getDay() !== 0 && next.getDay() !== 6) remaining -= 1;
  }
  return next;
}

function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN', { hour12: false });
}

function formatPercent(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '--';
  }
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function formatMoney(value?: number | null) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return '--';
  }
  return value.toLocaleString('zh-CN', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

function percentTone(value?: number | null) {
  if (!value) return 'text-slate-200';
  return value > 0 ? 'text-rose-300' : 'text-emerald-300';
}

function ReplayMetric({
  label,
  note,
  value,
  valueTone,
}: {
  label: string;
  note?: string;
  value: string;
  valueTone?: string;
}) {
  return (
    <div className="rounded-md border border-white/10 bg-white/5 p-3">
      <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 font-mono text-lg font-black tabular-nums text-slate-100',
          valueTone
        )}
      >
        {value}
      </div>
      {note ? (
        <div className="mt-1 text-[10px] font-bold text-slate-600">{note}</div>
      ) : null}
    </div>
  );
}

export function ExitPlanReplayPanel({
  accountId,
  draftTemplate,
  initialPlanId,
  initialRunId,
  onLocationChange,
}: {
  accountId: string;
  draftTemplate?: Record<string, unknown> | null;
  initialPlanId?: string;
  initialRunId?: string;
  onLocationChange: (location: ReplayLocation) => void;
}) {
  const { toast } = useToast();
  const yesterday = React.useMemo(() => {
    const value = new Date();
    value.setDate(value.getDate() - 1);
    while (value.getDay() === 0 || value.getDay() === 6) {
      value.setDate(value.getDate() - 1);
    }
    return value;
  }, []);
  const [selectedPlanId, setSelectedPlanId] = React.useState(
    initialPlanId || ''
  );
  const [useDraft, setUseDraft] = React.useState(
    Boolean(draftTemplate && !initialPlanId)
  );
  const [runId, setRunId] = React.useState(initialRunId || '');
  const [originMode, setOriginMode] = React.useState<
    'BUY_FILLS' | 'MANUAL_SNAPSHOT'
  >('BUY_FILLS');
  const [selectedOrderIds, setSelectedOrderIds] = React.useState<string[]>([]);
  const [startDate, setStartDate] = React.useState(
    dateInputValue(shiftWeekdays(yesterday, -19))
  );
  const [endDate, setEndDate] = React.useState(dateInputValue(yesterday));
  const [manualActivation, setManualActivation] = React.useState(
    `${dateInputValue(shiftWeekdays(yesterday, -19))}T09:30`
  );
  const [manualVolume, setManualVolume] = React.useState('');
  const [manualUnitCost, setManualUnitCost] = React.useState('');
  const [showCosts, setShowCosts] = React.useState(false);
  const [commissionRate, setCommissionRate] = React.useState('0.0003');
  const [minimumCommission, setMinimumCommission] = React.useState('5');
  const [stampTaxRate, setStampTaxRate] = React.useState('0.0005');
  const [transferFeeRate, setTransferFeeRate] = React.useState('0.00001');
  const [slippageRate, setSlippageRate] = React.useState('0.0001');

  const [plansResult] = useQuery({
    query: ExitPlansQuery,
    variables: {
      accountId: accountId || undefined,
      instrumentCode: undefined,
      limit: 200,
      sourceType: undefined,
      statuses: undefined,
    },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });
  const replayablePlans = React.useMemo(
    () => plansResult.data?.exitPlans ?? [],
    [plansResult.data?.exitPlans]
  );

  React.useEffect(() => {
    if (useDraft || selectedPlanId || replayablePlans.length === 0) return;
    const first = replayablePlans.find(plan => plan.status !== 'CANCELLED');
    if (first) setSelectedPlanId(first.planId);
  }, [replayablePlans, selectedPlanId, useDraft]);

  const preparationInput = React.useMemo(
    () => ({
      accountId,
      draftTemplate: useDraft ? draftTemplate : undefined,
      planId: useDraft ? undefined : selectedPlanId || undefined,
    }),
    [accountId, draftTemplate, selectedPlanId, useDraft]
  );
  const [preparationResult, refetchPreparation] = useQuery({
    query: ExitPlanReplayPreparationQuery,
    variables: { input: preparationInput },
    pause:
      !accountId ||
      (useDraft ? !draftTemplate : !selectedPlanId) ||
      Boolean(runId),
    requestPolicy: 'cache-and-network',
  });
  const preparation = preparationResult.data?.exitPlanReplayPreparation;

  React.useEffect(() => {
    if (!preparation?.buyFills.length || selectedOrderIds.length > 0) return;
    const preferred = preparation.buyFills
      .filter(item => item.selectedByPlan)
      .map(item => item.orderId);
    setSelectedOrderIds(
      preferred.length > 0
        ? preferred
        : preparation.buyFills.slice(0, 1).map(item => item.orderId)
    );
  }, [preparation?.buyFills, selectedOrderIds.length]);

  const [replayResult, refetchReplay] = useQuery({
    query: ExitPlanReplayQuery,
    variables: { runId },
    pause: !runId,
    requestPolicy: 'cache-and-network',
  });
  const replay = replayResult.data?.exitPlanReplay;
  const [historyResult, refetchHistory] = useQuery({
    query: ExitPlanReplayHistoryQuery,
    variables: { accountId: accountId || undefined, limit: 20 },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });
  const [update] = useSubscription({
    query: ExitPlanReplayUpdatesSubscription,
    variables: { accountId: accountId || undefined },
    pause: !accountId,
  });
  const [startResult, startReplay] = useMutation(StartExitPlanReplayMutation);
  const [cancelResult, cancelReplay] = useMutation(
    CancelExitPlanReplayMutation
  );

  React.useEffect(() => {
    if (!update.data?.exitPlanReplayUpdates || !runId) return;
    if (update.data.exitPlanReplayUpdates.runId !== runId) return;
    refetchReplay({ requestPolicy: 'network-only' });
    refetchHistory({ requestPolicy: 'network-only' });
  }, [refetchHistory, refetchReplay, runId, update.data]);

  React.useEffect(() => {
    if (!runId || (replay && terminalStatuses.has(replay.status))) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        refetchReplay({ requestPolicy: 'network-only' });
      }
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [refetchReplay, replay, runId]);

  const setQuickWindow = (days: number) => {
    setStartDate(
      dateInputValue(shiftWeekdays(new Date(`${endDate}T12:00`), -(days - 1)))
    );
  };

  const handleStart = async () => {
    if (!preparation) return;
    const operation = await startReplay({
      input: {
        accountId,
        commissionRate: Number(commissionRate),
        draftTemplate: useDraft ? draftTemplate : undefined,
        endTime: `${endDate}T15:00:00+08:00`,
        expectedConfigVersion: useDraft ? undefined : preparation.configVersion,
        idempotencyKey: createClientId('exit-plan-replay'),
        minimumCommission: Number(minimumCommission),
        origin:
          originMode === 'BUY_FILLS'
            ? { mode: originMode, orderIds: selectedOrderIds }
            : {
                activationTime: `${manualActivation}:00+08:00`,
                mode: originMode,
                unitCost: Number(manualUnitCost),
                volume: Number(manualVolume),
              },
        planId: useDraft ? undefined : selectedPlanId,
        slippageRate: Number(slippageRate),
        stampTaxRate: Number(stampTaxRate),
        startTime: `${startDate}T09:30:00+08:00`,
        transferFeeRate: Number(transferFeeRate),
      },
    });
    const response = operation.data?.startExitPlanReplay;
    if (operation.error || !response?.success) {
      toast({
        title: '回放启动失败',
        description: operation.error?.message || response?.message,
        variant: 'destructive',
      });
      return;
    }
    const nextRunId = response.replay?.runId || response.runId;
    if (nextRunId) {
      setRunId(nextRunId);
      onLocationChange({
        planId: useDraft ? undefined : selectedPlanId,
        runId: nextRunId,
      });
    }
    toast({ title: '卖出计划回放已启动', description: response.message });
  };

  const handleCancel = async () => {
    if (!runId) return;
    const operation = await cancelReplay({ runId });
    const response = operation.data?.cancelExitPlanReplay;
    toast({
      title: response?.success ? '回放已取消' : '取消失败',
      description: operation.error?.message || response?.message,
      variant: response?.success ? 'default' : 'destructive',
    });
    refetchReplay({ requestPolicy: 'network-only' });
  };

  const handleReset = () => {
    setRunId('');
    onLocationChange({ planId: useDraft ? undefined : selectedPlanId });
    refetchPreparation({ requestPolicy: 'network-only' });
  };

  const originValid =
    originMode === 'BUY_FILLS'
      ? selectedOrderIds.length > 0
      : Boolean(
          manualActivation &&
          Number(manualVolume) > 0 &&
          Number(manualUnitCost) > 0
        );
  const canStart = Boolean(
    preparation && startDate && endDate && originValid && !startResult.fetching
  );

  if (runId) {
    const summary = replay?.summary;
    const active = !replay || !terminalStatuses.has(replay.status);
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-base font-black text-slate-100">
                {replay?.instrumentCode || '卖出计划'}回放
              </h2>
              <span
                className={cn(
                  'rounded-sm border px-2 py-0.5 text-[10px] font-black',
                  replay?.status === 'COMPLETED'
                    ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-200'
                    : replay?.errorMessage
                      ? 'border-rose-400/20 bg-rose-400/10 text-rose-200'
                      : 'border-cyan-400/20 bg-cyan-400/10 text-cyan-200'
                )}
              >
                {replay?.status || 'LOADING'}
              </span>
            </div>
            <p className="mt-1 text-xs font-bold text-slate-500">
              {replay
                ? `${formatDateTime(replay.startTime)} — ${formatDateTime(replay.endTime)}`
                : '读取回放运行中'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {active ? (
              <Button
                disabled={cancelResult.fetching}
                onClick={() => void handleCancel()}
                size="sm"
                type="button"
                variant="outline"
              >
                {cancelResult.fetching ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Square />
                )}
                取消回放
              </Button>
            ) : null}
            <Button
              onClick={handleReset}
              size="sm"
              type="button"
              variant="outline"
            >
              <RotateCcw /> 新建回放
            </Button>
          </div>
        </div>

        {replayResult.error || replay?.errorMessage ? (
          <div className="mt-3 flex gap-2 rounded-md border border-rose-400/20 bg-rose-500/10 p-3 text-xs font-bold text-rose-100">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            {replayResult.error?.message || replay?.errorMessage}
          </div>
        ) : null}

        {active ? (
          <section className="mt-3 rounded-md border border-cyan-400/20 bg-cyan-500/5 p-4">
            <div className="flex items-center justify-between text-xs font-black text-cyan-100">
              <span className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" />
                严格 Tick 回放进行中
              </span>
              <span className="font-mono">
                {(replay?.progressPct ?? 0).toFixed(1)}%
              </span>
            </div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
              <div
                className="h-full bg-cyan-400 transition-[width]"
                style={{ width: `${replay?.progressPct ?? 0}%` }}
              />
            </div>
            <p className="mt-2 text-[11px] font-bold text-slate-500">
              已处理至 {formatDateTime(replay?.processedUntil)}
              ；运行结束前不会强制卖出剩余持仓。
            </p>
          </section>
        ) : null}

        {summary ? (
          <>
            <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
              <ReplayMetric
                label="计划收益"
                value={formatPercent(summary.planReturnPct)}
                valueTone={percentTone(summary.planReturnPct)}
              />
              <ReplayMetric
                label="继续持有"
                value={formatPercent(summary.holdReturnPct)}
                valueTone={percentTone(summary.holdReturnPct)}
              />
              <ReplayMetric
                label="立即卖出"
                value={formatPercent(summary.immediateSellReturnPct)}
                valueTone={percentTone(summary.immediateSellReturnPct)}
              />
              <ReplayMetric
                label="相对持有"
                value={formatPercent(summary.excessVsHoldPct)}
                valueTone={percentTone(summary.excessVsHoldPct)}
              />
              <ReplayMetric
                label="剩余持仓"
                note="区间末按市值计价"
                value={`${summary.remainingVolume.toLocaleString()} 股`}
              />
            </div>

            <section className="mt-3 rounded-md border border-white/10 bg-slate-950/80 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2 text-xs font-black text-slate-200">
                  <BarChart3 className="h-4 w-4 text-cyan-300" />
                  三路径净值对比
                </div>
                <span className="flex items-center gap-1.5 text-[10px] font-bold text-slate-500">
                  <ShieldCheck className="h-3.5 w-3.5 text-emerald-300" />
                  {replay?.dataQualityMessage}
                </span>
              </div>
              <div className="mt-3 h-80 min-w-0">
                <ResponsiveContainer height="100%" width="100%">
                  <LineChart data={replay?.curve ?? []}>
                    <CartesianGrid
                      stroke="rgba(148,163,184,0.08)"
                      vertical={false}
                    />
                    <XAxis
                      dataKey="timestamp"
                      minTickGap={48}
                      tick={{ fill: '#64748b', fontSize: 10 }}
                      tickFormatter={value =>
                        new Date(value).toLocaleDateString('zh-CN', {
                          month: '2-digit',
                          day: '2-digit',
                        })
                      }
                    />
                    <YAxis
                      tick={{ fill: '#64748b', fontSize: 10 }}
                      tickFormatter={value => `${Number(value).toFixed(1)}%`}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#08101d',
                        border: '1px solid rgba(255,255,255,.1)',
                        borderRadius: 6,
                        color: '#e2e8f0',
                      }}
                      labelFormatter={value => formatDateTime(String(value))}
                      formatter={(value: number) => formatPercent(value)}
                    />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Line
                      dataKey="planReturnPct"
                      dot={false}
                      name="计划卖出"
                      stroke="#22d3ee"
                      strokeWidth={2}
                    />
                    <Line
                      dataKey="holdReturnPct"
                      dot={false}
                      name="继续持有"
                      stroke="#818cf8"
                      strokeWidth={1.5}
                    />
                    <Line
                      dataKey="immediateSellReturnPct"
                      dot={false}
                      name="立即卖出"
                      stroke="#94a3b8"
                      strokeDasharray="4 4"
                      strokeWidth={1.5}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </section>

            <div className="mt-3 grid gap-3 xl:grid-cols-3">
              <section className="rounded-md border border-white/10 bg-slate-950/80 xl:col-span-2">
                <div className="border-b border-white/5 px-3 py-2.5 text-xs font-black text-slate-200">
                  触发与卖出事件
                </div>
                <div className="max-h-96 overflow-auto custom-scrollbar">
                  {(replay?.events ?? []).length === 0 ? (
                    <div className="p-8 text-center text-xs font-bold text-slate-600">
                      区间内未触发卖出规则
                    </div>
                  ) : (
                    replay?.events.map(event => (
                      <div
                        className="flex gap-3 border-b border-white/5 px-3 py-2.5 text-xs"
                        key={`${event.sequence}-${event.timestamp}`}
                      >
                        <span className="w-32 shrink-0 font-mono text-[10px] text-slate-600">
                          {formatDateTime(event.timestamp)}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block font-black text-slate-200">
                            {event.ruleType || event.eventType}
                          </span>
                          <span className="mt-0.5 block truncate text-[10px] font-bold text-slate-500">
                            {event.reason}
                          </span>
                        </span>
                        <span className="shrink-0 text-right font-mono text-slate-300">
                          <span className="block">
                            {formatMoney(event.price)}
                          </span>
                          <span className="block text-[10px] text-slate-600">
                            {event.volume.toLocaleString()} 股
                          </span>
                        </span>
                      </div>
                    ))
                  )}
                </div>
                {(replay?.actualSellReferences ?? []).length > 0 ? (
                  <div className="border-t border-amber-400/20 bg-amber-400/5 p-3 text-[10px] font-bold text-amber-100">
                    区间真实历史卖出仅作参考：
                    {replay?.actualSellReferences.map(item => (
                      <span className="ml-2 font-mono" key={item.orderId}>
                        {formatDateTime(item.timestamp)} · {item.volume} 股 @{' '}
                        {formatMoney(item.price)}
                      </span>
                    ))}
                  </div>
                ) : null}
              </section>

              <div className="grid content-start gap-3">
                <section className="rounded-md border border-emerald-400/20 bg-emerald-400/5 p-3">
                  <div className="flex items-center gap-2 text-xs font-black text-emerald-100">
                    <CheckCircle2 className="h-4 w-4" /> 区间事实结论
                  </div>
                  <p className="mt-2 text-xs font-bold leading-6 text-slate-300">
                    {summary.conclusion}
                  </p>
                </section>
                <section className="rounded-md border border-white/10 bg-slate-950/80 p-3">
                  <div className="text-xs font-black text-slate-200">
                    卖出后观察
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-2">
                    {(replay?.postExitHorizons ?? []).map(item => (
                      <div
                        className="rounded border border-white/5 p-2"
                        key={item.tradingDays}
                      >
                        <div className="text-[10px] font-bold text-slate-600">
                          +{item.tradingDays} 交易日
                        </div>
                        <div
                          className={cn(
                            'mt-1 font-mono text-xs font-black',
                            percentTone(item.returnAfterExitPct)
                          )}
                        >
                          {item.available
                            ? formatPercent(item.returnAfterExitPct)
                            : '数据不足'}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
                <section className="rounded-md border border-white/10 bg-slate-950/80 p-3 text-[11px] font-bold text-slate-500">
                  <div className="flex items-center gap-2 text-xs font-black text-slate-200">
                    <FileJson2 className="h-4 w-4 text-cyan-300" /> 可审计报告
                  </div>
                  <p className="mt-2 break-all">
                    JSON：{replay?.report?.jsonArtifact || '未生成'}
                  </p>
                  <p className="mt-1 break-all">
                    HTML：{replay?.report?.htmlArtifact || '未生成'}
                  </p>
                </section>
              </div>
            </div>
          </>
        ) : null}
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
      <div>
        <h2 className="flex items-center gap-2 text-base font-black text-slate-100">
          <FlaskConical className="h-4 w-4 text-cyan-300" /> 卖出计划回放测试
        </h2>
        <p className="mt-1 text-xs font-bold text-slate-500">
          使用历史 Tick 逐笔重演卖出计划，同时比较继续持有和起点立即卖出。
        </p>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-3">
        <div className="grid content-start gap-3 xl:col-span-2">
          <section className="rounded-md border border-white/10 bg-slate-950/80 p-3">
            <div className="flex items-center gap-2 text-xs font-black text-slate-200">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-cyan-400/10 font-mono text-[10px] text-cyan-200">
                1
              </span>
              选择卖出计划版本
            </div>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              {draftTemplate ? (
                <button
                  className={cn(
                    'rounded-md border p-3 text-left transition-colors',
                    useDraft
                      ? 'border-cyan-400/20 bg-cyan-400/10'
                      : 'border-white/10 hover:border-white/20'
                  )}
                  onClick={() => {
                    setUseDraft(true);
                    setSelectedOrderIds([]);
                  }}
                  type="button"
                >
                  <span className="block text-xs font-black text-slate-100">
                    当前未保存草稿
                  </span>
                  <span className="mt-1 block text-[10px] font-bold text-cyan-200">
                    启动时冻结为一次性快照
                  </span>
                </button>
              ) : null}
              <label
                className={cn(
                  'grid gap-1 rounded-md border p-3 text-xs font-bold',
                  !useDraft
                    ? 'border-cyan-400/20 bg-cyan-400/10 text-slate-300'
                    : 'border-white/10 text-slate-500'
                )}
              >
                已保存的不可变计划版本
                <select
                  className="h-9 rounded border border-white/10 bg-slate-950 px-2 font-mono text-slate-100"
                  onChange={event => {
                    setUseDraft(false);
                    setSelectedPlanId(event.target.value);
                    setSelectedOrderIds([]);
                    onLocationChange({
                      planId: event.target.value || undefined,
                    });
                  }}
                  value={selectedPlanId}
                >
                  <option value="">请选择计划</option>
                  {replayablePlans.map(plan => (
                    <option key={plan.planId} value={plan.planId}>
                      {plan.instrumentCode} · v{plan.configVersion} ·{' '}
                      {plan.status}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {preparationResult.error ? (
              <p className="mt-2 text-xs font-bold text-rose-300">
                {preparationResult.error.message}
              </p>
            ) : preparation ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] font-bold text-slate-500">
                <span className="rounded-sm bg-white/5 px-2 py-1 font-mono text-slate-300">
                  {preparation.instrumentCode}
                </span>
                <span>版本 v{preparation.configVersion}</span>
                <span>·</span>
                <span className="text-cyan-200">严格 Tick</span>
                {preparation.requiresDepth ? (
                  <span className="text-amber-200">· 要求盘口深度</span>
                ) : null}
              </div>
            ) : preparationResult.fetching ? (
              <div className="mt-3 flex items-center gap-2 text-xs font-bold text-slate-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                读取计划版本
              </div>
            ) : null}
          </section>

          <section className="rounded-md border border-white/10 bg-slate-950/80 p-3">
            <div className="flex items-center gap-2 text-xs font-black text-slate-200">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-cyan-400/10 font-mono text-[10px] text-cyan-200">
                2
              </span>
              确定历史持仓起点
            </div>
            <div className="mt-3 flex gap-2">
              {(['BUY_FILLS', 'MANUAL_SNAPSHOT'] as const).map(mode => (
                <button
                  className={cn(
                    'h-8 rounded-md border px-3 text-xs font-black',
                    originMode === mode
                      ? 'border-cyan-400/20 bg-cyan-400/10 text-cyan-100'
                      : 'border-white/10 text-slate-500'
                  )}
                  key={mode}
                  onClick={() => setOriginMode(mode)}
                  type="button"
                >
                  {mode === 'BUY_FILLS' ? '真实买入成交' : '手工历史快照'}
                </button>
              ))}
            </div>
            {originMode === 'BUY_FILLS' ? (
              <div className="mt-3 overflow-x-auto rounded-md border border-white/5">
                {(preparation?.buyFills ?? []).length === 0 ? (
                  <div className="p-6 text-center text-xs font-bold text-slate-600">
                    没有可用买入成交，请改用手工历史快照
                  </div>
                ) : (
                  <table className="w-full text-left text-xs">
                    <thead className="bg-white/5 text-[10px] font-black text-slate-600">
                      <tr>
                        <th className="px-3 py-2">选择</th>
                        <th className="px-3 py-2">成交时间</th>
                        <th className="px-3 py-2">委托</th>
                        <th className="px-3 py-2 text-right">数量</th>
                        <th className="px-3 py-2 text-right">价格</th>
                        <th className="px-3 py-2 text-right">估算买费</th>
                      </tr>
                    </thead>
                    <tbody>
                      {preparation?.buyFills.map(fill => (
                        <tr
                          className="border-t border-white/5"
                          key={fill.orderId}
                        >
                          <td className="px-3 py-2">
                            <input
                              checked={selectedOrderIds.includes(fill.orderId)}
                              onChange={() =>
                                setSelectedOrderIds(current =>
                                  current.includes(fill.orderId)
                                    ? current.filter(
                                        item => item !== fill.orderId
                                      )
                                    : [...current, fill.orderId]
                                )
                              }
                              type="checkbox"
                            />
                          </td>
                          <td className="px-3 py-2 font-mono text-[10px] text-slate-500">
                            {formatDateTime(fill.orderTime)}
                          </td>
                          <td className="px-3 py-2 font-mono text-slate-300">
                            #{fill.orderId}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-200">
                            {fill.tradedVolume.toLocaleString()}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-200">
                            {formatMoney(fill.tradedPrice)}
                          </td>
                          <td className="px-3 py-2 text-right font-mono text-slate-500">
                            {formatMoney(fill.estimatedBuyFeeCny)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
                {selectedOrderIds.length > 1 ? (
                  <div className="border-t border-cyan-400/10 bg-cyan-400/5 px-3 py-2 text-[10px] font-bold text-cyan-100">
                    选择多笔成交时，计划从最后一笔成交完成后激活。
                  </div>
                ) : null}
              </div>
            ) : (
              <div className="mt-3 grid gap-3 md:grid-cols-3">
                <label className="grid gap-1 text-xs font-bold text-slate-500">
                  激活时间
                  <input
                    className="h-9 rounded border border-white/10 bg-slate-950 px-2 font-mono text-slate-200"
                    onChange={event => setManualActivation(event.target.value)}
                    type="datetime-local"
                    value={manualActivation}
                  />
                </label>
                <label className="grid gap-1 text-xs font-bold text-slate-500">
                  历史持仓数量
                  <input
                    className="h-9 rounded border border-white/10 bg-slate-950 px-2 font-mono text-slate-200"
                    min={1}
                    onChange={event => setManualVolume(event.target.value)}
                    type="number"
                    value={manualVolume}
                  />
                </label>
                <label className="grid gap-1 text-xs font-bold text-slate-500">
                  每股全成本
                  <input
                    className="h-9 rounded border border-white/10 bg-slate-950 px-2 font-mono text-slate-200"
                    min={0}
                    onChange={event => setManualUnitCost(event.target.value)}
                    step="0.001"
                    type="number"
                    value={manualUnitCost}
                  />
                </label>
              </div>
            )}
          </section>

          <section className="rounded-md border border-white/10 bg-slate-950/80 p-3">
            <div className="flex items-center gap-2 text-xs font-black text-slate-200">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-cyan-400/10 font-mono text-[10px] text-cyan-200">
                3
              </span>
              设置历史区间与成本
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-3">
              <label className="grid gap-1 text-xs font-bold text-slate-500">
                开始交易日
                <input
                  className="h-9 rounded border border-white/10 bg-slate-950 px-2 font-mono text-slate-200"
                  max={endDate}
                  onChange={event => setStartDate(event.target.value)}
                  type="date"
                  value={startDate}
                />
              </label>
              <label className="grid gap-1 text-xs font-bold text-slate-500">
                结束交易日
                <input
                  className="h-9 rounded border border-white/10 bg-slate-950 px-2 font-mono text-slate-200"
                  max={dateInputValue(yesterday)}
                  min={startDate}
                  onChange={event => setEndDate(event.target.value)}
                  type="date"
                  value={endDate}
                />
              </label>
              <div className="flex items-end gap-1.5">
                {[5, 10, 20].map(days => (
                  <Button
                    className="h-9 px-2.5 text-[10px]"
                    key={days}
                    onClick={() => setQuickWindow(days)}
                    type="button"
                    variant="outline"
                  >
                    {days}日
                  </Button>
                ))}
              </div>
            </div>
            <button
              className="mt-3 flex items-center gap-2 text-[10px] font-black text-slate-500 hover:text-slate-200"
              onClick={() => setShowCosts(current => !current)}
              type="button"
            >
              <Database className="h-3.5 w-3.5" />
              {showCosts ? '收起成本参数' : '展开成本参数（默认按系统值）'}
            </button>
            {showCosts ? (
              <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                {[
                  ['佣金率', commissionRate, setCommissionRate],
                  ['最低佣金', minimumCommission, setMinimumCommission],
                  ['印花税率', stampTaxRate, setStampTaxRate],
                  ['过户费率', transferFeeRate, setTransferFeeRate],
                  ['滑点率', slippageRate, setSlippageRate],
                ].map(([label, value, setter]) => (
                  <label
                    className="grid gap-1 text-[10px] font-bold text-slate-600"
                    key={String(label)}
                  >
                    {String(label)}
                    <input
                      className="h-8 rounded border border-white/10 bg-slate-950 px-2 font-mono text-xs text-slate-300"
                      onChange={event =>
                        (
                          setter as React.Dispatch<React.SetStateAction<string>>
                        )(event.target.value)
                      }
                      step="0.00001"
                      type="number"
                      value={String(value)}
                    />
                  </label>
                ))}
              </div>
            ) : null}
          </section>
        </div>

        <aside className="grid content-start gap-3">
          <section className="rounded-md border border-cyan-400/20 bg-cyan-500/5 p-3">
            <div className="flex items-center gap-2 text-xs font-black text-cyan-100">
              <ShieldCheck className="h-4 w-4" />
              回放口径
            </div>
            <ul className="mt-2 grid gap-2 text-[11px] font-bold leading-5 text-slate-500">
              <li>· 卖出规则与实盘共用 ExitPlanBook 和统一风控/数量链路。</li>
              <li>
                · 量价动态规则缺少 Tick 或盘口深度时直接阻断，不用分钟线近似。
              </li>
              <li>· 区间末不强制平仓；剩余持仓按末价计入计划路径。</li>
              <li>· 真实历史卖出只做参考，不参与计划收益计算。</li>
            </ul>
          </section>
          {(preparation?.blockingReasons ?? []).length > 0 ? (
            <section className="rounded-md border border-amber-400/20 bg-amber-500/10 p-3 text-[11px] font-bold text-amber-100">
              {(preparation?.blockingReasons ?? []).map(reason => (
                <p key={reason}>· {reason}</p>
              ))}
            </section>
          ) : null}
          <Button
            className="h-11 w-full bg-cyan-500 font-black text-slate-950 hover:bg-cyan-400"
            disabled={!canStart}
            onClick={() => void handleStart()}
            type="button"
          >
            {startResult.fetching ? (
              <Loader2 className="animate-spin" />
            ) : (
              <Play />
            )}
            {startResult.fetching ? '正在创建回放' : '开始严格历史回放'}
          </Button>
          <section className="rounded-md border border-white/10 bg-slate-950/80">
            <div className="flex items-center gap-2 border-b border-white/5 px-3 py-2.5 text-xs font-black text-slate-200">
              <Clock3 className="h-4 w-4 text-slate-500" />
              最近回放
            </div>
            <div className="max-h-72 overflow-auto custom-scrollbar">
              {(historyResult.data?.exitPlanReplayHistory ?? []).length ===
              0 ? (
                <div className="p-6 text-center text-[11px] font-bold text-slate-600">
                  暂无回放记录
                </div>
              ) : (
                historyResult.data?.exitPlanReplayHistory.map(item => (
                  <button
                    className="flex w-full items-center justify-between gap-3 border-b border-white/5 px-3 py-2.5 text-left hover:bg-white/5"
                    key={item.runId}
                    onClick={() => {
                      setRunId(item.runId);
                      onLocationChange({
                        planId: item.planId || undefined,
                        runId: item.runId,
                      });
                    }}
                    type="button"
                  >
                    <span className="min-w-0">
                      <span className="block font-mono text-xs font-black text-slate-200">
                        {item.instrumentCode}
                      </span>
                      <span className="mt-0.5 block truncate text-[10px] text-slate-600">
                        {formatDateTime(item.createdAt)}
                      </span>
                    </span>
                    <span
                      className={cn(
                        'font-mono text-xs font-black',
                        percentTone(item.summary?.planReturnPct)
                      )}
                    >
                      {item.summary
                        ? formatPercent(item.summary.planReturnPct)
                        : item.status}
                    </span>
                  </button>
                ))
              )}
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
