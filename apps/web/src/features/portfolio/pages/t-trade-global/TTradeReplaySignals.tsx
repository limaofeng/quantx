import { ChevronDown, ChevronRight, Link2 } from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/utils/cn';

import type { ReplayEvidenceController } from '../../hooks/useTTradeReplayEvidence';

import {
  ReplayEvidenceFooter,
  ReplayEvidenceHeader,
  ReplayEvidenceSelect,
  ReplayEvidenceState,
} from './ReplayEvidenceChrome';
import { collapseEvidenceOnEscape } from './replayEvidenceKeyboard';
import {
  candidateStatusLabels,
  nullableScore,
  signalEventLabels,
  signalEventTone,
  signalPathLabels,
  signalPhaseLabels,
} from './signalPresentation';
import { TTradeSignalEvidence } from './TTradeSignalEvidence';
import { formatTime } from './utils';

const columns =
  '24px minmax(140px, 1fr) minmax(130px, 1fr) minmax(150px, 1.1fr) minmax(140px, 1fr) 100px 110px minmax(130px, 1fr)';

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
  React.useEffect(() => {
    if (focusedEventKey) {
      setExpandedKey(focusedEventKey);
      linkedRowRef.current?.focus();
    }
  }, [focusedEventKey]);
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
          <div style={{ minWidth: 1050 }}>
            <div
              className="sticky top-0 z-10 grid h-8 items-center gap-2 border-b border-white/[0.08] bg-[#0F1D30] px-3 text-ui-caption text-slate-400"
              style={{ gridTemplateColumns: columns }}
            >
              {[
                '',
                '时间',
                '标的',
                tracingEvidence ? '评估事件' : '信号事件',
                '路径 / 阶段',
                '机会分 / 阈值',
                '候选状态',
                '关联意图',
              ].map((label, index) => (
                <span key={index}>{label}</span>
              ))}
            </div>
            {evaluations.map(signal => {
              const snapshot = signal.signalSnapshot;
              const expanded = expandedKey === signal.eventKey;
              const detailId = `replay-evidence-${signal.id}`;
              const name = instrumentNames.get(signal.stockCode.toUpperCase());
              return (
                <article
                  key={signal.eventKey}
                  className={cn(
                    'border-b border-white/[0.06]',
                    expanded &&
                      'bg-blue-500/[0.035] ring-1 ring-inset ring-blue-400/30'
                  )}
                >
                  <button
                    ref={
                      focusedEventKey === signal.eventKey
                        ? linkedRowRef
                        : undefined
                    }
                    type="button"
                    aria-label={`${signalEventLabels[signal.eventType] || signal.eventType} ${signal.stockCode} ${formatTime(signal.evaluatedAt)}`}
                    aria-expanded={expanded}
                    aria-controls={detailId}
                    onClick={() =>
                      setExpandedKey(current =>
                        current === signal.eventKey ? null : signal.eventKey
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
                      {formatTime(signal.evaluatedAt)}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-slate-200">
                        {name || signal.stockCode}
                      </span>
                      {name && (
                        <span className="block font-mono text-ui-caption text-slate-400">
                          {signal.stockCode}
                        </span>
                      )}
                    </span>
                    <span
                      className={cn(
                        'w-fit rounded border px-2 py-1 text-ui-caption',
                        signalEventTone(signal.eventType)
                      )}
                    >
                      {signalEventLabels[signal.eventType] || signal.eventType}
                      {signal.category === 'CONTEXT' && (
                        <span className="mt-1 block text-slate-400">
                          上下文事件 · 非交易信号
                        </span>
                      )}
                    </span>
                    <span className="text-ui-caption text-slate-300">
                      {signalPathLabels[snapshot?.selectedPath || ''] ||
                        '未选择路径'}
                      <span className="mt-1 block text-slate-400">
                        {signalPhaseLabels[snapshot?.dominantPhase || ''] ||
                          '阶段未记录'}
                      </span>
                    </span>
                    <span className="font-mono text-ui-caption text-slate-200">
                      {nullableScore(snapshot?.opportunityScore)} /{' '}
                      {nullableScore(snapshot?.candidateThreshold)}
                    </span>
                    <span className="text-ui-caption text-slate-300">
                      {candidateStatusLabels[snapshot?.candidateStatus || ''] ||
                        '状态未记录'}
                    </span>
                    <span
                      className="truncate font-mono text-ui-caption text-slate-400"
                      title={signal.linkedIntentId || undefined}
                    >
                      {signal.linkedIntentId || '未关联意图'}
                    </span>
                  </button>
                  {expanded && (
                    <div
                      id={detailId}
                      className="space-y-3 border-t border-blue-400/20 p-3"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <h3 className="text-ui-label font-semibold text-slate-200">
                          {name || signal.stockCode} ·{' '}
                          {signalEventLabels[signal.eventType] ||
                            signal.eventType}
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
                  )}
                </article>
              );
            })}
          </div>
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
