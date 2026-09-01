import { Download, ExternalLink, RefreshCw } from 'lucide-react';
import { useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { IndicatorReportView } from '@/features/research/components/IndicatorReportView';
import { buildResearchRunPath } from '@/features/research/model';
import type { StockIndicatorReportMatchesQuery } from '@/generated/gql/graphql';

import { useIndicatorReport } from '../hooks/useIndicatorResearch';
import {
  describeIndicatorCondition,
  validateIndicatorConditions,
} from '../indicatorModel';
import type { IndicatorDefinition, ScreeningCriteria } from '../types';

type Match =
  StockIndicatorReportMatchesQuery['stockIndicatorReportMatches'][number];
const REPORT_MATCH_LABELS: Record<string, string> = {
  MATCHED: '条件匹配',
  REFERENCE_ONLY: '仅供参考：不是当前条件的精确报告',
  MISSING: '尚无匹配报告',
  UNSUPPORTED: '研究未覆盖',
  DATA_INSUFFICIENT: '样本不足',
  ARTIFACT_ERROR: '报告产物异常',
};

function downloadConfig(config: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(config, null, 2)], {
      type: 'application/json;charset=utf-8',
    })
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = 'indicator-study.json';
  link.click();
  URL.revokeObjectURL(url);
}

export function IndicatorReportDrawer({
  focus,
  onClose,
  criteria,
  indicators,
  match,
  loading,
  error,
  onRefresh,
  pending,
}: {
  focus: string | null;
  onClose: () => void;
  criteria: ScreeningCriteria;
  indicators: IndicatorDefinition[];
  match?: Match;
  loading: boolean;
  error?: string;
  onRefresh: () => void;
  pending: boolean;
}) {
  const [selected, setSelected] = useState('');
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const references = match?.reports ?? [];
  const reference =
    references.find(item => `${item.runKey}:${item.reportId}` === selected) ??
    references[0];
  const matchStatus = reference?.matchStatus ?? match?.status ?? 'MISSING';
  const matchReason = reference?.matchReason ?? match?.reason;
  const visibleBlockers = [...new Set(match?.blockers ?? [])].filter(
    blocker => !matchReason?.includes(blocker)
  );
  const {
    detail,
    fetching,
    error: detailError,
    refresh,
  } = useIndicatorReport(reference?.runKey ?? '', reference?.reportId ?? '');
  const indicator = indicators.find(item => `single:${item.id}` === focus);
  const conditionError = validateIndicatorConditions(
    criteria.indicatorConditions ?? []
  );
  return (
    <Sheet
      open={focus !== null}
      onOpenChange={open => {
        if (!open) onClose();
      }}
    >
      <SheetContent
        closeLabel="关闭研究报告"
        className="flex w-full flex-col sm:max-w-4xl motion-reduce:animate-none motion-reduce:transition-none"
        overlayClassName="bg-black/40"
        onOpenAutoFocus={event => {
          const activeElement = document.activeElement;
          if (
            activeElement instanceof HTMLElement &&
            activeElement !== document.body &&
            !(
              event.target instanceof HTMLElement &&
              event.target.contains(activeElement)
            )
          ) {
            returnFocusRef.current = activeElement;
          }
        }}
        onCloseAutoFocus={event => {
          event.preventDefault();
          const trigger = returnFocusRef.current;
          returnFocusRef.current = null;
          if (trigger?.isConnected) trigger.focus({ preventScroll: true });
        }}
      >
        <SheetHeader className="shrink-0 pr-8">
          <SheetTitle>
            {focus === 'joint'
              ? new Set(
                  (criteria.indicatorConditions ?? []).map(
                    condition => condition.indicatorId
                  )
                ).size >= 2
                ? '当前组合研究报告'
                : '当前条件研究报告'
              : `${indicator?.label ?? '指标'} · 单指标报告`}
          </SheetTitle>
          <SheetDescription>
            只读历史研究 ·{' '}
            {pending ? '对应未应用的条件草稿' : '对应当前筛选条件'}
          </SheetDescription>
        </SheetHeader>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1 custom-scrollbar">
          {indicator && (
            <div className="rounded-md border border-white/10 p-3 text-ui-label text-slate-300">
              <p>{indicator.description}</p>
              <p className="mt-1 font-mono text-ui-caption text-slate-400">
                {indicator.id} · {indicator.version} · {indicator.lookback} 日 ·{' '}
                {indicator.unit}
              </p>
              {!indicator.researchSupported && (
                <p className="mt-2 text-amber-200">
                  {indicator.unsupportedReason ??
                    '历史研究暂未覆盖，选股仍可使用。'}
                </p>
              )}
              <p className="mt-2 text-slate-400">
                单指标报告展示总体分组，不代表当前任意阈值的精确研究。
              </p>
            </div>
          )}
          {focus === 'joint' && (
            <div className="rounded-md border border-white/10 p-3 text-ui-label text-slate-300">
              {(criteria.indicatorConditions ?? []).map((condition, index) => (
                <p key={index}>
                  {describeIndicatorCondition(condition, indicators)}
                </p>
              ))}
            </div>
          )}
          {conditionError && focus === 'joint' && (
            <p role="alert" className="text-ui-label text-amber-200">
              {conditionError}；不会忽略未填条件来匹配其他报告。
            </p>
          )}
          {loading && (
            <p role="status" className="text-ui-label text-slate-400">
              正在按当前条件查找报告…
            </p>
          )}
          {error && (
            <p role="alert" className="text-ui-label text-rose-300">
              {error}
            </p>
          )}
          {match && (
            <section
              aria-label="报告匹配状态"
              className="space-y-2 rounded-md border border-white/10 p-3"
            >
              <h2
                className={
                  matchStatus === 'MATCHED'
                    ? 'text-ui-label text-blue-200'
                    : 'text-ui-label text-amber-200'
                }
              >
                {REPORT_MATCH_LABELS[matchStatus] ?? matchStatus}
              </h2>
              {matchReason && (
                <p className="text-ui-label text-slate-300">{matchReason}</p>
              )}
              {visibleBlockers.map(blocker => (
                <p key={blocker} className="text-ui-label text-amber-200">
                  {blocker}
                </p>
              ))}
              {!references.length && (
                <p className="text-ui-label text-slate-400">
                  报告由本地分析命令生成；页面不会启动任务或同步行情。
                </p>
              )}
              <Button
                size="sm"
                variant="outline"
                disabled={match.blockers.length > 0}
                onClick={() => downloadConfig(match.configJson)}
              >
                <Download className="mr-2 h-4 w-4" />
                下载研究配置
              </Button>
              {match.blockers.length === 0 && (
                <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted p-2 font-mono text-ui-caption text-slate-300">
                  {match.command}
                </pre>
              )}
            </section>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                onRefresh();
                refresh();
              }}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              刷新报告
            </Button>
            {reference && (
              <Button size="sm" variant="outline" asChild>
                <a
                  href={`${buildResearchRunPath(reference.studyId, reference.version, reference.runId, reference.runKey)}&report=${encodeURIComponent(reference.reportId)}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink className="mr-2 h-4 w-4" />
                  完整报告（新标签页）
                </a>
              </Button>
            )}
          </div>
          {references.length > 1 && (
            <Select
              value={
                reference ? `${reference.runKey}:${reference.reportId}` : ''
              }
              onValueChange={setSelected}
            >
              <SelectTrigger aria-label="历史报告版本">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {references.map(item => (
                  <SelectItem
                    key={`${item.runKey}:${item.reportId}`}
                    value={`${item.runKey}:${item.reportId}`}
                  >
                    {item.dataStart} → {item.dataEnd} · {item.runId}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          {fetching && !detail && (
            <p role="status" className="text-ui-label text-slate-400">
              正在读取报告…
            </p>
          )}
          {detailError && (
            <p role="alert" className="text-ui-label text-rose-300">
              {detailError.message}
            </p>
          )}
          {reference && !fetching && !detail && !detailError && (
            <p role="status" className="text-ui-label text-amber-200">
              报告已不可用，请刷新索引。
            </p>
          )}
          {detail && (
            <IndicatorReportView
              key={`${reference?.runKey}:${reference?.reportId}`}
              report={detail}
              compact
            />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}
