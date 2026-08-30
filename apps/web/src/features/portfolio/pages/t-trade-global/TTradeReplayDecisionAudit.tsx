import { ChevronDown, ChevronRight, Link2 } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { mapStrategyDecisionView } from '@/features/strategies/domain';
import { cn } from '@/utils/cn';

import type { ReplayEvidenceController } from '../../hooks/useTTradeReplayEvidence';

import {
  ReplayEvidenceFooter,
  ReplayEvidenceHeader,
  ReplayEvidenceSelect,
  ReplayEvidenceState,
} from './ReplayEvidenceChrome';
import {
  replayIntentTarget,
  replayReasonLabel,
} from './replayEvidencePresentation';
import {
  replayDecisionInstrumentCode,
  replayDecisionReason,
  replayDecisionTraceItems,
} from './replayWorkspace';
import { formatNumber, formatTime } from './utils';

const columns =
  '24px 150px minmax(120px, .8fr) 100px minmax(145px, 1fr) 100px minmax(220px, 1.5fr)';
const statusLabels: Readonly<Record<string, string>> = {
  PENDING: '等待处理',
  CREATED: '意图已生成',
  PENDING_APPROVAL: '等待确认',
  APPROVED: '已确认',
  SUBMITTED: '已提交',
  FILLED: '已成交',
  PARTIALLY_FILLED: '部分成交',
  REJECTED: '已拒绝',
  SUPPRESSED: '已抑制',
  CANCELLED: '已撤销',
  CANCELED: '已撤销',
  EXPIRED: '已过期',
  DELAYED: '风控延后',
  FAILED: '执行失败',
};
const sideLabel = (side: string) =>
  side.toUpperCase() === 'BUY'
    ? '买入'
    : side.toUpperCase() === 'SELL'
      ? '卖出'
      : side;
const valueLabel = (value: unknown) =>
  value == null || value === ''
    ? '未记录'
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value);
const summaryLabels: Readonly<Record<string, string>> = {
  instrument_code: '标的',
  timestamp: '策略时间',
  cadence: '触发来源',
  input_id: '输入 ID',
  trade_intent_count: '意图数量',
  record_kind: '记录类型',
  reason: '决策原因',
  market_context: '行情上下文',
  risk_caps: '风险约束',
  position_profile: '仓位配置',
};

function AuditSummary({
  title,
  values,
}: {
  title: string;
  values: Record<string, unknown>;
}) {
  const entries = Object.entries(values).filter(
    ([key]) => !['evaluation_references', 'format', 'tags'].includes(key)
  );
  return (
    <section className="rounded-panel border border-white/[0.08] p-3">
      <h4 className="text-ui-label font-semibold text-slate-200">{title}</h4>
      <dl className="mt-2 space-y-2 text-ui-caption">
        {entries.map(([key, value]) => (
          <div key={key} className="grid grid-cols-[110px_1fr] gap-3">
            <dt className="text-slate-400">{summaryLabels[key] || key}</dt>
            <dd className="break-all font-mono text-slate-300">
              {valueLabel(value)}
            </dd>
          </div>
        ))}
      </dl>
      {entries.length === 0 && (
        <p className="mt-2 text-ui-caption text-slate-400">没有额外摘要字段</p>
      )}
    </section>
  );
}

export function TTradeReplayDecisionAudit({
  controller,
  hasReplay,
  instrumentNames,
  onViewSignal,
}: {
  controller: ReplayEvidenceController;
  hasReplay: boolean;
  instrumentNames: ReadonlyMap<string, string>;
  onViewSignal: (eventKey: string) => void;
}) {
  const {
    auditPage: page,
    auditRecords,
    auditFilters: filters,
    setAuditFilters,
    auditLoading,
    auditError,
  } = controller;
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const scope = `${page?.evidence.runId}/${page?.evidence.backtestId}`;
  React.useEffect(() => setExpandedId(null), [scope]);
  const focusedDecisionId = filters.eventKey
    ? auditRecords[0]?.decision.id
    : null;
  React.useEffect(() => {
    if (focusedDecisionId) setExpandedId(focusedDecisionId);
  }, [focusedDecisionId]);
  const available =
    hasReplay && page?.evidence.availability === 'AVAILABLE' && !auditError;
  const summary = page?.summary;
  return (
    <section
      className="studio-workspace-surface flex h-full min-h-0 flex-col"
      aria-label="回放决策审计"
    >
      <ReplayEvidenceHeader
        title="决策审计"
        description="解释策略为何决策，以及交易意图经过定量、风控和模拟执行后的结果。"
        evidence={page?.evidence}
        loading={auditLoading}
        onRefresh={controller.refreshAudit}
        counts={[
          ['决策记录', summary?.decisionCount],
          ['产生意图', summary?.withIntentCount],
          ['未发意图', summary?.noIntentCount],
          ['风险阻断', summary?.riskBlockedCount],
        ]}
      />
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-white/[0.08] p-3">
        <ReplayEvidenceSelect
          label="标的"
          value={filters.stockCode}
          options={[...instrumentNames].map(([code, name]) => [
            code,
            `${name} ${code}`,
          ])}
          onChange={value =>
            setAuditFilters(current => ({
              ...current,
              stockCode: value || null,
            }))
          }
        />
        <ReplayEvidenceSelect
          label="决策"
          value={filters.withIntent == null ? '' : String(filters.withIntent)}
          options={[
            ['true', '产生意图'],
            ['false', '未发意图'],
          ]}
          onChange={value =>
            setAuditFilters(current => ({
              ...current,
              withIntent: value === '' ? null : value === 'true',
            }))
          }
        />
        <ReplayEvidenceSelect
          label="执行状态"
          value={filters.executionStatus}
          options={Object.entries(statusLabels)}
          onChange={value =>
            setAuditFilters(current => ({
              ...current,
              executionStatus: value || null,
            }))
          }
        />
        <Input
          aria-label="搜索决策审计"
          value={filters.search || ''}
          onChange={event =>
            setAuditFilters(current => ({
              ...current,
              search: event.target.value || null,
            }))
          }
          placeholder="标的、原因或关联 ID"
          className="h-control-compact min-w-40 flex-1 text-ui-label"
        />
        {Object.values(filters).some(
          value => value != null && value !== ''
        ) && (
          <Button variant="ghost" size="sm" onClick={() => setAuditFilters({})}>
            清除筛选
          </Button>
        )}
        {filters.eventKey && (
          <div className="w-full break-all text-ui-caption text-blue-200">
            关联信号 · <span className="font-mono">{filters.eventKey}</span>
          </div>
        )}
      </div>
      <div
        className="min-h-0 flex-1 overflow-auto custom-scrollbar"
        onKeyDown={event => {
          if (event.key === 'Escape') setExpandedId(null);
        }}
      >
        <ReplayEvidenceState
          hasReplay={hasReplay}
          evidence={page?.evidence}
          loading={auditLoading}
          error={auditError}
          empty={auditRecords.length === 0}
          onRefresh={controller.refreshAudit}
          emptyMessage={
            filters.eventKey
              ? '该事件没有直接关联的材料决策。事件本身仍是真实证据，不代表产生了交易意图。'
              : '当前版本或筛选下没有材料决策。'
          }
        />
        {available && auditRecords.length > 0 && (
          <div style={{ minWidth: 1020 }}>
            <div
              className="sticky top-0 z-10 grid h-8 items-center gap-2 border-b border-white/[0.08] bg-[#0F1D30] px-3 text-ui-caption text-slate-400"
              style={{ gridTemplateColumns: columns }}
            >
              {[
                '',
                '时间',
                '标的',
                '决策',
                '交易意图 / 目标',
                '执行状态',
                '原因 / 阻断',
              ].map((label, index) => (
                <span key={index}>{label}</span>
              ))}
            </div>
            {auditRecords.map(item => {
              const decision = {
                ...mapStrategyDecisionView(item.decision),
                decisionTrace: [
                  item.decision.reason,
                  ...item.decision.tags,
                ].filter((text): text is string => Boolean(text)),
              };
              const instrumentCode = replayDecisionInstrumentCode(decision);
              const name = instrumentNames.get(instrumentCode);
              const intent = decision.tradeIntents[0];
              const execution = intent
                ? item.executions.find(row => row.intentId === intent.id)
                : undefined;
              const status = execution?.orderStatus || intent?.status || '';
              const reason =
                execution?.reason ||
                intent?.reason ||
                replayDecisionReason(decision);
              const expanded = expandedId === decision.id;
              const detailId = `replay-audit-${decision.id}`;
              return (
                <article
                  key={decision.id}
                  className={cn(
                    'border-b border-white/[0.06]',
                    expanded &&
                      'bg-blue-500/[0.035] ring-1 ring-inset ring-blue-400/30'
                  )}
                >
                  <button
                    type="button"
                    aria-label={`决策 ${instrumentCode || '账户级'} ${formatTime(decision.decidedAt)}`}
                    aria-expanded={expanded}
                    aria-controls={detailId}
                    onClick={() =>
                      setExpandedId(current =>
                        current === decision.id ? null : decision.id
                      )
                    }
                    className="grid min-h-11 w-full items-center gap-2 px-3 py-2 text-left text-ui-label hover:bg-blue-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
                    style={{ gridTemplateColumns: columns }}
                  >
                    {expanded ? (
                      <ChevronDown className="h-3.5 w-3.5 text-blue-300" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5 text-slate-400" />
                    )}
                    <span className="font-mono text-ui-caption text-slate-400">
                      {formatTime(decision.decidedAt)}
                    </span>
                    <span className="min-w-0 text-slate-200">
                      {name || instrumentCode || '账户级决策'}
                      {name && (
                        <span className="block font-mono text-ui-caption text-slate-400">
                          {instrumentCode}
                        </span>
                      )}
                    </span>
                    <span className="text-ui-caption text-slate-300">
                      {intent
                        ? `产生意图 · ${decision.tradeIntents.length}`
                        : '未发意图'}
                    </span>
                    <span
                      className={cn(
                        'font-mono text-ui-caption',
                        intent?.side.toUpperCase() === 'BUY'
                          ? 'text-market-up'
                          : intent?.side.toUpperCase() === 'SELL'
                            ? 'text-market-down'
                            : 'text-slate-400'
                      )}
                    >
                      {intent
                        ? `${sideLabel(intent.side)} · ${replayIntentTarget(item.decision.tradeIntents[0])}`
                        : '无交易意图'}
                    </span>
                    <span
                      className={cn(
                        'text-ui-caption',
                        /REJECT|SUPPRESS|FAIL/.test(status)
                          ? 'text-rose-200'
                          : 'text-slate-300'
                      )}
                    >
                      {statusLabels[status] ||
                        status ||
                        (intent ? '待定量 / 执行' : '不适用')}
                    </span>
                    <span
                      title={reason}
                      className="truncate text-ui-caption text-slate-400"
                    >
                      {replayReasonLabel(reason)}
                    </span>
                  </button>
                  {expanded && (
                    <div
                      id={detailId}
                      className="space-y-3 border-t border-blue-400/20 p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <h3 className="text-ui-label font-semibold text-slate-200">
                          决策原因与执行链路
                        </h3>
                        <span className="break-all font-mono text-ui-caption text-slate-400">
                          trace · {item.decision.traceId || decision.id}
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {item.evaluationEventKeys.map(eventKey => (
                          <Button
                            key={eventKey}
                            variant="outline"
                            size="sm"
                            title={eventKey}
                            onClick={() => onViewSignal(eventKey)}
                          >
                            <Link2 className="mr-1.5 h-3.5 w-3.5" />
                            返回信号 · {eventKey.slice(0, 24)}
                          </Button>
                        ))}
                        {!item.evaluationEventKeys.length && (
                          <span className="text-ui-caption text-slate-400">
                            此记录未携带评估事件关联，不能反推信号。
                          </span>
                        )}
                      </div>
                      <section className="rounded-panel border border-white/[0.08] p-3">
                        <h4 className="text-ui-label font-semibold text-slate-200">
                          原因链
                        </h4>
                        <ol className="mt-2 space-y-2 text-ui-label text-slate-300">
                          {(replayDecisionTraceItems(decision).length
                            ? replayDecisionTraceItems(decision)
                            : [reason]
                          ).map((text, index) => (
                            <li key={`${index}-${text}`} className="flex gap-2">
                              <span className="font-mono text-blue-300">
                                {String(index + 1).padStart(2, '0')}
                              </span>
                              <span className="break-words" title={text}>
                                {replayReasonLabel(text)}
                              </span>
                            </li>
                          ))}
                        </ol>
                      </section>
                      <div className="grid gap-3 xl:grid-cols-2">
                        <AuditSummary
                          title="策略输入"
                          values={decision.inputSummary}
                        />
                        <AuditSummary
                          title="策略输出"
                          values={decision.outputSummary}
                        />
                      </div>
                      {Object.keys(decision.statePatch || {}).length > 0 && (
                        <AuditSummary
                          title="算法状态变化"
                          values={decision.statePatch || {}}
                        />
                      )}
                      <section className="rounded-panel border border-white/[0.08] p-3">
                        <h4 className="text-ui-label font-semibold text-slate-200">
                          交易意图与模拟执行
                        </h4>
                        <p className="mt-2 text-ui-caption text-slate-400">
                          TradeIntent → OrderSizer 定量 → OrderRisk 风控 →
                          回测委托 → 模拟成交。策略目标不等于最终委托股数。
                        </p>
                        {decision.tradeIntents.length === 0 ? (
                          <p className="mt-3 text-ui-label text-slate-300">
                            本次决策没有产生交易意图，定量、委托与成交不适用。
                          </p>
                        ) : (
                          <table className="mt-3 w-full text-left text-ui-caption">
                            <thead className="text-slate-400">
                              <tr>
                                {[
                                  '标的 / 方向',
                                  '策略目标',
                                  '定量结果',
                                  '风控 / 委托',
                                  '模拟成交',
                                  '原因',
                                ].map(label => (
                                  <th
                                    key={label}
                                    className="px-2 py-2 font-medium"
                                  >
                                    {label}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {decision.tradeIntents.map(intent => {
                                const result = item.executions.find(
                                  row => row.intentId === intent.id
                                );
                                return (
                                  <tr
                                    key={intent.id}
                                    className="border-t border-white/[0.06] text-slate-300"
                                  >
                                    <td className="px-2 py-2 font-mono">
                                      {intent.instrumentCode} ·{' '}
                                      <span
                                        className={
                                          intent.side.toUpperCase() === 'BUY'
                                            ? 'text-market-up'
                                            : intent.side.toUpperCase() ===
                                                'SELL'
                                              ? 'text-market-down'
                                              : 'text-slate-300'
                                        }
                                      >
                                        {sideLabel(intent.side)}
                                      </span>
                                      <span
                                        className="mt-1 block text-slate-400"
                                        title={intent.id}
                                      >
                                        意图 · {intent.id.slice(0, 20)}
                                      </span>
                                    </td>
                                    <td className="px-2 py-2 font-mono">
                                      {replayIntentTarget(
                                        item.decision.tradeIntents.find(
                                          row => row.id === intent.id
                                        )
                                      )}
                                    </td>
                                    <td className="px-2 py-2 font-mono">
                                      {result?.sizingResult ?? '未记录定量结果'}
                                    </td>
                                    <td className="px-2 py-2">
                                      {result?.riskDecision || '未记录风控结果'}
                                      <span className="mt-1 block">
                                        {statusLabels[
                                          result?.orderStatus || ''
                                        ] ||
                                          result?.orderStatus ||
                                          '未记录委托状态'}
                                      </span>
                                      {result?.orderId && (
                                        <span
                                          className="mt-1 block font-mono text-slate-400"
                                          title={result.orderId}
                                        >
                                          委托 · {result.orderId.slice(0, 20)}
                                        </span>
                                      )}
                                    </td>
                                    <td className="px-2 py-2 font-mono">
                                      {result?.executedVolume == null
                                        ? '未记录成交结果'
                                        : result.executedVolume > 0
                                          ? `${result.executedVolume} 股 · ${result.executedPrice == null ? '成交价未记录' : `@ ${formatNumber(result.executedPrice, 3)}`}`
                                          : '无成交'}
                                    </td>
                                    <td className="max-w-xs break-words px-2 py-2">
                                      {result?.reason ||
                                        intent.reason ||
                                        '无附加原因'}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        )}
                      </section>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </div>
      {available && (
        <ReplayEvidenceFooter
          count={auditRecords.length}
          total={summary?.decisionCount}
          more={Boolean(page?.pageInfo.hasNextPage)}
          loading={auditLoading}
          onMore={controller.loadMoreAudit}
        />
      )}
    </section>
  );
}
