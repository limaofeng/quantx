import { Link2 } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

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
  candidateStatusLabels,
  signalEventLabels,
  signalPathLabels,
} from './signalPresentation';
import { TTradeSignalEvidence } from './TTradeSignalEvidence';
import { TTradeSignalTable } from './TTradeSignalTable';

export function TTradeReplaySignals({
  controller,
  hasReplay,
  instrumentNames,
  onViewAudit,
}: {
  controller: ReplayEvidenceController;
  hasReplay: boolean;
  instrumentNames: ReadonlyMap<string, string>;
  onViewAudit: (eventKey: string) => void;
}) {
  const {
    evaluations,
    signalPage: page,
    signalFilters: filters,
    setSignalFilters,
    signalsLoading,
    signalError,
  } = controller;
  const [expandedKey, setExpandedKey] = React.useState<string | null>(null);
  const linkedRowRef = React.useRef<HTMLButtonElement>(null);
  const scope = `${page?.evidence.runId}/${page?.evidence.backtestId}`;
  React.useEffect(() => setExpandedKey(null), [scope]);
  const focusedEventKey =
    filters.eventKey &&
    evaluations.some(item => item.eventKey === filters.eventKey)
      ? filters.eventKey
      : null;
  useRevealReplayEvidenceTarget(
    filters.eventKey,
    focusedEventKey,
    linkedRowRef,
    setExpandedKey
  );
  const available =
    hasReplay && page?.evidence.availability === 'AVAILABLE' && !signalError;
  const summary = page?.summary;
  const tracingEvidence = Boolean(filters.eventKey && filters.includeContext);
  const update = (
    field: 'stockCode' | 'selectedPath' | 'candidateStatus' | 'search',
    value: string
  ) => setSignalFilters(current => ({ ...current, [field]: value || null }));
  return (
    <section
      className="studio-workspace-surface flex h-full min-h-0 flex-col"
      aria-label={tracingEvidence ? '回放关联评估证据' : '回放真实信号'}
    >
      <ReplayEvidenceHeader
        title={tracingEvidence ? '关联评估证据' : '真实信号'}
        description={
          tracingEvidence
            ? '按精确事件键查看审计来源；上下文事件不是交易信号。'
            : '记录真实机会事件；无交易意图不等于无信号，方向和数量在决策审计中查看。'
        }
        evidence={page?.evidence}
        loading={signalsLoading}
        onRefresh={controller.refreshSignals}
        counts={[
          [tracingEvidence ? '关联事件' : '信号事件', summary?.eventCount],
          ['唯一候选', summary?.candidateCount],
          ['关联意图', summary?.linkedIntentCount],
          ['已抑制事件', summary?.suppressedCount],
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
          onChange={value => update('stockCode', value)}
        />
        <ReplayEvidenceSelect
          label="路径"
          value={filters.selectedPath}
          options={Object.entries(signalPathLabels)}
          onChange={value => update('selectedPath', value)}
        />
        <ReplayEvidenceSelect
          label="候选状态"
          value={filters.candidateStatus}
          options={Object.entries(candidateStatusLabels)}
          onChange={value => update('candidateStatus', value)}
        />
        <Input
          aria-label="搜索信号"
          value={filters.search || ''}
          onChange={event => update('search', event.target.value)}
          placeholder="标的、事件或关联 ID"
          className="h-control-compact min-w-40 flex-1 text-ui-label"
        />
        {Object.values(filters).some(Boolean) && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSignalFilters({})}
          >
            清除筛选
          </Button>
        )}
        {(filters.eventKey || filters.candidateId) && (
          <div className="w-full break-all text-ui-caption text-blue-200">
            {filters.candidateId ? '候选链路' : '审计关联事件'} ·{' '}
            <span className="font-mono">
              {filters.candidateId || filters.eventKey}
            </span>
            {filters.candidateId &&
              ' · 查看同一版本的候选事件，可逐条跳转决策与执行。'}
          </div>
        )}
      </div>
      <div
        className="min-h-0 flex-1 overflow-auto custom-scrollbar"
        onKeyDown={event =>
          collapseEvidenceOnEscape(event, () => setExpandedKey(null))
        }
      >
        <ReplayEvidenceState
          hasReplay={hasReplay}
          evidence={page?.evidence}
          loading={signalsLoading}
          error={signalError}
          empty={evaluations.length === 0}
          onRefresh={controller.refreshSignals}
          emptyMessage={
            Object.values(filters).some(Boolean)
              ? '当前筛选下没有信号事件，可清除筛选查看全部。'
              : '该版本没有产生真实信号事件。审计中仍可查看未发意图或被阻断的决策。'
          }
        />
        {available && evaluations.length > 0 && (
          <TTradeSignalTable
            expandedId={
              evaluations.find(item => item.eventKey === expandedKey)?.id ||
              null
            }
            focusedId={
              evaluations.find(item => item.eventKey === focusedEventKey)?.id ||
              null
            }
            focusedRowRef={linkedRowRef}
            instrumentNames={instrumentNames}
            items={evaluations}
            onToggle={signal =>
              setExpandedKey(current =>
                current === signal.eventKey ? null : signal.eventKey
              )
            }
            rowAriaLabel={signal =>
              `${signalEventLabels[signal.eventType] || signal.eventType} ${signal.stockCode}`
            }
            tracingEvidence={tracingEvidence}
            renderDetails={signal => {
              const snapshot = signal.signalSnapshot;
              const name = instrumentNames.get(signal.stockCode.toUpperCase());
              return (
                <div className="space-y-3 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h3 className="text-ui-label font-semibold text-slate-200">
                      {name || signal.stockCode} ·{' '}
                      {signalEventLabels[signal.eventType] || signal.eventType}
                    </h3>
                    <span
                      title={signal.contentFingerprint}
                      className="text-ui-caption text-slate-400"
                    >
                      事件指纹{' '}
                      <span className="font-mono">
                        {signal.contentFingerprint.slice(0, 12)}
                      </span>
                    </span>
                  </div>
                  {snapshot ? (
                    <TTradeSignalEvidence snapshot={snapshot} />
                  ) : (
                    <p className="text-ui-label text-amber-100">
                      该事件未记录机会快照；事件身份和审计关联仍然保留。
                    </p>
                  )}
                  <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.06] pt-3">
                    <span className="max-w-full break-all font-mono text-ui-caption text-slate-400">
                      eventKey · {signal.eventKey}
                    </span>
                    <div className="flex gap-2">
                      {signal.candidateId && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            setSignalFilters({
                              candidateId: signal.candidateId,
                            })
                          }
                        >
                          查看候选链路
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => onViewAudit(signal.eventKey)}
                      >
                        <Link2 className="mr-1.5 h-3.5 w-3.5" />
                        查看决策审计
                      </Button>
                    </div>
                  </div>
                </div>
              );
            }}
          />
        )}
      </div>
      {available && (
        <ReplayEvidenceFooter
          count={evaluations.length}
          total={summary?.eventCount}
          more={Boolean(page?.pageInfo.hasNextPage)}
          loading={signalsLoading}
          onMore={controller.loadMoreSignals}
        />
      )}
    </section>
  );
}
