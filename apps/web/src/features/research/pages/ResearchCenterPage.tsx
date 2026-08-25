import {
  ArrowRight,
  BarChart3,
  CheckCircle2,
  FlaskConical,
  RefreshCw,
  Rows3,
} from 'lucide-react';
import { useState } from 'react';
import { Link } from 'wouter';

import {
  ResearchEmptyState,
  ResearchErrorState,
  ResearchLoadingState,
  ResearchStatusBadge,
} from '../components';
import { useResearchRuns } from '../hooks';
import { buildResearchRunPath, isSmallSample } from '../model';

export interface ResearchRunListItem {
  artifactErrors: string[];
  completedAt?: string | null;
  elapsedSeconds?: number | null;
  eventCount?: number | null;
  hasMetrics: boolean;
  key: string;
  runId: string;
  startedAt?: string | null;
  status: string;
  studyId: string;
  version: string;
}

const STUDY_LABELS: Record<string, string> = {
  'volume-shock': '异常放量 × 价格位置',
};

const FILTERS = [
  { label: '全部', value: null },
  { label: '成功', value: 'success' },
  { label: '失败', value: 'failed' },
] as const;

function formatDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '—'
    : date.toLocaleString('zh-CN', {
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        month: '2-digit',
        year: 'numeric',
      });
}

function QualityBadge({ run }: { run: ResearchRunListItem }) {
  if (run.artifactErrors.length > 0) {
    return (
      <span className="text-ui-caption font-bold text-rose-300">
        产物异常 {run.artifactErrors.length}
      </span>
    );
  }
  if (!run.hasMetrics) {
    return (
      <span className="text-ui-caption font-bold text-amber-300">统计缺失</span>
    );
  }
  if (isSmallSample(run.version, run.eventCount)) {
    return (
      <span className="text-ui-caption font-bold text-amber-200">
        小样本验证
      </span>
    );
  }
  return (
    <span className="text-ui-caption font-bold text-emerald-300">产物完整</span>
  );
}

export function ResearchRunsView({
  fetching,
  runs,
  total,
}: {
  fetching: boolean;
  runs: ResearchRunListItem[];
  total: number;
}) {
  if (fetching && runs.length === 0) {
    return <ResearchLoadingState label="正在读取研究运行索引" />;
  }
  if (runs.length === 0) {
    return (
      <ResearchEmptyState
        title="还没有研究结果"
        description="离线研究完成并生成受支持的结果产物后，会自动出现在这里。"
      />
    );
  }

  const successful = runs.filter(
    run => run.status.toLowerCase() === 'success'
  ).length;
  const totalEvents = runs.reduce((sum, run) => sum + (run.eventCount || 0), 0);

  return (
    <>
      <div className="grid grid-cols-4 gap-2 border-b border-white/[0.06] p-3">
        {[
          { icon: Rows3, label: '运行总数', value: total.toLocaleString() },
          { icon: CheckCircle2, label: '本页成功', value: successful },
          {
            icon: BarChart3,
            label: '本页事件',
            value: totalEvents.toLocaleString(),
          },
          {
            icon: FlaskConical,
            label: '研究类型',
            value: new Set(runs.map(run => run.studyId)).size,
          },
        ].map(item => {
          const Icon = item.icon;
          return (
            <div
              key={item.label}
              className="rounded-md border border-white/[0.05] bg-white/[0.02] px-3 py-2.5"
            >
              <div className="flex items-center gap-2 text-ui-micro font-black uppercase tracking-wider text-slate-600">
                <Icon className="h-3 w-3 text-blue-400" />
                {item.label}
              </div>
              <div className="mt-1.5 font-mono text-ui-heading font-black tabular-nums text-slate-100">
                {item.value}
              </div>
            </div>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full min-w-[860px] text-left text-ui-caption">
          <caption className="sr-only">研究运行列表</caption>
          <thead className="sticky top-0 z-10 bg-[#0b1423] text-ui-micro uppercase tracking-wider text-slate-600">
            <tr>
              <th className="px-ui-section py-3">状态</th>
              <th className="px-ui-section py-3">研究</th>
              <th className="px-ui-section py-3">版本</th>
              <th className="px-ui-section py-3">完成时间</th>
              <th className="px-ui-section py-3 text-right">事件数</th>
              <th className="px-ui-section py-3">质量</th>
              <th className="w-12 px-ui-section py-3">
                <span className="sr-only">操作</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.05]">
            {runs.map(run => {
              const href = buildResearchRunPath(
                run.studyId,
                run.version,
                run.runId,
                run.key
              );
              return (
                <tr
                  key={run.key}
                  className="group transition-colors hover:bg-white/[0.025]"
                >
                  <td className="px-ui-section py-3">
                    <ResearchStatusBadge status={run.status} />
                  </td>
                  <td className="px-ui-section py-3">
                    <Link
                      href={href}
                      className="cursor-pointer font-bold text-slate-200 outline-none transition-colors hover:text-blue-300 focus-visible:rounded focus-visible:ring-2 focus-visible:ring-blue-500"
                    >
                      {STUDY_LABELS[run.studyId] || run.studyId}
                    </Link>
                    <div className="mt-0.5 max-w-64 truncate font-mono text-ui-micro text-slate-600">
                      {run.runId}
                    </div>
                  </td>
                  <td className="px-ui-section py-3 font-mono text-slate-400">
                    {run.version}
                  </td>
                  <td className="whitespace-nowrap px-ui-section py-3 text-slate-500">
                    {formatDate(run.completedAt || run.startedAt)}
                  </td>
                  <td className="px-ui-section py-3 text-right font-mono font-bold tabular-nums text-slate-300">
                    {run.eventCount?.toLocaleString() ?? '—'}
                  </td>
                  <td className="px-ui-section py-3">
                    <QualityBadge run={run} />
                  </td>
                  <td className="px-ui-section py-3">
                    <Link
                      href={href}
                      aria-label={`查看 ${run.runId} 的研究结果`}
                      className="flex h-7 w-7 cursor-pointer items-center justify-center rounded text-slate-600 transition-colors hover:bg-blue-500/10 hover:text-blue-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                    >
                      <ArrowRight className="h-3.5 w-3.5" />
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="shrink-0 border-t border-white/[0.05] px-ui-section py-2 text-right text-ui-micro text-slate-600">
        当前展示最近 {runs.length.toLocaleString()} 次运行，单次最多加载 100 条
      </div>
    </>
  );
}

export default function ResearchCenterPage() {
  const [status, setStatus] = useState<string | null>(null);
  const { error, fetching, refresh, runs, total } = useResearchRuns(status);

  return (
    <main className="studio-workspace-surface flex h-full min-h-0 flex-col overflow-hidden">
      <header className="studio-workspace-surface flex shrink-0 flex-wrap items-center gap-3 border-b border-white/[0.06] px-ui-section py-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-blue-400" />
            <h1 className="text-ui-body font-black text-slate-100">研究中心</h1>
          </div>
          <p className="mt-1 text-ui-caption text-slate-500">
            查看最近 100 次离线因子研究的可复现结果、样本质量与统计检验。
          </p>
        </div>
        <div
          className="flex items-center gap-1"
          role="group"
          aria-label="运行状态"
        >
          {FILTERS.map(filter => (
            <button
              key={filter.label}
              type="button"
              aria-pressed={status === filter.value}
              onClick={() => setStatus(filter.value)}
              className={`h-8 cursor-pointer rounded px-3 text-ui-caption font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                status === filter.value
                  ? 'bg-blue-500/15 text-blue-200'
                  : 'text-slate-500 hover:bg-white/[0.04] hover:text-slate-300'
              }`}
            >
              {filter.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={fetching}
          className="flex h-8 cursor-pointer items-center gap-2 rounded-md border border-white/10 px-3 text-ui-caption font-bold text-slate-400 transition-colors hover:border-blue-500/40 hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-wait disabled:opacity-60"
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${fetching ? 'animate-spin motion-reduce:animate-none' : ''}`}
          />
          刷新
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        {error ? (
          <ResearchErrorState message={error.message} onRetry={refresh} />
        ) : (
          <ResearchRunsView fetching={fetching} runs={runs} total={total} />
        )}
      </div>
    </main>
  );
}
