import {
  Activity,
  ArrowRight,
  Check,
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
  type SignalEvaluationLike,
  type TTradeMonitorLike,
} from './TTradeLiveMonitor';
import { formatNumber, formatTime } from './utils';

function nullableScore(value?: number | null) {
  return value == null || !Number.isFinite(value)
    ? '不可计算'
    : formatNumber(value, 1);
}

const signalEventTypes = new Set([
  'FSM_TRANSITION',
  'CANDIDATE_LATCHED',
  'CANDIDATE_AWAITING_APPROVAL',
  'CANDIDATE_SUPPRESSED',
  'CANDIDATE_REARMING',
  'CANDIDATE_CLEARED',
  'CANDIDATE_STATE_CHANGED',
  'INTENT_LINKED',
]);

const signalEventLabels: Readonly<Record<string, string>> = {
  FSM_TRANSITION: '形态状态迁移',
  CANDIDATE_LATCHED: '候选已锁存',
  CANDIDATE_AWAITING_APPROVAL: '候选等待确认',
  CANDIDATE_SUPPRESSED: '候选已抑制',
  CANDIDATE_REARMING: '候选等待再武装',
  CANDIDATE_CLEARED: '候选已清除',
  CANDIDATE_STATE_CHANGED: '候选状态变更',
  INTENT_LINKED: '交易意图已关联',
};

const candidateStatusLabels: Readonly<Record<string, string>> = {
  NONE: '无候选',
  LATCHED: '候选已锁存',
  AWAITING_APPROVAL: '等待人工确认',
  SUPPRESSED: '候选已抑制',
  REARMING: '等待再武装',
};

const signalPathLabels: Readonly<Record<string, string>> = {
  PULLBACK_REBOUND: '回撤反弹',
  MOMENTUM_ACCELERATION: '早期动量',
};

const signalPhaseLabels: Readonly<Record<string, string>> = {
  NONE: '暂无主导形态',
  OBSERVING: '观察中',
  PULLBACK_FORMING: '回撤形成',
  LOW_STABILIZING: '低点企稳',
  REBOUND_CONFIRMING: '反弹确认',
  BASELINING: '建立基线',
  MOMENTUM_BUILDING: '动量形成',
  ACCELERATING: '加速确认',
  OVEREXTENDED: '过度延伸',
  CANDIDATE_LATCHED: '候选锁存',
  SUPPRESSED: '已抑制',
  PULLBACK_OBSERVING: '回撤 · 观察',
  PULLBACK_LOW_STABILIZING: '回撤 · 低点企稳',
  PULLBACK_REBOUND_CONFIRMING: '回撤 · 反弹确认',
  PULLBACK_CANDIDATE_LATCHED: '回撤 · 候选锁存',
  PULLBACK_SUPPRESSED: '回撤 · 已抑制',
  MOMENTUM_OBSERVING: '动量 · 观察',
  MOMENTUM_BASELINING: '动量 · 建立基线',
  MOMENTUM_ACCELERATING: '动量 · 加速确认',
  MOMENTUM_OVEREXTENDED: '动量 · 过度延伸',
  MOMENTUM_CANDIDATE_LATCHED: '动量 · 候选锁存',
  MOMENTUM_SUPPRESSED: '动量 · 已抑制',
};

function signalEventTone(eventType: string) {
  if (eventType === 'CANDIDATE_SUPPRESSED') {
    return 'border-rose-400/25 bg-rose-400/[0.05] text-rose-200';
  }
  if (
    eventType === 'CANDIDATE_AWAITING_APPROVAL' ||
    eventType === 'CANDIDATE_REARMING'
  ) {
    return 'border-amber-400/25 bg-amber-400/[0.05] text-amber-200';
  }
  if (eventType === 'INTENT_LINKED') {
    return 'border-emerald-400/25 bg-emerald-400/[0.05] text-emerald-200';
  }
  return 'border-blue-400/25 bg-blue-400/[0.05] text-blue-200';
}

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
  const [selectedSignalId, setSelectedSignalId] = React.useState('');
  const selectedSignal =
    signals.find(item => item.id === selectedSignalId) || signals[0];
  const selectedSnapshot = selectedSignal?.signalSnapshot;
  const selectedTraceMatchesSignal = Boolean(
    selectedTrace &&
    selectedSnapshot?.candidateId &&
    selectedTrace.accountId === selectedSignal?.accountId &&
    selectedTrace.strategyRunId === selectedSignal?.runId &&
    selectedTrace.candidateId === selectedSnapshot.candidateId
  );

  React.useEffect(() => {
    if (!focusStockCode) return;
    const focused = signals.find(item => item.stockCode === focusStockCode);
    if (focused) setSelectedSignalId(focused.id);
    onFocusHandled?.();
  }, [focusStockCode, onFocusHandled, signals]);

  const selectSignal = React.useCallback(
    (signal: SignalEvaluationLike) => {
      setSelectedSignalId(signal.id);
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

      {pending.length > 0 && (
        <section
          className="shrink-0 border-b border-amber-400/15 bg-amber-400/[0.025] p-ui-section"
          aria-labelledby="pending-opportunity-title"
        >
          <h3
            id="pending-opportunity-title"
            className="mb-3 flex items-center gap-2 text-ui-label font-black text-amber-100"
          >
            <Activity className="h-4 w-4" />
            等待人工确认
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

      <div className="grid min-h-0 flex-1 xl:grid-cols-3">
        <section
          className="flex min-h-[360px] min-w-0 flex-col border-b border-white/[0.05] xl:col-span-2 xl:min-h-0 xl:border-b-0 xl:border-r"
          aria-label="真实信号列表"
        >
          <div className="shrink-0 border-b border-white/[0.05] px-ui-section py-2 text-ui-caption text-slate-500">
            真实信号来自持久化 opportunity evaluation，不包含普通持仓监控行
          </div>
          <div className="hidden shrink-0 grid-cols-5 gap-3 border-b border-white/[0.05] bg-white/[0.015] px-ui-section py-2 text-ui-caption font-bold text-slate-600 lg:grid">
            <span>信号 / 标的</span>
            <span>状态 / 路径</span>
            <span>机会分</span>
            <span>首要阻断</span>
            <span>源时间</span>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
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
                  当前没有候选、形态迁移或意图关联记录。持仓标的请在“总览”或“做T仓位”中查看。
                </p>
              </div>
            )}
            {signals.map(signal => {
              const snapshot = signal.signalSnapshot;
              const active = selectedSignal?.id === signal.id;
              const name = instrumentNames.get(signal.stockCode);
              const path = snapshot?.selectedPath
                ? signalPathLabels[snapshot.selectedPath] || snapshot.selectedPath
                : signalPhaseLabels[snapshot?.dominantPhase || ''] || '未选择路径';
              const blocker = snapshot?.topBlockers[0]?.label;
              return (
                <button
                  key={signal.id}
                  type="button"
                  aria-label={`查看信号 ${name || signal.stockCode} ${signalEventLabels[signal.eventType] || signal.eventType}`}
                  aria-pressed={active}
                  onClick={() => selectSignal(signal)}
                  className={cn(
                    'grid w-full cursor-pointer gap-2 border-b border-white/[0.05] border-l-2 px-ui-section py-2.5 text-left text-ui-caption transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70 lg:grid-cols-5 lg:gap-3',
                    active
                      ? 'border-l-blue-400 bg-blue-500/[0.09]'
                      : 'border-l-transparent hover:bg-blue-500/[0.04]'
                  )}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-bold text-slate-200">
                      {signalEventLabels[signal.eventType] || signal.eventType}
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
                      {candidateStatusLabels[snapshot?.candidateStatus || ''] ||
                        '状态未提供'}
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
                  <span className={blocker ? 'text-amber-100' : 'text-slate-600'}>
                    {blocker || '无首要阻断'}
                  </span>
                  <span className="font-mono text-ui-micro text-slate-600">
                    {formatTime(snapshot?.sourceAt || signal.evaluatedAt)}
                    {signal.coalescedCount > 1 && (
                      <span className="mt-0.5 block">合并 ×{signal.coalescedCount}</span>
                    )}
                  </span>
                </button>
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
        </section>

        <aside
          className="min-h-0 overflow-y-auto p-ui-section custom-scrollbar"
          aria-label="信号详情"
        >
          {!selectedSignal && (
            <div className="flex h-full min-h-64 flex-col items-center justify-center text-center">
              <ArrowRight className="h-8 w-8 text-slate-800" />
              <div className="mt-3 text-ui-label font-bold text-slate-500">
                选择一条信号查看详情
              </div>
              <p className="mt-1 text-ui-caption text-slate-600">
                可查看候选状态、评分、阻断原因与全链路追溯。
              </p>
            </div>
          )}
          {selectedSignal && (
            <div className="space-y-3">
              <header className="border-b border-white/[0.06] pb-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-ui-title font-bold text-slate-100">
                      {instrumentNames.get(selectedSignal.stockCode) ||
                        selectedSignal.stockCode}
                    </h3>
                    <div className="mt-0.5 font-mono text-ui-caption text-slate-600">
                      {selectedSignal.stockCode} · {selectedSignal.runId}
                    </div>
                  </div>
                  <span
                    className={cn(
                      'shrink-0 border px-1.5 py-0.5 text-ui-caption font-bold',
                      signalEventTone(selectedSignal.eventType)
                    )}
                  >
                    {signalEventLabels[selectedSignal.eventType] ||
                      selectedSignal.eventType}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-ui-micro text-slate-600">
                  <span>
                    源时间{' '}
                    {formatTime(
                      selectedSnapshot?.sourceAt || selectedSignal.evaluatedAt
                    )}
                  </span>
                  <span>{selectedSignal.eventType}</span>
                  <span>policy {selectedSignal.policyVersion}</span>
                </div>
              </header>

              {!selectedSnapshot && (
                <div className="border border-amber-400/20 bg-amber-400/[0.04] p-3 text-ui-caption leading-5 text-amber-100">
                  该信号记录没有可展示的机会快照；仍保留事件身份用于审计。
                </div>
              )}
              {selectedSnapshot && (
                <>
                  <div className="grid grid-cols-2 gap-2 text-ui-caption">
                    <div className="border border-white/[0.06] p-2.5">
                      <div className="text-slate-600">候选状态</div>
                      <div className="mt-1 font-bold text-slate-200">
                        {candidateStatusLabels[selectedSnapshot.candidateStatus] ||
                          selectedSnapshot.candidateStatus}
                      </div>
                    </div>
                    <div className="border border-white/[0.06] p-2.5">
                      <div className="text-slate-600">形态 / 路径</div>
                      <div className="mt-1 font-bold text-slate-200">
                        {selectedSnapshot.selectedPath
                          ? signalPathLabels[selectedSnapshot.selectedPath] ||
                            selectedSnapshot.selectedPath
                          : signalPhaseLabels[selectedSnapshot.dominantPhase] ||
                            selectedSnapshot.dominantPhase}
                      </div>
                    </div>
                    <div className="border border-white/[0.06] p-2.5">
                      <div className="text-slate-600">机会分 / 候选阈值</div>
                      <div className="mt-1 font-mono font-bold text-slate-200">
                        {nullableScore(selectedSnapshot.opportunityScore)} /{' '}
                        {nullableScore(selectedSnapshot.candidateThreshold)}
                      </div>
                    </div>
                    <div className="border border-white/[0.06] p-2.5">
                      <div className="text-slate-600">数据健康</div>
                      <div className="mt-1 font-bold text-slate-200">
                        {selectedSnapshot.dataHealth}
                      </div>
                    </div>
                  </div>

                  <section className="border border-white/[0.06] p-3">
                    <h4 className="text-ui-caption font-bold text-slate-300">
                      阻断原因
                    </h4>
                    {selectedSnapshot.topBlockers.length === 0 ? (
                      <p className="mt-2 text-ui-caption text-slate-600">
                        当前信号没有首要阻断。
                      </p>
                    ) : (
                      <ul className="mt-2 space-y-2">
                        {selectedSnapshot.topBlockers.map(blocker => (
                          <li
                            key={blocker.code}
                            className="border-l-2 border-amber-400/50 pl-2 text-ui-caption"
                          >
                            <div className="font-bold text-amber-100">
                              {blocker.label}
                            </div>
                            <div className="mt-0.5 leading-4 text-slate-600">
                              {blocker.detail || blocker.code}
                            </div>
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>

                  {selectedSnapshot.scoreContributions.length > 0 && (
                    <section className="border border-white/[0.06] p-3">
                      <h4 className="text-ui-caption font-bold text-slate-300">
                        评分贡献
                      </h4>
                      <ul className="mt-2 space-y-1.5 text-ui-caption">
                        {selectedSnapshot.scoreContributions
                          .slice(0, 6)
                          .map(contribution => (
                            <li
                              key={contribution.code}
                              className="flex items-center justify-between gap-3"
                            >
                              <span className="truncate text-slate-500">
                                {contribution.label}
                              </span>
                              <span className="shrink-0 font-mono text-slate-300">
                                {formatNumber(contribution.points, 1)} /{' '}
                                {formatNumber(contribution.maxPoints, 1)}
                              </span>
                            </li>
                          ))}
                      </ul>
                    </section>
                  )}

                  {selectedSnapshot.candidateId &&
                    selectedSignal.accountId === accountId &&
                    onRequestCandidateTrace && (
                      <button
                        type="button"
                        aria-label={`追溯候选 ${selectedSnapshot.candidateId}（账户 ${selectedSignal.accountId}，运行 ${selectedSignal.runId}）`}
                        className="flex h-control-compact w-full cursor-pointer items-center justify-center gap-1.5 border border-blue-400/25 bg-blue-400/[0.05] px-3 text-ui-caption font-bold text-blue-200 transition-colors hover:bg-blue-400/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                        onClick={() =>
                          onRequestCandidateTrace({
                            accountId: selectedSignal.accountId,
                            strategyRunId: selectedSignal.runId,
                            candidateId: selectedSnapshot.candidateId!,
                          })
                        }
                      >
                        <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
                        查看候选全链路追溯
                      </button>
                    )}
                </>
              )}

              {selectedTraceMatchesSignal && selectedTrace && (
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
          )}
        </aside>
      </div>
      <div className="sr-only" aria-live="polite">
        真实信号已刷新，共 {signals.length} 条，待确认 {pending.length} 个
      </div>
    </div>
  );
}
