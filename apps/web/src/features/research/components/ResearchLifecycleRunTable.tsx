import {
  ArrowRight,
  CircleAlert,
  CircleCheck,
  CircleDashed,
  Clock3,
  LoaderCircle,
} from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'wouter';

import type { ResearchLifecycleRun } from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import { researchLifecycleRunHref } from '../model';

export type ResearchLifecycleRunRow = ResearchLifecycleRun;

const STUDY_LABELS: Record<string, string> = {
  'indicator-study': '单指标与条件交集研究',
  'next-day-selection': '次日上涨概率',
  'volume-shock': '异常放量 × 价格位置',
};

const STAGE_LABELS: Record<string, string> = {
  DEVELOPMENT: 'DEVELOPMENT',
  FINAL_EVALUATION: 'FINAL_EVALUATION',
  RESEARCH: '研究证据',
};

const CONCLUSION_LABELS: Record<string, string> = {
  ACTIVE_ELIGIBLE: '可进入 ACTIVE',
  BLOCKED: '门禁阻断',
  SHADOW_ELIGIBLE: '可进入 SHADOW',
};

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('zh-CN', {
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    month: '2-digit',
    year: 'numeric',
  });
}

function studyLabel(studyId: string) {
  return STUDY_LABELS[studyId] || studyId || '不可用';
}

function statusMeta(status: string) {
  switch (status) {
    case 'QUEUED':
      return {
        className: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
        icon: Clock3,
        label: '排队中',
      };
    case 'RUNNING':
      return {
        className: 'border-blue-400/30 bg-blue-400/10 text-blue-200',
        icon: LoaderCircle,
        label: '运行中',
      };
    case 'SUCCEEDED':
      return {
        className: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200',
        icon: CircleCheck,
        label: '成功',
      };
    case 'FAILED':
      return {
        className: 'border-rose-400/25 bg-rose-400/10 text-rose-200',
        icon: CircleAlert,
        label: '失败',
      };
    case 'CANCELLED':
      return {
        className: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
        icon: CircleDashed,
        label: '已取消',
      };
    default:
      return {
        className: 'border-slate-500/30 bg-slate-500/10 text-slate-300',
        icon: CircleDashed,
        label: status || '不可用',
      };
  }
}

function LifecycleStatusBadge({ status }: { status: string }) {
  const meta = statusMeta(status);
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        'inline-flex h-5 items-center gap-1 rounded-control border px-1.5 text-ui-caption font-semibold',
        meta.className
      )}
    >
      <Icon
        aria-hidden="true"
        className={cn(
          'h-3 w-3',
          status === 'RUNNING' && 'animate-spin motion-reduce:animate-none'
        )}
      />
      {meta.label}
    </span>
  );
}

function progressOrConclusion(run: ResearchLifecycleRun): ReactNode {
  if (run.training) {
    if (run.status === 'FAILED') {
      return (
        <span
          className="block max-w-56 truncate text-rose-200"
          title={run.training.errorMessage || undefined}
        >
          {run.training.errorCode || run.training.errorMessage || '失败'}
        </span>
      );
    }
    if (run.status === 'QUEUED' && run.training.queueReason) {
      return (
        <span
          className="block max-w-56 truncate text-amber-200"
          title={run.training.queueReason}
        >
          {run.training.queueReason}
        </span>
      );
    }
    if (run.status === 'SUCCEEDED' && run.training.conclusion) {
      return (
        <span className="text-emerald-200">
          {CONCLUSION_LABELS[run.training.conclusion] ||
            run.training.conclusion}
        </span>
      );
    }
    const completed = run.training.completedUnits.toLocaleString();
    const total = run.training.totalUnits.toLocaleString();
    return (
      <span className="font-mono tabular-nums text-slate-300">
        {completed} / {total}
      </span>
    );
  }

  if (run.artifact) {
    if (run.artifact.artifactErrors.length > 0) {
      return (
        <span
          className="block max-w-56 truncate text-rose-200"
          title={run.artifact.artifactErrors.join('；')}
        >
          产物异常 {run.artifact.artifactErrors.length} 项
        </span>
      );
    }
    return (
      <span
        className={
          run.artifact.hasMetrics ? 'text-emerald-200' : 'text-amber-200'
        }
      >
        {run.artifact.hasMetrics ? '证据有效' : '指标不可用'}
      </span>
    );
  }

  return <span className="text-slate-600">—</span>;
}

export interface ResearchLifecycleRunTableProps {
  ariaLabel?: string;
  className?: string;
  emptyLabel?: string;
  fetching?: boolean;
  runs: readonly ResearchLifecycleRun[];
}

export function ResearchLifecycleRunTable({
  ariaLabel = '研究生命周期运行列表',
  className,
  emptyLabel = '没有符合条件的运行。',
  fetching = false,
  runs,
}: ResearchLifecycleRunTableProps) {
  return (
    <div
      className={cn('max-h-[32rem] overflow-auto custom-scrollbar', className)}
    >
      <table className="w-full min-w-[900px] border-collapse text-left text-ui-label">
        <caption className="sr-only">{ariaLabel}</caption>
        <thead className="sticky top-0 z-10 bg-[#0b1423] text-ui-caption text-slate-500">
          <tr className="border-b border-white/[0.06]">
            <th className="h-ui-table-header px-3 font-semibold">状态</th>
            <th className="h-ui-table-header px-3 font-semibold">研究</th>
            <th className="h-ui-table-header px-3 font-semibold">阶段</th>
            <th className="h-ui-table-header px-3 font-semibold">
              数据集或版本
            </th>
            <th className="h-ui-table-header px-3 font-semibold">后端</th>
            <th className="h-ui-table-header px-3 font-semibold">进度或结论</th>
            <th className="h-ui-table-header px-3 font-semibold">更新时间</th>
            <th className="h-ui-table-header px-3 font-semibold">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.05]">
          {fetching && runs.length === 0 ? (
            <tr>
              <td
                className="px-3 py-ui-empty text-center text-ui-caption text-slate-500"
                colSpan={8}
              >
                正在读取运行索引…
              </td>
            </tr>
          ) : runs.length === 0 ? (
            <tr>
              <td
                className="px-3 py-ui-empty text-center text-ui-caption text-slate-500"
                colSpan={8}
              >
                {emptyLabel}
              </td>
            </tr>
          ) : (
            runs.map(run => {
              const href = researchLifecycleRunHref(run);
              const version =
                run.training?.datasetVersion || run.artifact?.version;
              const backend =
                run.training?.resolvedBackend || run.training?.requestedBackend;
              const updatedAt =
                run.updatedAt ||
                run.completedAt ||
                run.startedAt ||
                run.requestedAt;
              return (
                <tr key={run.id} className="group hover:bg-blue-500/[0.04]">
                  <td className="whitespace-nowrap px-3 py-ui-table-cell-y">
                    <LifecycleStatusBadge status={run.status} />
                  </td>
                  <td className="max-w-56 px-3 py-ui-table-cell-y">
                    <div
                      className="truncate font-semibold text-slate-200"
                      title={studyLabel(run.studyId)}
                    >
                      {studyLabel(run.studyId)}
                    </div>
                    <div
                      className="mt-0.5 truncate font-mono text-ui-caption text-slate-600"
                      title={run.runId}
                    >
                      {run.runId || '不可用'}
                    </div>
                  </td>
                  <td className="whitespace-nowrap px-3 py-ui-table-cell-y text-slate-400">
                    {STAGE_LABELS[run.stage] || run.stage || '不可用'}
                  </td>
                  <td className="max-w-52 px-3 py-ui-table-cell-y">
                    <span
                      className="block truncate font-mono text-slate-300"
                      title={version || undefined}
                    >
                      {version || '—'}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-3 py-ui-table-cell-y font-mono text-slate-400">
                    {backend || '—'}
                  </td>
                  <td className="max-w-64 px-3 py-ui-table-cell-y">
                    {progressOrConclusion(run)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-ui-table-cell-y font-mono text-ui-caption text-slate-500">
                    {formatDate(updatedAt)}
                  </td>
                  <td className="whitespace-nowrap px-3 py-ui-table-cell-y">
                    {href ? (
                      <Link
                        href={href}
                        className="inline-flex h-control-compact items-center gap-1 rounded-control px-2 text-ui-caption font-semibold text-blue-300 outline-none transition-colors hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
                      >
                        查看
                        <ArrowRight aria-hidden="true" className="h-3 w-3" />
                      </Link>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
