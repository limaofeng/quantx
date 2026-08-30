import {
  Activity,
  Check,
  ChevronDown,
  ChevronRight,
  Database,
  Link2,
  Loader2,
  Network,
  ShieldAlert,
  X,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/utils/cn';

import { hasCandidateTraceIdentity, traceRelatedIdGroups } from './clientTrust';
import {
  canApproveSnapshot,
  isKnownSignalSnapshot,
  type MonitorSession,
  type SignalSnapshot,
} from './monitoring';
import {
  nullableScore,
  signalEventTypes,
  signalEventLabels,
  candidateStatusLabels,
  signalPathLabels,
  signalPhaseLabels,
  signalEventTone,
} from './signalPresentation';
import {
  type SignalEvaluationLike,
  type TTradeMonitorLike,
} from './TTradeLiveMonitor';
import { TTradeSignalEvidence } from './TTradeSignalEvidence';
import { formatNumber, formatTime } from './utils';

export type CandidateTraceLike = {
  accountId: string;
  candidateId: string;
  strategyRunId: string;
  instrumentCode: string;
  sourceEvaluationId: string;
  integrityStatus: string;
  sourceIdentity: {
    sourceTimeMs?: string | null;
    tickOrdinal?: string | null;
    continuityGeneration?: string | null;
    tradeDate?: string | null;
    candidateFingerprint?: string | null;
    policyVersion?: string | null;
    featureSchemaVersion?: string | null;
    profileVersion?: string | null;
  };
  missingReasons: readonly {
    code: string;
    stage: string;
    expected: boolean;
    detail: string;
  }[];
  links: {
    evaluationIds: readonly string[];
    intentIds: readonly string[];
    clientOrderIds: readonly string[];
    correlationIds: readonly string[];
    brokerOrderIds: readonly string[];
    orderIds: readonly string[];
    tradeIds: readonly string[];
    batchIds: readonly string[];
    exitPlanIds: readonly string[];
    exitPlanEventIds: readonly string[];
  };
  events: readonly {
    stage: string;
    eventType: string;
    entityId: string;
    occurredAt: string;
    status?: string | null;
    relatedIds: unknown;
    details: unknown;
  }[];
};

export type CandidateTraceSelection = {
  accountId: string;
  strategyRunId: string;
  candidateId: string;
};

const traceStageLabels: Record<string, string> = {
  EVALUATION: '机会评估',
  TRADE_INTENT: '交易意图',
  T_TRADE_BATCH: '做 T 批次',
  PENDING_ORDER: '订单命令',
  ORDER_CORRELATION: '订单关联',
  BROKER_ORDER: '券商委托',
  BROKER_TRADE: '券商成交',
  AUTO_EXIT_PLAN: '退出计划',
  AUTO_EXIT_PLAN_EVENT: '退出事件',
};

const traceLinkDefinitions = [
  ['evaluationIds', '评估 ID'],
  ['intentIds', '交易意图 ID'],
  ['clientOrderIds', '客户端订单 ID'],
  ['correlationIds', '关联 ID'],
  ['brokerOrderIds', '券商委托 ID'],
  ['orderIds', '订单 ID'],
  ['tradeIds', '成交 ID'],
  ['batchIds', '做 T 批次 ID'],
  ['exitPlanIds', '退出计划 ID'],
  ['exitPlanEventIds', '退出事件 ID'],
] as const;

function traceDetailText(value: unknown) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
  return Object.entries(value)
    .slice(0, 8)
    .map(([key, item]) => {
      if (item == null) return null;
      const rendered =
        typeof item === 'object'
          ? JSON.stringify(item) || '[object]'
          : String(item);
      return `${key}=${rendered.slice(0, 160)}`;
    })
    .filter((item): item is string => Boolean(item))
    .join(' · ');
}

function CandidateTracePanel({
  accountId,
  candidateId,
  strategyRunId,
  error,
  loading,
  onClose,
  trace,
}: {
  accountId: string;
  candidateId: string;
  strategyRunId: string;
  error?: string;
  loading: boolean;
  onClose: () => void;
  trace?: CandidateTraceLike | null;
}) {
  const traceIdentity = { accountId, strategyRunId, candidateId };
  const traceMatchesSelection = hasCandidateTraceIdentity(trace, traceIdentity);
  const traceForDisplay = traceMatchesSelection ? trace : undefined;
  const traceError =
    trace && !traceMatchesSelection
      ? '追溯响应身份与当前账户、运行或候选不一致，已阻止展示'
      : error;
  const linkGroups = traceForDisplay
    ? traceLinkDefinitions
        .map(([key, label]) => ({
          key,
          label,
          ids: traceForDisplay.links[key].filter(
            id => typeof id === 'string' && id.trim().length > 0
          ),
        }))
        .filter(group => group.ids.length > 0)
    : [];
  const integrityLabel =
    traceForDisplay?.integrityStatus === 'COMPLETE'
      ? '链路完整'
      : traceForDisplay?.integrityStatus === 'IN_PROGRESS'
        ? '正常进行中'
        : traceForDisplay?.integrityStatus === 'BROKEN'
          ? '链路断裂'
          : '读取中';
  const linkCount = linkGroups.reduce(
    (total, group) => total + group.ids.length,
    0
  );

  return (
    <section
      aria-label={`账户 ${accountId}，运行 ${strategyRunId}，候选 ${candidateId} 全链路追溯`}
      aria-live="polite"
      aria-busy={loading}
      className="mb-3 border border-cyan-400/20 bg-cyan-400/[0.035] p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="flex items-center gap-2 text-ui-caption font-black text-cyan-100">
            <Network className="h-3.5 w-3.5" aria-hidden="true" />
            候选全链路追溯
          </h4>
          <div className="mt-1 break-all font-mono text-ui-micro text-slate-600">
            {strategyRunId} / {candidateId}
          </div>
        </div>
        <button
          type="button"
          aria-label="关闭候选追溯"
          className="flex h-7 w-7 shrink-0 items-center justify-center border border-white/10 text-slate-500 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
          onClick={onClose}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {loading && (
        <div
          role="status"
          className="flex items-center py-ui-section text-ui-micro text-slate-500"
        >
          <Loader2
            aria-hidden="true"
            className="mr-2 h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
          />
          从持久化真源读取时间线…
        </div>
      )}
      {!loading && traceError && (
        <div
          role="alert"
          className="mt-3 text-ui-micro leading-4 text-rose-200"
        >
          追溯读取失败：{traceError}
        </div>
      )}
      {!loading && !traceError && !traceForDisplay && (
        <div className="mt-3 text-ui-micro leading-4 text-amber-200">
          当前账户未找到该候选的 MATERIAL 真源记录。
        </div>
      )}
      {!loading && !traceError && traceForDisplay && (
        <div className="mt-3 space-y-3">
          <div role="status" className="sr-only">
            候选 {candidateId} 的追溯已加载
          </div>
          <div className="grid grid-cols-2 gap-2 text-ui-micro">
            <div className="border border-white/[0.06] p-2">
              <div className="text-slate-600">完整性</div>
              <div
                className={cn(
                  'mt-1 font-black',
                  traceForDisplay.integrityStatus === 'BROKEN'
                    ? 'text-rose-300'
                    : traceForDisplay.integrityStatus === 'COMPLETE'
                      ? 'text-emerald-300'
                      : 'text-amber-200'
                )}
              >
                {integrityLabel}
              </div>
            </div>
            <div className="border border-white/[0.06] p-2">
              <div className="text-slate-600">事实节点</div>
              <div className="mt-1 font-mono text-slate-200">
                {traceForDisplay.events.length} 事件 · {linkCount} 关联
              </div>
            </div>
          </div>
          <div className="border border-white/[0.06] p-2 text-ui-micro leading-4 text-slate-500">
            <div>
              source{' '}
              {traceForDisplay.sourceIdentity.continuityGeneration || '未知'}/
              {traceForDisplay.sourceIdentity.sourceTimeMs || '未知'}/
              {traceForDisplay.sourceIdentity.tickOrdinal || '未知'}
            </div>
            <div>
              policy {traceForDisplay.sourceIdentity.policyVersion || '未知'} ·
              feature{' '}
              {traceForDisplay.sourceIdentity.featureSchemaVersion || '未知'} ·
              profile {traceForDisplay.sourceIdentity.profileVersion || '未知'}
            </div>
          </div>
          {linkGroups.length > 0 && (
            <section
              aria-label="追溯关联 ID"
              className="border border-white/[0.06] p-2 text-ui-micro"
            >
              <div className="mb-2 font-black text-slate-400">关联 ID</div>
              <div className="space-y-2">
                {linkGroups.map(group => (
                  <div key={group.key}>
                    <div className="text-slate-600">{group.label}</div>
                    <ul className="mt-1 flex flex-wrap gap-1">
                      {group.ids.map(id => (
                        <li key={`${group.key}:${id}`}>
                          <code className="break-all border border-white/[0.06] px-1 py-0.5 text-slate-300">
                            {id}
                          </code>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          )}
          {traceForDisplay.missingReasons.length > 0 && (
            <ul className="space-y-1.5 text-ui-micro">
              {traceForDisplay.missingReasons.map(reason => (
                <li
                  key={`${reason.stage}:${reason.code}`}
                  className={cn(
                    'border px-2 py-1.5 leading-4',
                    reason.expected
                      ? 'border-amber-400/15 text-amber-100'
                      : 'border-rose-400/25 text-rose-100'
                  )}
                >
                  <span className="font-black">
                    {reason.expected ? '尚未发生' : '异常缺失'} ·{' '}
                    {traceStageLabels[reason.stage] || reason.stage}
                  </span>
                  <span className="block text-slate-500">{reason.detail}</span>
                </li>
              ))}
            </ul>
          )}
          <ol className="space-y-2 border-l border-cyan-400/20 pl-3">
            {traceForDisplay.events.map(event => {
              const detail = traceDetailText(event.details);
              const relatedIdGroups = traceRelatedIdGroups(event.relatedIds);
              return (
                <li
                  key={`${event.stage}:${event.entityId}:${event.occurredAt}`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2 text-ui-micro">
                    <span className="font-black text-slate-200">
                      {traceStageLabels[event.stage] || event.stage} ·{' '}
                      {event.eventType}
                    </span>
                    <span className="font-mono text-slate-600">
                      {formatTime(event.occurredAt)}
                    </span>
                  </div>
                  <div className="mt-0.5 break-all font-mono text-ui-micro text-slate-600">
                    {event.entityId}
                    {event.status ? ` · ${event.status}` : ''}
                  </div>
                  {detail && (
                    <div className="mt-1 break-words text-ui-micro leading-4 text-slate-500">
                      {detail}
                    </div>
                  )}
                  {relatedIdGroups.length > 0 && (
                    <div className="mt-1 space-y-0.5 text-ui-micro text-slate-500">
                      <span className="font-bold text-slate-600">
                        关联 ID：
                      </span>
                      {relatedIdGroups.map(group => (
                        <div key={group.key} className="break-all">
                          {group.key} · {group.ids.join(' · ')}
                        </div>
                      ))}
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      )}
    </section>
  );
}

function TTradeSignalDetails({
  accountId,
  actionLoading,
  canApproveAccount,
  candidateTrace,
  candidateTraceError,
  candidateTraceLoading,
  dataTrusted,
  instrumentName,
  isLiveAuto,
  onApprove,
  onReject,
  onRequestCandidateTrace,
  selectedTrace,
  session,
  signal,
}: {
  accountId: string;
  actionLoading: boolean;
  canApproveAccount: boolean;
  candidateTrace?: CandidateTraceLike | null;
  candidateTraceError?: string;
  candidateTraceLoading: boolean;
  dataTrusted: boolean;
  instrumentName?: string;
  isLiveAuto: boolean;
  onApprove: (session: MonitorSession, snapshot: SignalSnapshot) => void;
  onReject: (session: MonitorSession, snapshot: SignalSnapshot) => void;
  onRequestCandidateTrace?: (selection: CandidateTraceSelection | null) => void;
  selectedTrace?: CandidateTraceSelection | null;
  session?: MonitorSession;
  signal: SignalEvaluationLike;
}) {
  const snapshot = signal.signalSnapshot;
  const pending = Boolean(
    session &&
    snapshot?.candidateStatus === 'AWAITING_APPROVAL' &&
    snapshot.pendingEntryIntentId
  );
  const compatible = snapshot ? isKnownSignalSnapshot(snapshot) : false;
  const approveAllowed = Boolean(
    snapshot && dataTrusted && canApproveAccount && canApproveSnapshot(snapshot)
  );
  const traceMatches = Boolean(
    selectedTrace &&
    snapshot?.candidateId &&
    selectedTrace.accountId === signal.accountId &&
    selectedTrace.strategyRunId === signal.runId &&
    selectedTrace.candidateId === snapshot.candidateId
  );

  return (
    <div className="space-y-3 border-t border-white/[0.06] bg-[#0a1727] p-ui-section">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-ui-label font-black text-slate-100">
            {instrumentName || signal.stockCode} ·{' '}
            {signalEventLabels[signal.eventType] || signal.eventType}
          </h3>
          <p className="mt-1 font-mono text-ui-micro text-slate-600">
            {signal.stockCode} · {signal.runId} ·{' '}
            {formatTime(signal.evaluatedAt)}
          </p>
        </div>
        <span
          className={cn(
            'border px-2 py-1 text-ui-micro font-black',
            pending
              ? 'border-amber-400/25 bg-amber-400/[0.06] text-amber-200'
              : signalEventTone(signal.eventType)
          )}
        >
          {pending
            ? isLiveAuto
              ? 'LIVE 降级待确认'
              : '等待人工确认'
            : candidateStatusLabels[snapshot?.candidateStatus || ''] ||
              '状态未提供'}
        </span>
      </header>

      {isLiveAuto && !pending && (
        <div className="grid grid-cols-4 border border-cyan-400/15 bg-cyan-400/[0.035] text-ui-micro">
          {[
            ['自动重验', '已通过'],
            ['订单风控', '已校验'],
            ['委托状态', session?.entryOrderStatus || '等待执行事实'],
            ['退出方式', '成交后自动退出'],
          ].map(([label, value]) => (
            <div
              key={label}
              className="border-r border-cyan-400/10 px-3 py-2 last:border-r-0"
            >
              <div className="text-cyan-300">{label}</div>
              <div className="mt-1 font-bold text-cyan-100">{value}</div>
            </div>
          ))}
        </div>
      )}

      {pending && (
        <div className="flex items-start gap-2 border border-amber-400/20 bg-amber-400/[0.05] px-3 py-2 text-ui-micro leading-4 text-amber-100">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {isLiveAuto
            ? '正式 LIVE 的自动执行授权或账户事实发生变化，本信号已安全降级；确认时仍会重新校验行情、资金、数量和风险。'
            : 'CANARY 仅允许逐笔人工确认；确认时会重新校验行情、资金、数量和风险。'}
        </div>
      )}

      {!snapshot ? (
        <div className="border border-amber-400/20 bg-amber-400/[0.04] p-3 text-ui-caption leading-5 text-amber-100">
          该信号记录没有可展示的机会快照；仍保留事件身份用于审计。
        </div>
      ) : (
        <>
          <TTradeSignalEvidence snapshot={snapshot} />

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-white/[0.05] pt-3">
            {snapshot.candidateId &&
              signal.accountId === accountId &&
              onRequestCandidateTrace && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  aria-label={`追溯候选 ${snapshot.candidateId}（账户 ${signal.accountId}，运行 ${signal.runId}）`}
                  className="h-control-compact rounded-sm border-blue-400/25 text-ui-caption text-blue-200"
                  onClick={() =>
                    onRequestCandidateTrace({
                      accountId: signal.accountId,
                      strategyRunId: signal.runId,
                      candidateId: snapshot.candidateId!,
                    })
                  }
                >
                  <Link2 className="mr-1.5 h-3.5 w-3.5" />
                  查看全链路
                </Button>
              )}
            {pending && session && (
              <>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-control-compact rounded-sm text-ui-caption text-slate-500"
                  disabled={actionLoading}
                  onClick={() => onReject(session, snapshot)}
                >
                  <X className="mr-1.5 h-3.5 w-3.5" />
                  忽略本次
                </Button>
                <Button
                  type="button"
                  size="sm"
                  className="h-control-compact rounded-sm bg-market-buy-cta text-ui-caption text-white hover:bg-market-buy-cta/90"
                  disabled={actionLoading || !approveAllowed}
                  onClick={() => onApprove(session, snapshot)}
                >
                  <Check className="mr-1.5 h-3.5 w-3.5" />
                  确认并提交
                </Button>
              </>
            )}
          </div>

          {pending && (!compatible || !approveAllowed) && (
            <div role="status" className="text-ui-micro text-amber-200">
              {!dataTrusted
                ? '当前连接尚未恢复可信快照，已禁用确认。'
                : !canApproveAccount
                  ? '当前会话无确认权限，已禁用确认。'
                  : !compatible
                    ? '版本不兼容或未知枚举，已保守禁用确认。'
                    : '候选身份、状态版本或 TTL 无效，已禁用确认。'}
            </div>
          )}
        </>
      )}

      {traceMatches && selectedTrace && (
        <CandidateTracePanel
          accountId={selectedTrace.accountId}
          candidateId={selectedTrace.candidateId}
          error={candidateTraceError}
          loading={candidateTraceLoading}
          onClose={() => onRequestCandidateTrace?.(null)}
          strategyRunId={selectedTrace.strategyRunId}
          trace={candidateTrace}
        />
      )}
    </div>
  );
}

export function TTradeSignalsView({
  accountId,
  actionLoading,
  canApproveAccount,
  candidateTrace,
  candidateTraceError,
  candidateTraceLoading = false,
  dataTrusted,
  evaluations,
  evaluationsError,
  focusStockCode,
  hasMoreEvaluations,
  loadingEvaluations,
  monitorError,
  monitor,
  onApprove,
  onFocusHandled,
  onLoadMoreEvaluations,
  onRequestCandidateTrace,
  onReject,
  selectedTrace,
}: {
  accountId: string;
  actionLoading: boolean;
  canApproveAccount: boolean;
  candidateTrace?: CandidateTraceLike | null;
  candidateTraceError?: string;
  candidateTraceLoading?: boolean;
  dataTrusted: boolean;
  evaluations: readonly SignalEvaluationLike[];
  evaluationsError?: string | null;
  focusStockCode?: string | null;
  hasMoreEvaluations: boolean;
  loadingEvaluations: boolean;
  monitorError?: string | null;
  monitor?: TTradeMonitorLike;
  onApprove: (session: MonitorSession, snapshot: SignalSnapshot) => void;
  onFocusHandled?: () => void;
  onLoadMoreEvaluations: () => void;
  onRequestCandidateTrace?: (selection: CandidateTraceSelection | null) => void;
  onReject: (session: MonitorSession, snapshot: SignalSnapshot) => void;
  selectedTrace?: CandidateTraceSelection | null;
}) {
  const pending = (monitor?.sessions || []).flatMap(session => {
    const snapshot = session.signalSnapshot;
    return snapshot?.candidateStatus === 'AWAITING_APPROVAL' &&
      snapshot.pendingEntryIntentId
      ? [{ session, snapshot }]
      : [];
  });
  const signals = React.useMemo(
    () =>
      evaluations.filter(
        item =>
          item.eventKind === 'MATERIAL' && signalEventTypes.has(item.eventType)
      ),
    [evaluations]
  );
  const instrumentNames = React.useMemo(
    () =>
      new Map(
        (monitor?.holdings || []).map(holding => [
          holding.stockCode,
          holding.instrumentName,
        ])
      ),
    [monitor?.holdings]
  );
  const sessionsByIdentity = React.useMemo(
    () =>
      new Map(
        (monitor?.sessions || []).map(session => [
          `${session.runId}:${session.stockCode}`,
          session,
        ])
      ),
    [monitor?.sessions]
  );
  const [expandedSignalId, setExpandedSignalId] = React.useState<string | null>(
    null
  );
  const rolloutStage = String(monitor?.rolloutStage || 'SHADOW').toUpperCase();
  const isLiveAuto = rolloutStage === 'LIVE';

  React.useEffect(() => {
    if (!focusStockCode) return;
    const focused = signals.find(item => item.stockCode === focusStockCode);
    if (focused) setExpandedSignalId(focused.id);
    onFocusHandled?.();
  }, [focusStockCode, onFocusHandled, signals]);

  React.useEffect(() => {
    if (
      expandedSignalId &&
      !signals.some(item => item.id === expandedSignalId)
    ) {
      setExpandedSignalId(null);
    }
  }, [expandedSignalId, signals]);

  React.useEffect(() => {
    if (!expandedSignalId) return;
    const collapseOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpandedSignalId(null);
    };
    window.addEventListener('keydown', collapseOnEscape);
    return () => window.removeEventListener('keydown', collapseOnEscape);
  }, [expandedSignalId]);

  const toggleSignal = React.useCallback(
    (signal: SignalEvaluationLike) => {
      setExpandedSignalId(current =>
        current === signal.id ? null : signal.id
      );
      const candidateId = signal.signalSnapshot?.candidateId;
      if (
        selectedTrace &&
        (selectedTrace.accountId !== signal.accountId ||
          selectedTrace.strategyRunId !== signal.runId ||
          selectedTrace.candidateId !== candidateId)
      ) {
        onRequestCandidateTrace?.(null);
      }
    },
    [onRequestCandidateTrace, selectedTrace]
  );

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.05] px-ui-section py-3">
        <div>
          <h2 className="text-ui-body font-bold text-slate-100">机会信号</h2>
          <p className="mt-0.5 text-ui-caption text-slate-600">
            仅展示候选生命周期、形态迁移和意图关联等真实信号事实
          </p>
        </div>
        <div className="flex items-center gap-2 text-ui-caption">
          <span
            className={cn(
              'border px-2 py-1 font-black',
              isLiveAuto
                ? 'border-cyan-400/30 bg-cyan-400/[0.07] text-cyan-200'
                : rolloutStage === 'CANARY'
                  ? 'border-amber-400/30 bg-amber-400/[0.07] text-amber-200'
                  : 'border-white/10 bg-white/[0.03] text-slate-500'
            )}
          >
            {isLiveAuto
              ? 'LIVE · 自动执行'
              : rolloutStage === 'CANARY'
                ? 'CANARY · 人工确认'
                : `${rolloutStage} · 新买入关闭`}
          </span>
          <span className="border border-blue-400/20 bg-blue-400/[0.05] px-2 py-1 font-bold text-blue-200">
            信号 {signals.length}
          </span>
          <span className="border border-amber-400/20 bg-amber-400/[0.06] px-2 py-1 font-bold text-amber-200">
            待确认 {pending.length}
          </span>
        </div>
      </header>

      {!dataTrusted && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.07] px-ui-section py-2.5 text-ui-caption leading-4 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          正在显示最后一个可信快照；查询失败或订阅未连接，数据可能已过期，确认买入已禁用。
        </div>
      )}

      {monitorError && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-ui-section py-2.5 text-ui-caption leading-4 text-rose-100"
        >
          <ShieldAlert
            className="mt-0.5 h-3.5 w-3.5 shrink-0"
            aria-hidden="true"
          />
          账户监控服务返回异常：{monitorError}；确认买入仍由服务端门禁重新校验。
        </div>
      )}

      {evaluationsError && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-ui-section py-2.5 text-ui-caption leading-4 text-rose-100"
        >
          <ShieldAlert
            className="mt-0.5 h-3.5 w-3.5 shrink-0"
            aria-hidden="true"
          />
          真实信号读取失败；
          {evaluations.length > 0
            ? '当前仍显示上次成功读取的信号。'
            : '当前没有可展示的信号记录。'}
        </div>
      )}
      {!evaluationsError && loadingEvaluations && evaluations.length > 0 && (
        <div
          role="status"
          aria-busy="true"
          className="flex shrink-0 items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-ui-section py-2 text-ui-micro text-cyan-100"
        >
          <Loader2
            className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在刷新真实信号，暂保留上次结果…
        </div>
      )}

      {pending.length > 0 && signals.length === 0 && (
        <section
          className="shrink-0 border-b border-amber-400/15 bg-amber-400/[0.025] p-ui-section"
          aria-labelledby="pending-opportunity-title"
        >
          <h3
            id="pending-opportunity-title"
            className="mb-3 flex items-center gap-2 text-ui-label font-black text-amber-100"
          >
            <Activity className="h-4 w-4" />
            待确认信号（评估列表尚未同步）
          </h3>
          <div className="grid gap-2 xl:grid-cols-2">
            {pending.map(({ session, snapshot }) => {
              const compatible = isKnownSignalSnapshot(snapshot);
              const approveAllowed =
                dataTrusted &&
                canApproveAccount &&
                canApproveSnapshot(snapshot);
              return (
                <article
                  key={snapshot.candidateId || session.runId}
                  className="border border-white/[0.07] bg-[#0b1628] p-ui-section"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-ui-body font-black text-slate-100">
                        {snapshot.instrumentCode}
                      </div>
                      <div className="mt-1 text-ui-micro text-slate-600">
                        {snapshot.selectedPath || '未选择路径'} · 源时间{' '}
                        {formatTime(snapshot.sourceAt)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-ui-heading font-black text-amber-200">
                        {nullableScore(snapshot.opportunityScore)} /{' '}
                        {formatNumber(snapshot.candidateThreshold, 1)}
                      </div>
                      <div className="text-ui-micro text-slate-600">
                        规则机会分 / 候选阈值
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-ui-micro sm:grid-cols-4">
                    <div className="border border-white/[0.05] p-2">
                      <span className="text-slate-600">数据健康</span>
                      <div className="mt-1 text-slate-200">
                        {snapshot.dataHealth}
                      </div>
                    </div>
                    <div className="border border-white/[0.05] p-2">
                      <span className="text-slate-600">确认重验线</span>
                      <div className="mt-1 font-mono text-slate-200">
                        {formatNumber(snapshot.revalidateThreshold, 1)}
                      </div>
                    </div>
                    <div className="border border-white/[0.05] p-2">
                      <span className="text-slate-600">计划金额</span>
                      <div className="mt-1 font-mono text-slate-200">
                        {session.plannedEntryAmount == null
                          ? '不可计算'
                          : `¥${formatNumber(session.plannedEntryAmount, 0)}`}
                      </div>
                    </div>
                    <div className="border border-white/[0.05] p-2">
                      <span className="text-slate-600">候选截止</span>
                      <div className="mt-1 text-slate-200">
                        {formatTime(snapshot.candidateExpiresAt)}
                      </div>
                    </div>
                  </div>
                  {(!compatible || !approveAllowed) && (
                    <div
                      role="status"
                      className="mt-3 flex items-start gap-2 text-ui-micro leading-4 text-amber-200"
                    >
                      <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {!dataTrusted
                        ? '当前连接尚未恢复可信快照，已禁用确认。'
                        : !canApproveAccount
                          ? '当前会话无确认权限，已禁用确认。'
                          : !compatible
                            ? '版本不兼容或未知枚举，已保守禁用确认。'
                            : '候选身份、状态版本或 TTL 无效，已禁用确认；提交时由服务端重新校验交易资格。'}
                    </div>
                  )}
                  <div className="mt-3 flex justify-end gap-2 border-t border-white/[0.05] pt-3">
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-control-compact rounded-sm text-ui-caption text-slate-500"
                      disabled={actionLoading}
                      onClick={() => onReject(session, snapshot)}
                    >
                      <X className="mr-1.5 h-3.5 w-3.5" />
                      忽略本次
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="h-control-compact rounded-sm bg-market-buy-cta text-ui-caption text-white hover:bg-market-buy-cta/90"
                      disabled={actionLoading || !approveAllowed}
                      onClick={() => onApprove(session, snapshot)}
                    >
                      <Check className="mr-1.5 h-3.5 w-3.5" />
                      确认买入
                    </Button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      )}

      <div className="flex min-h-0 flex-1">
        <section
          className="flex min-h-[360px] min-w-0 flex-1 flex-col"
          aria-label="真实信号列表"
        >
          <div className="shrink-0 border-b border-white/[0.05] px-ui-section py-2 text-ui-caption text-slate-500">
            真实信号来自持久化 opportunity evaluation，不包含普通持仓监控行
          </div>
          <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
            <div style={{ minWidth: 920 }}>
              <div
                className="sticky top-0 z-10 grid h-8 items-center gap-3 border-b border-white/[0.05] bg-[#0b1628] px-ui-section text-ui-micro font-black text-slate-600"
                style={{
                  gridTemplateColumns:
                    '28px minmax(140px, 1fr) minmax(130px, .85fr) 100px minmax(180px, 1.2fr) 120px 24px',
                }}
              >
                <span />
                <span>信号 / 标的</span>
                <span>状态 / 路径</span>
                <span>机会分</span>
                <span>首要阻断</span>
                <span>源时间</span>
                <span />
              </div>
              {loadingEvaluations && evaluations.length === 0 && (
                <div
                  role="status"
                  aria-busy="true"
                  className="flex h-full min-h-64 items-center justify-center text-ui-label text-slate-600"
                >
                  <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
                  读取真实信号…
                </div>
              )}
              {!loadingEvaluations && signals.length === 0 && (
                <div className="flex h-full min-h-64 flex-col items-center justify-center px-ui-empty text-center">
                  <Database className="h-9 w-9 text-slate-800" />
                  <div className="mt-3 text-ui-body font-bold text-slate-400">
                    暂无真实信号
                  </div>
                  <p className="mt-1 max-w-md text-ui-caption leading-5 text-slate-600">
                    当前没有候选、形态迁移或意图关联记录。持仓标的请在“总览”或“仓位与批次”中查看。
                  </p>
                </div>
              )}
              {signals.map(signal => {
                const snapshot = signal.signalSnapshot;
                const expanded = expandedSignalId === signal.id;
                const name = instrumentNames.get(signal.stockCode);
                const session = sessionsByIdentity.get(
                  `${signal.runId}:${signal.stockCode}`
                );
                const path = snapshot?.selectedPath
                  ? signalPathLabels[snapshot.selectedPath] ||
                    snapshot.selectedPath
                  : signalPhaseLabels[snapshot?.dominantPhase || ''] ||
                    '未选择路径';
                const blocker = snapshot?.topBlockers[0]?.label;
                return (
                  <article
                    key={signal.id}
                    className={cn(
                      'border-b border-white/[0.05] bg-[#091422]',
                      expanded && 'border border-blue-400/25 bg-[#0a1727]'
                    )}
                  >
                    <button
                      type="button"
                      aria-label={`查看信号 ${name || signal.stockCode} ${signalEventLabels[signal.eventType] || signal.eventType}`}
                      aria-expanded={expanded}
                      aria-controls={`signal-detail-${signal.id}`}
                      onClick={() => toggleSignal(signal)}
                      className="grid min-h-12 w-full cursor-pointer items-center gap-3 px-ui-section py-2 text-left text-ui-caption transition-colors hover:bg-blue-500/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
                      style={{
                        gridTemplateColumns:
                          '28px minmax(140px, 1fr) minmax(130px, .85fr) 100px minmax(180px, 1.2fr) 120px 24px',
                      }}
                    >
                      <span className="text-slate-600">
                        {expanded ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate font-bold text-slate-200">
                          {signalEventLabels[signal.eventType] ||
                            signal.eventType}
                        </span>
                        <span className="mt-0.5 block truncate font-mono text-ui-micro text-slate-600">
                          {name ? `${name} · ` : ''}
                          {signal.stockCode}
                        </span>
                      </span>
                      <span className="min-w-0">
                        <span
                          className={cn(
                            'inline-flex border px-1.5 py-0.5 font-bold',
                            signalEventTone(signal.eventType)
                          )}
                        >
                          {snapshot?.candidateStatus === 'AWAITING_APPROVAL' &&
                          isLiveAuto
                            ? '降级待确认'
                            : candidateStatusLabels[
                                snapshot?.candidateStatus || ''
                              ] || '状态未提供'}
                        </span>
                        <span className="mt-1 block truncate text-slate-500">
                          {path}
                        </span>
                      </span>
                      <span className="font-mono text-slate-300">
                        {nullableScore(snapshot?.opportunityScore)}
                        <span className="block text-ui-micro text-slate-600">
                          阈值 {nullableScore(snapshot?.candidateThreshold)}
                        </span>
                      </span>
                      <span
                        className={
                          blocker ? 'text-amber-100' : 'text-slate-600'
                        }
                      >
                        {blocker || '无首要阻断'}
                      </span>
                      <span className="font-mono text-ui-micro text-slate-600">
                        {formatTime(snapshot?.sourceAt || signal.evaluatedAt)}
                        {signal.coalescedCount > 1 && (
                          <span className="mt-0.5 block">
                            合并 ×{signal.coalescedCount}
                          </span>
                        )}
                      </span>
                      <span className="text-slate-600">›</span>
                    </button>
                    {expanded && (
                      <div id={`signal-detail-${signal.id}`}>
                        <TTradeSignalDetails
                          accountId={accountId}
                          actionLoading={actionLoading}
                          canApproveAccount={canApproveAccount}
                          candidateTrace={candidateTrace}
                          candidateTraceError={candidateTraceError}
                          candidateTraceLoading={candidateTraceLoading}
                          dataTrusted={dataTrusted}
                          instrumentName={name}
                          isLiveAuto={isLiveAuto}
                          onApprove={onApprove}
                          onReject={onReject}
                          onRequestCandidateTrace={onRequestCandidateTrace}
                          selectedTrace={selectedTrace}
                          session={session}
                          signal={signal}
                        />
                      </div>
                    )}
                  </article>
                );
              })}
              {hasMoreEvaluations && (
                <div className="p-ui-section">
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-control-compact w-full text-ui-caption text-slate-400"
                    disabled={loadingEvaluations}
                    onClick={onLoadMoreEvaluations}
                  >
                    {loadingEvaluations ? '加载中…' : '加载更多信号'}
                  </Button>
                </div>
              )}
            </div>
          </div>
        </section>
      </div>
      <div className="sr-only" aria-live="polite">
        真实信号已刷新，共 {signals.length} 条，待确认 {pending.length} 个
      </div>
    </div>
  );
}
