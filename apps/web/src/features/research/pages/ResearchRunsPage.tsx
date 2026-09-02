import { ChevronLeft, ChevronRight, Filter, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  StudioPanel,
  StudioPanelContent,
  StudioPanelDescription,
  StudioPanelHeader,
  StudioPanelTitle,
} from '@/components/ui/studio-layout';
import {
  ResearchLifecycleRunStage,
  ResearchLifecycleRunStatus,
  type ResearchLifecycleRunFilter,
} from '@/generated/gql/graphql';

import { ResearchCenterFrame } from '../components/ResearchCenterFrame';
import { ResearchLifecycleRunTable } from '../components/ResearchLifecycleRunTable';
import { useResearchLifecycleRuns } from '../hooks';

const ALL = 'ALL';

function ErrorNotice({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      className="flex items-center justify-between gap-3 rounded-control border border-rose-400/20 bg-rose-400/[0.06] px-3 py-2 text-ui-caption text-rose-200"
      role="alert"
    >
      <span>{message}</span>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex h-control-compact shrink-0 cursor-pointer items-center gap-1 rounded-control px-2 text-ui-caption font-semibold text-blue-300 outline-none transition-colors hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        <RefreshCw aria-hidden="true" className="h-3 w-3" />
        重试
      </button>
    </div>
  );
}

function selectValue(value: string | null) {
  return value || ALL;
}

export default function ResearchRunsPage() {
  const [studyId, setStudyId] = useState(ALL);
  const [stage, setStage] = useState(ALL);
  const [status, setStatus] = useState(ALL);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [search, setSearch] = useState('');
  const [limit, setLimit] = useState(20);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setOffset(0);
  }, [dateFrom, dateTo, limit, search, stage, status, studyId]);

  const filter = useMemo<ResearchLifecycleRunFilter>(
    () => ({
      dateFrom: dateFrom || null,
      dateTo: dateTo || null,
      search: search.trim() || null,
      stages:
        stage === ALL
          ? null
          : [
              stage === 'RESEARCH'
                ? ResearchLifecycleRunStage.Research
                : stage === 'DEVELOPMENT'
                  ? ResearchLifecycleRunStage.Development
                  : ResearchLifecycleRunStage.FinalEvaluation,
            ],
      statuses:
        status === ALL
          ? null
          : [
              status === 'QUEUED'
                ? ResearchLifecycleRunStatus.Queued
                : status === 'RUNNING'
                  ? ResearchLifecycleRunStatus.Running
                  : status === 'SUCCEEDED'
                    ? ResearchLifecycleRunStatus.Succeeded
                    : status === 'FAILED'
                      ? ResearchLifecycleRunStatus.Failed
                      : ResearchLifecycleRunStatus.Cancelled,
            ],
      studyId: studyId === ALL ? null : studyId,
    }),
    [dateFrom, dateTo, search, stage, status, studyId]
  );
  const lifecycle = useResearchLifecycleRuns(filter, limit, offset);
  const pageCount = Math.max(1, Math.ceil(lifecycle.total / limit));
  const currentPage = Math.floor(offset / limit) + 1;
  const canGoPrevious = offset > 0;
  const canGoNext = offset + limit < lifecycle.total;

  return (
    <ResearchCenterFrame
      title="实验运行"
      description="所有研究类型共用一张权威生命周期列表，筛选和分页均由服务端完成。"
      actions={
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => lifecycle.refresh()}
          disabled={lifecycle.fetching}
          data-testid="research-runs-refresh"
        >
          <RefreshCw
            aria-hidden="true"
            className={
              lifecycle.fetching
                ? 'animate-spin motion-reduce:animate-none'
                : ''
            }
          />
          刷新
        </Button>
      }
    >
      <StudioPanel className="min-w-0">
        <StudioPanelHeader className="items-start">
          <div className="min-w-0">
            <StudioPanelTitle>统一运行索引</StudioPanelTitle>
            <StudioPanelDescription>
              全局按更新时间倒序稳定排序；total 表示当前筛选后的完整结果数。
            </StudioPanelDescription>
          </div>
          <div className="inline-flex items-center gap-2 text-ui-caption text-slate-500">
            <Filter aria-hidden="true" className="h-3.5 w-3.5" />
            {lifecycle.total.toLocaleString()} 条
          </div>
        </StudioPanelHeader>
        <StudioPanelContent className="space-y-ui-panel">
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-6">
            <label className="space-y-1 xl:col-span-2">
              <span className="text-ui-caption text-slate-500">研究类型</span>
              <Select
                value={selectValue(studyId === ALL ? null : studyId)}
                onValueChange={value => setStudyId(value === ALL ? ALL : value)}
              >
                <SelectTrigger
                  aria-label="研究类型"
                  className="h-control-compact text-ui-label"
                >
                  <SelectValue placeholder="全部研究类型" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>全部研究类型</SelectItem>
                  <SelectItem value="next-day-selection">
                    次日上涨概率
                  </SelectItem>
                  <SelectItem value="indicator-study">指标研究</SelectItem>
                  <SelectItem value="volume-shock">异常放量</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="space-y-1">
              <span className="text-ui-caption text-slate-500">阶段</span>
              <Select value={stage} onValueChange={setStage}>
                <SelectTrigger
                  aria-label="生命周期阶段"
                  className="h-control-compact text-ui-label"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>全部阶段</SelectItem>
                  <SelectItem value="RESEARCH">研究证据</SelectItem>
                  <SelectItem value="DEVELOPMENT">DEVELOPMENT</SelectItem>
                  <SelectItem value="FINAL_EVALUATION">
                    FINAL_EVALUATION
                  </SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="space-y-1">
              <span className="text-ui-caption text-slate-500">状态</span>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger
                  aria-label="运行状态"
                  className="h-control-compact text-ui-label"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>全部状态</SelectItem>
                  <SelectItem value="QUEUED">排队中</SelectItem>
                  <SelectItem value="RUNNING">运行中</SelectItem>
                  <SelectItem value="SUCCEEDED">成功</SelectItem>
                  <SelectItem value="FAILED">失败</SelectItem>
                  <SelectItem value="CANCELLED">已取消</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="space-y-1">
              <span className="text-ui-caption text-slate-500">起始日期</span>
              <Input
                type="date"
                value={dateFrom}
                onChange={event => setDateFrom(event.target.value)}
                className="h-control-compact text-ui-label"
                aria-label="起始日期"
              />
            </label>
            <label className="space-y-1">
              <span className="text-ui-caption text-slate-500">结束日期</span>
              <Input
                type="date"
                value={dateTo}
                onChange={event => setDateTo(event.target.value)}
                className="h-control-compact text-ui-label"
                aria-label="结束日期"
              />
            </label>
          </div>
          <label className="block max-w-xl space-y-1">
            <span className="text-ui-caption text-slate-500">
              搜索运行标识、数据集或版本
            </span>
            <Input
              maxLength={128}
              value={search}
              onChange={event => setSearch(event.target.value)}
              placeholder="搜索运行标识、数据集或版本"
              aria-label="搜索运行标识、数据集或版本"
              className="h-control-compact text-ui-label"
            />
          </label>

          {lifecycle.error ? (
            <ErrorNotice
              message={lifecycle.error.message}
              onRetry={lifecycle.refresh}
            />
          ) : (
            <ResearchLifecycleRunTable
              runs={lifecycle.runs}
              fetching={lifecycle.fetching}
              emptyLabel="没有符合当前筛选条件的运行。"
              ariaLabel="统一研究生命周期运行"
            />
          )}

          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-3">
            <span className="text-ui-caption text-slate-500">
              {lifecycle.total === 0
                ? '0 条结果'
                : `${offset + 1}–${Math.min(offset + limit, lifecycle.total)} / ${lifecycle.total.toLocaleString()} 条`}
            </span>
            <div className="flex items-center gap-2">
              <label className="inline-flex items-center gap-2 text-ui-caption text-slate-500">
                每页
                <Select
                  value={String(limit)}
                  onValueChange={value => setLimit(Number(value))}
                >
                  <SelectTrigger
                    aria-label="每页数量"
                    className="h-control-compact w-20 text-ui-label"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="10">10</SelectItem>
                    <SelectItem value="20">20</SelectItem>
                    <SelectItem value="50">50</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <button
                type="button"
                aria-label="上一页"
                disabled={!canGoPrevious || lifecycle.fetching}
                onClick={() => setOffset(Math.max(0, offset - limit))}
                className="inline-flex h-control-compact w-control-compact cursor-pointer items-center justify-center rounded-control border border-white/10 text-slate-400 outline-none transition-colors hover:border-blue-400/40 hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronLeft aria-hidden="true" className="h-3.5 w-3.5" />
              </button>
              <span className="min-w-16 text-center font-mono text-ui-caption tabular-nums text-slate-400">
                {currentPage} / {pageCount}
              </span>
              <button
                type="button"
                aria-label="下一页"
                disabled={!canGoNext || lifecycle.fetching}
                onClick={() => setOffset(offset + limit)}
                className="inline-flex h-control-compact w-control-compact cursor-pointer items-center justify-center rounded-control border border-white/10 text-slate-400 outline-none transition-colors hover:border-blue-400/40 hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <ChevronRight aria-hidden="true" className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>
        </StudioPanelContent>
      </StudioPanel>
    </ResearchCenterFrame>
  );
}
