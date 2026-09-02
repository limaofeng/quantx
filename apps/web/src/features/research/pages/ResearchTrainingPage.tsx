import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  Database,
  Info,
  Microchip,
  Plus,
  RefreshCw,
  ServerCog,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  StudioPanel,
  StudioPanelContent,
  StudioPanelDescription,
  StudioPanelHeader,
  StudioPanelTitle,
} from '@/components/ui/studio-layout';
import {
  ResearchLifecycleRunStage,
  type ResearchLifecycleRunFilter,
} from '@/generated/gql/graphql';

import { ResearchCenterFrame } from '../components/ResearchCenterFrame';
import { ResearchLifecycleRunTable } from '../components/ResearchLifecycleRunTable';
import {
  useResearchLifecycleRuns,
  useStockSelectionDatasetVersions,
  useStockSelectionTrainingCapabilities,
} from '../hooks';

type TrainingStageTab = 'ALL' | 'DEVELOPMENT' | 'FINAL_EVALUATION';

const STAGE_TABS: Array<{ label: string; value: TrainingStageTab }> = [
  { label: '全部', value: 'ALL' },
  { label: 'DEVELOPMENT', value: 'DEVELOPMENT' },
  { label: 'FINAL', value: 'FINAL_EVALUATION' },
];

function formatDate(value?: string | null) {
  if (!value) return '不可用';
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? '不可用'
    : date.toLocaleString('zh-CN', {
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        month: '2-digit',
        year: 'numeric',
      });
}

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

function ReadinessRow({
  icon: Icon,
  label,
  value,
  tone = 'neutral',
}: {
  icon: typeof Cpu;
  label: string;
  value: string;
  tone?: 'success' | 'warning' | 'neutral';
}) {
  const valueClass =
    tone === 'success'
      ? 'text-emerald-200'
      : tone === 'warning'
        ? 'text-amber-200'
        : 'text-slate-300';
  return (
    <div className="flex items-center gap-3 border-b border-white/[0.06] py-3 last:border-b-0">
      <Icon aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-400" />
      <span className="min-w-0 flex-1 text-ui-label text-slate-400">
        {label}
      </span>
      <span className={`text-right text-ui-label font-semibold ${valueClass}`}>
        {value}
      </span>
    </div>
  );
}

function gpuStatusText(status?: string | null) {
  switch (status) {
    case 'GPU_AVAILABLE':
      return '已通过资格';
    case 'GPU_INSUFFICIENT_MEMORY':
      return '显存不足';
    case 'GPU_UNAVAILABLE_BUILD':
      return '构建不可用';
    case 'GPU_UNAVAILABLE_RUNTIME':
      return '运行时不可用';
    case 'GPU_UNQUALIFIED':
      return '未通过资格';
    case 'CPU_AVAILABLE':
      return 'CPU 路径';
    default:
      return '不可用';
  }
}

export default function ResearchTrainingPage() {
  const [stage, setStage] = useState<TrainingStageTab>('ALL');
  const [search, setSearch] = useState('');

  const filter = useMemo<ResearchLifecycleRunFilter>(
    () => ({
      dateFrom: null,
      dateTo: null,
      search: search.trim() || null,
      stages:
        stage === 'ALL'
          ? null
          : [
              stage === 'DEVELOPMENT'
                ? ResearchLifecycleRunStage.Development
                : ResearchLifecycleRunStage.FinalEvaluation,
            ],
      statuses: null,
      studyId: 'next-day-selection',
    }),
    [search, stage]
  );
  const lifecycle = useResearchLifecycleRuns(filter, 20, 0);
  const capabilities = useStockSelectionTrainingCapabilities();
  const datasets = useStockSelectionDatasetVersions();
  const certifiedCount = datasets.data.filter(
    dataset => dataset.status.toUpperCase() === 'CERTIFIED'
  ).length;
  const capability = capabilities.data;
  const readinessKnown =
    Boolean(capability) && !capabilities.fetching && !capabilities.error;
  const datasetsKnown = !datasets.fetching && !datasets.error;
  const cpuReady = capability?.cpuAvailable === true;
  const capabilityFresh =
    capability?.fresh === true && Boolean(capability.updatedAt);
  const canStartTraining =
    readinessKnown &&
    datasetsKnown &&
    certifiedCount > 0 &&
    cpuReady &&
    capabilityFresh;
  const blockedReason = !datasetsKnown
    ? datasets.error
      ? '认证数据集读取失败，请先重试或前往数据管理。'
      : '正在读取认证数据集；确认数据集状态后才可创建训练。'
    : capabilities.error
      ? '能力状态读取失败，请先重试或检查运行环境。'
      : capabilities.fetching && !capability
        ? '正在读取能力状态；确认 CPU 和快照新鲜后才可创建训练。'
        : !capability
          ? '能力快照缺失，请先前往运行环境。'
          : certifiedCount === 0
            ? '没有已认证数据集，请先前往数据管理。'
            : !cpuReady
              ? 'CPU 当前不可用，请先检查运行环境。'
              : !capabilityFresh
                ? '能力快照缺失或已过期，请先刷新运行环境状态。'
                : '';
  const hasGpuWarning =
    readinessKnown && capability?.gpuStatus !== 'GPU_AVAILABLE';
  const trainingAction = canStartTraining ? (
    <Button asChild size="lg" data-testid="training-new-button">
      <Link href="/research/training/new">
        <Plus aria-hidden="true" />
        新建训练
      </Link>
    </Button>
  ) : (
    <Button type="button" size="lg" disabled data-testid="training-new-button">
      <Plus aria-hidden="true" />
      新建训练
    </Button>
  );

  return (
    <ResearchCenterFrame
      title="模型训练"
      description="创建 DEVELOPMENT，审阅验证证据，再决定是否执行 FINAL。"
      actions={
        <div className="flex items-center gap-2">
          {trainingAction}
          {!canStartTraining && (
            <span className="max-w-56 text-ui-caption text-amber-200">
              {blockedReason}
            </span>
          )}
        </div>
      }
    >
      <div className="grid min-w-0 gap-ui-section xl:grid-cols-[minmax(0,1fr)_18rem]">
        <StudioPanel className="order-2 min-w-0 xl:order-1">
          <StudioPanelHeader className="gap-3">
            <div className="min-w-0">
              <StudioPanelTitle>模型训练任务</StudioPanelTitle>
              <StudioPanelDescription>
                统一生命周期索引的 next-day-selection 视图。
              </StudioPanelDescription>
            </div>
            <button
              type="button"
              onClick={() => lifecycle.refresh()}
              disabled={lifecycle.fetching}
              aria-label="刷新训练任务"
              className="inline-flex h-control-compact w-control-compact shrink-0 cursor-pointer items-center justify-center rounded-control border border-white/10 text-slate-400 outline-none transition-colors hover:border-blue-400/40 hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500 disabled:cursor-wait disabled:opacity-50"
            >
              <RefreshCw
                aria-hidden="true"
                className={
                  lifecycle.fetching
                    ? 'h-3.5 w-3.5 animate-spin motion-reduce:animate-none'
                    : 'h-3.5 w-3.5'
                }
              />
            </button>
          </StudioPanelHeader>
          <StudioPanelContent className="space-y-ui-panel">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div
                className="flex items-center gap-1"
                role="tablist"
                aria-label="训练阶段"
              >
                {STAGE_TABS.map(tab => {
                  const active = stage === tab.value;
                  return (
                    <button
                      key={tab.value}
                      type="button"
                      role="tab"
                      aria-selected={active}
                      onClick={() => setStage(tab.value)}
                      className={`h-control-compact cursor-pointer rounded-control px-3 text-ui-caption font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-500 ${active ? 'bg-blue-500/15 text-blue-200' : 'text-slate-500 hover:bg-blue-500/[0.06] hover:text-slate-200'}`}
                    >
                      {tab.label}
                    </button>
                  );
                })}
              </div>
              <label className="flex min-w-64 flex-1 items-center gap-2 sm:max-w-xs">
                <span className="sr-only">搜索运行名称或数据集</span>
                <Input
                  value={search}
                  onChange={event => setSearch(event.target.value)}
                  placeholder="搜索运行名称或数据集"
                  aria-label="搜索运行名称或数据集"
                  maxLength={128}
                  className="h-control-compact text-ui-label"
                />
              </label>
            </div>
            {lifecycle.error ? (
              <ErrorNotice
                message={lifecycle.error.message}
                onRetry={lifecycle.refresh}
              />
            ) : (
              <ResearchLifecycleRunTable
                runs={lifecycle.runs}
                fetching={lifecycle.fetching}
                emptyLabel="还没有 next-day-selection 训练运行。"
                ariaLabel="模型训练任务"
              />
            )}
            <div className="flex items-start gap-2 border-t border-white/[0.06] pt-3 text-ui-caption text-slate-500">
              <Info
                aria-hidden="true"
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400"
              />
              <span>
                训练成功不会自动登记或发布模型；请在运行详情审阅证据后分别执行
                FINAL 和登记。
              </span>
            </div>
          </StudioPanelContent>
        </StudioPanel>

        <StudioPanel className="order-1 h-fit xl:order-2">
          <StudioPanelHeader>
            <StudioPanelTitle>训练准备度</StudioPanelTitle>
          </StudioPanelHeader>
          <StudioPanelContent>
            {capabilities.error && (
              <ErrorNotice
                message="能力状态读取失败。"
                onRetry={capabilities.refresh}
              />
            )}
            {datasets.error && (
              <ErrorNotice
                message="数据集状态读取失败。"
                onRetry={datasets.refresh}
              />
            )}
            {!capabilities.error && !datasets.error && (
              <div>
                <ReadinessRow
                  icon={Database}
                  label="认证数据集"
                  value={datasets.fetching ? '读取中' : `${certifiedCount}`}
                  tone={certifiedCount > 0 ? 'success' : 'warning'}
                />
                <ReadinessRow
                  icon={Cpu}
                  label="CPU"
                  value={
                    capability
                      ? capability.cpuAvailable
                        ? '可用'
                        : '不可用'
                      : '不可用'
                  }
                  tone={capability?.cpuAvailable ? 'success' : 'warning'}
                />
                <ReadinessRow
                  icon={Microchip}
                  label="GPU"
                  value={gpuStatusText(capability?.gpuStatus)}
                  tone={
                    capability?.gpuStatus === 'GPU_AVAILABLE'
                      ? 'success'
                      : 'warning'
                  }
                />
                <ReadinessRow
                  icon={ServerCog}
                  label="快照"
                  value={
                    capability
                      ? capabilityFresh
                        ? '新鲜'
                        : '需刷新'
                      : '不可用'
                  }
                  tone={capabilityFresh ? 'success' : 'warning'}
                />
                <div className="mt-3 text-ui-caption text-slate-600">
                  更新时间：{formatDate(capability?.updatedAt)}
                </div>
              </div>
            )}
            {hasGpuWarning && (
              <div
                className="mt-3 flex items-start gap-2 rounded-control border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-ui-caption text-amber-200"
                role="status"
              >
                <AlertTriangle
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0"
                />
                <span>
                  GPU 未通过资格验证；AUTO 可由服务端解析为 CPU，不阻断 CPU
                  训练。
                </span>
              </div>
            )}
            <div className="mt-4 space-y-1 border-t border-white/[0.06] pt-3">
              <Link
                href="/settings/data"
                className="flex h-control-default items-center justify-between rounded-control px-2 text-ui-label font-semibold text-blue-300 outline-none transition-colors hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                <span className="inline-flex items-center gap-2">
                  <Database aria-hidden="true" className="h-3.5 w-3.5" />
                  管理数据集
                </span>
                <span aria-hidden="true">›</span>
              </Link>
              <Link
                href="/settings/status"
                className="flex h-control-default items-center justify-between rounded-control px-2 text-ui-label font-semibold text-blue-300 outline-none transition-colors hover:bg-blue-500/10 hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                <span className="inline-flex items-center gap-2">
                  <ServerCog aria-hidden="true" className="h-3.5 w-3.5" />
                  查看运行环境
                </span>
                <span aria-hidden="true">›</span>
              </Link>
            </div>
            {!canStartTraining && (
              <div className="mt-3 flex items-start gap-2 text-ui-caption text-amber-200">
                <AlertTriangle
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0"
                />
                {blockedReason}
              </div>
            )}
            {canStartTraining && (
              <div className="mt-3 flex items-start gap-2 text-ui-caption text-emerald-200">
                <CheckCircle2
                  aria-hidden="true"
                  className="mt-0.5 h-3.5 w-3.5 shrink-0"
                />
                训练创建门禁已满足。
              </div>
            )}
          </StudioPanelContent>
        </StudioPanel>
      </div>
    </ResearchCenterFrame>
  );
}
