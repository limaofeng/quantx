import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  MousePointerClick,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import * as React from 'react';

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
import { cn } from '@/utils/cn';

import { formatEntryCurrency, formatEntryDateTime } from '../model/draft';
import type {
  EntryPlanController,
  EntryPlanStatus,
  EntryPlanView,
} from '../model/types';

const statusPresentation: Record<
  EntryPlanStatus,
  { label: string; className: string; icon: React.ElementType }
> = {
  ACCUMULATING: {
    label: '继续加仓',
    className: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-100',
    icon: Play,
  },
  ARMED: {
    label: '监控中',
    className: 'border-cyan-400/25 bg-cyan-400/10 text-cyan-100',
    icon: Play,
  },
  AWAITING_APPROVAL: {
    label: '待确认',
    className: 'border-amber-400/25 bg-amber-400/10 text-amber-100',
    icon: Clock3,
  },
  CANCELLED: {
    label: '已取消',
    className: 'border-slate-400/20 bg-slate-400/10 text-slate-300',
    icon: Pause,
  },
  COMPLETED: {
    label: '已完成',
    className: 'border-emerald-400/25 bg-emerald-400/10 text-emerald-100',
    icon: CheckCircle2,
  },
  DRAINING: {
    label: '等待回报收敛',
    className: 'border-blue-400/25 bg-blue-400/10 text-blue-100',
    icon: RefreshCw,
  },
  ENTRY_PENDING: {
    label: '委托处理中',
    className: 'border-blue-400/25 bg-blue-400/10 text-blue-100',
    icon: RefreshCw,
  },
  ERROR: {
    label: '需要处理',
    className: 'border-rose-400/25 bg-rose-400/10 text-rose-100',
    icon: AlertTriangle,
  },
  EXPIRED: {
    label: '已到期',
    className: 'border-amber-400/20 bg-amber-400/10 text-amber-200',
    icon: Clock3,
  },
  PAUSED: {
    label: '已暂停',
    className: 'border-slate-400/20 bg-slate-400/10 text-slate-300',
    icon: Pause,
  },
};

const strategyLabels = {
  MANUAL_TRIGGER: '人工触发',
  PRICE_LADDER: '价格阶梯',
  TREND_PULLBACK_CONFIRMATION: '趋势回撤',
} as const;

export function EntryPlanCard({
  controller,
  onSelect,
  plan,
  selected,
}: {
  controller: EntryPlanController;
  onSelect: (planId: string) => void;
  plan: EntryPlanView;
  selected: boolean;
}) {
  const status = statusPresentation[plan.status];
  const StatusIcon = status.icon;
  const progress = Math.min(
    100,
    plan.maxTotalAmountCny > 0
      ? (plan.filledAmountCny / plan.maxTotalAmountCny) * 100
      : 0
  );
  const locked = plan.status === 'ENTRY_PENDING' || plan.status === 'DRAINING';
  const isManualRule = plan.strategy === 'MANUAL_TRIGGER';
  const evaluationDisabled = locked || (isManualRule && !plan.primaryRuleId);
  const EvaluationIcon = isManualRule ? MousePointerClick : RefreshCw;
  const [actionBusy, setActionBusy] = React.useState(false);
  const [actionError, setActionError] = React.useState<string | null>(null);

  async function runAction(action: () => Promise<void>) {
    setActionBusy(true);
    setActionError(null);
    try {
      await action();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '计划操作失败');
    } finally {
      setActionBusy(false);
    }
  }

  return (
    <article
      className={cn(
        'rounded-lg border p-3 transition-colors duration-200',
        selected
          ? 'border-emerald-400/40 bg-emerald-400/[0.06]'
          : 'border-white/10 bg-[#0b1120]/80'
      )}
      data-testid={`entry-plan-card-${plan.id}`}
    >
      <button
        aria-pressed={selected}
        className="min-h-11 w-full cursor-pointer rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400"
        onClick={() => onSelect(plan.id)}
        type="button"
      >
        <span className="flex items-start justify-between gap-2">
          <span className="min-w-0">
            <span className="block truncate text-sm font-black text-slate-100">
              {plan.instrumentName}
            </span>
            <span className="mt-0.5 block font-mono text-[10px] text-slate-500">
              {plan.instrumentCode} ·{' '}
              {plan.bucket === 'core' ? '核心仓' : '活跃仓'}
            </span>
          </span>
          <span
            className={cn(
              'inline-flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-[10px] font-black',
              status.className
            )}
          >
            <StatusIcon aria-hidden="true" className="h-3 w-3" />
            {status.label}
          </span>
        </span>
      </button>

      <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
        <div className="rounded bg-white/[0.035] p-2">
          <div className="text-slate-500">当前 / 目标</div>
          <div className="mt-1 font-mono font-bold text-slate-200">
            {plan.currentPositionPct.toFixed(1)}% /{' '}
            {plan.targetPositionPct?.toFixed(1) ?? '--'}%
          </div>
        </div>
        <div className="rounded bg-white/[0.035] p-2">
          <div className="text-slate-500">真实已买 / 预算</div>
          <div className="mt-1 truncate font-mono font-bold text-slate-200">
            {formatEntryCurrency(plan.filledAmountCny)} /{' '}
            {formatEntryCurrency(plan.maxTotalAmountCny)}
          </div>
        </div>
      </div>

      <div
        aria-label={`计划预算已使用 ${progress.toFixed(0)}%`}
        className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/10"
        role="progressbar"
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={Math.round(progress)}
      >
        <div
          className="h-full rounded-full bg-emerald-400 transition-[width] motion-reduce:transition-none"
          style={{ width: `${progress}%` }}
        />
      </div>

      <div className="mt-3 space-y-1 text-[11px] leading-4 text-slate-400">
        <p>策略：{strategyLabels[plan.strategy]}</p>
        <p>最近判断：{plan.lastDecision}</p>
        <p>下次检查：{formatEntryDateTime(plan.nextEvaluationAt)}</p>
        <p className="inline-flex items-center gap-1 text-slate-300">
          <ShieldCheck
            aria-hidden="true"
            className="h-3.5 w-3.5 text-emerald-300"
          />
          {plan.authorizationLabel} ·{' '}
          {plan.exitProtectionEnabled
            ? '成交后有卖出保护'
            : '成交后无自动卖出保护'}
        </p>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 border-t border-white/5 pt-3">
        {plan.status === 'PAUSED' ? (
          <Button
            disabled={actionBusy}
            size="sm"
            type="button"
            onClick={() => void runAction(() => controller.resumePlan(plan.id))}
          >
            <Play />
            恢复监控
          </Button>
        ) : (
          <Button
            disabled={actionBusy}
            size="sm"
            type="button"
            variant="outline"
            onClick={() =>
              void runAction(() => controller.pausePlan(plan.id, false))
            }
          >
            <Pause />
            暂停触发
          </Button>
        )}
        <Button
          disabled={actionBusy || evaluationDisabled}
          size="sm"
          type="button"
          variant="outline"
          title={
            locked
              ? '委托或回报尚未收敛，暂不能编辑或重复检查'
              : isManualRule && !plan.primaryRuleId
                ? '人工规则标识尚未同步，请刷新后重试'
                : undefined
          }
          onClick={() => {
            if (isManualRule && plan.primaryRuleId) {
              void runAction(() =>
                controller.triggerManualRule(plan.id, plan.primaryRuleId!)
              );
              return;
            }
            void runAction(() => controller.evaluatePlan(plan.id));
          }}
        >
          <EvaluationIcon />
          {isManualRule ? '触发本批检查' : '立即检查'}
        </Button>
        {plan.hasWorkingOrder ? (
          <Button
            disabled={actionBusy}
            size="sm"
            type="button"
            variant="destructive"
            onClick={() =>
              void runAction(() => controller.cancelPlan(plan.id, true))
            }
          >
            <Trash2 />
            取消计划并撤销买单
          </Button>
        ) : null}
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              disabled={actionBusy}
              size="sm"
              type="button"
              variant="destructive"
            >
              <Trash2 />
              取消计划
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>确认取消买入计划</AlertDialogTitle>
              <AlertDialogDescription>
                取消只会停止新的买入触发，不会卖出已经买入的股份，也不会取消已经激活的卖出保护。可能到达券商的委托仍需等待真实回报。
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>返回</AlertDialogCancel>
              <AlertDialogAction
                disabled={actionBusy}
                onClick={() =>
                  void runAction(() => controller.cancelPlan(plan.id, false))
                }
              >
                确认取消计划
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
      {locked ? (
        <p className="mt-2 text-[11px] text-blue-200">
          当前委托或回报尚未收敛，结构编辑已锁定。
        </p>
      ) : null}
      {actionError ? (
        <p className="mt-2 text-[11px] text-rose-300" role="alert">
          {actionError}
        </p>
      ) : null}
    </article>
  );
}
