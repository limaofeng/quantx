import { ArrowLeft, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useIndicatorReport } from '@/features/screening/hooks/useIndicatorResearch';
import type { ResearchRunQuery } from '@/generated/gql/graphql';

import { IndicatorReportView } from './IndicatorReportView';
import { ResearchStatusBadge } from './ResearchStatusBadge';

export function IndicatorStudyRun({
  run,
}: {
  run: NonNullable<ResearchRunQuery['researchRun']>;
}) {
  const initialReport =
    new URLSearchParams(window.location.search).get('report') ?? '';
  const [selected, setSelected] = useState(initialReport);
  const reference = selected
    ? run.indicatorReports.find(item => item.reportId === selected)
    : run.indicatorReports[0];
  const { detail, error, fetching, refresh } = useIndicatorReport(
    reference?.runKey ?? '',
    reference?.reportId ?? ''
  );
  return (
    <main className="studio-workspace-surface h-full overflow-y-auto text-slate-200">
      <header className="sticky top-0 z-20 flex flex-wrap items-center gap-3 border-b border-white/10 bg-background p-ui-section">
        <Button size="icon" variant="outline" asChild>
          <Link href="/research" aria-label="返回研究中心">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-ui-title font-semibold">指标历史研究</h1>
          <p className="truncate font-mono text-ui-caption text-slate-400">
            {run.summary.version} / {run.summary.runId}
          </p>
        </div>
        <ResearchStatusBadge status={run.summary.status} />
        <Button
          size="sm"
          variant="outline"
          disabled={fetching}
          onClick={refresh}
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          刷新报告
        </Button>
      </header>
      <div className="studio-content-width mx-auto space-y-3 p-ui-section">
        {run.artifactErrors.length > 0 && (
          <p role="alert" className="text-ui-label text-rose-300">
            {run.artifactErrors.join('；')}
          </p>
        )}
        {reference ? (
          <Select value={reference.reportId} onValueChange={setSelected}>
            <SelectTrigger aria-label="选择指标报告">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {run.indicatorReports.map(item => (
                <SelectItem key={item.reportId} value={item.reportId}>
                  {item.kind === 'joint' ? '条件交集报告' : '单指标'} ·{' '}
                  {item.indicatorIds.join(' + ')}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <p
            role="status"
            className="rounded-md border border-amber-400/20 p-3 text-ui-label text-amber-200"
          >
            {selected
              ? '链接中的指标报告不存在，未替换为其他报告。'
              : '本次运行没有可用指标报告。请检查离线运行状态与产物。'}
            {selected && (
              <Button
                size="sm"
                variant="outline"
                onClick={() => setSelected('')}
              >
                返回本次报告列表
              </Button>
            )}
          </p>
        )}
        {fetching && !detail && (
          <p role="status" className="text-ui-label text-slate-400">
            正在读取结构化研究结果…
          </p>
        )}
        {error && (
          <p role="alert" className="text-ui-label text-rose-300">
            {error.message}
          </p>
        )}
        {detail && (
          <IndicatorReportView key={reference?.reportId} report={detail} />
        )}
      </div>
    </main>
  );
}
