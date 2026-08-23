import {
  Activity,
  Check,
  Clock3,
  History,
  Link2,
  Loader2,
  Network,
  ShieldAlert,
  X,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { cn } from '@/utils/cn';

import type { LiveMarketQuote } from '../../hooks/useRealTimeHoldings';

import {
  hasCandidateTraceIdentity,
  traceRelatedIdGroups,
} from './clientTrust';
import {
  canApproveSnapshot,
  isKnownSignalSnapshot,
  type MonitorSession,
  type SignalSnapshot,
} from './monitoring';
import {
  TTradeLiveBoard,
  type SignalEvaluationLike,
  type TTradeMonitorLike,
} from './TTradeLiveMonitor';
import type { QuoteHistoryByCode } from './useLiveQuoteHistory';
import { formatNumber, formatTime } from './utils';

function nullableScore(value?: number | null) {
  return value == null || !Number.isFinite(value)
    ? '不可计算'
    : formatNumber(value, 1);
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
  const traceMatchesSelection = hasCandidateTraceIdentity(
    trace,
    traceIdentity
  );
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
  const linkCount = linkGroups.reduce((total, group) => total + group.ids.length, 0);

  return (
    <section
      aria-label={`账户 ${accountId}，运行 ${strategyRunId}，候选 ${candidateId} 全链路追溯`}
      aria-live="polite"
      aria-busy={loading}
      className="mb-3 border border-cyan-400/20 bg-cyan-400/[0.035] p-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="flex items-center gap-2 text-[10px] font-black text-cyan-100">
            <Network className="h-3.5 w-3.5" aria-hidden="true" />
            候选全链路追溯
          </h4>
          <div className="mt-1 break-all font-mono text-[8px] text-slate-600">
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
          className="flex items-center py-5 text-[9px] text-slate-500"
        >
          <Loader2
            aria-hidden="true"
            className="mr-2 h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
          />
          从持久化真源读取时间线…
        </div>
      )}
      {!loading && traceError && (
        <div role="alert" className="mt-3 text-[9px] leading-4 text-rose-200">
          追溯读取失败：{traceError}
        </div>
      )}
      {!loading && !traceError && !traceForDisplay && (
        <div className="mt-3 text-[9px] leading-4 text-amber-200">
          当前账户未找到该候选的 MATERIAL 真源记录。
        </div>
      )}
      {!loading && !traceError && traceForDisplay && (
        <div className="mt-3 space-y-3">
          <div role="status" className="sr-only">
            候选 {candidateId} 的追溯已加载
          </div>
          <div className="grid grid-cols-2 gap-2 text-[9px]">
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
          <div className="border border-white/[0.06] p-2 text-[8px] leading-4 text-slate-500">
            <div>
              source {traceForDisplay.sourceIdentity.continuityGeneration || '未知'}/
              {traceForDisplay.sourceIdentity.sourceTimeMs || '未知'}/
              {traceForDisplay.sourceIdentity.tickOrdinal || '未知'}
            </div>
            <div>
              policy {traceForDisplay.sourceIdentity.policyVersion || '未知'} · feature{' '}
              {traceForDisplay.sourceIdentity.featureSchemaVersion || '未知'} · profile{' '}
              {traceForDisplay.sourceIdentity.profileVersion || '未知'}
            </div>
          </div>
          {linkGroups.length > 0 && (
            <section
              aria-label="追溯关联 ID"
              className="border border-white/[0.06] p-2 text-[8px]"
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
            <ul className="space-y-1.5 text-[9px]">
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
                  <div className="flex flex-wrap items-center justify-between gap-2 text-[9px]">
                    <span className="font-black text-slate-200">
                      {traceStageLabels[event.stage] || event.stage} ·{' '}
                      {event.eventType}
                    </span>
                    <span className="font-mono text-slate-600">
                      {formatTime(event.occurredAt)}
                    </span>
                  </div>
                  <div className="mt-0.5 break-all font-mono text-[8px] text-slate-600">
                    {event.entityId}
                    {event.status ? ` · ${event.status}` : ''}
                  </div>
                  {detail && (
                    <div className="mt-1 break-words text-[8px] leading-4 text-slate-500">
                      {detail}
                    </div>
                  )}
                  {relatedIdGroups.length > 0 && (
                    <div className="mt-1 space-y-0.5 text-[8px] text-slate-500">
                      <span className="font-bold text-slate-600">关联 ID：</span>
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
  historyByCode,
  hasMoreEvaluations,
  loadingEvaluations,
  loadingMonitor = false,
  monitorError,
  monitor,
  onApprove,
  onLoadMoreEvaluations,
  onRequestCandidateTrace,
  onReject,
  quotes,
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
  historyByCode?: QuoteHistoryByCode;
  hasMoreEvaluations: boolean;
  loadingEvaluations: boolean;
  loadingMonitor?: boolean;
  monitorError?: string | null;
  monitor?: TTradeMonitorLike;
  onApprove: (session: MonitorSession, snapshot: SignalSnapshot) => void;
  onLoadMoreEvaluations: () => void;
  onRequestCandidateTrace?: (selection: CandidateTraceSelection | null) => void;
  onReject: (session: MonitorSession, snapshot: SignalSnapshot) => void;
  quotes: ReadonlyMap<string, LiveMarketQuote>;
  selectedTrace?: CandidateTraceSelection | null;
}) {
  const pending = (monitor?.sessions || []).flatMap(session => {
    const snapshot = session.signalSnapshot;
    return snapshot?.candidateStatus === 'AWAITING_APPROVAL' &&
      snapshot.pendingEntryIntentId
      ? [{ session, snapshot }]
      : [];
  });

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-white/[0.05] px-4 py-3">
        <div>
          <h2 className="text-sm font-black text-slate-100">服务端信号</h2>
          <p className="mt-0.5 text-[10px] text-slate-600">
            当前快照、候选身份与历史证据均来自 V3 opportunity 真源
          </p>
        </div>
        <span className="border border-amber-400/20 bg-amber-400/[0.06] px-2 py-1 text-[9px] font-black text-amber-200">
          待确认 {pending.length}
        </span>
      </header>

      {!dataTrusted && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.07] px-4 py-2.5 text-[10px] leading-4 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          正在显示最后一个可信快照；查询失败或订阅未连接，数据可能已过期，确认买入已禁用。
        </div>
      )}

      {monitorError && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-4 py-2.5 text-[10px] leading-4 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          账户监控服务返回异常：{monitorError}；确认买入仍由服务端门禁重新校验。
        </div>
      )}

      {evaluationsError && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-4 py-2.5 text-[10px] leading-4 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          MATERIAL 评估证据读取失败；{evaluations.length > 0
            ? '当前仍显示上次成功读取的证据。'
            : '当前没有可展示的历史证据。'}
        </div>
      )}
      {!evaluationsError && loadingEvaluations && evaluations.length > 0 && (
        <div
          role="status"
          aria-busy="true"
          className="flex shrink-0 items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-4 py-2 text-[9px] text-cyan-100"
        >
          <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          正在刷新 MATERIAL 评估证据，暂保留上次结果…
        </div>
      )}

      {pending.length > 0 && (
        <section
          className="shrink-0 border-b border-amber-400/15 bg-amber-400/[0.025] p-4"
          aria-labelledby="pending-opportunity-title"
        >
          <h3
            id="pending-opportunity-title"
            className="mb-3 flex items-center gap-2 text-xs font-black text-amber-100"
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
                  className="border border-white/[0.07] bg-[#0b1628] p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-black text-slate-100">
                        {snapshot.instrumentCode}
                      </div>
                      <div className="mt-1 text-[9px] text-slate-600">
                        {snapshot.selectedPath || '未选择路径'} · 源时间{' '}
                        {formatTime(snapshot.sourceAt)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-mono text-lg font-black text-amber-200">
                        {nullableScore(snapshot.opportunityScore)} /{' '}
                        {formatNumber(snapshot.candidateThreshold, 1)}
                      </div>
                      <div className="text-[9px] text-slate-600">
                        规则机会分 / 候选阈值
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-[9px] sm:grid-cols-4">
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
                      className="mt-3 flex items-start gap-2 text-[9px] leading-4 text-amber-200"
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
                      className="h-8 rounded-sm text-[10px] text-slate-500"
                      disabled={actionLoading}
                      onClick={() => onReject(session, snapshot)}
                    >
                      <X className="mr-1.5 h-3.5 w-3.5" />
                      忽略本次
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      className="h-8 rounded-sm bg-market-buy-cta text-[10px] text-white hover:bg-market-buy-cta/90"
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

      <div className="grid min-h-0 flex-1 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <section
          className="flex min-h-[360px] min-w-0 flex-col border-b border-white/[0.05] xl:min-h-0 xl:border-b-0 xl:border-r"
          aria-label="当前持仓信号"
        >
          <div className="shrink-0 border-b border-white/[0.05] px-4 py-2 text-[9px] font-black uppercase tracking-[0.12em] text-slate-600">
            当前最新快照 · 点击标的查看双 FSM、门禁与贡献
          </div>
          <TTradeLiveBoard
            evaluations={evaluations}
            historyByCode={historyByCode}
            loading={loadingMonitor}
            monitor={monitor}
            quotes={quotes}
          />
        </section>
        <section
          className="min-h-0 overflow-y-auto p-4 custom-scrollbar"
          aria-labelledby="signal-evidence-title"
        >
          <div className="mb-3 flex items-center justify-between gap-2">
            <h3
              id="signal-evidence-title"
              className="flex items-center gap-2 text-xs font-black text-slate-200"
            >
              <History className="h-4 w-4 text-cyan-300" />
              MATERIAL 评估证据
            </h3>
            <span className="font-mono text-[9px] text-slate-600">
              {evaluations.length}
            </span>
          </div>
          {selectedTrace && (
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
          <div className="space-y-2">
            {evaluations.map(item => {
              const candidateId = item.signalSnapshot?.candidateId;
              return (
                <article
                  key={item.id}
                  className="border border-white/[0.06] bg-[#0b1628] p-3 text-[9px]"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-bold text-slate-200">
                        {item.stockCode}
                      </div>
                      <div className="mt-1 font-mono text-slate-600">
                        源时间{' '}
                        {formatTime(
                          item.signalSnapshot?.sourceAt || item.evaluatedAt
                        )}
                      </div>
                    </div>
                    <span
                      className={cn(
                        'border px-1.5 py-0.5 font-black',
                        item.eventKind === 'MATERIAL'
                          ? 'border-cyan-400/20 text-cyan-200'
                          : 'border-white/10 text-slate-500'
                      )}
                    >
                      {item.eventKind}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center justify-between border-t border-white/[0.05] pt-2">
                    <span className="text-slate-500">
                      {item.eventType} · ×{item.coalescedCount}
                    </span>
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-slate-300">
                        {nullableScore(item.signalSnapshot?.opportunityScore)}{' '}
                        分
                      </span>
                      {item.eventKind === 'MATERIAL' &&
                        candidateId &&
                        item.accountId === accountId &&
                        onRequestCandidateTrace && (
                        <button
                          type="button"
                          aria-label={`追溯候选 ${candidateId}（账户 ${item.accountId}，运行 ${item.runId}）`}
                          className="inline-flex items-center gap-1 border border-cyan-400/20 px-1.5 py-0.5 font-black text-cyan-200 hover:bg-cyan-400/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
                          onClick={() =>
                            onRequestCandidateTrace({
                              accountId: item.accountId,
                              strategyRunId: item.runId,
                              candidateId,
                            })
                          }
                        >
                          <Link2 className="h-3 w-3" aria-hidden="true" />
                          追溯
                        </button>
                      )}
                    </div>
                  </div>
                </article>
              );
            })}
            {loadingEvaluations && evaluations.length === 0 && (
              <div className="flex items-center justify-center py-10 text-[10px] text-slate-600">
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
                读取评估证据…
              </div>
            )}
            {!loadingEvaluations && evaluations.length === 0 && (
              <div className="py-10 text-center text-[10px] text-slate-600">
                <Clock3 className="mx-auto mb-2 h-7 w-7 text-slate-800" />
                暂无持久化 MATERIAL 事件
              </div>
            )}
          </div>
          {hasMoreEvaluations && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="mt-3 h-8 w-full text-[10px] text-slate-400"
              disabled={loadingEvaluations}
              onClick={onLoadMoreEvaluations}
            >
              {loadingEvaluations ? '加载中…' : '加载更多评估'}
            </Button>
          )}
        </section>
      </div>
      <div className="sr-only" aria-live="polite">
        服务端信号已刷新，待确认 {pending.length} 个
      </div>
    </div>
  );
}
