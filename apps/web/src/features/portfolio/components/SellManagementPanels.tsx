import {
  CheckCircle2,
  CirclePause,
  Clock3,
  ExternalLink,
  FlaskConical,
  History,
  Loader2,
  Plus,
  RefreshCw,
  ShieldAlert,
  Trash2,
  XCircle,
} from 'lucide-react';
import * as React from 'react';
import { useMutation, useQuery, useSubscription } from 'urql';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
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
import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
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
  ConfirmExitPlanAuthorizationMutation,
  ConfirmExitIntentMutation,
  CreateManualExitPlanMutation,
  DeleteExitPlanHistoryMutation,
  EvaluateExitPlanNowMutation,
  ExitPlanCapabilitiesQuery,
  ExitPlanCostBasisCandidatesQuery,
  ExitPlanEventsQuery,
  ExitPlanHoldingCapacityQuery,
  ExitPlanUpdatesSubscription,
  ExitPlansQuery,
  PreviewExitPlanAuthorizationMutation,
  PreviewExitIntentMutation,
  ReconcileExitPlanCapacityMutation,
  RejectExitIntentMutation,
  SetExitPlanEnabledMutation,
  UpdateManualExitPlanMutation,
} from '../hooks/usePortfolio';
import type { Position } from '../types';

import {
  ExitPlanCapacityBanner,
  ExitPlanCostBasisEditor,
  ExitPlanCostBasisSummary,
} from './ExitPlanCostBasis';
import {
  summarizeSelectedCostBasis,
  type ManualCostBasisMode,
} from './exitPlanCostBasisUtils';
import { ExitPlanNotices } from './ExitPlanNotices';
import { getExitRuleLabel } from './exitRuleLabels';
import {
  ManualExitRuleEditor,
  type ManualExitRuleDraft,
} from './ManualExitRuleEditor';

type ExitPlan = NonNullable<
  NonNullable<ReturnType<typeof useExitPlans>['data']>['exitPlans']
>[number];

interface ExitPlanAuthorizationChallenge {
  accountId: string;
  authorizationExpiresAt: string;
  authorizationFingerprint: string;
  challengeExpiresAt: string;
  challengeId: string;
  configVersion: number;
  costBasis: unknown;
  confirmationToken: string;
  executionPolicy: unknown;
  exitedVolume: number;
  idempotencyKey: string;
  instrumentCode: string;
  otherProtections: Array<{
    pending: boolean;
    planId: string;
    remainingVolume: number;
    sourceType: string;
    status: string;
  }>;
  planId: string;
  position: {
    availableVolume: number;
    frozenVolume: number;
    positionUpdatedAt?: string | null;
    t1UnavailableVolume: number;
    totalVolume: number;
    yesterdayVolume: number;
  };
  protectedVolume: number;
  readiness: unknown;
  remainingVolume: number;
  rules: unknown;
  t1Policy: string;
  warnings: string[];
}

const activeStatuses = new Set([
  'ACTIVE',
  'ERROR',
  'EXIT_PENDING',
  'PARTIALLY_EXITED',
  'PAUSED',
  'PENDING_ENTRY',
]);
const terminalStatuses = new Set(['COMPLETED', 'CANCELLED']);

const sourceLabels: Record<string, string> = {
  LIMIT_UP_BOARD: '打板卖出计划',
  LIMIT_UP_ENTRY: '打板卖出计划',
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

const executionOwnerLabels: Record<string, string> = {
  STRATEGY_RUN: '策略运行',
  T_ASSISTANT_EXECUTION: '做 T 助手执行',
  ENTRY_PLAN: '建仓计划',
  BOARD_ASSISTANT_EXECUTION: '打板助手执行',
  EXIT_PLAN: '退出计划',
  MANUAL_COMMAND: '人工命令',
};

const executionEnvironmentLabels: Record<string, string> = {
  PAPER: '模拟执行',
  LIVE: '实盘执行',
  BACKTEST: '回测执行',
};

function executionOwnerLabel(ownerType: string) {
  return executionOwnerLabels[ownerType] || `未知执行归属（${ownerType}）`;
}

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
  const [update] = useSubscription({
    query: ExitPlanUpdatesSubscription,
    variables: {
      accountId: accountId || undefined,
      instrumentCode: instrumentCode || undefined,
    },
    pause: !accountId,
  });

  React.useEffect(() => {
    if (!accountId) return undefined;
    const refresh = () => {
      if (document.visibilityState === 'visible') {
        refetch({ requestPolicy: 'network-only' });
      }
    };
    const timer = window.setInterval(refresh, 60_000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [accountId, refetch]);

  React.useEffect(() => {
    if (!update.data?.exitPlanUpdates) return undefined;
    const timer = window.setTimeout(() => {
      if (document.visibilityState === 'visible') {
        refetch({ requestPolicy: 'network-only' });
      }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [refetch, update.data?.exitPlanUpdates]);

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

function PlanCard({
  busy,
  instrumentName,
  onCancel,
  onConfirmIntent,
  onEvaluate,
  onEdit,
  onNavigate,
  onReplay,
  onRejectIntent,
  onToggle,
  plan,
}: {
  busy: boolean;
  instrumentName?: string | null;
  onCancel: (plan: ExitPlan) => void;
  onConfirmIntent: (plan: ExitPlan) => void;
  onEvaluate: (plan: ExitPlan) => void;
  onEdit: (plan: ExitPlan) => void;
  onNavigate: (path: string) => void;
  onReplay: (plan: ExitPlan) => void;
  onRejectIntent: (plan: ExitPlan) => void;
  onToggle: (plan: ExitPlan) => void;
  plan: ExitPlan;
}) {
  const terminal = plan.status === 'COMPLETED' || plan.status === 'CANCELLED';
  const ownerBindingInvalid =
    plan.executionOwner.ownerType !== 'EXIT_PLAN' ||
    plan.executionOwner.ownerId !== plan.planId;
  const pending =
    plan.status === 'EXIT_PENDING' || Boolean(plan.pendingClientOrderId);
  const recoveryLocked = Boolean(plan.recoveryAction);
  const requiresRebuild = plan.recoveryAction === 'CANCEL_AND_REBUILD';
  const rules = Array.isArray(plan.rules) ? plan.rules : [];
  const authorizationExpiresAt = plan.autoExitAuthorizationExpiresAt
    ? new Date(plan.autoExitAuthorizationExpiresAt).getTime()
    : 0;
  const liveAuthorizationActive =
    plan.environment === 'LIVE' &&
    plan.autoExitAuthorized &&
    plan.autoExitAuthorizationConfigVersion === plan.configVersion &&
    authorizationExpiresAt > Date.now();
  return (
    <article className="rounded-md border border-white/8 bg-[#0b1120]/80 p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="flex min-w-0 items-baseline gap-2 text-ui-body font-black text-slate-100">
              {instrumentName ? (
                <span className="truncate">{instrumentName}</span>
              ) : null}
              <span
                className={cn(
                  'shrink-0 font-mono',
                  instrumentName && 'text-ui-label text-slate-400'
                )}
              >
                {plan.instrumentCode}
              </span>
            </h3>
            <span className="rounded border border-white/10 px-2 py-0.5 text-ui-caption font-black text-slate-400">
              {sourceLabels[plan.sourceType] || plan.sourceType}
            </span>
            <span
              className={cn(
                'rounded border px-2 py-0.5 text-ui-caption font-black',
                statusTone(plan.status)
              )}
            >
              {statusLabels[plan.status] || plan.status}
            </span>
            {plan.environment === 'LIVE' ? (
              <span
                className={cn(
                  'rounded border px-2 py-0.5 text-ui-caption font-black',
                  liveAuthorizationActive
                    ? 'border-emerald-400/30 text-emerald-200'
                    : 'border-amber-400/30 text-amber-200'
                )}
              >
                {liveAuthorizationActive
                  ? '实盘自动卖出已授权'
                  : '实盘卖出需逐次确认'}
              </span>
            ) : (
              <span className="rounded border border-slate-500/30 px-2 py-0.5 text-ui-caption font-black text-slate-400">
                {executionEnvironmentLabels[plan.environment] ||
                  `未知环境（${plan.environment}）`}
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-ui-caption font-bold text-slate-500">
            <span>计划卖出 {plan.protectedVolume.toLocaleString()} 股</span>
            <span>已卖 {plan.exitedVolume.toLocaleString()} 股</span>
            <span>剩余 {plan.remainingVolume.toLocaleString()} 股</span>
            <span>版本 v{plan.configVersion}</span>
            <span>
              执行归属 {executionOwnerLabel(plan.executionOwner.ownerType)}
            </span>
            <span>
              来源归属{' '}
              {executionOwnerLabel(plan.sourceExecutionOwner.ownerType)}
            </span>
            <span>状态修订 r{plan.stateVersion}</span>
          </div>
          <div className="mt-2">
            <ExitPlanCostBasisSummary showFrozenAt value={plan.costBasis} />
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {rules.map((rule, index) => {
              const value = rule as { strategy?: string; rule_id?: string };
              return (
                <span
                  className="rounded bg-white/[0.04] px-2 py-1 text-ui-caption font-bold text-slate-400"
                  key={value.rule_id || `${plan.planId}-${index}`}
                >
                  {getExitRuleLabel(value.strategy)}
                </span>
              );
            })}
          </div>
          {ownerBindingInvalid ? (
            <p
              className="mt-2 text-ui-caption font-bold text-rose-300"
              role="alert"
            >
              执行归属校验失败，计划操作已停用
            </p>
          ) : null}
          <ExitPlanNotices plan={plan} />
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <Button
            disabled={busy || ownerBindingInvalid}
            onClick={() => onReplay(plan)}
            size="sm"
            type="button"
            variant="outline"
          >
            <FlaskConical />
            回放测试
          </Button>
          {plan.pendingIntentId && !plan.pendingClientOrderId && (
            <>
              <Button
                disabled={busy || ownerBindingInvalid}
                onClick={() => onConfirmIntent(plan)}
                size="sm"
                type="button"
              >
                <CheckCircle2 />
                预览并确认 SELL
              </Button>
              <Button
                disabled={busy || ownerBindingInvalid}
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
          {!terminal && !recoveryLocked && (
            <Button
              disabled={busy || pending || ownerBindingInvalid}
              onClick={() => onToggle(plan)}
              size="sm"
              type="button"
              variant="outline"
            >
              {plan.enabled ? <CirclePause /> : <CheckCircle2 />}
              {plan.enabled ? '暂停' : '恢复'}
            </Button>
          )}
          {!terminal && !recoveryLocked && (
            <Button
              disabled={busy || ownerBindingInvalid}
              onClick={() => onEvaluate(plan)}
              size="sm"
              type="button"
              variant="outline"
            >
              <RefreshCw />
              立即检查
            </Button>
          )}
          {plan.canEditRules &&
            !terminal &&
            !ownerBindingInvalid &&
            !recoveryLocked && (
              <Button
                disabled={busy || pending || ownerBindingInvalid}
                onClick={() => onEdit(plan)}
                size="sm"
                type="button"
                variant="outline"
              >
                编辑计划
              </Button>
            )}
          {plan.editRoute && !plan.canEditRules && !ownerBindingInvalid && (
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
              disabled={
                busy ||
                pending ||
                ownerBindingInvalid ||
                plan.recoveryAction === 'COMPLETE_RECONCILIATION'
              }
              onClick={() => onCancel(plan)}
              size="sm"
              type="button"
              variant="destructive"
            >
              <XCircle />
              {requiresRebuild ? '取消旧计划' : '取消'}
            </Button>
          )}
        </div>
      </div>
      <div className="mt-3 border-t border-white/5 pt-2 text-ui-caption font-medium text-slate-600">
        最近评估 {formatDateTime(plan.lastEvaluatedAt)} · 更新{' '}
        {formatDateTime(plan.updatedAt)}
      </div>
    </article>
  );
}

export function ManualPlanEditor({
  accountId,
  editingPlan,
  initialInstrumentCode,
  onFinishedEditing,
  onReplayDraft,
  onSaved,
}: {
  accountId: string;
  editingPlan?: ExitPlan | null;
  initialInstrumentCode?: string;
  onFinishedEditing: () => void;
  onReplayDraft?: (template: Record<string, unknown>) => void;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const [open, setOpen] = React.useState(false);
  const [instrumentCode, setInstrumentCode] = React.useState(
    initialInstrumentCode || ''
  );
  const [protectedVolume, setProtectedVolume] = React.useState('');
  const [costBasisMode, setCostBasisMode] =
    React.useState<ManualCostBasisMode>('BROKER_BUY_ORDERS');
  const [selectedOrderIds, setSelectedOrderIds] = React.useState<string[]>([]);
  const [manualUnitCost, setManualUnitCost] = React.useState('');
  const [executionMode, setExecutionMode] = React.useState<'paper' | 'live'>(
    'paper'
  );
  const [requestLiveAuthorization, setRequestLiveAuthorization] =
    React.useState(false);
  const [remark, setRemark] = React.useState('');
  const [authorizationChallenge, setAuthorizationChallenge] =
    React.useState<ExitPlanAuthorizationChallenge | null>(null);
  const [authorizationError, setAuthorizationError] = React.useState<
    string | null
  >(null);
  const createRequestRef = React.useRef<{
    fingerprint: string;
    idempotencyKey: string;
  } | null>(null);
  const updateRequestRef = React.useRef<{
    fingerprint: string;
    idempotencyKey: string;
  } | null>(null);
  const [rules, setRules] = React.useState<ManualExitRuleDraft[]>(() => [
    {
      id: createClientId('exit-rule'),
      parametersText: '{"target_price":0}',
      priority: 500,
      ruleType: 'TARGET_PRICE',
    },
  ]);
  const [capabilities] = useQuery({ query: ExitPlanCapabilitiesQuery });
  const normalizedCode = instrumentCode.trim().toUpperCase();
  const [capacity, refetchCapacity] = useQuery({
    query: ExitPlanHoldingCapacityQuery,
    variables: { accountId, instrumentCode: normalizedCode },
    pause: (!open && !editingPlan) || !accountId || !normalizedCode,
    requestPolicy: 'cache-and-network',
  });
  const [costBasisCandidates] = useQuery({
    query: ExitPlanCostBasisCandidatesQuery,
    variables: {
      accountId,
      instrumentCode: normalizedCode,
      limit: 100,
    },
    pause: !open || Boolean(editingPlan) || !accountId || !normalizedCode,
    requestPolicy: 'cache-and-network',
  });
  const [createResult, createPlan] = useMutation(CreateManualExitPlanMutation);
  const [updateResult, updatePlan] = useMutation(UpdateManualExitPlanMutation);
  const [authorizationPreviewResult, previewAuthorization] = useMutation(
    PreviewExitPlanAuthorizationMutation
  );
  const [authorizationConfirmResult, confirmAuthorization] = useMutation(
    ConfirmExitPlanAuthorizationMutation
  );
  const [reconcileResult, reconcileCapacity] = useMutation(
    ReconcileExitPlanCapacityMutation
  );
  const ruleTypes = capabilities.data?.exitPlanCapabilities.ruleTypes ?? [];
  const candidateItems =
    costBasisCandidates.data?.exitPlanCostBasisCandidates?.items ?? [];
  const selectedCostBasis = summarizeSelectedCostBasis(
    candidateItems,
    selectedOrderIds
  );
  const requestedVolume = Number(protectedVolume);
  const costBasisInvalid = editingPlan
    ? false
    : costBasisMode === 'BROKER_BUY_ORDERS'
      ? selectedCostBasis.volume < requestedVolume ||
        selectedCostBasis.volume <= 0
      : !Number.isFinite(Number(manualUnitCost)) || Number(manualUnitCost) <= 0;

  const serializedRules = React.useCallback(
    () =>
      rules.map(rule => ({
        enabled: true,
        once: false,
        parameters: JSON.parse(rule.parametersText || '{}') as object,
        priority: Number(rule.priority),
        rule_id: rule.id,
        sizing: { mode: 'ALL_REMAINING' },
        strategy: rule.ruleType,
      })),
    [rules]
  );

  const replayDraft = () => {
    if (!onReplayDraft) return;
    try {
      onReplayDraft({
        account_id: accountId,
        auto_exit_authorized: false,
        bucket: 'manual',
        config_version: editingPlan ? editingPlan.configVersion + 1 : 1,
        instrument_code: normalizedCode,
        metadata: {
          draft_protected_volume: Number(protectedVolume),
          remark,
        },
        plan_id: createClientId('exit-plan-draft'),
        rules: serializedRules(),
        source_id: editingPlan?.planId || 'DRAFT',
        source_type: 'MANUAL_EXIT_PLAN',
      });
    } catch (error) {
      toast({
        title: '草稿不能回放',
        description: error instanceof Error ? error.message : String(error),
        variant: 'destructive',
      });
    }
  };

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
    setExecutionMode(editingPlan.environment === 'LIVE' ? 'live' : 'paper');
    setRequestLiveAuthorization(editingPlan.autoExitAuthorized);
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
              parametersText: '{"target_price":0}',
              priority: 500,
              ruleType: 'TARGET_PRICE',
            },
          ]
    );
    setOpen(true);
  }, [editingPlan]);

  React.useEffect(() => {
    if (editingPlan) return;
    setInstrumentCode(initialInstrumentCode || '');
    setSelectedOrderIds([]);
  }, [editingPlan, initialInstrumentCode]);

  const close = () => {
    createRequestRef.current = null;
    updateRequestRef.current = null;
    setAuthorizationChallenge(null);
    setAuthorizationError(null);
    setOpen(false);
    if (editingPlan) onFinishedEditing();
  };

  const recheckCapacity = async () => {
    const operation = await reconcileCapacity({
      accountId,
      instrumentCode: normalizedCode,
    });
    const result = operation.data?.reconcileExitPlanCapacity;
    if (operation.error || !result?.ready) {
      toast({
        description:
          operation.error?.message ||
          result?.capacityError ||
          '最新持仓仍不足以覆盖计划认领数量',
        title: '持仓对账未通过',
        variant: 'destructive',
      });
      return;
    }
    refetchCapacity({ requestPolicy: 'network-only' });
    toast({
      description: `持仓 ${result.totalVolume} 股 · 计划认领 ${result.protectedVolume} 股`,
      title: '持仓对账已恢复',
    });
    onSaved();
  };

  const requestAuthorizationPreview = async ({
    configVersion,
    planId,
  }: {
    configVersion: number;
    planId: string;
  }) => {
    const idempotencyKey = createClientId('exit-plan-authorization');
    const operation = await previewAuthorization({
      input: {
        accountId,
        expectedConfigVersion: configVersion,
        idempotencyKey,
        planId,
      },
    });
    const response = operation.data?.previewExitPlanAuthorization;
    if (operation.error || !response?.success || !response.preview) {
      throw new Error(
        operation.error?.message ||
          response?.message ||
          '未收到自动实盘卖出授权预览'
      );
    }
    const preview = response.preview;
    setAuthorizationError(null);
    setAuthorizationChallenge({
      accountId: preview.accountId,
      authorizationExpiresAt: preview.authorizationExpiresAt,
      authorizationFingerprint: preview.authorizationFingerprint,
      challengeExpiresAt: preview.challengeExpiresAt,
      challengeId: preview.challengeId,
      configVersion: preview.configVersion,
      confirmationToken: preview.confirmationToken,
      costBasis: preview.costBasis,
      executionPolicy: preview.executionPolicy,
      exitedVolume: preview.exitedVolume,
      idempotencyKey,
      instrumentCode: preview.instrumentCode,
      otherProtections: preview.otherProtections,
      planId: preview.planId,
      position: preview.position,
      protectedVolume: preview.protectedVolume,
      readiness: preview.readiness,
      remainingVolume: preview.remainingVolume,
      rules: preview.rules,
      t1Policy: preview.t1Policy,
      warnings: preview.warnings,
    });
  };

  const submit = async () => {
    let savedPlan: { configVersion: number; planId: string } | undefined;
    try {
      const rulesPayload = serializedRules();
      if (editingPlan) {
        const updateInput = {
          accountId,
          autoExitAuthorized: false,
          configVersion: editingPlan.configVersion,
          executionMode,
          planId: editingPlan.planId,
          protectedVolume: Number(protectedVolume),
          remark,
          rules: rulesPayload,
        };
        const fingerprint = JSON.stringify(updateInput);
        if (updateRequestRef.current?.fingerprint !== fingerprint) {
          updateRequestRef.current = {
            fingerprint,
            idempotencyKey: createClientId('exit-plan-update'),
          };
        }
        const result = await updatePlan({
          input: {
            ...updateInput,
            idempotencyKey: updateRequestRef.current.idempotencyKey,
          },
        });
        if (result.error) throw result.error;
        savedPlan = result.data?.updateManualExitPlan;
      } else {
        const createInput = {
          accountId,
          autoExitAuthorized: false,
          bucket: 'manual',
          costBasis: {
            mode: costBasisMode,
            orderIds:
              costBasisMode === 'BROKER_BUY_ORDERS'
                ? selectedOrderIds
                : undefined,
            unitCostCny:
              costBasisMode === 'MANUAL_UNIT_COST'
                ? Number(manualUnitCost)
                : undefined,
          },
          enabled: true,
          executionMode,
          instrumentCode: normalizedCode,
          protectedVolume: Number(protectedVolume),
          remark,
          rules: rulesPayload,
        };
        const fingerprint = JSON.stringify(createInput);
        if (createRequestRef.current?.fingerprint !== fingerprint) {
          createRequestRef.current = {
            fingerprint,
            idempotencyKey: createClientId('exit-plan-create'),
          };
        }
        const result = await createPlan({
          input: {
            ...createInput,
            idempotencyKey: createRequestRef.current.idempotencyKey,
          },
        });
        if (result.error) throw result.error;
        savedPlan = result.data?.createManualExitPlan;
      }
      if (!savedPlan) throw new Error('服务端未返回已保存的计划');
      if (editingPlan) updateRequestRef.current = null;
      else createRequestRef.current = null;
      toast({
        description: `${normalizedCode} · ${protectedVolume} 股`,
        title: editingPlan ? '人工计划已更新' : '人工计划已创建',
      });
      onSaved();
    } catch (error) {
      toast({
        description: error instanceof Error ? error.message : String(error),
        title: editingPlan ? '计划更新失败' : '计划创建失败',
        variant: 'destructive',
      });
      return;
    }

    if (executionMode === 'live' && requestLiveAuthorization && savedPlan) {
      try {
        await requestAuthorizationPreview(savedPlan);
        return;
      } catch (error) {
        toast({
          description: error instanceof Error ? error.message : String(error),
          title: '计划已保存，但授权预览失败',
          variant: 'destructive',
        });
      }
    }
    close();
  };

  const cancelLiveAuthorization = () => {
    toast({
      description: '计划仍会监控；触发实盘卖出时需要你逐次确认。',
      title: '实盘计划已保存，自动卖出未授权',
    });
    close();
  };

  const confirmLiveAuthorization = async () => {
    if (!authorizationChallenge) return;
    setAuthorizationError(null);
    try {
      const operation = await confirmAuthorization({
        input: {
          accountId: authorizationChallenge.accountId,
          challengeId: authorizationChallenge.challengeId,
          confirmationToken: authorizationChallenge.confirmationToken,
          expectedConfigVersion: authorizationChallenge.configVersion,
          idempotencyKey: authorizationChallenge.idempotencyKey,
          planId: authorizationChallenge.planId,
        },
      });
      const response = operation.data?.confirmExitPlanAuthorization;
      if (operation.error || !response?.success || !response.authorized) {
        throw new Error(
          operation.error?.message ||
            response?.message ||
            '自动实盘卖出授权未生效'
        );
      }
      toast({
        description: `仅绑定当前计划版本，有效至 ${formatDateTime(
          response.authorizationExpiresAt
        )}；本次确认没有创建委托。`,
        title: '自动实盘卖出已授权',
      });
      close();
      onSaved();
    } catch (error) {
      setAuthorizationError(
        error instanceof Error ? error.message : String(error)
      );
    }
  };

  const authorizationChallengeExpired = authorizationChallenge
    ? new Date(authorizationChallenge.challengeExpiresAt).getTime() <=
      Date.now()
    : false;

  if (!open) {
    return (
      <Button onClick={() => setOpen(true)} type="button">
        <Plus />
        手动添加计划
      </Button>
    );
  }

  return (
    <section className="w-full basis-full rounded-md border border-blue-400/20 bg-blue-500/[0.06] p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-ui-body font-black text-slate-100">
            {editingPlan ? '编辑人工计划' : '人工计划编辑器'}
          </h3>
          <p className="mt-1 text-ui-caption font-bold text-slate-500">
            先选择“什么情况下卖”，再填写触发参数；添加多个条件时，任一条件满足即可执行。
          </p>
        </div>
        <Button onClick={close} size="sm" variant="ghost">
          收起
        </Button>
      </div>
      <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <label className="grid content-start gap-1 text-ui-label font-bold text-slate-400">
          股票
          <Input
            className="h-control-default rounded-md border border-white/10 bg-[#080d18] px-3 font-mono text-slate-100 outline-none focus:border-blue-400/50"
            onChange={event => {
              setInstrumentCode(event.target.value);
              setSelectedOrderIds([]);
            }}
            placeholder="300917.SZ"
            readOnly={Boolean(editingPlan)}
            value={instrumentCode}
          />
        </label>
        <div className="grid content-start gap-1">
          <label
            className="text-ui-label font-bold text-slate-400"
            htmlFor="manual-plan-sell-volume"
          >
            计划卖出数量
          </label>
          <Input
            aria-describedby="manual-plan-sell-volume-help"
            className="h-control-default rounded-md border border-white/10 bg-[#080d18] px-3 font-mono text-slate-100 outline-none focus:border-blue-400/50"
            id="manual-plan-sell-volume"
            min={1}
            onChange={event => setProtectedVolume(event.target.value)}
            type="number"
            value={protectedVolume}
          />
          <p
            className="text-ui-caption font-medium leading-4 text-slate-500"
            id="manual-plan-sell-volume-help"
          >
            触发条件满足后，最多卖出该数量；创建计划不会立即下单。
          </p>
        </div>
        <label className="grid content-start gap-1 text-ui-label font-bold text-slate-400">
          模式
          <NativeSelect
            className="h-control-default rounded-md border border-white/10 bg-[#080d18] px-3 text-slate-100 outline-none focus:border-blue-400/50"
            onChange={event => {
              const mode = event.target.value as 'paper' | 'live';
              setExecutionMode(mode);
              if (mode === 'paper') setRequestLiveAuthorization(false);
            }}
            value={executionMode}
          >
            <option value="paper">模拟</option>
            <option value="live">实盘</option>
          </NativeSelect>
        </label>
        {executionMode === 'live' ? (
          <div className="grid content-start gap-1">
            <span className="text-ui-label font-bold text-slate-400">授权</span>
            <label className="flex h-9 items-center gap-2 text-ui-label font-bold text-slate-300">
              <input
                aria-describedby="manual-plan-live-authorization-help"
                checked={requestLiveAuthorization}
                onChange={event =>
                  setRequestLiveAuthorization(event.target.checked)
                }
                type="checkbox"
              />
              保存后预览并授权自动实盘卖出
            </label>
            <p
              className="text-ui-caption font-medium leading-4 text-amber-200/70"
              id="manual-plan-live-authorization-help"
            >
              未授权时，触发 SELL 仍需逐次人工确认。
            </p>
          </div>
        ) : (
          <div className="grid content-start gap-1">
            <span className="text-ui-label font-bold text-slate-400">授权</span>
            <p className="flex h-9 items-center text-ui-caption font-bold text-slate-500">
              模拟模式不会提交实盘委托
            </p>
          </div>
        )}
      </div>
      <label className="mt-3 grid gap-1 text-ui-label font-bold text-slate-400">
        备注
        <Input
          className="h-control-default rounded-md border border-white/10 bg-[#080d18] px-3 text-slate-100 outline-none focus:border-blue-400/50"
          onChange={event => setRemark(event.target.value)}
          placeholder="这项计划的卖出目的或备注"
          value={remark}
        />
      </label>
      <ExitPlanCapacityBanner
        busy={reconcileResult.fetching}
        capacity={capacity.data?.exitPlanHoldingCapacity}
        onReconcile={recheckCapacity}
      />
      <ExitPlanCostBasisEditor
        candidates={candidateItems}
        candidatesFetching={costBasisCandidates.fetching}
        editingCostBasis={editingPlan?.costBasis}
        historyWarning={
          costBasisCandidates.data?.exitPlanCostBasisCandidates?.historyWarning
        }
        manualUnitCost={manualUnitCost}
        mode={costBasisMode}
        onManualUnitCostChange={setManualUnitCost}
        onModeChange={setCostBasisMode}
        onSelectedOrderIdsChange={setSelectedOrderIds}
        requestedVolume={requestedVolume}
        selectedOrderIds={selectedOrderIds}
      />
      <div className="mt-3 grid gap-3">
        {rules.map((rule, index) => (
          <ManualExitRuleEditor
            capabilities={ruleTypes}
            canDelete={rules.length > 1}
            index={index}
            key={rule.id}
            onChange={nextRule =>
              setRules(current =>
                current.map(item => (item.id === rule.id ? nextRule : item))
              )
            }
            onDelete={() =>
              setRules(current => current.filter(item => item.id !== rule.id))
            }
            rule={rule}
          />
        ))}
      </div>
      <div className="mt-3 flex flex-wrap justify-between gap-2">
        <Button
          onClick={() =>
            setRules(current => [
              ...current,
              {
                id: createClientId('exit-rule'),
                parametersText: '{"target_price":0}',
                priority: 500,
                ruleType: 'TARGET_PRICE',
              },
            ])
          }
          type="button"
          variant="outline"
        >
          <Plus />
          添加另一个条件
        </Button>
        <div className="flex flex-wrap items-center gap-2">
          {onReplayDraft ? (
            <Button
              disabled={
                !normalizedCode ||
                Number(protectedVolume) <= 0 ||
                rules.length === 0
              }
              onClick={replayDraft}
              type="button"
              variant="outline"
            >
              <FlaskConical />
              回放当前草稿
            </Button>
          ) : null}
          <Button
            disabled={
              createResult.fetching ||
              updateResult.fetching ||
              authorizationPreviewResult.fetching ||
              authorizationConfirmResult.fetching ||
              !normalizedCode ||
              Number(protectedVolume) <= 0 ||
              costBasisInvalid ||
              capacity.data?.exitPlanHoldingCapacity?.capacityStatus ===
                'RECONCILE_REQUIRED' ||
              rules.length === 0
            }
            onClick={submit}
            type="button"
          >
            {(createResult.fetching ||
              updateResult.fetching ||
              authorizationPreviewResult.fetching) && (
              <Loader2 className="animate-spin" />
            )}
            {executionMode === 'live' && requestLiveAuthorization
              ? '保存并预览授权'
              : editingPlan
                ? '保存计划修改'
                : '创建卖出计划'}
          </Button>
        </div>
      </div>
      <AlertDialog
        open={Boolean(authorizationChallenge)}
        onOpenChange={nextOpen => {
          if (
            !nextOpen &&
            authorizationChallenge &&
            !authorizationConfirmResult.fetching
          ) {
            cancelLiveAuthorization();
          }
        }}
      >
        <AlertDialogContent className="max-h-[90vh] overflow-y-auto border-amber-400/25 bg-[#0b1120] text-slate-100 sm:max-w-2xl">
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2 text-ui-title">
              <ShieldAlert className="h-5 w-5 text-amber-300" />
              确认自动实盘卖出授权
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3 text-left text-ui-body leading-6 text-slate-400">
                <p className="rounded-md border border-amber-400/20 bg-amber-400/[0.08] p-3 text-amber-50">
                  授权后，当任一卖出规则触发，系统可不再逐次询问，直接进入实时风控并可能提交
                  SELL。本次确认只授权当前计划版本，不会立即创建委托。
                </p>
                {authorizationChallenge ? (
                  <>
                    <dl className="grid grid-cols-2 gap-2 text-ui-label sm:grid-cols-3">
                      {[
                        ['股票', authorizationChallenge.instrumentCode],
                        [
                          '计划版本',
                          `v${authorizationChallenge.configVersion}`,
                        ],
                        [
                          '计划卖出',
                          `${authorizationChallenge.protectedVolume.toLocaleString()} 股`,
                        ],
                        [
                          '待卖数量',
                          `${authorizationChallenge.remainingVolume.toLocaleString()} 股`,
                        ],
                        [
                          '已卖数量',
                          `${authorizationChallenge.exitedVolume.toLocaleString()} 股`,
                        ],
                        [
                          '当前可卖',
                          `${authorizationChallenge.position.availableVolume.toLocaleString()} 股`,
                        ],
                        [
                          'T+1 暂不可卖',
                          `${authorizationChallenge.position.t1UnavailableVolume.toLocaleString()} 股`,
                        ],
                      ].map(([label, value]) => (
                        <div
                          className="rounded-md border border-white/10 bg-white/[0.025] p-2.5"
                          key={label}
                        >
                          <dt className="text-slate-500">{label}</dt>
                          <dd className="mt-1 font-mono font-black text-slate-200">
                            {value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                    <div className="rounded-md border border-white/10 p-3 text-ui-label leading-5 text-slate-400">
                      <p>T+1 策略：{authorizationChallenge.t1Policy}</p>
                      <p>
                        持仓快照：总持仓{' '}
                        {authorizationChallenge.position.totalVolume.toLocaleString()}
                        股 · 冻结{' '}
                        {authorizationChallenge.position.frozenVolume.toLocaleString()}
                        股 · 昨仓{' '}
                        {authorizationChallenge.position.yesterdayVolume.toLocaleString()}
                        股
                      </p>
                      <p>
                        授权有效至：
                        {formatDateTime(
                          authorizationChallenge.authorizationExpiresAt
                        )}
                      </p>
                      <p>
                        本次确认有效至：
                        {formatDateTime(
                          authorizationChallenge.challengeExpiresAt
                        )}
                      </p>
                      <p>
                        持仓快照时间：
                        {formatDateTime(
                          authorizationChallenge.position.positionUpdatedAt
                        )}
                      </p>
                      <p className="break-all font-mono text-ui-caption text-slate-600">
                        授权指纹：
                        {authorizationChallenge.authorizationFingerprint}
                      </p>
                    </div>
                    <details className="rounded-md border border-white/10 p-3 text-ui-label">
                      <summary className="cursor-pointer font-bold text-slate-300">
                        查看完整卖出规则与执行策略
                      </summary>
                      <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all rounded bg-black/20 p-2 font-mono text-ui-caption leading-4 text-slate-500">
                        {JSON.stringify(
                          {
                            costBasis: authorizationChallenge.costBasis,
                            executionPolicy:
                              authorizationChallenge.executionPolicy,
                            readiness: authorizationChallenge.readiness,
                            rules: authorizationChallenge.rules,
                          },
                          null,
                          2
                        )}
                      </pre>
                    </details>
                    {authorizationChallenge.otherProtections.length > 0 ? (
                      <p className="text-ui-label text-amber-200">
                        当前另有{' '}
                        {authorizationChallenge.otherProtections.length}{' '}
                        个退出计划占用该持仓；授权范围已按服务端快照固定。
                      </p>
                    ) : null}
                    <ul className="list-disc space-y-1 pl-5 text-ui-label text-slate-500">
                      {authorizationChallenge.warnings.map(warning => (
                        <li key={warning}>{warning}</li>
                      ))}
                    </ul>
                  </>
                ) : null}
                {authorizationChallengeExpired ? (
                  <p className="text-rose-300" role="alert">
                    本次授权确认已过期。请取消后重新保存并获取预览。
                  </p>
                ) : null}
                {authorizationError ? (
                  <p className="text-rose-300" role="alert">
                    {authorizationError}
                  </p>
                ) : null}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              disabled={authorizationConfirmResult.fetching}
              type="button"
            >
              仅保存，不授权
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={
                authorizationConfirmResult.fetching ||
                authorizationChallengeExpired
              }
              onClick={event => {
                event.preventDefault();
                void confirmLiveAuthorization();
              }}
              type="button"
            >
              {authorizationConfirmResult.fetching ? (
                <Loader2 className="animate-spin" />
              ) : (
                <CheckCircle2 />
              )}
              {authorizationConfirmResult.fetching
                ? '正在授权…'
                : '确认授权自动实盘卖出'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

export function ExitPlansPanel({
  accountId,
  holdings = [],
  instrumentCode,
  onNavigate,
  onReplayDraft = () => undefined,
  onReplayPlan = () => undefined,
}: {
  accountId: string;
  holdings?: readonly Position[];
  instrumentCode?: string;
  onNavigate: (path: string) => void;
  onReplayDraft?: (template: Record<string, unknown>) => void;
  onReplayPlan?: (planId: string) => void;
}) {
  const { toast } = useToast();
  const { confirm: confirmDialog } = useAppDialog();
  const [editingPlan, setEditingPlan] = React.useState<ExitPlan | null>(null);
  const plans = useExitPlans(accountId, instrumentCode);
  const [toggleResult, togglePlan] = useMutation(SetExitPlanEnabledMutation);
  const toggleRequestRef = React.useRef(
    new Map<string, { fingerprint: string; idempotencyKey: string }>()
  );
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
    title: string,
    successDescription?: string
  ) => {
    const result = await action();
    toast({
      description: result.error?.message || successDescription,
      title: result.error ? `${title}失败` : title,
      variant: result.error ? 'destructive' : 'default',
    });
    plans.refetch({ requestPolicy: 'network-only' });
  };
  const cancelExitPlan = async (plan: ExitPlan) => {
    const rebuilding = plan.recoveryAction === 'CANCEL_AND_REBUILD';
    if (rebuilding) {
      const accepted = await confirmDialog({
        title: '取消旧计划并准备重建？',
        description:
          '券商事实修复已经完成，但旧计划保留隔离审计，不能继续恢复。取消后不会立即卖出；请再点击“手动添加计划”，按最新持仓重新创建并单独确认实盘授权。',
        confirmText: '取消旧计划',
        cancelText: '暂不处理',
        variant: 'warning',
      });
      if (!accepted) return;
    }
    await run(
      () =>
        cancelPlan({
          configVersion: plan.configVersion,
          planId: plan.planId,
        }),
      rebuilding ? '旧卖出计划已取消' : '卖出计划已取消',
      rebuilding
        ? '请点击“手动添加计划”，按最新持仓重新创建；自动实盘卖出仍需重新授权。'
        : undefined
    );
  };
  const setPlanEnabled = async (plan: ExitPlan) => {
    const enabled = !plan.enabled;
    const fingerprint = JSON.stringify({
      accountId,
      configVersion: plan.configVersion,
      enabled,
      planId: plan.planId,
    });
    const existingRequest = toggleRequestRef.current.get(plan.planId);
    const request =
      existingRequest?.fingerprint === fingerprint
        ? existingRequest
        : {
            fingerprint,
            idempotencyKey: createClientId('exit-plan-set-enabled'),
          };
    if (existingRequest !== request) {
      toggleRequestRef.current.set(plan.planId, request);
    }
    const result = await togglePlan({
      configVersion: plan.configVersion,
      enabled,
      idempotencyKey: request.idempotencyKey,
      planId: plan.planId,
    });
    if (!result.error) toggleRequestRef.current.delete(plan.planId);
    return result;
  };
  const visiblePlans = (plans.data?.exitPlans ?? []).filter(plan =>
    activeStatuses.has(plan.status)
  );
  const instrumentNames = React.useMemo(
    () =>
      new Map(
        holdings
          .filter(holding => Boolean(holding.instrumentName))
          .map(holding => [holding.stockCode, holding.instrumentName] as const)
      ),
    [holdings]
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
    const accepted = await confirmDialog({
      title: '确认卖出意图',
      description: (
        <div className="space-y-3">
          <dl className="grid grid-cols-[72px_1fr] gap-x-3 gap-y-1 rounded-panel border border-slate-200 bg-slate-50 p-3 text-ui-label dark:border-white/10 dark:bg-slate-900">
            <dt className="text-slate-500 dark:text-slate-400">标的 / 方向</dt>
            <dd className="font-mono text-slate-900 dark:text-slate-100">
              {preview.instrumentCode} {preview.side}
            </dd>
            <dt className="text-slate-500 dark:text-slate-400">数量</dt>
            <dd className="font-mono text-slate-900 dark:text-slate-100">
              {preview.targetVolume ?? '--'} 股
            </dd>
            <dt className="text-slate-500 dark:text-slate-400">参考价</dt>
            <dd className="font-mono text-slate-900 dark:text-slate-100">
              {preview.referencePrice ?? '--'}
            </dd>
          </dl>
          {(preview.warnings ?? []).length > 0 && (
            <ul className="space-y-1 text-amber-700 dark:text-amber-300">
              {(preview.warnings ?? []).map(warning => (
                <li key={warning}>• {warning}</li>
              ))}
            </ul>
          )}
        </div>
      ),
      confirmText: '确认卖出',
      cancelText: '返回检查',
      variant: 'warning',
    });
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
          <h2 className="text-ui-title font-black text-slate-100">卖出计划</h2>
          <p className="mt-1 text-ui-label font-bold text-slate-500">
            统一监控打板、T 批次、止盈/止损、人工计划和人工清仓。
          </p>
        </div>
        <ManualPlanEditor
          accountId={accountId}
          editingPlan={editingPlan}
          initialInstrumentCode={instrumentCode}
          onFinishedEditing={() => setEditingPlan(null)}
          onReplayDraft={onReplayDraft}
          onSaved={() => plans.refetch({ requestPolicy: 'network-only' })}
        />
      </div>
      {plans.error && (
        <div className="mt-3 rounded border border-rose-400/20 bg-rose-500/10 p-3 text-ui-label font-bold text-rose-100">
          {plans.error.message}
        </div>
      )}
      <div className="mt-3 grid gap-2">
        {plans.fetching && visiblePlans.length === 0 ? (
          <div className="flex items-center justify-center gap-2 py-16 text-ui-body font-bold text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载卖出计划
          </div>
        ) : visiblePlans.length === 0 ? (
          <div className="rounded-md border border-dashed border-white/10 py-16 text-center text-ui-body font-bold text-slate-500">
            暂无进行中的卖出计划
          </div>
        ) : (
          visiblePlans.map(plan => (
            <PlanCard
              busy={busy}
              instrumentName={instrumentNames.get(plan.instrumentCode)}
              key={plan.planId}
              onCancel={item => void cancelExitPlan(item)}
              onConfirmIntent={item => void approvePendingIntent(item)}
              onEvaluate={item =>
                void run(
                  () => evaluatePlan({ planId: item.planId }),
                  '已请求立即检查'
                )
              }
              onEdit={item => setEditingPlan(item)}
              onNavigate={onNavigate}
              onReplay={item => onReplayPlan(item.planId)}
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
                  () => setPlanEnabled(item),
                  item.enabled ? '卖出计划已暂停' : '卖出计划已恢复'
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
          ? 'border-primary/40 bg-primary/10'
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
        <span className="block text-ui-label font-black text-slate-200">
          {label}
        </span>
        <span className="mt-1 block text-ui-caption font-bold leading-5 text-slate-500">
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
        <h2 className="text-ui-title font-black text-slate-100">持仓清仓</h2>
        <p className="mt-1 text-ui-label font-bold text-slate-500">
          清仓是明确动作；确认后按股票创建独立卖出计划并由统一状态机执行。
        </p>
      </div>
      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <section className="rounded-md border border-white/8 bg-[#0b1120]/70">
          <div className="flex items-center justify-between border-b border-white/5 p-3">
            <span className="text-ui-label font-black text-slate-200">
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
                      <span className="block truncate text-ui-body font-black text-slate-100">
                        {position.instrumentName || code}
                      </span>
                      <span className="font-mono text-ui-caption text-slate-600">
                        {code}
                      </span>
                    </span>
                  </span>
                  <span className="text-right font-mono text-ui-label text-slate-300">
                    <span className="block">持仓 {position.volume}</span>
                    <span className="block text-ui-caption text-slate-600">
                      当前可卖 {position.canUseVolume}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </section>
        <section className="rounded-md border border-white/8 bg-[#0b1120]/70 p-3">
          <h3 className="text-ui-label font-black text-slate-200">
            本次清仓规则
          </h3>
          <p className="mt-1 text-ui-caption font-bold text-amber-200">
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
          <label className="mt-4 grid gap-1 text-ui-label font-bold text-slate-400">
            执行模式（必选）
            <NativeSelect
              className="h-control-default rounded border border-white/10 bg-[#080d18] px-2 text-slate-200"
              onChange={event =>
                setExecutionMode(event.target.value as 'paper' | 'live' | '')
              }
              value={executionMode}
            >
              <option value="">请选择</option>
              <option value="paper">模拟</option>
              <option value="live">实盘（卖出意图需再次确认）</option>
            </NativeSelect>
          </label>
          {conflicts.length > 0 && (
            <div className="mt-4 rounded border border-amber-400/20 bg-amber-500/10 p-3">
              <div className="flex items-center gap-2 text-ui-label font-black text-amber-100">
                <ShieldAlert className="h-4 w-4" />
                冲突计划 {conflicts.length} 条
              </div>
              <div className="mt-2 grid gap-1 text-ui-caption font-bold text-amber-200/80">
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
                  <div className="grid gap-2 text-ui-body">
                    <p>本次将为 {selected.length} 只股票分别创建卖出计划。</p>
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
  const { confirm } = useAppDialog();
  const { toast } = useToast();
  const [, deleteHistory] = useMutation(DeleteExitPlanHistoryMutation);
  const [deletedPlanIds, setDeletedPlanIds] = React.useState<Set<string>>(
    () => new Set()
  );
  const [deleting, setDeleting] = React.useState(false);
  const deletionPendingRef = React.useRef(false);
  const historyScrollRef = React.useRef<HTMLElement>(null);
  const menuTriggerRef = React.useRef<HTMLButtonElement | null>(null);
  const { closeMenu, menu, openAtPointer, setMenu } = useStudioMenu<string>();
  const allPlans = (plans.data?.exitPlans ?? []).filter(
    plan =>
      !terminalStatuses.has(plan.status) || !deletedPlanIds.has(plan.planId)
  );
  const [selectedPlanId, setSelectedPlanId] = React.useState('');
  const activePlanId = allPlans.some(plan => plan.planId === selectedPlanId)
    ? selectedPlanId
    : allPlans[0]?.planId || '';
  const menuPlan = allPlans.find(plan => plan.planId === menu?.payload);
  const canDelete = (plan: ExitPlan) =>
    terminalStatuses.has(plan.status) && !plan.pendingClientOrderId;

  React.useEffect(() => closeMenu(), [accountId, closeMenu]);

  async function deletePlan(plan: ExitPlan) {
    if (!canDelete(plan) || deletionPendingRef.current) return;
    deletionPendingRef.current = true;
    setDeleting(true);
    try {
      const confirmed = await confirm({
        title: `删除 ${plan.instrumentCode} 的卖出记录？`,
        description: `将从历史列表移除这条${statusLabels[plan.status]}记录（${formatDateTime(plan.updatedAt)}）。底层计划、委托、成交及审计数据仍保留，不会撤单或发起交易。`,
        confirmText: '删除记录',
        cancelText: '取消',
      });
      if (!confirmed) return;
      const result = await deleteHistory({ planId: plan.planId });
      const response = result.data?.deleteExitPlanHistory;
      if (result.error || !response?.success) {
        throw new Error(
          result.error?.message || response?.message || '请稍后重试'
        );
      }
      setDeletedPlanIds(previous => new Set([...previous, plan.planId]));
      setSelectedPlanId(previous => (previous === plan.planId ? '' : previous));
      plans.refetch({ requestPolicy: 'network-only' });
      toast({ title: '卖出记录已删除', description: response.message });
    } catch (error) {
      toast({
        title: '删除卖出记录失败',
        description: error instanceof Error ? error.message : '请稍后重试',
        variant: 'destructive',
      });
    } finally {
      deletionPendingRef.current = false;
      setDeleting(false);
    }
  }

  const [events] = useQuery({
    query: ExitPlanEventsQuery,
    variables: { limit: 200, planId: activePlanId },
    pause: !activePlanId,
    requestPolicy: 'cache-and-network',
  });

  return (
    <div className="grid min-h-0 flex-1 gap-3 overflow-hidden p-3 xl:grid-cols-[360px_minmax(0,1fr)]">
      <section
        ref={historyScrollRef}
        className="min-h-0 overflow-y-auto rounded-md border border-white/8 bg-[#0b1120]/70 custom-scrollbar"
      >
        <div className="sticky top-0 flex items-center gap-2 border-b border-white/5 bg-[#0b1120] p-3 text-ui-label font-black text-slate-200">
          <History className="h-4 w-4 text-market-down" />
          卖出计划
        </div>
        <div className="divide-y divide-white/5">
          {allPlans.map(plan => (
            <button
              className={cn(
                'w-full px-3 py-3 text-left hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-blue-400/70',
                activePlanId === plan.planId && 'bg-blue-500/10'
              )}
              key={plan.planId}
              aria-pressed={activePlanId === plan.planId}
              onClick={() => setSelectedPlanId(plan.planId)}
              onContextMenu={event => {
                menuTriggerRef.current = event.currentTarget;
                event.currentTarget.focus();
                setSelectedPlanId(plan.planId);
                openAtPointer(event, plan.planId);
              }}
              onKeyDown={event => {
                if (
                  event.key !== 'ContextMenu' &&
                  !(event.shiftKey && event.key === 'F10')
                )
                  return;
                event.preventDefault();
                menuTriggerRef.current = event.currentTarget;
                setSelectedPlanId(plan.planId);
                const rect = event.currentTarget.getBoundingClientRect();
                setMenu({
                  anchor: { kind: 'point', x: rect.left, y: rect.bottom },
                  payload: plan.planId,
                });
              }}
              type="button"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-ui-label font-black text-slate-100">
                  {plan.instrumentCode}
                </span>
                <span
                  className={cn(
                    'text-ui-caption font-black',
                    statusTone(plan.status)
                  )}
                >
                  {statusLabels[plan.status] || plan.status}
                </span>
              </div>
              <div className="mt-1 text-ui-caption font-bold text-slate-500">
                {sourceLabels[plan.sourceType] || plan.sourceType} ·{' '}
                {formatDateTime(plan.updatedAt)}
              </div>
            </button>
          ))}
          {allPlans.length === 0 && (
            <div className="py-ui-empty text-center text-ui-label text-slate-500">
              {plans.fetching ? '加载卖出记录…' : '暂无卖出记录'}
            </div>
          )}
        </div>
      </section>
      <section className="min-h-0 overflow-y-auto rounded-md border border-white/8 bg-[#0b1120]/70 p-3 custom-scrollbar">
        <h2 className="text-ui-body font-black text-slate-100">
          统一卖出时间线
        </h2>
        <p className="mt-1 text-ui-caption font-bold text-slate-500">
          规则触发、计划变更、委托状态和真实成交均以持久化事件展示。
        </p>
        <div className="mt-4 grid gap-2">
          {!activePlanId ? (
            <div className="py-ui-empty text-center text-ui-label text-slate-500">
              选择卖出记录查看时间线
            </div>
          ) : events.fetching && !events.data ? (
            <div className="flex justify-center py-ui-empty text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : (events.data?.exitPlanEvents ?? []).length === 0 ? (
            <div className="rounded border border-dashed border-white/10 py-ui-empty text-center text-ui-label font-bold text-slate-500">
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
                    <span className="text-ui-label font-black text-slate-200">
                      {event.eventType}
                    </span>
                    <span className="font-mono text-ui-caption text-slate-600">
                      {formatDateTime(event.createdAt)}
                    </span>
                  </div>
                  <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all font-mono text-ui-caption leading-5 text-slate-500">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                </div>
              </div>
            ))
          )}
        </div>
      </section>
      <StudioMenu
        ariaLabel="卖出记录菜单"
        closeOnScrollRef={historyScrollRef}
        returnFocusRef={menuTriggerRef}
        menu={menu}
        onClose={closeMenu}
        width={176}
        items={[
          {
            id: 'delete',
            icon: <Trash2 size={14} />,
            label: '删除记录',
            disabled: !menuPlan || !canDelete(menuPlan) || deleting,
            shortcut: menuPlan && !canDelete(menuPlan) ? '不可删除' : undefined,
            onSelect: () => {
              if (menuPlan) void deletePlan(menuPlan);
            },
          },
        ]}
      />
    </div>
  );
}
