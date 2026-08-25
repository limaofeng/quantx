import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  OctagonX,
  PauseCircle,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import {
  AccountExecutionSafetyQuery,
  ConfirmAccountExecutionControlMutation,
  PreviewAccountExecutionControlMutation,
  useTradingSafety,
} from '@/features/trading-safety';
import { AccountExecutionControlAction } from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import {
  getAccountExecutionGatePresentation,
  getBackupFreshness,
  getSnapshotFreshness,
  type AccountExecutionGateFreshness,
} from './accountExecutionGatePresentation';

const actionLabels: Record<AccountExecutionControlAction, string> = {
  [AccountExecutionControlAction.BeginControlledWindow]: '建立账户实盘窗口',
  [AccountExecutionControlAction.EnableRiskIncrease]: '启用新增风险',
  [AccountExecutionControlAction.PauseRiskIncrease]: '暂停新增风险',
  [AccountExecutionControlAction.KillSwitch]: '账户紧急停止',
  [AccountExecutionControlAction.ClearKillSwitch]: '清除紧急停止',
};

function useNow() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function FreshnessIndicator({
  freshness,
}: {
  freshness: AccountExecutionGateFreshness;
}) {
  const compactLabel = freshness.countdownLabel.replace('距过期 ', '剩 ');
  return (
    <div
      className="w-24 shrink-0"
      aria-label={`新鲜度：${freshness.countdownLabel}`}
    >
      <span
        className={cn(
          'block text-right font-mono text-[10px] font-medium leading-3 tabular-nums',
          freshness.tone === 'fresh'
            ? 'text-emerald-300'
            : freshness.tone === 'warning'
              ? 'text-warning'
              : 'text-rose-300'
        )}
      >
        {compactLabel}
      </span>
      <div
        role="progressbar"
        aria-label="剩余有效时间"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(freshness.progressPercent)}
        className="mt-1 h-1 overflow-hidden rounded-full bg-slate-800"
      >
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-300 motion-reduce:transition-none',
            freshness.tone === 'fresh'
              ? 'bg-emerald-400'
              : freshness.tone === 'warning'
                ? 'bg-warning'
                : 'bg-rose-400'
          )}
          style={{ width: `${freshness.progressPercent}%` }}
        />
      </div>
    </div>
  );
}

export function TradingSafetySettingsPanel() {
  const now = useNow();
  const { accountId, refreshSafety } = useTradingSafety();
  const [{ data, fetching }, refresh] = useQuery({
    query: AccountExecutionSafetyQuery,
    variables: { accountId },
    pause: !accountId,
    requestPolicy: 'network-only',
  });
  const [, previewControl] = useMutation(
    PreviewAccountExecutionControlMutation
  );
  const [, confirmControl] = useMutation(
    ConfirmAccountExecutionControlMutation
  );
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [showGateCodes, setShowGateCodes] = useState(false);
  const [pending, setPending] = useState<{
    action: AccountExecutionControlAction;
    challengeId: string;
    confirmationToken: string;
  } | null>(null);
  const safety = data?.accountExecutionSafety;
  const failedChecks = useMemo(
    () => safety?.checks.filter(check => !check.passed) ?? [],
    [safety?.checks]
  );
  const passedCheckCount = (safety?.checks.length ?? 0) - failedChecks.length;

  const reload = () => {
    refresh({ requestPolicy: 'network-only' });
    refreshSafety();
  };

  const preview = async (action: AccountExecutionControlAction) => {
    if (!safety) return;
    setSubmitting(true);
    setMessage('');
    const result = await previewControl({
      input: {
        accountId,
        action,
        stateVersion: safety.stateVersion,
        snapshotId:
          action === AccountExecutionControlAction.BeginControlledWindow
            ? safety.snapshotId || ''
            : '',
        reason:
          action === AccountExecutionControlAction.PauseRiskIncrease ||
          action === AccountExecutionControlAction.KillSwitch
            ? reason.trim()
            : '',
        idempotencyKey: `account-execution:${crypto.randomUUID()}`,
      },
    });
    const payload = result.data?.previewAccountExecutionControl;
    const issued = payload?.preview;
    if (!payload?.success || !issued?.confirmationToken) {
      setMessage(payload?.message || result.error?.message || '控制预览失败');
      setSubmitting(false);
      return;
    }
    setPending({
      action,
      challengeId: String(issued.challengeId),
      confirmationToken: issued.confirmationToken,
    });
    setMessage('预览已锁定 60 秒，请核对后确认。');
    setSubmitting(false);
  };

  const confirm = async () => {
    if (!pending) return;
    setSubmitting(true);
    const result = await confirmControl({
      input: {
        challengeId: pending.challengeId,
        confirmationToken: pending.confirmationToken,
      },
    });
    const payload = result.data?.confirmAccountExecutionControl;
    setMessage(payload?.message || result.error?.message || '账户执行控制失败');
    setPending(null);
    setReason('');
    setSubmitting(false);
    reload();
  };

  if (!accountId) {
    return <p className="text-sm text-amber-300">当前用户没有可用资金账户。</p>;
  }

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <header className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs font-medium uppercase text-primary">
            Account execution control
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-slate-100">
            账户交易安全
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
            这里只控制账户级实盘授权、对账窗口与紧急停止。做
            T、打板和普通策略各自的功能门禁不会写入这里。
          </p>
        </div>
        <button
          type="button"
          onClick={reload}
          disabled={fetching}
          className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground hover:bg-muted disabled:opacity-50"
        >
          <RefreshCw className={cn('h-4 w-4', fetching && 'animate-spin')} />
          刷新
        </button>
      </header>

      <section className="grid gap-3 md:grid-cols-4">
        {[
          ['授权状态', safety?.authorizationState || 'LOADING'],
          ['执行模式', safety?.executionMode || 'OBSERVE_ONLY'],
          ['对账状态', safety?.reconcileStatus || 'UNKNOWN'],
          ['状态版本', String(safety?.stateVersion ?? '—')],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-xl border border-border bg-card p-4"
          >
            <p className="text-xs text-slate-500">{label}</p>
            <p className="mt-2 font-mono text-sm font-semibold text-slate-100">
              {value}
            </p>
          </div>
        ))}
      </section>

      <section className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start gap-3">
          {safety?.canIncreaseRisk ? (
            <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-400" />
          ) : (
            <AlertTriangle className="mt-0.5 h-5 w-5 text-warning" />
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-medium text-slate-100">
              {safety?.summary || '账户安全状态加载中'}
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              账户 {accountId} · 快照 {safety?.snapshotId || '无'} · 实盘窗口
              {safety?.executionWindowActive ? '已建立' : '未建立'}
            </p>
          </div>
        </div>

        <div className="mt-5 flex flex-wrap gap-2">
          {!safety?.executionWindowActive && (
            <button
              type="button"
              disabled={submitting || !safety?.snapshotId}
              onClick={() =>
                preview(AccountExecutionControlAction.BeginControlledWindow)
              }
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-40"
            >
              <ShieldCheck className="h-4 w-4" /> 建立实盘窗口
            </button>
          )}
          {safety?.authorizationState !== 'ENABLED' &&
            safety?.authorizationState !== 'KILLED' && (
              <button
                type="button"
                disabled={submitting || !safety?.canActivateAutomation}
                onClick={() =>
                  preview(AccountExecutionControlAction.EnableRiskIncrease)
                }
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-40"
              >
                <CheckCircle2 className="h-4 w-4" /> 启用新增风险
              </button>
            )}
          {safety?.authorizationState === 'ENABLED' && (
            <button
              type="button"
              disabled={submitting || !reason.trim()}
              onClick={() =>
                preview(AccountExecutionControlAction.PauseRiskIncrease)
              }
              className="inline-flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2 text-xs font-medium text-foreground disabled:opacity-40"
            >
              <PauseCircle className="h-4 w-4" /> 暂停新增风险
            </button>
          )}
          {safety?.authorizationState !== 'KILLED' ? (
            <button
              type="button"
              disabled={submitting || !reason.trim()}
              onClick={() => preview(AccountExecutionControlAction.KillSwitch)}
              className="inline-flex items-center gap-2 rounded-lg bg-destructive px-3 py-2 text-xs font-medium text-destructive-foreground disabled:opacity-40"
            >
              <OctagonX className="h-4 w-4" /> 账户紧急停止
            </button>
          ) : (
            <button
              type="button"
              disabled={submitting}
              onClick={() =>
                preview(AccountExecutionControlAction.ClearKillSwitch)
              }
              className="inline-flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2 text-xs font-medium text-foreground disabled:opacity-40"
            >
              清除紧急停止
            </button>
          )}
        </div>

        <label className="mt-4 block text-xs text-slate-400">
          暂停或紧急停止原因
          <input
            value={reason}
            onChange={event => setReason(event.target.value)}
            maxLength={512}
            placeholder="说明本次风险控制原因"
            className="mt-2 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>

        {pending && (
          <div className="mt-4 rounded-lg border border-border bg-muted p-4">
            <p className="text-sm font-medium text-foreground">
              待确认：{actionLabels[pending.action]}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              确认将消费一次性挑战；状态或快照变化时服务端会拒绝应用。
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={confirm}
                disabled={submitting}
                className="rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-40"
              >
                确认应用
              </button>
              <button
                type="button"
                onClick={() => setPending(null)}
                disabled={submitting}
                className="rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground"
              >
                取消
              </button>
            </div>
          </div>
        )}
        {message && <p className="mt-3 text-xs text-slate-300">{message}</p>}
      </section>

      <section className="rounded-xl border border-border bg-card p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-medium text-slate-100">
              账户实盘准入检查
            </h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              逐项确认账户是否具备实盘观察、风险控制和新增风险条件。
            </p>
          </div>
          <div className="flex items-center gap-2">
            {safety && (
              <span className="rounded-full border border-border bg-muted px-2.5 py-1 text-xs text-slate-300">
                {passedCheckCount}/{safety.checks.length} 已通过
              </span>
            )}
            <button
              type="button"
              aria-pressed={showGateCodes}
              onClick={() => setShowGateCodes(current => !current)}
              className="cursor-pointer rounded-lg border border-border px-2.5 py-1 text-xs text-slate-400 transition-colors duration-200 hover:border-primary/40 hover:bg-primary/10 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
            >
              {showGateCodes ? '隐藏技术标识' : '显示技术标识'}
            </button>
          </div>
        </div>
        <div
          data-testid="account-execution-gates"
          className="mt-3 grid gap-2 md:grid-cols-2"
        >
          {(safety?.checks ?? []).map(check => {
            const presentation = getAccountExecutionGatePresentation(
              check.code
            );
            const freshness =
              check.code === 'SNAPSHOT_FRESH'
                ? getSnapshotFreshness(
                    safety?.reconciliationAgeSeconds,
                    safety?.checkedAt,
                    now
                  )
                : check.code === 'RECENT_BACKUP'
                  ? getBackupFreshness(safety?.lastBackupAt, now)
                  : null;
            return (
              <div
                key={check.code}
                data-execution-gate={check.code}
                className={cn(
                  'flex h-[72px] items-start gap-3 overflow-hidden rounded-lg border px-3 py-2',
                  freshness?.tone === 'expired'
                    ? 'border-rose-400/30 bg-rose-400/5'
                    : !check.passed || freshness?.tone === 'warning'
                      ? 'border-warning/30 bg-warning/5'
                      : 'border-border bg-muted'
                )}
              >
                {freshness ? (
                  <Clock3
                    aria-hidden="true"
                    className={cn(
                      'mt-0.5 h-4 w-4 shrink-0',
                      freshness.tone === 'fresh'
                        ? 'text-emerald-400'
                        : freshness.tone === 'warning'
                          ? 'text-warning motion-safe:animate-pulse'
                          : 'text-rose-400'
                    )}
                  />
                ) : check.passed ? (
                  <CheckCircle2
                    aria-hidden="true"
                    className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400"
                  />
                ) : (
                  <AlertTriangle
                    aria-hidden="true"
                    className="mt-0.5 h-4 w-4 shrink-0 text-warning"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm font-medium leading-4 text-slate-200">
                      {presentation.label}
                    </p>
                    <span
                      className={cn(
                        'shrink-0 text-[11px] font-medium',
                        freshness?.tone === 'expired'
                          ? 'text-rose-300'
                          : !check.passed || freshness?.tone === 'warning'
                            ? 'text-warning'
                            : 'text-emerald-300'
                      )}
                    >
                      {freshness?.tone === 'expired' && check.passed
                        ? '等待刷新'
                        : freshness?.tone === 'warning' && check.passed
                          ? '即将过期'
                          : check.passed
                            ? '已通过'
                            : '需处理'}
                    </span>
                  </div>
                  <div className="mt-1 flex min-w-0 items-start gap-3">
                    <p
                      className={cn(
                        'min-w-0 flex-1 text-xs leading-4',
                        check.passed ? 'text-slate-500' : 'text-amber-200/80'
                      )}
                    >
                      {check.passed
                        ? presentation.passedDescription
                        : check.message}
                    </p>
                    {freshness && <FreshnessIndicator freshness={freshness} />}
                  </div>
                  {showGateCodes && (
                    <code
                      title={check.code}
                      className="mt-0.5 block break-all text-[10px] leading-3 text-slate-600"
                    >
                      {check.code}
                    </code>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        {safety && !failedChecks.length && (
          <p className="mt-3 text-xs text-emerald-300">
            所有账户事实门禁均已通过。
          </p>
        )}
      </section>
    </div>
  );
}
