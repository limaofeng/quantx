import { ChevronDown, ChevronRight, Link2 } from 'lucide-react';
import type { RefObject } from 'react';

import { Button } from '@/components/ui/button';
import type {
  ExecutionTraceView,
  StrategyDecision,
  TradeIntentView,
} from '@/features/strategies/domain';
import { cn } from '@/utils/cn';

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

export const decisionExecutionStatusLabels: Readonly<Record<string, string>> = {
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

export type DecisionAuditRawIntent = {
  id?: string | null;
  targetVolume?: number | null;
  targetAmount?: number | null;
  targetPositionPct?: number | null;
};

export type TTradeDecisionAuditRecord = {
  decision: StrategyDecision;
  executions: readonly ExecutionTraceView[];
  evaluationEventKeys?: readonly string[];
  rawTradeIntents?: readonly DecisionAuditRawIntent[];
};

const columns =
  '24px 150px minmax(120px, .8fr) 100px minmax(145px, 1fr) 110px minmax(220px, 1.5fr)';

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

function intentTarget(
  record: TTradeDecisionAuditRecord,
  intent: TradeIntentView,
  index: number
) {
  const raw =
    record.rawTradeIntents?.find(item => item.id === intent.id) ||
    record.rawTradeIntents?.[index];
  const replayTarget = replayIntentTarget(raw);
  if (replayTarget !== '由定量层确定') return replayTarget;
  if (intent.quantityIntent != null && intent.quantityIntent !== '') {
    return `数量意图 ${String(intent.quantityIntent)}`;
  }
  if (intent.priceIntent != null && intent.priceIntent !== '') {
    return `价格意图 ${String(intent.priceIntent)}`;
  }
  return replayTarget;
}

export function TTradeDecisionAuditTable({
  executionMode,
  expandedId,
  focusedId,
  focusedRowRef,
  instrumentNames,
  onToggle,
  onViewSignal,
  records,
}: {
  executionMode: 'LIVE' | 'BACKTEST';
  expandedId: string | null;
  focusedId?: string | null;
  focusedRowRef?: RefObject<HTMLButtonElement>;
  instrumentNames: ReadonlyMap<string, string>;
  onToggle: (decisionId: string) => void;
  onViewSignal?: (eventKey: string) => void;
  records: readonly TTradeDecisionAuditRecord[];
}) {
  const executionLabel = executionMode === 'LIVE' ? '实盘执行' : '模拟执行';
  return (
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
      {records.map(record => {
        const { decision } = record;
        const instrumentCode = replayDecisionInstrumentCode(decision);
        const name = instrumentNames.get(instrumentCode.toUpperCase());
        const intent = decision.tradeIntents[0];
        const execution = intent
          ? record.executions.find(row => row.intentId === intent.id)
          : undefined;
        const status =
          execution?.orderStatus ||
          execution?.fillStatus ||
          intent?.status ||
          '';
        const reason =
          execution?.reason || intent?.reason || replayDecisionReason(decision);
        const expanded = expandedId === decision.id;
        const detailId = `t-trade-audit-${decision.id}`;
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
              ref={focusedId === decision.id ? focusedRowRef : undefined}
              type="button"
              aria-label={`决策 ${instrumentCode || '账户级'} ${formatTime(decision.decidedAt)}`}
              aria-expanded={expanded}
              aria-controls={detailId}
              onClick={() => onToggle(decision.id)}
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
                  ? `${sideLabel(intent.side)} · ${intentTarget(record, intent, 0)}`
                  : '无交易意图'}
              </span>
              <span
                className={cn(
                  'text-ui-caption',
                  /REJECT|SUPPRESS|FAIL|KILL/.test(status)
                    ? 'text-rose-200'
                    : 'text-slate-300'
                )}
              >
                {decisionExecutionStatusLabels[status] ||
                  status ||
                  (intent ? `待${executionLabel}` : '不适用')}
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
                    decision · {decision.id}
                  </span>
                </div>
                {onViewSignal && (
                  <div className="flex flex-wrap gap-2">
                    {(record.evaluationEventKeys || []).map(eventKey => (
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
                    {!record.evaluationEventKeys?.length && (
                      <span className="text-ui-caption text-slate-400">
                        此记录未携带评估事件关联，不能反推信号。
                      </span>
                    )}
                  </div>
                )}
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
                    交易意图与{executionLabel}
                  </h4>
                  <p className="mt-2 text-ui-caption text-slate-400">
                    {executionMode === 'LIVE'
                      ? 'TradeIntent → OrderSizer 定量 → OrderRisk 风控 → 实盘委托 → 券商成交回报。策略意图和委托状态都不能替代真实成交。'
                      : 'TradeIntent → OrderSizer 定量 → OrderRisk 风控 → 回测委托 → 模拟成交。策略目标不等于最终委托股数。'}
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
                            executionMode === 'LIVE' ? '真实成交' : '模拟成交',
                            '原因',
                          ].map(label => (
                            <th key={label} className="px-2 py-2 font-medium">
                              {label}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {decision.tradeIntents.map((rowIntent, index) => {
                          const result = record.executions.find(
                            row => row.intentId === rowIntent.id
                          );
                          return (
                            <tr
                              key={rowIntent.id}
                              className="border-t border-white/[0.06] text-slate-300"
                            >
                              <td className="px-2 py-2 font-mono">
                                {rowIntent.instrumentCode} ·{' '}
                                <span
                                  className={
                                    rowIntent.side.toUpperCase() === 'BUY'
                                      ? 'text-market-up'
                                      : rowIntent.side.toUpperCase() === 'SELL'
                                        ? 'text-market-down'
                                        : 'text-slate-300'
                                  }
                                >
                                  {sideLabel(rowIntent.side)}
                                </span>
                                <span
                                  className="mt-1 block text-slate-400"
                                  title={rowIntent.id}
                                >
                                  意图 · {rowIntent.id.slice(0, 20)}
                                </span>
                              </td>
                              <td className="px-2 py-2 font-mono">
                                {intentTarget(record, rowIntent, index)}
                              </td>
                              <td className="px-2 py-2 font-mono">
                                {result?.sizingResult ?? '未记录定量结果'}
                              </td>
                              <td className="px-2 py-2">
                                {result?.riskDecision || '未记录风控结果'}
                                <span className="mt-1 block">
                                  {decisionExecutionStatusLabels[
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
                                  rowIntent.reason ||
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
  );
}
