import { AlertTriangle, Database, Loader2, RefreshCw } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

import type { ReplayAuditPage } from '../../hooks/useTTradeReplayEvidence';

import { replayEvidenceUnavailableMessage } from './replayEvidencePresentation';

export type ReplayEvidenceInfo = ReplayAuditPage['evidence'];

export function ReplayEvidenceHeader({
  title,
  description,
  evidence,
  counts,
  loading,
  onRefresh,
}: {
  title: string;
  description: string;
  evidence?: ReplayEvidenceInfo;
  counts: readonly (readonly [string, number | undefined])[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const available = evidence?.availability === 'AVAILABLE';
  return (
    <>
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/[0.08] px-ui-section py-3">
        <div className="min-w-0">
          <h2 className="text-ui-title font-semibold text-slate-100">
            {title}
          </h2>
          <p className="mt-1 text-ui-caption text-slate-400">{description}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {evidence && (
            <span
              title={evidence.contentFingerprint || undefined}
              className="flex items-center gap-1 rounded border border-blue-400/20 px-2 py-1 font-mono text-ui-caption text-blue-200"
            >
              <Database className="h-3.5 w-3.5" />V{evidence.backtestVersion} ·{' '}
              {!available
                ? '归档不可用'
                : evidence.source === 'RUN_PROJECTION'
                  ? '运行投影'
                  : evidence.sealed
                    ? '已密封'
                    : '历史归档'}
            </span>
          )}
          <Button
            variant="ghost"
            size="icon"
            aria-label={`刷新${title}`}
            disabled={loading}
            onClick={onRefresh}
            className="h-control-compact w-control-compact"
          >
            <RefreshCw
              className={`h-4 w-4 ${loading ? 'animate-spin motion-reduce:animate-none' : ''}`}
            />
          </Button>
        </div>
      </header>
      <div className="grid shrink-0 grid-cols-4 border-b border-white/[0.08] bg-white/[0.02]">
        {counts.map(([label, count]) => (
          <div
            key={label}
            className="border-r border-white/[0.06] px-3 py-2 last:border-r-0"
          >
            <div className="text-ui-caption text-slate-400">{label}</div>
            <div className="mt-1 font-mono text-ui-title font-semibold text-slate-200">
              {available ? (count ?? '—') : '—'}
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

export function ReplayEvidenceState({
  hasReplay,
  evidence,
  loading,
  error,
  empty,
  emptyMessage,
  onRefresh,
}: {
  hasReplay: boolean;
  evidence?: ReplayEvidenceInfo;
  loading: boolean;
  error?: string;
  empty: boolean;
  emptyMessage: string;
  onRefresh: () => void;
}) {
  if (!hasReplay)
    return (
      <div className="p-3 text-center text-ui-body text-slate-400">
        请先选择一条回放记录
      </div>
    );
  if (error || evidence?.availability === 'UNAVAILABLE')
    return (
      <div
        role="alert"
        className="m-3 rounded-panel border border-amber-400/20 bg-amber-400/5 p-3 text-ui-label text-amber-100"
      >
        <div className="flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <p className="break-words">
            {error || replayEvidenceUnavailableMessage(evidence?.reasonCode)}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          onClick={onRefresh}
        >
          重新读取
        </Button>
      </div>
    );
  if ((loading || !evidence) && empty)
    return (
      <div
        role="status"
        className="flex items-center justify-center gap-2 p-3 text-ui-label text-slate-400"
      >
        <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
        正在读取当前版本证据…
      </div>
    );
  if (empty)
    return (
      <div
        role="status"
        className="p-3 text-center text-ui-body text-slate-400"
      >
        {emptyMessage}
      </div>
    );
  return null;
}

export function ReplayEvidenceSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value?: string | null;
  options: readonly (readonly [string, string])[];
  onChange: (value: string) => void;
}) {
  return (
    <Select
      value={value || '__ALL__'}
      onValueChange={value => onChange(value === '__ALL__' ? '' : value)}
    >
      <SelectTrigger
        aria-label={label}
        className="h-control-compact w-auto min-w-28 text-ui-label"
      >
        <SelectValue placeholder={label} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="__ALL__">全部{label}</SelectItem>
        {options.map(([value, title]) => (
          <SelectItem key={value} value={value}>
            {title}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function ReplayEvidenceFooter({
  count,
  total,
  more,
  loading,
  onMore,
}: {
  count: number;
  total?: number;
  more: boolean;
  loading: boolean;
  onMore: () => void;
}) {
  return (
    <footer className="flex shrink-0 items-center justify-between border-t border-white/[0.08] px-3 py-2 text-ui-caption text-slate-400">
      <span>
        已显示 {count} / {total ?? '—'} 条
      </span>
      <Button
        variant="outline"
        size="sm"
        aria-disabled={loading || !more}
        className="aria-disabled:pointer-events-none aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
        onClick={() => {
          if (!loading && more) onMore();
        }}
      >
        {loading ? '读取中…' : more ? '加载更多' : '已全部加载'}
      </Button>
    </footer>
  );
}
