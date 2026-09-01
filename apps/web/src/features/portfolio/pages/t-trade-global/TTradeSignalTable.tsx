import { ChevronDown, ChevronRight } from 'lucide-react';
import type { ReactNode, RefObject } from 'react';

import { cn } from '@/utils/cn';

import {
  signalSummaryTopBlocker,
  type SignalSnapshotSummary,
} from './monitoring';
import {
  candidateStatusLabels,
  nullableScore,
  signalCandidateStatusTone,
  signalEventLabels,
  signalEventTone,
  signalPathLabels,
  signalPhaseLabels,
} from './signalPresentation';
import { formatTime } from './utils';

export type TTradeSignalListItem = {
  id: string;
  eventKey?: string | null;
  category?: string | null;
  stockCode: string;
  eventType: string;
  evaluatedAt: string;
  linkedIntentId?: string | null;
  coalescedCount?: number | null;
  signalSnapshot?: SignalSnapshotSummary | null;
};

const columns =
  '24px 150px minmax(130px, .9fr) minmax(150px, 1fr) minmax(145px, 1fr) 110px 130px minmax(180px, 1.25fr)';

function signalResult(item: TTradeSignalListItem) {
  const snapshot = item.signalSnapshot;
  const linkedIntentId = item.linkedIntentId;
  if (linkedIntentId) {
    return {
      label:
        snapshot?.candidateStatus === 'AWAITING_APPROVAL'
          ? '意图等待确认'
          : '已关联交易意图',
      detail: linkedIntentId,
      tone: 'text-blue-200',
    };
  }
  if (snapshot?.pendingEntryIntentId) {
    return {
      label: '意图等待确认',
      detail: '候选尚未完成执行关联',
      tone: 'text-amber-100',
    };
  }
  const blocker = signalSummaryTopBlocker(snapshot);
  if (blocker) {
    return {
      label: blocker.label,
      detail: blocker.detail || blocker.code,
      tone: 'text-amber-100',
    };
  }
  if (!snapshot) {
    return {
      label: '未关联意图',
      detail: '快照缺失，阻断未知',
      tone: 'text-amber-100',
    };
  }
  return {
    label: '未关联意图',
    detail: '暂无首要阻断',
    tone: 'text-slate-400',
  };
}

export function TTradeSignalTable<T extends TTradeSignalListItem>({
  expandedId,
  focusedId,
  focusedRowRef,
  instrumentNames,
  items,
  onToggle,
  renderDetails,
  rowAriaLabel,
  tracingEvidence = false,
}: {
  expandedId: string | null;
  focusedId?: string | null;
  focusedRowRef?: RefObject<HTMLButtonElement>;
  instrumentNames: ReadonlyMap<string, string>;
  items: readonly T[];
  onToggle: (item: T) => void;
  renderDetails: (item: T) => ReactNode;
  rowAriaLabel?: (item: T) => string;
  tracingEvidence?: boolean;
}) {
  return (
    <div style={{ minWidth: 1120 }}>
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
          '结果 / 首要阻断',
        ].map((label, index) => (
          <span key={index}>{label}</span>
        ))}
      </div>
      {items.map(item => {
        const snapshot = item.signalSnapshot;
        const expanded = expandedId === item.id;
        const detailId = `t-trade-signal-${item.id}`;
        const name = instrumentNames.get(item.stockCode.toUpperCase());
        const result = signalResult(item);
        const candidateStatus = snapshot?.candidateStatus || '';
        return (
          <article
            key={item.id}
            className={cn(
              'border-b border-white/[0.06]',
              expanded &&
                'bg-blue-500/[0.035] ring-1 ring-inset ring-blue-400/30'
            )}
          >
            <button
              ref={focusedId === item.id ? focusedRowRef : undefined}
              type="button"
              aria-label={
                rowAriaLabel?.(item) ||
                `${signalEventLabels[item.eventType] || item.eventType} ${item.stockCode} ${formatTime(item.evaluatedAt)}`
              }
              aria-expanded={expanded}
              aria-controls={detailId}
              onClick={() => onToggle(item)}
              className="grid min-h-11 w-full items-center gap-2 px-3 py-2 text-left text-ui-label hover:bg-blue-500/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
              style={{ gridTemplateColumns: columns }}
            >
              {expanded ? (
                <ChevronDown className="h-3.5 w-3.5 text-blue-300" />
              ) : (
                <ChevronRight className="h-3.5 w-3.5 text-slate-400" />
              )}
              <span className="font-mono text-ui-caption text-slate-400">
                {formatTime(item.evaluatedAt)}
                {(item.coalescedCount || 0) > 1 && (
                  <span className="mt-0.5 block">
                    合并 ×{item.coalescedCount}
                  </span>
                )}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-slate-200">
                  {name || item.stockCode}
                </span>
                {name && (
                  <span className="block font-mono text-ui-caption text-slate-400">
                    {item.stockCode}
                  </span>
                )}
              </span>
              <span
                className={cn(
                  'w-fit rounded border px-2 py-1 text-ui-caption',
                  signalEventTone(item.eventType)
                )}
              >
                {signalEventLabels[item.eventType] || item.eventType}
                {item.category === 'CONTEXT' && (
                  <span className="mt-1 block text-slate-400">
                    上下文事件 · 非交易信号
                  </span>
                )}
              </span>
              <span className="text-ui-caption text-slate-300">
                {signalPathLabels[snapshot?.selectedPath || ''] || '未选择路径'}
                <span className="mt-1 block text-slate-400">
                  {signalPhaseLabels[snapshot?.dominantPhase || ''] ||
                    '阶段未记录'}
                </span>
              </span>
              <span className="font-mono text-ui-caption text-slate-200">
                {nullableScore(snapshot?.opportunityScore)} /{' '}
                {nullableScore(snapshot?.candidateThreshold)}
              </span>
              <span
                className={cn(
                  'w-fit rounded border px-2 py-1 text-ui-caption',
                  signalCandidateStatusTone(candidateStatus, item.eventType)
                )}
              >
                {candidateStatusLabels[candidateStatus] || '状态未记录'}
              </span>
              <span className={cn('min-w-0 text-ui-caption', result.tone)}>
                <span className="block truncate" title={result.label}>
                  {result.label}
                </span>
                <span
                  className="mt-1 block truncate font-mono text-slate-400"
                  title={result.detail}
                >
                  {result.detail}
                </span>
              </span>
            </button>
            {expanded && (
              <div id={detailId} className="border-t border-blue-400/20">
                {renderDetails(item)}
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}
