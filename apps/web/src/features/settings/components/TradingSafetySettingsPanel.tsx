import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  History,
  OctagonX,
  PauseCircle,
  RefreshCw,
  ShieldCheck,
  Wrench,
} from 'lucide-react';
import { useEffect, useState } from 'react';
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
  AccountSafetyHistoryRange,
} from '@/generated/gql/graphql';
import { createClientId } from '@/utils/clientId';
import { cn } from '@/utils/cn';

import { AccountExecutionGateCurrentView } from './AccountExecutionGateCurrentView';
import { AccountExecutionSafetyHistoryView } from './AccountExecutionSafetyHistoryView';

const actionLabels: Record<AccountExecutionControlAction, string> = {
  [AccountExecutionControlAction.BeginControlledWindow]: '建立账户实盘窗口',
  [AccountExecutionControlAction.EnableRiskIncrease]: '启用买入权限',
  [AccountExecutionControlAction.PauseRiskIncrease]: '暂停买入权限',
  [AccountExecutionControlAction.KillSwitch]: '账户紧急停止',
  [AccountExecutionControlAction.ClearKillSwitch]: '清除紧急停止',
  [AccountExecutionControlAction.RepairQuarantinedOrder]: '修复隔离委托',
};

interface QuarantinedOrderTarget {
  clientOrderId: string;
  planId: string;
  intentId: string;
  quarantineReason: string;
  brokerOrderId: string;
  repairable: boolean;
  blockedReason: string;
  quarantinedAt: string;
  sourceSequence: number;
}

const quarantineReasonLabels: Record<string, string> = {
  ACCOUNT_WIDE_STALE_SELL: '账户隔离时仍在途的卖单',
  BINDING_MISMATCH: '委托与退出计划绑定不一致',
  BROKER_EXECUTION_AFTER_RELEASE: '计划释放后券商又上报执行事实',
  PHYSICAL_DELIVERY_GATE_REJECTED: '物理发送前最终闸门拒绝',
  PLACE_ORDER_BINDING_MISSING: '在途卖单缺少完整持久化绑定',
  PLAN_PENDING_RELEASE_FAILED: '计划 pending 意图释放失败',
};

const quarantineBlockedReasonLabels: Record<string, string> = {
  BROKER_TERMINAL_EVIDENCE_REQUIRED: '等待券商终态与成交数量完整收敛',
  DURABLE_BINDING_UNPROVEN: '持久化计划、意图或委托绑定无法证明',
  LATEST_FULL_SNAPSHOT_REQUIRED: '等待最新完整账户快照',
  LATEST_FULL_SNAPSHOT_EVIDENCE_UNAVAILABLE: '最新完整快照证据不可用',
  SNAPSHOT_NOT_NEWER_THAN_QUARANTINE: '快照必须严格晚于隔离事实',
  SNAPSHOT_SEQUENCE_NOT_NEWER_THAN_QUARANTINE: '快照序号必须严格晚于隔离事实',
};

function useNow() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

export function TradingSafetySettingsPanel() {
  const now = useNow();
  const { accountId, fetching, error, refreshSafety, safety } =
    useTradingSafety();
  const [, previewControl] = useMutation(
    PreviewAccountExecutionControlMutation
  );
  const [, confirmControl] = useMutation(
    ConfirmAccountExecutionControlMutation
  );
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
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
    clientOrderId?: string;
    quarantineReason?: string;
  } | null>(null);
  const reload = () => {
    refreshSafety();
  };

  const preview = async (
    action: AccountExecutionControlAction,
    quarantinedOrder?: QuarantinedOrderTarget
  ) => {
    if (!safety) return;
    setSubmitting(true);
    setMessage('');
    const result = await previewControl({
      input: {
        accountId,
        action,
        stateVersion: safety.stateVersion,
        snapshotId:
          action === AccountExecutionControlAction.BeginControlledWindow ||
          action === AccountExecutionControlAction.RepairQuarantinedOrder
            ? safety.snapshotId || ''
            : '',
        ...(quarantinedOrder
          ? {
              clientOrderId: quarantinedOrder.clientOrderId,
              quarantineReason: quarantinedOrder.quarantineReason,
            }
          : {}),
        reason:
          action === AccountExecutionControlAction.PauseRiskIncrease ||
          action === AccountExecutionControlAction.KillSwitch ||
          action === AccountExecutionControlAction.RepairQuarantinedOrder
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
      clientOrderId: quarantinedOrder?.clientOrderId,
      quarantineReason: quarantinedOrder?.quarantineReason,
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
          [
            '授权状态',
            safety?.authorizationState || (error ? 'UNKNOWN' : 'LOADING'),
          ],
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
              {error
                ? '账户安全状态查询失败'
                : safety?.summary || '账户安全状态加载中'}
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
          暂停、紧急停止或隔离修复原因
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
              {pending.clientOrderId ? ` · ${pending.clientOrderId}` : ''}
            </p>
            <p className="mt-1 text-ui-label text-muted-foreground">
              确认将消费一次性挑战；状态或快照变化时服务端会拒绝应用。
              {pending.quarantineReason
                ? ` 隔离原因：${
                    quarantineReasonLabels[pending.quarantineReason] ||
                    pending.quarantineReason
                  }。`
                : ''}
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

      {!!safety?.quarantinedOrders.length && (
        <section className="rounded-panel border border-rose-400/25 bg-card p-ui-section">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-rose-300" />
            <div>
              <h2 className="text-ui-body font-medium text-slate-100">
                隔离委托显式修复
              </h2>
              <p className="mt-1 max-w-3xl text-ui-label leading-5 text-slate-400">
                系统不会自动重放或解除这些卖单。每条修复都绑定当前完整快照；全部修复后，还需下一份严格更新的干净快照才能恢复账户对账。
              </p>
            </div>
          </div>

          <div className="mt-4 space-y-2">
            {safety.quarantinedOrders.map(order => (
              <article
                key={`${order.clientOrderId}:${order.quarantineReason}`}
                className="rounded-lg border border-rose-400/15 bg-rose-400/5 p-3"
                aria-label={`隔离委托 ${order.clientOrderId}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-mono text-ui-body font-medium text-slate-100">
                      {order.clientOrderId}
                    </p>
                    <p className="mt-1 text-ui-label text-rose-100/80">
                      {quarantineReasonLabels[order.quarantineReason] ||
                        order.quarantineReason}
                    </p>
                    <p className="mt-1 break-all text-ui-caption text-slate-500">
                      计划 {order.planId || '—'} · 意图 {order.intentId || '—'}
                      {order.brokerOrderId
                        ? ` · 券商委托 ${order.brokerOrderId}`
                        : ''}
                      {order.sourceSequence
                        ? ` · 源序号 ${order.sourceSequence}`
                        : ''}
                    </p>
                    {!order.repairable && (
                      <p className="mt-2 text-ui-label text-amber-200">
                        {quarantineBlockedReasonLabels[order.blockedReason] ||
                          order.blockedReason ||
                          '当前还不具备显式修复条件'}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    disabled={
                      submitting ||
                      !order.repairable ||
                      !safety.snapshotId ||
                      !reason.trim()
                    }
                    onClick={() =>
                      preview(
                        AccountExecutionControlAction.RepairQuarantinedOrder,
                        order
                      )
                    }
                    className="inline-flex items-center gap-2 rounded-lg border border-rose-300/30 bg-rose-300/10 px-3 py-2 text-ui-label font-medium text-rose-100 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Wrench className="h-4 w-4" /> 修复委托{' '}
                    {order.clientOrderId}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="rounded-panel border border-border bg-card p-ui-section">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-ui-body font-medium text-slate-100">
              账户实盘准入检查
            </h2>
            <p className="mt-1 text-ui-label leading-5 text-slate-500">
              {gateView === 'current'
                ? '先看结论和需关注项，再按链路核对全部权威检查。'
                : '回看明确异常的发生、恢复与影响范围；休市待机不计入异常。'}
            </p>
          </div>
          <div className="inline-flex min-h-8 rounded-lg border border-border bg-slate-950/40 p-0.5">
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
        </div>
        {gateView === 'current' ? (
          error ? (
            <div
              role="alert"
              className="mt-4 rounded-lg border border-destructive/30 bg-card p-ui-section text-ui-label text-destructive"
            >
              无法取得账户准入判定：{error.message}
              。请检查服务连接后点击页面顶部“刷新”重试；当前不允许交易操作。
            </div>
          ) : (
            <AccountExecutionGateCurrentView now={now} safety={safety} />
          )
        ) : (
          <AccountExecutionSafetyHistoryView
            fetching={historyResult.fetching}
            history={historyResult.data?.accountExecutionSafetyHistory}
            now={now}
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
