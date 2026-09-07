import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Database,
  FlaskConical,
  Gauge,
  RefreshCw,
  ServerCog,
} from 'lucide-react';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import {
  StudioPanel,
  StudioPanelContent,
  StudioPanelDescription,
  StudioPanelHeader,
  StudioPanelTitle,
} from '@/components/ui/studio-layout';

import { ResearchCenterFrame } from '../components/ResearchCenterFrame';
import {
  useResearchRuns,
  useStockSelectionDatasetVersions,
  useStockSelectionTrainingCapabilities,
  useStockSelectionTrainingRuns,
} from '../hooks';
import { buildResearchRunPath } from '../model';

const STUDY_LABELS: Record<string, string> = {
  'indicator-study': '单指标与条件交集研究',
  'next-day-selection': '次日上涨概率模型训练',
  'volume-shock': '异常放量 × 价格位置',
};

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

function trainingStageLabel(runKind: string) {
  return runKind === 'FINAL_EVALUATION' ? 'FINAL_EVALUATION' : 'DEVELOPMENT';
}

function statusLabel(status: string) {
  switch (status) {
    case 'QUEUED':
      return '排队中';
    case 'RUNNING':
      return '运行中';
    case 'SUCCEEDED':
      return '成功';
    case 'FAILED':
      return '失败';
    case 'CANCELLED':
      return '已取消';
    default:
      return status || '不可用';
  }
}

type ArtifactRun = ReturnType<typeof useResearchRuns>['runs'][number];

function PartialError({
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

function ActionItem({
  children,
  description,
  href,
  tone = 'warning',
}: {
  children: ReactNode;
  description?: string;
  href: string;
  tone?: 'danger' | 'warning';
}) {
  return (
    <div className="flex items-start gap-3 rounded-control border border-white/[0.06] bg-white/[0.02] px-3 py-3">
      {tone === 'danger' ? (
        <CircleAlert
          aria-hidden="true"
          className="mt-0.5 h-4 w-4 shrink-0 text-rose-300"
        />
      ) : (
        <AlertTriangle
          aria-hidden="true"
          className="mt-0.5 h-4 w-4 shrink-0 text-amber-300"
        />
      )}
      <div className="min-w-0 flex-1">
        <div className="text-ui-label font-semibold text-slate-200">
          {children}
        </div>
        {description && (
          <div className="mt-1 text-ui-caption text-slate-500">
            {description}
          </div>
        )}
      </div>
      <Link
        href={href}
        className="inline-flex h-control-compact shrink-0 cursor-pointer items-center gap-1 rounded-control border border-white/10 px-2 text-ui-caption font-semibold text-blue-300 outline-none transition-colors hover:border-blue-400/40 hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        处理
        <ArrowRight aria-hidden="true" className="h-3 w-3" />
      </Link>
    </div>
  );
}

function EmptyPanel({ children }: { children: string }) {
  return (
    <div className="py-ui-empty text-center text-ui-caption text-slate-600">
      {children}
    </div>
  );
}

function LoadingNotice({ children }: { children: string }) {
  return (
    <div
      className="flex items-center gap-2 py-ui-empty text-center text-ui-caption text-slate-500"
      role="status"
      aria-live="polite"
    >
      <RefreshCw
        aria-hidden="true"
        className="h-3.5 w-3.5 animate-spin text-blue-300 motion-reduce:animate-none"
      />
      <span>{children}</span>
    </div>
  );
}

function ProgressBar({
  completed,
  total,
}: {
  completed: number;
  total: number;
}) {
  const ratio = total > 0 ? Math.min(1, Math.max(0, completed / total)) : 0;
  return (
    <div className="min-w-28">
      <div className="h-1.5 overflow-hidden rounded-control bg-slate-800">
        <div
          className="h-full rounded-control bg-blue-500"
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
      <div className="mt-1 font-mono text-ui-caption tabular-nums text-slate-500">
        {total > 0 ? `${Math.round(ratio * 100)}%` : '不可用'}
      </div>
    </div>
  );
}

type TrainingRun = ReturnType<
  typeof useStockSelectionTrainingRuns
>['runs'][number];

function OverviewEvidenceTable({
  artifactRuns,
  trainingRuns,
}: {
  artifactRuns: readonly ArtifactRun[];
  trainingRuns: readonly TrainingRun[];
}) {
  const rows = [
    ...artifactRuns.slice(0, 4).map(run => ({
      action: '查看证据',
      href: buildResearchRunPath(run.studyId, run.version, run.runId, run.key),
      id: `artifact:${run.key}`,
      research: STUDY_LABELS[run.studyId] || run.studyId,
      stage: '研究证据',
      version: run.version,
      conclusion:
        run.hasMetrics && run.artifactErrors.length === 0
          ? '证据有效'
          : '不可用',
      updatedAt: run.completedAt || run.startedAt,
    })),
    ...trainingRuns.slice(0, 4).map(run => ({
      action: run.registerable ? '审阅并登记' : '查看运行',
      href: `/research/training/runs/${encodeURIComponent(run.runId)}`,
      id: `training:${run.runId}`,
      research: '次日上涨概率',
      stage: trainingStageLabel(run.runKind),
      version: run.datasetVersion || '—',
      conclusion:
        run.conclusion ||
        (run.status === 'SUCCEEDED' ? '证据有效' : statusLabel(run.status)),
      updatedAt: run.completedAt || run.startedAt || run.requestedAt,
    })),
  ].sort((a, b) => {
    const left = a.updatedAt ? Date.parse(a.updatedAt) : 0;
    const right = b.updatedAt ? Date.parse(b.updatedAt) : 0;
    return right - left;
  });

  if (rows.length === 0)
    return <EmptyPanel>还没有可审阅的有效证据。</EmptyPanel>;

  return (
    <div className="overflow-auto">
      <table className="w-full min-w-[760px] border-collapse text-left text-ui-label">
        <caption className="sr-only">最近有效证据</caption>
        <thead className="text-ui-caption text-slate-500">
          <tr className="border-b border-white/[0.06]">
            <th className="h-ui-table-header px-3 font-semibold">研究</th>
            <th className="h-ui-table-header px-3 font-semibold">阶段</th>
            <th className="h-ui-table-header px-3 font-semibold">
              数据集或版本
            </th>
            <th className="h-ui-table-header px-3 font-semibold">结论</th>
            <th className="h-ui-table-header px-3 font-semibold">更新时间</th>
            <th className="h-ui-table-header px-3 font-semibold">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.05]">
          {rows.slice(0, 6).map(row => (
            <tr key={row.id} className="hover:bg-blue-500/[0.04]">
              <td className="px-3 py-ui-table-cell-y font-semibold text-slate-200">
                {row.research}
              </td>
              <td className="px-3 py-ui-table-cell-y text-slate-400">
                {row.stage}
              </td>
              <td className="max-w-56 truncate px-3 py-ui-table-cell-y font-mono text-slate-400">
                {row.version}
              </td>
              <td className="px-3 py-ui-table-cell-y">
                <span
                  className={
                    row.conclusion === '证据有效'
                      ? 'text-emerald-200'
                      : 'text-amber-200'
                  }
                >
                  {row.conclusion}
                </span>
              </td>
              <td className="whitespace-nowrap px-3 py-ui-table-cell-y font-mono text-ui-caption text-slate-500">
                {formatDate(row.updatedAt)}
              </td>
              <td className="px-3 py-ui-table-cell-y">
                <Link
                  href={row.href}
                  className="inline-flex h-control-compact cursor-pointer items-center gap-1 rounded-control px-2 text-ui-caption font-semibold text-blue-300 outline-none transition-colors hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
                >
                  {row.action}
                  <ArrowRight aria-hidden="true" className="h-3 w-3" />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ResearchCenterPage() {
  const research = useResearchRuns(null, null);
  const training = useStockSelectionTrainingRuns();
  const capabilities = useStockSelectionTrainingCapabilities();
  const datasets = useStockSelectionDatasetVersions();

  const certifiedDatasets = useMemo(
    () =>
      datasets.data.filter(
        dataset => dataset.status.toUpperCase() === 'CERTIFIED'
      ),
    [datasets.data]
  );
  const activeTrainingRuns = useMemo(
    () =>
      training.runs.filter(
        run => run.status === 'QUEUED' || run.status === 'RUNNING'
      ),
    [training.runs]
  );
  const failedTrainingRuns = useMemo(
    () => training.runs.filter(run => run.status === 'FAILED').slice(0, 3),
    [training.runs]
  );
  const pendingFinalRuns = useMemo(
    () =>
      training.runs
        .filter(
          run =>
            run.runKind === 'FINAL_EVALUATION' &&
            run.status === 'SUCCEEDED' &&
            run.registerable
        )
        .slice(0, 3),
    [training.runs]
  );
  const recentArtifactRuns = useMemo(
    () =>
      research.runs.filter(
        run =>
          run.status.toLowerCase() === 'success' &&
          run.hasMetrics &&
          run.artifactErrors.length === 0
      ),
    [research.runs]
  );
  const recentTrainingRuns = useMemo(
    () => training.runs.filter(run => run.status === 'SUCCEEDED'),
    [training.runs]
  );

  const capabilitySnapshotLoaded =
    !capabilities.fetching && !capabilities.error;
  const datasetsLoading = datasets.fetching && datasets.data.length === 0;
  const capabilityLoading = capabilities.fetching && !capabilities.data;
  const trainingLoading = training.fetching && training.runs.length === 0;
  const researchLoading = research.fetching && research.runs.length === 0;
  const missingCertifiedDataset =
    !datasets.fetching && !datasets.error && certifiedDatasets.length === 0;
  const capabilityNeedsAttention =
    capabilitySnapshotLoaded &&
    (!capabilities.data?.fresh || !capabilities.data.updatedAt);
  const gpuNeedsAttention =
    capabilitySnapshotLoaded &&
    Boolean(capabilities.data) &&
    capabilities.data?.gpuStatus !== 'GPU_AVAILABLE';
  const pendingActionCount =
    Number(missingCertifiedDataset) +
    Number(capabilityNeedsAttention) +
    Number(gpuNeedsAttention) +
    failedTrainingRuns.length +
    pendingFinalRuns.length;

  const actions = (
    <>
      <Button asChild size="lg" data-testid="research-new-training">
        <Link href="/research/training/new">
          <FlaskConical aria-hidden="true" />
          新建模型训练
        </Link>
      </Button>
      <Button
        type="button"
        size="lg"
        variant="outline"
        disabled
        title="当前 Web 端没有指标研究创建 API"
        data-testid="research-new-indicator-disabled"
      >
        新建指标研究
      </Button>
    </>
  );

  return (
    <ResearchCenterFrame
      title="研究中心"
      description="发起研究、管理实验、审阅证据，并将合格结果交接给模型库。"
      actions={actions}
    >
      <div className="rounded-control border border-blue-400/15 bg-blue-500/[0.04] px-3 py-2 text-ui-caption text-slate-400">
        指标研究创建入口暂未开放：当前 Web 端没有真实创建
        API；已有研究结果仍可在实验运行中审阅。
      </div>

      <div className="grid gap-ui-section xl:grid-cols-2">
        <StudioPanel>
          <StudioPanelHeader>
            <div className="flex items-center gap-2">
              <AlertTriangle
                aria-hidden="true"
                className="h-4 w-4 text-amber-300"
              />
              <StudioPanelTitle>需要处理</StudioPanelTitle>
            </div>
            <span className="text-ui-caption text-slate-600">
              {pendingActionCount} 项
            </span>
          </StudioPanelHeader>
          <StudioPanelContent className="space-y-2">
            {datasets.error && (
              <PartialError
                message="认证数据集状态读取失败。"
                onRetry={datasets.refresh}
              />
            )}
            {datasetsLoading && (
              <LoadingNotice>正在读取认证数据集…</LoadingNotice>
            )}
            {missingCertifiedDataset && (
              <ActionItem
                href="/settings/data/research"
                description="请在数据管理中认证可用于训练的数据集。"
              >
                没有认证数据集
              </ActionItem>
            )}
            {capabilities.error && (
              <PartialError
                message="训练能力读取失败。"
                onRetry={capabilities.refresh}
              />
            )}
            {capabilityLoading && (
              <LoadingNotice>正在读取训练能力快照…</LoadingNotice>
            )}
            {!capabilities.error && capabilityNeedsAttention && (
              <ActionItem
                href="/settings/status"
                description="检查 CPU/GPU 能力快照和更新时间。"
              >
                训练能力快照缺失或已过期
              </ActionItem>
            )}
            {!capabilities.error && gpuNeedsAttention && (
              <ActionItem
                href="/settings/status"
                description="GPU 资格问题不阻断 CPU 训练，但需要了解实际后端。"
              >
                GPU 尚未通过资格验证
              </ActionItem>
            )}
            {failedTrainingRuns.map(run => (
              <ActionItem
                key={run.runId}
                href={`/research/training/runs/${encodeURIComponent(run.runId)}`}
                description={
                  run.errorMessage ||
                  run.errorCode ||
                  '查看失败阶段和可执行的重试方向。'
                }
                tone="danger"
              >
                训练运行失败
                <span className="sr-only">{run.runId}</span>
              </ActionItem>
            ))}
            {pendingFinalRuns.map(run => (
              <ActionItem
                key={run.runId}
                href={`/research/training/runs/${encodeURIComponent(run.runId)}`}
                description="FINAL 证据已经完成，请人工审阅后登记模型。"
              >
                FINAL 证据等待人工登记
                <span className="sr-only">{run.runId}</span>
              </ActionItem>
            ))}
            {!datasets.error &&
              !capabilities.error &&
              !datasetsLoading &&
              !capabilityLoading &&
              certifiedDatasets.length > 0 &&
              !capabilityNeedsAttention &&
              !gpuNeedsAttention &&
              failedTrainingRuns.length === 0 &&
              pendingFinalRuns.length === 0 && (
                <EmptyPanel>当前没有需要人工处理的事项。</EmptyPanel>
              )}
          </StudioPanelContent>
        </StudioPanel>

        <StudioPanel>
          <StudioPanelHeader>
            <div className="flex items-center gap-2">
              <Clock3 aria-hidden="true" className="h-4 w-4 text-blue-300" />
              <StudioPanelTitle>进行中</StudioPanelTitle>
            </div>
            <span className="text-ui-caption text-slate-600">
              {activeTrainingRuns.length} 项
            </span>
          </StudioPanelHeader>
          <StudioPanelContent className="space-y-2">
            {training.error && (
              <PartialError
                message="训练运行读取失败。"
                onRetry={training.refresh}
              />
            )}
            {trainingLoading && (
              <LoadingNotice>正在读取训练运行…</LoadingNotice>
            )}
            {!training.error &&
              !trainingLoading &&
              activeTrainingRuns.length === 0 && (
                <EmptyPanel>当前没有排队或运行中的任务。</EmptyPanel>
              )}
            {activeTrainingRuns.slice(0, 4).map(run => (
              <Link
                key={run.runId}
                href={`/research/training/runs/${encodeURIComponent(run.runId)}`}
                className="flex cursor-pointer items-center gap-3 rounded-control border border-white/[0.06] bg-white/[0.02] px-3 py-3 outline-none transition-colors hover:border-blue-400/30 hover:bg-blue-500/[0.05] focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                <Gauge
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0 text-blue-300"
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-ui-label font-semibold text-slate-200">
                    次日上涨概率 · {trainingStageLabel(run.runKind)}
                  </div>
                  <div className="mt-1 flex items-center gap-3">
                    <ProgressBar
                      completed={run.completedUnits}
                      total={run.totalUnits}
                    />
                    <span className="whitespace-nowrap text-ui-caption text-blue-200">
                      {statusLabel(run.status)}
                    </span>
                  </div>
                </div>
                <ArrowRight
                  aria-hidden="true"
                  className="h-4 w-4 shrink-0 text-slate-600"
                />
              </Link>
            ))}
            {!training.error && activeTrainingRuns.length > 4 && (
              <Link
                href="/research/training"
                className="block pt-1 text-center text-ui-caption font-semibold text-blue-300 hover:text-blue-200"
              >
                查看全部进行中任务
              </Link>
            )}
          </StudioPanelContent>
        </StudioPanel>
      </div>

      <StudioPanel>
        <StudioPanelHeader>
          <div className="flex items-center gap-2">
            <CheckCircle2
              aria-hidden="true"
              className="h-4 w-4 text-emerald-300"
            />
            <StudioPanelTitle>最近有效证据</StudioPanelTitle>
          </div>
          <Link
            href="/research/runs"
            className="text-ui-caption font-semibold text-blue-300 hover:text-blue-200"
          >
            查看实验运行
          </Link>
        </StudioPanelHeader>
        <StudioPanelDescription className="px-ui-panel pt-2">
          有效产物和训练结论保留在各自详情中，概览只展示最近少量证据。
        </StudioPanelDescription>
        <StudioPanelContent className="pt-2">
          {research.error && (
            <PartialError
              message="离线研究证据读取失败。"
              onRetry={research.refresh}
            />
          )}
          {training.error && (
            <PartialError
              message="训练证据读取失败。"
              onRetry={training.refresh}
            />
          )}
          {((researchLoading &&
            recentArtifactRuns.length === 0 &&
            !research.error) ||
            (trainingLoading &&
              recentTrainingRuns.length === 0 &&
              !training.error)) &&
          recentArtifactRuns.length === 0 &&
          recentTrainingRuns.length === 0 ? (
            <LoadingNotice>正在读取有效证据…</LoadingNotice>
          ) : (
            <OverviewEvidenceTable
              artifactRuns={research.error ? [] : recentArtifactRuns}
              trainingRuns={training.error ? [] : recentTrainingRuns}
            />
          )}
        </StudioPanelContent>
      </StudioPanel>

      <div className="flex flex-wrap items-center gap-3 text-ui-caption text-slate-600">
        <span className="inline-flex items-center gap-1.5">
          <Database aria-hidden="true" className="h-3.5 w-3.5" />
          认证数据集 {datasets.error ? '不可用' : certifiedDatasets.length}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <ServerCog aria-hidden="true" className="h-3.5 w-3.5" />
          能力状态{' '}
          {capabilities.error
            ? '不可用'
            : capabilityLoading
              ? '读取中'
              : capabilities.data?.fresh
                ? '新鲜'
                : '需检查'}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <RefreshCw aria-hidden="true" className="h-3.5 w-3.5" />
          训练页每 5 秒更新活动任务
        </span>
      </div>
    </ResearchCenterFrame>
  );
}
