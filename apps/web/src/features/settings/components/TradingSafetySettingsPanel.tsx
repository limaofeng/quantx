import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  History,
  OctagonX,
  PauseCircle,
  RefreshCw,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Input } from '@/components/ui/input';
import {
  ConfirmAccountExecutionControlMutation,
  AccountExecutionSafetyHistoryQuery,
  PreviewAccountExecutionControlMutation,
  useTradingSafety,
} from '@/features/trading-safety';
import {
  AccountExecutionControlAction,
  AccountExecutionSafetyCheckStatus,
  AccountSafetyHistoryRange,
  AccountSafetyHistoryStatus,
  type TradingSafety_AccountExecutionSafetyHistoryQuery,
} from '@/generated/gql/graphql';
import { createClientId } from '@/utils/clientId';
import { cn } from '@/utils/cn';

import {
  getAccountExecutionGatePresentation,
  getBackupFreshness,
  getSnapshotFreshness,
  type AccountExecutionGateFreshness,
} from './accountExecutionGatePresentation';

const actionLabels: Record<AccountExecutionControlAction, string> = {
  [AccountExecutionControlAction.BeginControlledWindow]: '建立账户实盘窗口',
  [AccountExecutionControlAction.EnableRiskIncrease]: '启用买入权限',
  [AccountExecutionControlAction.PauseRiskIncrease]: '暂停买入权限',
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
      className="flex w-28 shrink-0 items-center gap-1.5"
      aria-label={`新鲜度：${freshness.countdownLabel}`}
    >
      <div
        role="progressbar"
        aria-label="剩余有效时间"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(freshness.progressPercent)}
        className="h-1 min-w-8 flex-1 overflow-hidden rounded-full bg-slate-950/70 ring-1 ring-inset ring-white/5"
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
      <span
        className={cn(
          'shrink-0 font-mono text-ui-caption font-medium leading-3 tabular-nums',
          freshness.tone === 'fresh'
            ? 'text-emerald-300'
            : freshness.tone === 'warning'
              ? 'text-warning'
              : 'text-rose-300'
        )}
      >
        {compactLabel}
      </span>
    </div>
  );
}

type GateVisualTone = 'success' | 'standby' | 'warning' | 'danger';

type AccountSafetyHistory =
  TradingSafety_AccountExecutionSafetyHistoryQuery['accountExecutionSafetyHistory'];

const historyRanges: Array<{
  value: AccountSafetyHistoryRange;
  label: string;
}> = [
  { value: AccountSafetyHistoryRange.Hours_24, label: '24 小时' },
  { value: AccountSafetyHistoryRange.Days_7, label: '7 天' },
  { value: AccountSafetyHistoryRange.Days_30, label: '30 天' },
  { value: AccountSafetyHistoryRange.Days_90, label: '90 天' },
  { value: AccountSafetyHistoryRange.Year_1, label: '1 年' },
];

const historyStatusPresentation: Record<
  AccountSafetyHistoryStatus,
  { label: string; className: string }
> = {
  [AccountSafetyHistoryStatus.Passed]: {
    label: '通过',
    className: 'bg-emerald-400',
  },
  [AccountSafetyHistoryStatus.Standby]: {
    label: '休市待机',
    className: 'bg-primary',
  },
  [AccountSafetyHistoryStatus.Failed]: {
    label: '异常',
    className: 'bg-rose-400',
  },
  [AccountSafetyHistoryStatus.Unknown]: {
    label: '未观测',
    className: 'bg-slate-600',
  },
};

function formatHistoryTime(value: string | null | undefined) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(value));
}

function formatIncidentDuration(openedAt: string, resolvedAt?: string | null) {
  const end = resolvedAt ? new Date(resolvedAt).getTime() : Date.now();
  const minutes = Math.max(
    1,
    Math.round((end - new Date(openedAt).getTime()) / 60_000)
  );
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.round((minutes / 60) * 10) / 10;
  return `${hours} 小时`;
}

function SafetyHistoryView({
  fetching,
  history,
  range,
  selectedCode,
  setRange,
  setSelectedCode,
}: {
  fetching: boolean;
  history?: AccountSafetyHistory;
  range: AccountSafetyHistoryRange;
  selectedCode: string | null;
  setRange: (value: AccountSafetyHistoryRange) => void;
  setSelectedCode: (value: string | null) => void;
}) {
  const incidents = (history?.incidents ?? []).filter(
    incident => !selectedCode || incident.checkCode === selectedCode
  );
  const groupedIncidents = incidents.reduce<Record<string, typeof incidents>>(
    (groups, incident) => {
      const day = new Intl.DateTimeFormat('zh-CN', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        weekday: 'short',
      }).format(new Date(incident.openedAt));
      (groups[day] ??= []).push(incident);
      return groups;
    },
    {}
  );

  return (
    <div className="mt-4 space-y-ui-section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1" aria-label="历史范围">
          {historyRanges.map(option => (
            <button
              key={option.value}
              type="button"
              aria-pressed={range === option.value}
              onClick={() => setRange(option.value)}
              className={cn(
                'cursor-pointer rounded-md border px-2.5 py-1 text-ui-label transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70',
                range === option.value
                  ? 'border-primary/40 bg-primary/15 text-blue-100'
                  : 'border-border text-slate-400 hover:border-primary/30 hover:text-slate-200'
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-ui-caption text-slate-400">
          {Object.entries(historyStatusPresentation).map(([status, item]) => (
            <span key={status} className="inline-flex items-center gap-1.5">
              <span className={cn('h-2 w-2 rounded-sm', item.className)} />
              {item.label}
            </span>
          ))}
        </div>
      </div>

      {!history?.available ? (
        <div className="rounded-lg border border-slate-700/60 bg-slate-900/35 p-ui-section">
          <p className="text-ui-body font-medium text-slate-200">
            {fetching ? '正在读取准入历史…' : '准入历史暂不可用'}
          </p>
          {!fetching && (
            <p className="mt-1 text-ui-label leading-5 text-slate-400">
              主系统当前判定不受影响。可前往
              <a
                href="/settings/status"
                className="mx-1 text-primary underline-offset-4 hover:underline"
              >
                服务状态
              </a>
              检查独立 Monitor。
            </p>
          )}
        </div>
      ) : (
        <>
          {!history.observerFresh && (
            <div className="rounded-lg border border-slate-600/60 bg-slate-800/50 px-3 py-2 text-ui-label text-slate-300">
              Monitor 最近没有拿到新观测；灰色区间代表未观测，不等同于准入失败。
            </div>
          )}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-ui-caption text-slate-500">
            <span>开始观测 {formatHistoryTime(history.firstObservedAt)}</span>
            <span>最近观测 {formatHistoryTime(history.lastObservedAt)}</span>
            <span>{history.incidents.length} 个异常事件</span>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {history.checks.map(check => {
              const presentation = getAccountExecutionGatePresentation(
                check.code
              );
              const current = historyStatusPresentation[check.currentStatus];
              const selected = selectedCode === check.code;
              return (
                <button
                  key={check.code}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => setSelectedCode(selected ? null : check.code)}
                  className={cn(
                    'cursor-pointer rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70',
                    selected
                      ? 'border-primary/45 bg-primary/10'
                      : 'border-slate-700/50 bg-slate-900/35 hover:border-slate-600'
                  )}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-ui-body font-medium text-slate-100">
                      {presentation.label}
                    </span>
                    <span className="inline-flex items-center gap-1.5 text-ui-caption text-slate-400">
                      <span
                        className={cn('h-2 w-2 rounded-sm', current.className)}
                      />
                      {current.label}
                    </span>
                  </div>
                  <div className="mt-2 flex h-3 overflow-hidden rounded-sm bg-slate-950">
                    {check.points.map(point => (
                      <span
                        key={point.start}
                        className={cn(
                          'min-w-px flex-1',
                          historyStatusPresentation[point.status].className
                        )}
                        title={`${formatHistoryTime(point.start)} · ${historyStatusPresentation[point.status].label} · 覆盖 ${Math.round(point.coveragePct)}%`}
                      />
                    ))}
                  </div>
                  <div className="mt-2 flex items-center justify-between text-ui-caption text-slate-500">
                    <span>观测覆盖 {Math.round(check.coveragePct)}%</span>
                    <span>{check.incidentCount} 次异常</span>
                  </div>
                </button>
              );
            })}
          </div>

          <div className="border-t border-border pt-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-ui-body font-medium text-slate-100">
                  异常事件
                </h3>
                <p className="mt-1 text-ui-label text-slate-500">
                  只有明确失败才形成事件；休市待机和观测中断不计入异常。
                </p>
              </div>
              {selectedCode && (
                <button
                  type="button"
                  onClick={() => setSelectedCode(null)}
                  className="cursor-pointer text-ui-label text-primary hover:underline"
                >
                  清除筛选
                </button>
              )}
            </div>
            {incidents.length === 0 ? (
              <div className="mt-3 rounded-lg border border-emerald-400/15 bg-emerald-400/5 px-3 py-3 text-ui-label text-emerald-200">
                所选范围内没有确认的准入异常。
              </div>
            ) : (
              <div className="mt-3 space-y-ui-section">
                {Object.entries(groupedIncidents).map(([day, dayIncidents]) => (
                  <section key={day}>
                    <h4 className="text-ui-label font-medium text-slate-400">
                      {day}
                    </h4>
                    <div className="mt-2 space-y-2">
                      {dayIncidents.map(incident => {
                        const presentation =
                          getAccountExecutionGatePresentation(
                            incident.checkCode
                          );
                        return (
                          <article
                            key={incident.id}
                            className="rounded-lg border border-rose-400/20 bg-rose-400/5 p-3"
                          >
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div>
                                <h5 className="text-ui-body font-medium text-slate-100">
                                  {presentation.label}
                                </h5>
                                <p className="mt-1 text-ui-label leading-5 text-rose-100/80">
                                  {incident.lastMessage}
                                </p>
                              </div>
                              <span className="rounded-full border border-rose-400/25 bg-rose-400/10 px-2 py-0.5 text-ui-caption text-rose-200">
                                {incident.active
                                  ? incident.observationFresh
                                    ? '处理中'
                                    : '历史异常 · 观测中断'
                                  : '已恢复'}
                              </span>
                            </div>
                            <p className="mt-2 text-ui-caption text-slate-500">
                              {formatHistoryTime(incident.openedAt)} 开始
                              <span className="mx-2">·</span>
                              {incident.resolvedAt
                                ? `${formatHistoryTime(incident.resolvedAt)} 恢复`
                                : '尚未确认恢复'}
                              <span className="mx-2">·</span>
                              持续{' '}
                              {formatIncidentDuration(
                                incident.openedAt,
                                incident.resolvedAt
                              )}
                            </p>
                          </article>
                        );
                      })}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function GateStatusMark({
  temporal,
  tone,
}: {
  temporal: boolean;
  tone: GateVisualTone;
}) {
  const StatusIcon =
    tone === 'standby'
      ? Clock3
      : temporal
        ? Clock3
        : tone === 'success'
          ? CheckCircle2
          : AlertTriangle;
  return (
    <span
      className={cn(
        'flex h-7 w-7 shrink-0 items-center justify-center rounded-md border',
        tone === 'success'
          ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'
          : tone === 'standby'
            ? 'border-primary/25 bg-primary/10 text-primary'
            : tone === 'warning'
              ? 'border-warning/25 bg-warning/10 text-warning'
              : 'border-rose-400/25 bg-rose-400/10 text-rose-300'
      )}
      aria-hidden="true"
    >
      <StatusIcon
        className={cn(
          'h-3.5 w-3.5',
          temporal && tone === 'warning' && 'motion-safe:animate-pulse'
        )}
      />
    </span>
  );
}

export function TradingSafetySettingsPanel() {
  const now = useNow();
  const { accountId, fetching, refreshSafety, safety } = useTradingSafety();
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
  const [gateView, setGateView] = useState<'current' | 'history'>('current');
  const [historyRange, setHistoryRange] = useState(
    AccountSafetyHistoryRange.Days_30
  );
  const [selectedHistoryCode, setSelectedHistoryCode] = useState<string | null>(
    null
  );
  const [historyResult] = useQuery({
    query: AccountExecutionSafetyHistoryQuery,
    variables: { accountId, range: historyRange },
    pause: !accountId || gateView !== 'history',
    requestPolicy: 'network-only',
  });
  const [pending, setPending] = useState<{
    action: AccountExecutionControlAction;
    challengeId: string;
    confirmationToken: string;
  } | null>(null);
  const failedChecks = useMemo(
    () =>
      safety?.checks.filter(
        check => check.status === AccountExecutionSafetyCheckStatus.Failed
      ) ?? [],
    [safety?.checks]
  );
  const standbyChecks = useMemo(
    () =>
      safety?.checks.filter(
        check => check.status === AccountExecutionSafetyCheckStatus.Standby
      ) ?? [],
    [safety?.checks]
  );
  const passedCheckCount =
    (safety?.checks.length ?? 0) - failedChecks.length - standbyChecks.length;

  const reload = () => {
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
        idempotencyKey: `account-execution:${createClientId()}`,
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
    return (
      <p className="text-ui-body text-amber-300">当前用户没有可用资金账户。</p>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-ui-section">
      <header className="flex items-start justify-between gap-ui-section">
        <div>
          <p className="text-ui-label font-medium uppercase text-primary">
            Account execution control
          </p>
          <h1 className="mt-2 text-ui-display font-semibold text-slate-100">
            账户交易安全
          </h1>
          <p className="mt-2 max-w-3xl text-ui-body leading-6 text-slate-400">
            这里只控制账户级实盘授权、对账窗口与紧急停止。做
            T、打板和普通策略各自的功能门禁不会写入这里。
          </p>
        </div>
        <button
          type="button"
          onClick={reload}
          disabled={fetching}
          className="inline-flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-ui-label text-muted-foreground hover:bg-muted disabled:opacity-50"
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
            className="rounded-panel border border-border bg-card p-ui-section"
          >
            <p className="text-ui-label text-slate-500">{label}</p>
            <p className="mt-2 font-mono text-ui-body font-semibold text-slate-100">
              {value}
            </p>
          </div>
        ))}
      </section>

      <section className="rounded-panel border border-border bg-card p-ui-section">
        <div className="flex items-start gap-3">
          {safety?.canIncreaseRisk ? (
            <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-400" />
          ) : (
            <AlertTriangle className="mt-0.5 h-5 w-5 text-warning" />
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-ui-body font-medium text-slate-100">
              {safety?.summary || '账户安全状态加载中'}
            </h2>
            <p className="mt-1 text-ui-label leading-5 text-slate-500">
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
              className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-ui-label font-medium text-primary-foreground disabled:opacity-40"
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
                className="inline-flex items-center gap-2 rounded-lg bg-primary px-3 py-2 text-ui-label font-medium text-primary-foreground disabled:opacity-40"
              >
                <CheckCircle2 className="h-4 w-4" /> 启用买入权限
              </button>
            )}
          {safety?.authorizationState === 'ENABLED' && (
            <button
              type="button"
              disabled={submitting || !reason.trim()}
              onClick={() =>
                preview(AccountExecutionControlAction.PauseRiskIncrease)
              }
              className="inline-flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2 text-ui-label font-medium text-foreground disabled:opacity-40"
            >
              <PauseCircle className="h-4 w-4" /> 暂停买入权限
            </button>
          )}
          {safety?.authorizationState !== 'KILLED' ? (
            <button
              type="button"
              disabled={submitting || !reason.trim()}
              onClick={() => preview(AccountExecutionControlAction.KillSwitch)}
              className="inline-flex items-center gap-2 rounded-lg bg-destructive px-3 py-2 text-ui-label font-medium text-destructive-foreground disabled:opacity-40"
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
              className="inline-flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2 text-ui-label font-medium text-foreground disabled:opacity-40"
            >
              清除紧急停止
            </button>
          )}
        </div>

        <label className="mt-4 block text-ui-label text-slate-400">
          暂停或紧急停止原因
          <Input
            value={reason}
            onChange={event => setReason(event.target.value)}
            maxLength={512}
            placeholder="说明本次风险控制原因"
            className="mt-2 w-full rounded-lg border border-input bg-background px-3 py-2 text-ui-body text-foreground outline-none focus:border-primary"
          />
        </label>

        {pending && (
          <div className="mt-4 rounded-lg border border-border bg-muted p-ui-section">
            <p className="text-ui-body font-medium text-foreground">
              待确认：{actionLabels[pending.action]}
            </p>
            <p className="mt-1 text-ui-label text-muted-foreground">
              确认将消费一次性挑战；状态或快照变化时服务端会拒绝应用。
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={confirm}
                disabled={submitting}
                className="rounded-lg bg-primary px-3 py-2 text-ui-label font-medium text-primary-foreground disabled:opacity-40"
              >
                确认应用
              </button>
              <button
                type="button"
                onClick={() => setPending(null)}
                disabled={submitting}
                className="rounded-lg border border-border px-3 py-2 text-ui-label text-muted-foreground"
              >
                取消
              </button>
            </div>
          </div>
        )}
        {message && (
          <p className="mt-3 text-ui-label text-slate-300">{message}</p>
        )}
      </section>

      <section className="rounded-panel border border-border bg-card p-ui-section">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-ui-body font-medium text-slate-100">
              账户实盘准入检查
            </h2>
            <p className="mt-1 text-ui-label leading-5 text-slate-500">
              逐项确认账户是否具备实盘观察、风险控制和买入条件。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <div className="inline-flex rounded-lg border border-border bg-slate-950/40 p-0.5">
              <button
                type="button"
                aria-pressed={gateView === 'current'}
                onClick={() => setGateView('current')}
                className={cn(
                  'inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-ui-label transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70',
                  gateView === 'current'
                    ? 'bg-primary/15 text-blue-100'
                    : 'text-slate-500 hover:text-slate-300'
                )}
              >
                <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                当前准入
              </button>
              <button
                type="button"
                aria-pressed={gateView === 'history'}
                onClick={() => setGateView('history')}
                className={cn(
                  'inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-ui-label transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70',
                  gateView === 'history'
                    ? 'bg-primary/15 text-blue-100'
                    : 'text-slate-500 hover:text-slate-300'
                )}
              >
                <History className="h-3.5 w-3.5" aria-hidden="true" />
                异常历史
              </button>
            </div>
            {gateView === 'current' && safety && (
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-ui-label font-medium',
                  failedChecks.length
                    ? 'border-warning/20 bg-warning/10 text-amber-200'
                    : standbyChecks.length
                      ? 'border-primary/20 bg-primary/10 text-blue-200'
                      : 'border-emerald-400/20 bg-emerald-400/10 text-emerald-200'
                )}
              >
                {failedChecks.length ? (
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                ) : standbyChecks.length ? (
                  <Clock3 className="h-3 w-3" aria-hidden="true" />
                ) : (
                  <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                )}
                {standbyChecks.length && !failedChecks.length
                  ? `${passedCheckCount} 项通过 · ${standbyChecks.length} 项休市待机`
                  : `${passedCheckCount}/${safety.checks.length} 已通过`}
              </span>
            )}
            {gateView === 'current' && (
              <button
                type="button"
                aria-pressed={showGateCodes}
                onClick={() => setShowGateCodes(current => !current)}
                className="cursor-pointer rounded-lg border border-border px-2.5 py-1 text-ui-label text-slate-400 transition-colors duration-200 hover:border-primary/40 hover:bg-primary/10 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70"
              >
                {showGateCodes ? '隐藏技术标识' : '显示技术标识'}
              </button>
            )}
          </div>
        </div>
        {gateView === 'current' ? (
          <>
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
                const temporal =
                  check.code === 'SNAPSHOT_FRESH' ||
                  check.code === 'RECENT_BACKUP';
                const failed =
                  check.status === AccountExecutionSafetyCheckStatus.Failed;
                const standby =
                  check.status === AccountExecutionSafetyCheckStatus.Standby;
                const tone: GateVisualTone =
                  freshness?.tone === 'expired' ||
                  (failed && check.code === 'KILL_SWITCH_CLEAR')
                    ? 'danger'
                    : standby
                      ? 'standby'
                      : failed || freshness?.tone === 'warning'
                        ? 'warning'
                        : 'success';
                const statusLabel = standby
                  ? '休市待机'
                  : freshness?.tone === 'expired'
                    ? '已过期'
                    : freshness?.tone === 'warning' && !failed
                      ? '即将过期'
                      : !failed
                        ? '已通过'
                        : '需处理';
                return (
                  <article
                    key={check.code}
                    data-execution-gate={check.code}
                    data-gate-tone={tone}
                    aria-label={`${presentation.label}：${statusLabel}`}
                    className={cn(
                      'flex h-[72px] items-center gap-3 overflow-hidden rounded-lg border px-3 py-2',
                      tone === 'success'
                        ? 'border-slate-700/50 bg-slate-900/35'
                        : tone === 'standby'
                          ? 'border-primary/25 bg-primary/5'
                          : tone === 'warning'
                            ? 'border-warning/30 bg-warning/5'
                            : 'border-rose-400/30 bg-rose-400/5'
                    )}
                  >
                    <GateStatusMark temporal={temporal} tone={tone} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start justify-between gap-3">
                        <h3 className="text-ui-body font-medium leading-4 text-slate-100">
                          {presentation.label}
                        </h3>
                        <span
                          className={cn(
                            'shrink-0 rounded-full border px-1.5 py-0.5 text-ui-caption font-medium leading-3',
                            tone === 'success'
                              ? 'border-emerald-400/15 bg-emerald-400/10 text-emerald-300'
                              : tone === 'standby'
                                ? 'border-primary/20 bg-primary/10 text-primary'
                                : tone === 'warning'
                                  ? 'border-warning/20 bg-warning/10 text-warning'
                                  : 'border-rose-400/20 bg-rose-400/10 text-rose-300'
                          )}
                        >
                          {statusLabel}
                        </span>
                      </div>
                      <div className="mt-1 flex min-w-0 items-start gap-3">
                        <p
                          className={cn(
                            'min-w-0 flex-1 text-ui-label leading-4',
                            tone === 'success'
                              ? 'text-slate-400'
                              : tone === 'standby'
                                ? 'text-blue-200/80'
                                : tone === 'warning'
                                  ? 'text-amber-200/80'
                                  : 'text-rose-200/80'
                          )}
                        >
                          {failed || standby
                            ? check.message
                            : check.status ===
                                AccountExecutionSafetyCheckStatus.Passed
                              ? presentation.passedDescription
                              : check.message}
                        </p>
                        {freshness && (
                          <FreshnessIndicator freshness={freshness} />
                        )}
                      </div>
                      {showGateCodes && (
                        <code
                          title={check.code}
                          className="mt-0.5 block break-all text-ui-caption leading-3 text-slate-600"
                        >
                          {check.code}
                        </code>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
            {safety && !failedChecks.length && (
              <p className="mt-3 text-ui-label text-emerald-300">
                {standbyChecks.length
                  ? '账户事实门禁正常；当前休市待机。'
                  : '所有账户事实门禁均已通过。'}
              </p>
            )}
          </>
        ) : (
          <SafetyHistoryView
            fetching={historyResult.fetching}
            history={historyResult.data?.accountExecutionSafetyHistory}
            range={historyRange}
            selectedCode={selectedHistoryCode}
            setRange={setHistoryRange}
            setSelectedCode={setSelectedHistoryCode}
          />
        )}
      </section>
    </div>
  );
}
