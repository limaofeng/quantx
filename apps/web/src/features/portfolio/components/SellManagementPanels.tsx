import {
  AlertTriangle,
  CheckCircle2,
  CirclePause,
  Clock3,
  ExternalLink,
  History,
  Loader2,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  XCircle,
} from 'lucide-react';
import * as React from 'react';
import { useMutation, useQuery } from 'urql';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { createClientId } from '@/utils/clientId';
import { cn } from '@/utils/cn';

import type {
  LiquidationCompletionStrategy,
  LiquidationConflictStrategy,
  LiquidationExecutionOptions,
} from '../hooks/useLiquidationActions';
import {
  CancelExitPlanMutation,
  ConfirmExitIntentMutation,
  CreateManualExitPlanMutation,
  EvaluateExitPlanNowMutation,
  ExitPlanCapabilitiesQuery,
  ExitPlanEventsQuery,
  ExitPlanHoldingCapacityQuery,
  ExitPlansQuery,
  PreviewExitIntentMutation,
  RejectExitIntentMutation,
  SetExitPlanEnabledMutation,
  UpdateManualExitPlanMutation,
} from '../hooks/usePortfolio';
import type { Position } from '../types';

type ExitPlan = NonNullable<
  NonNullable<ReturnType<typeof useExitPlans>['data']>['exitPlans']
>[number];

interface ExitPlanRuleDraft {
  id: string;
  parametersText: string;
  priority: number;
  ruleType: string;
}

const activeStatuses = new Set([
  'ACTIVE',
  'ERROR',
  'EXIT_PENDING',
  'PARTIALLY_EXITED',
  'PAUSED',
  'PENDING_ENTRY',
]);

const sourceLabels: Record<string, string> = {
  LIMIT_UP_BOARD: '打板退出计划',
  LIMIT_UP_ENTRY: '打板退出计划',
  MANUAL_LIQUIDATION: '人工清仓',
  MANUAL_POSITION: '人工计划',
  TAKE_PROFIT: '止盈/止损计划',
  T_TRADE_BATCH: 'T 批次退出',
};

const statusLabels: Record<string, string> = {
  ACTIVE: '监控中',
  CANCELLED: '已取消',
  COMPLETED: '已完成',
  ERROR: '异常',
  EXIT_PENDING: '待成交',
  PARTIALLY_EXITED: '部分成交',
  PAUSED: '已暂停',
  PENDING_ENTRY: '等待持仓',
};

function useExitPlans(accountId: string, instrumentCode?: string) {
  const [result, refetch] = useQuery({
    query: ExitPlansQuery,
    variables: {
      accountId: accountId || undefined,
      instrumentCode: instrumentCode || undefined,
      limit: 200,
      sourceType: undefined,
      statuses: undefined,
    },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });

  React.useEffect(() => {
    if (!accountId) return undefined;
    const refresh = () => {
      if (document.visibilityState === 'visible') {
        refetch({ requestPolicy: 'network-only' });
      }
    };
    const timer = window.setInterval(refresh, 2_000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [accountId, refetch]);

  return { ...result, refetch };
}

function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString('zh-CN');
}

function statusTone(status: string) {
  if (status === 'ERROR') return 'border-rose-400/30 text-rose-200';
  if (status === 'EXIT_PENDING') return 'border-amber-400/30 text-amber-200';
  if (status === 'COMPLETED') return 'border-emerald-400/30 text-emerald-200';
  if (status === 'PAUSED') return 'border-slate-500/30 text-slate-400';
  return 'border-blue-400/25 text-blue-200';
}

function PlanWarnings({ plan }: { plan: ExitPlan }) {
  const warnings = [
    plan.dataQuality !== 'GOOD' && plan.dataQuality !== 'OK'
      ? `行情：${plan.dataQuality}`
      : '',
    plan.pendingIntentId ? '卖出意图待处理' : '',
    plan.pendingClientOrderId ? `待成交：${plan.pendingClientOrderId}` : '',
    plan.lastError || '',
    plan.completionNote || '',
  ].filter(Boolean);
  if (warnings.length === 0) return null;
  return (
    <div className="mt-2 grid gap-1">
      {warnings.map(item => (
        <div
          className="flex items-start gap-1.5 text-[11px] font-bold text-amber-200"
          key={item}
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="break-all">{item}</span>
        </div>
      ))}
    </div>
  );
}

function PlanCard({
  busy,
  onCancel,
  onConfirmIntent,
  onEvaluate,
  onEdit,
  onNavigate,
  onRejectIntent,
  onToggle,
  plan,
}: {
  busy: boolean;
  onCancel: (plan: ExitPlan) => void;
  onConfirmIntent: (plan: ExitPlan) => void;
  onEvaluate: (plan: ExitPlan) => void;
  onEdit: (plan: ExitPlan) => void;
  onNavigate: (path: string) => void;
  onRejectIntent: (plan: ExitPlan) => void;
  onToggle: (plan: ExitPlan) => void;
  plan: ExitPlan;
}) {
  const terminal = plan.status === 'COMPLETED' || plan.status === 'CANCELLED';
  const pending =
    plan.status === 'EXIT_PENDING' || Boolean(plan.pendingClientOrderId);
  const rules = Array.isArray(plan.rules) ? plan.rules : [];
  return (
    <article className="rounded-md border border-white/8 bg-[#0b1120]/80 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-mono text-sm font-black text-slate-100">
              {plan.instrumentCode}
            </h3>
            <span className="rounded border border-white/10 px-2 py-0.5 text-[10px] font-black text-slate-400">
              {sourceLabels[plan.sourceType] || plan.sourceType}
            </span>
            <span
              className={cn(
                'rounded border px-2 py-0.5 text-[10px] font-black',
                statusTone(plan.status)
              )}
            >
              {statusLabels[plan.status] || plan.status}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] font-bold text-slate-500">
            <span>保护 {plan.protectedVolume.toLocaleString()} 股</span>
            <span>已卖 {plan.exitedVolume.toLocaleString()} 股</span>
            <span>剩余 {plan.remainingVolume.toLocaleString()} 股</span>
            <span>版本 v{plan.configVersion}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {rules.map((rule, index) => {
              const value = rule as { strategy?: string; rule_id?: string };
              return (
                <span
                  className="rounded bg-white/[0.04] px-2 py-1 text-[10px] font-bold text-slate-400"
                  key={value.rule_id || `${plan.planId}-${index}`}
                >
                  {value.strategy || '退出规则'}
                </span>
              );
            })}
          </div>
          <PlanWarnings plan={plan} />
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          {plan.pendingIntentId && !plan.pendingClientOrderId && (
            <>
              <Button
                disabled={busy}
                onClick={() => onConfirmIntent(plan)}
                size="sm"
                type="button"
              >
                <CheckCircle2 />
                预览并确认 SELL
              </Button>
              <Button
                disabled={busy}
                onClick={() => onRejectIntent(plan)}
                size="sm"
                type="button"
                variant="outline"
              >
                <XCircle />
                拒绝意图
              </Button>
            </>
          )}
          {!terminal && (
            <Button
              disabled={busy || pending}
              onClick={() => onToggle(plan)}
              size="sm"
              type="button"
              variant="outline"
            >
              {plan.enabled ? <CirclePause /> : <CheckCircle2 />}
              {plan.enabled ? '暂停' : '恢复'}
            </Button>
          )}
          {!terminal && (
            <Button
              disabled={busy}
              onClick={() => onEvaluate(plan)}
              size="sm"
              type="button"
              variant="outline"
            >
              <RefreshCw />
              立即检查
            </Button>
          )}
          {plan.canEditRules && !terminal && (
            <Button
              disabled={busy || pending}
              onClick={() => onEdit(plan)}
              size="sm"
              type="button"
              variant="outline"
            >
              编辑计划
            </Button>
          )}
          {plan.editRoute && !plan.canEditRules && (
            <Button
              onClick={() => onNavigate(plan.editRoute || '/liquidation')}
              size="sm"
              type="button"
              variant="outline"
            >
              <ExternalLink />
              返回来源编辑
            </Button>
          )}
          {!terminal && (
            <Button
              disabled={busy || pending}
              onClick={() => onCancel(plan)}
              size="sm"
              type="button"
              variant="destructive"
            >
              <XCircle />
              取消
            </Button>
          )}
        </div>
      </div>
      <div className="mt-3 border-t border-white/5 pt-2 text-[10px] font-medium text-slate-600">
        最近评估 {formatDateTime(plan.lastEvaluatedAt)} · 更新{' '}
        {formatDateTime(plan.updatedAt)}
      </div>
    </article>
  );
}

function ManualPlanEditor({
  accountId,
  editingPlan,
  initialInstrumentCode,
  onFinishedEditing,
  onSaved,
}: {
  accountId: string;
  editingPlan?: ExitPlan | null;
  initialInstrumentCode?: string;
  onFinishedEditing: () => void;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const [instrumentCode, setInstrumentCode] = React.useState(
    initialInstrumentCode || ''
  );
  const [protectedVolume, setProtectedVolume] = React.useState('');
  const [executionMode, setExecutionMode] = React.useState<'paper' | 'live'>(
    'paper'
  );
  const [authorized, setAuthorized] = React.useState(false);
  const [remark, setRemark] = React.useState('');
  const [rules, setRules] = React.useState<ExitPlanRuleDraft[]>(() => [
    {
      id: createClientId('exit-rule'),
      parametersText: '{"target_price": 0}',
      priority: 500,
      ruleType: 'TARGET_PRICE',
    },
  ]);
  const [capabilities] = useQuery({ query: ExitPlanCapabilitiesQuery });
  const normalizedCode = instrumentCode.trim().toUpperCase();
  const [capacity] = useQuery({
    query: ExitPlanHoldingCapacityQuery,
    variables: { accountId, instrumentCode: normalizedCode },
    pause: !accountId || !normalizedCode,
    requestPolicy: 'cache-and-network',
  });
  const [createResult, createPlan] = useMutation(CreateManualExitPlanMutation);
  const [updateResult, updatePlan] = useMutation(UpdateManualExitPlanMutation);
  const ruleTypes = capabilities.data?.exitPlanCapabilities.ruleTypes ?? [];

  React.useEffect(() => {
    if (!editingPlan) return;
    const sourceRules = Array.isArray(editingPlan.rules)
      ? (editingPlan.rules as Array<Record<string, unknown>>)
      : [];
    const metadata =
      editingPlan.metadata && typeof editingPlan.metadata === 'object'
        ? (editingPlan.metadata as Record<string, unknown>)
        : {};
    setInstrumentCode(editingPlan.instrumentCode);
    setProtectedVolume(String(editingPlan.protectedVolume));
    setExecutionMode(editingPlan.executionMode === 'live' ? 'live' : 'paper');
    setAuthorized(editingPlan.autoExitAuthorized);
    setRemark(typeof metadata.remark === 'string' ? metadata.remark : '');
    setRules(
      sourceRules.length > 0
        ? sourceRules.map((rule, index) => ({
            id:
              typeof rule.rule_id === 'string'
                ? rule.rule_id
                : `${editingPlan.planId}-${index}`,
            parametersText: JSON.stringify(rule.parameters ?? {}),
            priority: typeof rule.priority === 'number' ? rule.priority : 500,
            ruleType:
              typeof rule.strategy === 'string'
                ? rule.strategy
                : 'TARGET_PRICE',
          }))
        : [
            {
              id: createClientId('exit-rule'),
              parametersText: '{"target_price": 0}',
              priority: 500,
              ruleType: 'TARGET_PRICE',
            },
          ]
    );
    setOpen(true);
  }, [editingPlan]);

  const close = () => {
    setOpen(false);
    if (editingPlan) onFinishedEditing();
  };

  const submit = async () => {
    try {
      const serializedRules = rules.map(rule => ({
        enabled: true,
        once: false,
        parameters: JSON.parse(rule.parametersText || '{}') as object,
        priority: Number(rule.priority),
        rule_id: rule.id,
        sizing: { mode: 'ALL_REMAINING' },
        strategy: rule.ruleType,
      }));
      const result = editingPlan
        ? await updatePlan({
            input: {
              accountId,
              autoExitAuthorized: authorized,
              configVersion: editingPlan.configVersion,
              executionMode,
              planId: editingPlan.planId,
              protectedVolume: Number(protectedVolume),
              remark,
              rules: serializedRules,
            },
          })
        : await createPlan({
            input: {
              accountId,
              autoExitAuthorized: authorized,
              bucket: 'manual',
              enabled: true,
              executionMode,
              instrumentCode: normalizedCode,
              protectedVolume: Number(protectedVolume),
              remark,
              rules: serializedRules,
            },
          });
      if (result.error) throw result.error;
      toast({
        description: `${normalizedCode} · ${protectedVolume} 股`,
        title: editingPlan ? '人工计划已更新' : '人工计划已创建',
      });
      close();
      onSaved();
    } catch (error) {
      toast({
        description: error instanceof Error ? error.message : String(error),
        title: editingPlan ? '计划更新失败' : '计划创建失败',
        variant: 'destructive',
      });
    }
  };

  if (!open) {
    return (
      <Button onClick={() => setOpen(true)} type="button">
        <Plus />
        手动添加计划
      </Button>
    );
  }

  return (
    <section className="rounded-md border border-blue-400/20 bg-blue-500/[0.06] p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-black text-slate-100">
            {editingPlan ? '编辑人工计划' : '人工计划编辑器'}
          </h3>
          <p className="mt-1 text-[11px] font-bold text-slate-500">
            多条规则为 OR；priority 越大越先执行。
          </p>
        </div>
        <Button onClick={close} size="sm" variant="ghost">
          收起
        </Button>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label className="grid gap-1 text-xs font-bold text-slate-400">
          股票
          <input
            className="h-9 rounded-md border border-white/10 bg-[#080d18] px-3 font-mono text-slate-100 outline-none focus:border-blue-400/50"
            onChange={event => setInstrumentCode(event.target.value)}
            placeholder="300917.SZ"
            readOnly={Boolean(editingPlan)}
            value={instrumentCode}
          />
        </label>
        <label className="grid gap-1 text-xs font-bold text-slate-400">
          保护数量
          <input
            className="h-9 rounded-md border border-white/10 bg-[#080d18] px-3 font-mono text-slate-100 outline-none focus:border-blue-400/50"
            min={1}
            onChange={event => setProtectedVolume(event.target.value)}
            type="number"
            value={protectedVolume}
          />
        </label>
        <label className="grid gap-1 text-xs font-bold text-slate-400">
          模式
          <select
            className="h-9 rounded-md border border-white/10 bg-[#080d18] px-3 text-slate-100 outline-none focus:border-blue-400/50"
            onChange={event =>
              setExecutionMode(event.target.value as 'paper' | 'live')
            }
            value={executionMode}
          >
            <option value="paper">模拟</option>
            <option value="live">实盘</option>
          </select>
        </label>
        <label className="flex items-end gap-2 pb-2 text-xs font-bold text-slate-400">
          <input
            checked={authorized}
            onChange={event => setAuthorized(event.target.checked)}
            type="checkbox"
          />
          授权触发后自动进入卖出风控
        </label>
      </div>
      <label className="mt-3 grid gap-1 text-xs font-bold text-slate-400">
        备注
        <input
          className="h-9 rounded-md border border-white/10 bg-[#080d18] px-3 text-slate-100 outline-none focus:border-blue-400/50"
          onChange={event => setRemark(event.target.value)}
          placeholder="这项计划的卖出目的或备注"
          value={remark}
        />
      </label>
      {capacity.data?.exitPlanHoldingCapacity && (
        <div className="mt-3 rounded border border-white/8 bg-black/10 px-3 py-2 text-[11px] font-bold text-slate-400">
          持仓 {capacity.data.exitPlanHoldingCapacity.totalVolume} · 已保护{' '}
          {capacity.data.exitPlanHoldingCapacity.protectedVolume} · 可认领{' '}
          {capacity.data.exitPlanHoldingCapacity.unallocatedVolume} 股
        </div>
      )}
      <div className="mt-3 grid gap-2">
        {rules.map((rule, index) => (
          <div
            className="grid gap-2 rounded border border-white/8 bg-black/10 p-2 lg:grid-cols-[220px_100px_minmax(260px,1fr)_36px]"
            key={rule.id}
          >
            <select
              aria-label={`规则 ${index + 1} 类型`}
              className="h-9 rounded border border-white/10 bg-[#080d18] px-2 text-xs text-slate-200"
              onChange={event =>
                setRules(current =>
                  current.map(item =>
                    item.id === rule.id
                      ? { ...item, ruleType: event.target.value }
                      : item
                  )
                )
              }
              value={rule.ruleType}
            >
              {ruleTypes.map(item => (
                <option key={item.ruleType} value={item.ruleType}>
                  {item.label} · {item.ruleType}
                </option>
              ))}
            </select>
            <input
              aria-label={`规则 ${index + 1} 优先级`}
              className="h-9 rounded border border-white/10 bg-[#080d18] px-2 font-mono text-xs text-slate-200"
              onChange={event =>
                setRules(current =>
                  current.map(item =>
                    item.id === rule.id
                      ? { ...item, priority: Number(event.target.value) }
                      : item
                  )
                )
              }
              type="number"
              value={rule.priority}
            />
            <input
              aria-label={`规则 ${index + 1} 参数 JSON`}
              className="h-9 rounded border border-white/10 bg-[#080d18] px-2 font-mono text-xs text-slate-200"
              onChange={event =>
                setRules(current =>
                  current.map(item =>
                    item.id === rule.id
                      ? { ...item, parametersText: event.target.value }
                      : item
                  )
                )
              }
              value={rule.parametersText}
            />
            <Button
              aria-label={`删除规则 ${index + 1}`}
              disabled={rules.length === 1}
              onClick={() =>
                setRules(current => current.filter(item => item.id !== rule.id))
              }
              size="icon"
              type="button"
              variant="ghost"
            >
              <Trash2 />
            </Button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap justify-between gap-2">
        <Button
          onClick={() =>
            setRules(current => [
              ...current,
              {
                id: createClientId('exit-rule'),
                parametersText: '{}',
                priority: 500,
                ruleType: ruleTypes[0]?.ruleType || 'TARGET_PRICE',
              },
            ])
          }
          type="button"
          variant="outline"
        >
          <Plus />
          添加 OR 规则
        </Button>
        <Button
          disabled={
            createResult.fetching ||
            updateResult.fetching ||
            !normalizedCode ||
            Number(protectedVolume) <= 0 ||
            rules.length === 0
          }
          onClick={submit}
          type="button"
        >
          {(createResult.fetching || updateResult.fetching) && (
            <Loader2 className="animate-spin" />
          )}
          {editingPlan ? '保存计划修改' : '创建退出计划'}
        </Button>
      </div>
    </section>
  );
}

export function ExitPlansPanel({
  accountId,
  instrumentCode,
  onNavigate,
}: {
  accountId: string;
  instrumentCode?: string;
  onNavigate: (path: string) => void;
}) {
  const { toast } = useToast();
  const [editingPlan, setEditingPlan] = React.useState<ExitPlan | null>(null);
  const plans = useExitPlans(accountId, instrumentCode);
  const [toggleResult, togglePlan] = useMutation(SetExitPlanEnabledMutation);
  const [cancelResult, cancelPlan] = useMutation(CancelExitPlanMutation);
  const [evaluateResult, evaluatePlan] = useMutation(
    EvaluateExitPlanNowMutation
  );
  const [previewResult, previewIntent] = useMutation(PreviewExitIntentMutation);
  const [confirmResult, confirmIntent] = useMutation(ConfirmExitIntentMutation);
  const [rejectResult, rejectIntent] = useMutation(RejectExitIntentMutation);
  const busy =
    toggleResult.fetching ||
    cancelResult.fetching ||
    evaluateResult.fetching ||
    previewResult.fetching ||
    confirmResult.fetching ||
    rejectResult.fetching;
  const run = async (
    action: () => Promise<{ error?: Error }>,
    title: string
  ) => {
    const result = await action();
    toast({
      description: result.error?.message,
      title: result.error ? `${title}失败` : title,
      variant: result.error ? 'destructive' : 'default',
    });
    plans.refetch({ requestPolicy: 'network-only' });
  };
  const visiblePlans = (plans.data?.exitPlans ?? []).filter(plan =>
    activeStatuses.has(plan.status)
  );
  const approvePendingIntent = async (plan: ExitPlan) => {
    if (!plan.pendingIntentId) return;
    const previewOperation = await previewIntent({
      intentId: plan.pendingIntentId,
      planId: plan.planId,
    });
    const preview = previewOperation.data?.previewExitIntent.preview;
    if (previewOperation.error || !preview) {
      toast({
        description:
          previewOperation.error?.message ||
          previewOperation.data?.previewExitIntent.message,
        title: '卖出预览失败',
        variant: 'destructive',
      });
      return;
    }
    const accepted = window.confirm(
      [
        `${preview.instrumentCode} ${preview.side}`,
        `数量：${preview.targetVolume ?? '--'} 股`,
        `参考价：${preview.referencePrice ?? '--'}`,
        ...(preview.warnings ?? []),
      ].join('\n')
    );
    if (!accepted) return;
    const confirmation = await confirmIntent({
      confirmationToken: preview.confirmationToken,
      intentId: plan.pendingIntentId,
      planId: plan.planId,
    });
    toast({
      description:
        confirmation.data?.confirmExitIntent.message ||
        confirmation.error?.message,
      title: confirmation.data?.confirmExitIntent.success
        ? '卖出意图已确认'
        : '卖出意图确认失败',
      variant: confirmation.data?.confirmExitIntent.success
        ? 'default'
        : 'destructive',
    });
    plans.refetch({ requestPolicy: 'network-only' });
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-black text-slate-100">退出计划</h2>
          <p className="mt-1 text-xs font-bold text-slate-500">
            统一监控打板、T 批次、止盈/止损、人工计划和人工清仓。
          </p>
        </div>
        <ManualPlanEditor
          accountId={accountId}
          editingPlan={editingPlan}
          initialInstrumentCode={instrumentCode}
          onFinishedEditing={() => setEditingPlan(null)}
          onSaved={() => plans.refetch({ requestPolicy: 'network-only' })}
        />
      </div>
      {plans.error && (
        <div className="mt-3 rounded border border-rose-400/20 bg-rose-500/10 p-3 text-xs font-bold text-rose-100">
          {plans.error.message}
        </div>
      )}
      <div className="mt-3 grid gap-2">
        {plans.fetching && visiblePlans.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm font-bold text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载退出计划
          </div>
        ) : visiblePlans.length === 0 ? (
          <div className="rounded-md border border-dashed border-white/10 py-16 text-center text-sm font-bold text-slate-500">
            暂无进行中的退出计划
          </div>
        ) : (
          visiblePlans.map(plan => (
            <PlanCard
              busy={busy}
              key={plan.planId}
              onCancel={item =>
                void run(
                  () =>
                    cancelPlan({
                      configVersion: item.configVersion,
                      planId: item.planId,
                    }),
                  '退出计划已取消'
                )
              }
              onConfirmIntent={item => void approvePendingIntent(item)}
              onEvaluate={item =>
                void run(
                  () => evaluatePlan({ planId: item.planId }),
                  '已请求立即检查'
                )
              }
              onEdit={item => setEditingPlan(item)}
              onNavigate={onNavigate}
              onRejectIntent={item =>
                void run(
                  () =>
                    rejectIntent({
                      intentId: item.pendingIntentId || '',
                      planId: item.planId,
                    }),
                  '卖出意图已拒绝'
                )
              }
              onToggle={item =>
                void run(
                  () =>
                    togglePlan({
                      configVersion: item.configVersion,
                      enabled: !item.enabled,
                      planId: item.planId,
                    }),
                  item.enabled ? '退出计划已暂停' : '退出计划已恢复'
                )
              }
              plan={plan}
            />
          ))
        )}
      </div>
    </div>
  );
}

function ChoiceCard({
  checked,
  description,
  label,
  onChange,
  value,
}: {
  checked: boolean;
  description: string;
  label: string;
  onChange: (value: string) => void;
  value: string;
}) {
  return (
    <label
      className={cn(
        'flex cursor-pointer gap-2 rounded-md border p-3 transition-colors',
        checked
          ? 'border-red-400/40 bg-red-500/10'
          : 'border-white/8 bg-white/[0.02] hover:border-white/20'
      )}
    >
      <input
        checked={checked}
        className="mt-0.5"
        name={
          value.startsWith('AVAILABLE') || value.startsWith('UNTIL')
            ? 'completion'
            : 'conflict'
        }
        onChange={() => onChange(value)}
        type="radio"
      />
      <span>
        <span className="block text-xs font-black text-slate-200">{label}</span>
        <span className="mt-1 block text-[11px] font-bold leading-5 text-slate-500">
          {description}
        </span>
      </span>
    </label>
  );
}

export function PositionLiquidationPanel({
  accountId,
  holdings,
  isSubmitting,
  liquidateMultiple,
}: {
  accountId: string;
  holdings: Position[];
  isSubmitting: boolean;
  liquidateMultiple: (
    stockCodes: string[],
    options: LiquidationExecutionOptions
  ) => Promise<unknown>;
}) {
  const { toast } = useToast();
  const [selected, setSelected] = React.useState<string[]>([]);
  const [completion, setCompletion] = React.useState<
    LiquidationCompletionStrategy | ''
  >('');
  const [conflict, setConflict] = React.useState<
    LiquidationConflictStrategy | ''
  >('');
  const [executionMode, setExecutionMode] = React.useState<
    'paper' | 'live' | ''
  >('');
  const plans = useExitPlans(accountId);
  const positions = holdings.filter(item => Number(item.volume || 0) > 0);
  const selectedSet = new Set(selected);
  const conflicts = (plans.data?.exitPlans ?? []).filter(
    plan =>
      selectedSet.has(plan.instrumentCode) && activeStatuses.has(plan.status)
  );
  const canSubmit = Boolean(
    selected.length && completion && conflict && executionMode
  );

  const submit = async () => {
    if (!completion || !conflict || !executionMode) return;
    try {
      await liquidateMultiple(selected, {
        autoExitAuthorized: executionMode === 'paper',
        completionStrategy: completion,
        conflictStrategy: conflict,
        executionMode,
      });
      toast({ title: '清仓计划已创建' });
      plans.refetch({ requestPolicy: 'network-only' });
    } catch (error) {
      toast({
        description: error instanceof Error ? error.message : String(error),
        title: '清仓计划创建失败',
        variant: 'destructive',
      });
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
      <div>
        <h2 className="text-base font-black text-slate-100">持仓清仓</h2>
        <p className="mt-1 text-xs font-bold text-slate-500">
          清仓是明确动作；确认后按股票创建独立退出计划并由统一状态机执行。
        </p>
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="rounded-md border border-white/8 bg-[#0b1120]/70">
          <div className="flex items-center justify-between border-b border-white/5 p-3">
            <span className="text-xs font-black text-slate-200">
              已选 {selected.length} / {positions.length}
            </span>
            <Button
              onClick={() =>
                setSelected(
                  selected.length === positions.length
                    ? []
                    : positions.map(item => item.stockCode.toUpperCase())
                )
              }
              size="sm"
              type="button"
              variant="outline"
            >
              {selected.length === positions.length ? '取消全选' : '一键全选'}
            </Button>
          </div>
          <div className="divide-y divide-white/5">
            {positions.map(position => {
              const code = position.stockCode.toUpperCase();
              return (
                <label
                  className="flex cursor-pointer items-center justify-between gap-3 px-3 py-3 hover:bg-white/[0.03]"
                  key={position.id}
                >
                  <span className="flex min-w-0 items-center gap-3">
                    <input
                      checked={selectedSet.has(code)}
                      onChange={() =>
                        setSelected(current =>
                          current.includes(code)
                            ? current.filter(item => item !== code)
                            : [...current, code]
                        )
                      }
                      type="checkbox"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-black text-slate-100">
                        {position.instrumentName || code}
                      </span>
                      <span className="font-mono text-[10px] text-slate-600">
                        {code}
                      </span>
                    </span>
                  </span>
                  <span className="text-right font-mono text-xs text-slate-300">
                    <span className="block">持仓 {position.volume}</span>
                    <span className="block text-[10px] text-slate-600">
                      当前可卖 {position.canUseVolume}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </section>
        <section className="rounded-md border border-white/8 bg-[#0b1120]/70 p-3">
          <h3 className="text-xs font-black text-slate-200">本次清仓规则</h3>
          <p className="mt-1 text-[11px] font-bold text-amber-200">
            以下选项没有默认值，必须逐项明确选择。
          </p>
          <div className="mt-3 grid gap-2">
            <ChoiceCard
              checked={completion === 'AVAILABLE_NOW'}
              description="只保护确认时可卖数量；不继续等待 T+1。"
              label="仅卖当前可用"
              onChange={value =>
                setCompletion(value as LiquidationCompletionStrategy)
              }
              value="AVAILABLE_NOW"
            />
            <ChoiceCard
              checked={completion === 'UNTIL_SNAPSHOT_CLEARED'}
              description="保护确认时总持仓；可卖部分先处理，其余跨日继续。后续新买不纳入。"
              label="持续至快照清完"
              onChange={value =>
                setCompletion(value as LiquidationCompletionStrategy)
              }
              value="UNTIL_SNAPSHOT_CLEARED"
            />
          </div>
          <div className="mt-4 grid gap-2">
            <ChoiceCard
              checked={conflict === 'UNALLOCATED_ONLY'}
              description="保留原计划，只清理尚未被任何计划保护的数量。"
              label="只卖未分配数量"
              onChange={value =>
                setConflict(value as LiquidationConflictStrategy)
              }
              value="UNALLOCATED_ONLY"
            />
            <ChoiceCard
              checked={conflict === 'REPLACE_CANCELLABLE'}
              description="取消没有待成交委托的冲突计划，再创建本次清仓计划。"
              label="替换可取消计划"
              onChange={value =>
                setConflict(value as LiquidationConflictStrategy)
              }
              value="REPLACE_CANCELLABLE"
            />
          </div>
          <label className="mt-4 grid gap-1 text-xs font-bold text-slate-400">
            执行模式（必选）
            <select
              className="h-9 rounded border border-white/10 bg-[#080d18] px-2 text-slate-200"
              onChange={event =>
                setExecutionMode(event.target.value as 'paper' | 'live' | '')
              }
              value={executionMode}
            >
              <option value="">请选择</option>
              <option value="paper">模拟</option>
              <option value="live">实盘（卖出意图需再次确认）</option>
            </select>
          </label>
          {conflicts.length > 0 && (
            <div className="mt-4 rounded border border-amber-400/20 bg-amber-500/10 p-3">
              <div className="flex items-center gap-2 text-xs font-black text-amber-100">
                <ShieldAlert className="h-4 w-4" />
                冲突计划 {conflicts.length} 条
              </div>
              <div className="mt-2 grid gap-1 text-[11px] font-bold text-amber-200/80">
                {conflicts.map(plan => (
                  <div key={plan.planId}>
                    {plan.instrumentCode} ·{' '}
                    {sourceLabels[plan.sourceType] || plan.sourceType} · 剩余{' '}
                    {plan.remainingVolume} 股 ·{' '}
                    {statusLabels[plan.status] || plan.status}
                  </div>
                ))}
              </div>
            </div>
          )}
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                className="mt-4 w-full"
                disabled={!canSubmit || isSubmitting}
                type="button"
                variant="destructive"
              >
                {isSubmitting && <Loader2 className="animate-spin" />}
                创建清仓计划
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认本次持仓清仓</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div className="grid gap-2 text-sm">
                    <p>本次将为 {selected.length} 只股票分别创建退出计划。</p>
                    <p>完成策略：{completion}</p>
                    <p>冲突策略：{conflict}</p>
                    <p>执行模式：{executionMode}</p>
                    {conflicts.length > 0 && (
                      <p className="font-bold text-amber-600">
                        已列出 {conflicts.length}{' '}
                        条冲突计划；待成交计划不会被替换。
                      </p>
                    )}
                    <p className="font-bold text-slate-700">
                      后续新买股份不会自动加入本次清仓。
                    </p>
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>返回检查</AlertDialogCancel>
                <AlertDialogAction onClick={() => void submit()}>
                  确认创建
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </section>
      </div>
    </div>
  );
}

export function SellHistoryPanel({ accountId }: { accountId: string }) {
  const plans = useExitPlans(accountId);
  const allPlans = plans.data?.exitPlans ?? [];
  const [selectedPlanId, setSelectedPlanId] = React.useState('');
  const activePlanId = selectedPlanId || allPlans[0]?.planId || '';
  const [events] = useQuery({
    query: ExitPlanEventsQuery,
    variables: { limit: 200, planId: activePlanId },
    pause: !activePlanId,
    requestPolicy: 'cache-and-network',
  });

  return (
    <div className="grid min-h-0 flex-1 gap-3 overflow-hidden p-3 xl:grid-cols-[360px_minmax(0,1fr)]">
      <section className="min-h-0 overflow-y-auto rounded-md border border-white/8 bg-[#0b1120]/70 custom-scrollbar">
        <div className="sticky top-0 flex items-center gap-2 border-b border-white/5 bg-[#0b1120] p-3 text-xs font-black text-slate-200">
          <History className="h-4 w-4 text-red-300" />
          卖出计划
        </div>
        <div className="divide-y divide-white/5">
          {allPlans.map(plan => (
            <button
              className={cn(
                'w-full px-3 py-3 text-left hover:bg-white/[0.03] focus:outline-none focus:ring-1 focus:ring-inset focus:ring-red-400/40',
                activePlanId === plan.planId && 'bg-white/[0.05]'
              )}
              key={plan.planId}
              onClick={() => setSelectedPlanId(plan.planId)}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs font-black text-slate-100">
                  {plan.instrumentCode}
                </span>
                <span
                  className={cn(
                    'text-[10px] font-black',
                    statusTone(plan.status)
                  )}
                >
                  {statusLabels[plan.status] || plan.status}
                </span>
              </div>
              <div className="mt-1 text-[10px] font-bold text-slate-500">
                {sourceLabels[plan.sourceType] || plan.sourceType} ·{' '}
                {formatDateTime(plan.updatedAt)}
              </div>
            </button>
          ))}
        </div>
      </section>
      <section className="min-h-0 overflow-y-auto rounded-md border border-white/8 bg-[#0b1120]/70 p-3 custom-scrollbar">
        <h2 className="text-sm font-black text-slate-100">统一卖出时间线</h2>
        <p className="mt-1 text-[11px] font-bold text-slate-500">
          规则触发、计划变更、委托状态和真实成交均以持久化事件展示。
        </p>
        <div className="mt-4 grid gap-2">
          {events.fetching && !events.data ? (
            <div className="flex justify-center py-12 text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : (events.data?.exitPlanEvents ?? []).length === 0 ? (
            <div className="rounded border border-dashed border-white/10 py-12 text-center text-xs font-bold text-slate-500">
              暂无事件
            </div>
          ) : (
            (events.data?.exitPlanEvents ?? []).map(event => (
              <div
                className="grid grid-cols-[20px_minmax(0,1fr)] gap-2"
                key={event.eventId}
              >
                <div className="flex flex-col items-center">
                  <Clock3 className="h-4 w-4 text-red-300" />
                  <div className="mt-1 h-full w-px bg-white/8" />
                </div>
                <div className="mb-2 rounded border border-white/8 bg-white/[0.025] p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-black text-slate-200">
                      {event.eventType}
                    </span>
                    <span className="font-mono text-[10px] text-slate-600">
                      {formatDateTime(event.createdAt)}
                    </span>
                  </div>
                  <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-5 text-slate-500">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                </div>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
