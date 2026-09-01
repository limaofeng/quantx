import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { mapStrategyDecisionView } from '@/features/strategies/domain';

import type { ReplayEvidenceController } from '../../hooks/useTTradeReplayEvidence';

import {
  ReplayEvidenceFooter,
  ReplayEvidenceHeader,
  ReplayEvidenceSelect,
  ReplayEvidenceState,
} from './ReplayEvidenceChrome';
import {
  collapseEvidenceOnEscape,
  useRevealReplayEvidenceTarget,
} from './replayEvidenceKeyboard';
import {
  decisionExecutionStatusLabels,
  TTradeDecisionAuditTable,
  type TTradeDecisionAuditRecord,
} from './TTradeDecisionAuditTable';

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
  const linkedRowRef = React.useRef<HTMLButtonElement>(null);
  const scope = `${page?.evidence.runId}/${page?.evidence.backtestId}`;
  React.useEffect(() => setExpandedId(null), [scope]);
  const focusedDecisionId = filters.eventKey
    ? auditRecords[0]?.decision.id
    : null;
  useRevealReplayEvidenceTarget(
    filters.eventKey,
    focusedDecisionId,
    linkedRowRef,
    setExpandedId
  );
  const available =
    hasReplay && page?.evidence.availability === 'AVAILABLE' && !auditError;
  const summary = page?.summary;
  const records = React.useMemo<TTradeDecisionAuditRecord[]>(
    () =>
      auditRecords.map(item => ({
        decision: {
          ...mapStrategyDecisionView(item.decision),
          decisionTrace: [item.decision.reason, ...item.decision.tags].filter(
            (text): text is string => Boolean(text)
          ),
        },
        evaluationEventKeys: item.evaluationEventKeys,
        executions: item.executions,
        rawTradeIntents: item.decision.tradeIntents,
      })),
    [auditRecords]
  );

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
          options={Object.entries(decisionExecutionStatusLabels)}
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
        onKeyDown={event =>
          collapseEvidenceOnEscape(event, () => setExpandedId(null))
        }
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
        {available && records.length > 0 && (
          <TTradeDecisionAuditTable
            executionMode="BACKTEST"
            expandedId={expandedId}
            focusedId={focusedDecisionId}
            focusedRowRef={linkedRowRef}
            instrumentNames={instrumentNames}
            onToggle={decisionId =>
              setExpandedId(current =>
                current === decisionId ? null : decisionId
              )
            }
            onViewSignal={onViewSignal}
            records={records}
          />
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
