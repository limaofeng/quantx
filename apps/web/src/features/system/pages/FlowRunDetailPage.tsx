import { format } from 'date-fns';
import {
  Activity,
  AlertCircle,
  ArrowDownToLine,
  ArrowLeft,
  Braces,
  CheckCircle2,
  Clock3,
  Copy,
  FileSearch,
  PauseCircle,
  RefreshCw,
  Search,
  Terminal,
  Timer,
  Wifi,
  WifiOff,
  XCircle,
} from 'lucide-react';
import React from 'react';
import { useQuery, useSubscription } from 'urql';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { TaskHistoryFlowRunLogsDocument } from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { MarketSyncEvidence } from '../components/MarketSyncEvidence';

import {
  filterFlowRunLogs,
  formatFlowRunDuration,
  formatFlowRunParameterValue,
  getFlowRunLogLevel,
  getFlowRunStatusVisual,
  isFlowRunLogFilter,
  isLiveFlowRunState,
  mergeFlowRunLogs,
  normalizeFlowRunState,
  resolveFlowRunElapsedSeconds,
  safeParseFlowRunParameters,
  type FlowRunLogFilter,
  type FlowRunLogRecord,
  type FlowRunStatusTone,
} from './flow-run-detail/flowRunDetailModel';

const GET_FLOW_RUN_DETAIL = `
  query GetFlowRunDetail($id: String!) {
    flowRun(runId: $id) {
      id
      flowName
      state
      startedAt
      finishedAt
      totalRunTime
      parameters
      taskRuns {
        id
        name
        state
        startedAt
        finishedAt
        totalRunTime
        taskInputs
      }
      detailedLogs {
        time
        level
        message
      }
    }
  }
`;

interface FlowTaskRun {
  id: string;
  name?: string | null;
  state?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  totalRunTime?: number | null;
  taskInputs?: unknown;
}

interface FlowRunDetail {
  id: string;
  flowName?: string | null;
  state?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  totalRunTime?: number | null;
  parameters?: unknown;
  taskRuns?: FlowTaskRun[] | null;
  detailedLogs?: FlowRunLogRecord[] | null;
}

interface FlowRunDetailQuery {
  flowRun?: FlowRunDetail | null;
}

type InspectorTab = 'overview' | 'tasks' | 'parameters';

const LIVE_RUN_REFRESH_INTERVAL_MS = 10_000;

const STATUS_TONE_CLASSES: Record<FlowRunStatusTone, string> = {
  info: 'border-blue-400/25 bg-blue-500/10 text-blue-300',
  success: 'border-emerald-400/25 bg-emerald-500/10 text-emerald-300',
  warning: 'border-amber-400/25 bg-amber-500/10 text-amber-300',
  danger: 'border-rose-400/25 bg-rose-500/10 text-rose-300',
  neutral: 'border-slate-500/25 bg-slate-500/10 text-slate-400',
};

const STATUS_DOT_CLASSES: Record<FlowRunStatusTone, string> = {
  info: 'bg-blue-400',
  success: 'bg-emerald-400',
  warning: 'bg-amber-400',
  danger: 'bg-rose-400',
  neutral: 'bg-slate-400',
};

function useWideInspectorLayout() {
  const getMatches = React.useCallback(() => {
    if (typeof window === 'undefined') return true;
    return window.matchMedia('(min-width: 1200px)').matches;
  }, []);
  const [isWide, setIsWide] = React.useState(getMatches);

  React.useEffect(() => {
    const mediaQuery = window.matchMedia('(min-width: 1200px)');
    const handleChange = () => setIsWide(mediaQuery.matches);
    handleChange();
    mediaQuery.addEventListener('change', handleChange);
    return () => mediaQuery.removeEventListener('change', handleChange);
  }, []);

  return isWide;
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return format(date, 'yyyy-MM-dd HH:mm:ss');
}

function formatLogTime(value: string | null | undefined) {
  if (!value) return '--:--:--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--:--:--';
  return format(date, 'HH:mm:ss');
}

function formatJson(value: unknown) {
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value ?? {}, null, 2);
}

async function copyText(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand('copy');
  textarea.remove();
  if (!copied) throw new Error('Clipboard unavailable');
}

function StatusIcon({
  state,
  className,
}: {
  state?: string | null;
  className?: string;
}) {
  const normalizedState = normalizeFlowRunState(state);
  if (normalizedState === 'COMPLETED') {
    return <CheckCircle2 className={className} />;
  }
  if (normalizedState === 'FAILED' || normalizedState === 'CRASHED') {
    return <XCircle className={className} />;
  }
  if (normalizedState === 'RUNNING') {
    return <Activity className={className} />;
  }
  if (normalizedState === 'PAUSED') {
    return <PauseCircle className={className} />;
  }
  return <Clock3 className={className} />;
}

function RunStatusBadge({ state }: { state?: string | null }) {
  const visual = getFlowRunStatusVisual(state);
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center gap-1.5 rounded-control border px-2 text-ui-caption font-semibold',
        STATUS_TONE_CLASSES[visual.tone]
      )}
    >
      <StatusIcon state={state} className="h-3.5 w-3.5" />
      {visual.label}
    </span>
  );
}

function FlowRunLoadingState() {
  return (
    <DataStudioPageFrame
      activeMode="OVERVIEW"
      description="正在加载任务运行详情"
      title="任务详情"
    >
      <div
        aria-label="正在加载任务详情"
        className="flex h-full min-h-0 flex-col gap-ui-panel"
      >
        <div className="flex min-h-[76px] items-center gap-ui-panel rounded-panel border border-white/[0.06] bg-[#0b1120]/80 px-ui-panel">
          <Skeleton className="h-control-default w-control-default rounded-control" />
          <div className="min-w-0 flex-1 space-y-2">
            <Skeleton className="h-5 w-56" />
            <Skeleton className="h-4 w-96 max-w-full" />
          </div>
          <Skeleton className="h-control-compact w-20" />
          <Skeleton className="h-control-compact w-20" />
        </div>
        <div className="grid min-h-0 flex-1 gap-ui-panel xl:grid-cols-[minmax(0,3fr)_minmax(280px,1fr)]">
          <Skeleton className="h-full min-h-[320px] rounded-panel" />
          <Skeleton className="h-full min-h-[200px] rounded-panel" />
        </div>
      </div>
    </DataStudioPageFrame>
  );
}

function FlowRunErrorState({
  id,
  message,
  notFound = false,
  onRetry,
}: {
  id: string;
  message: string;
  notFound?: boolean;
  onRetry: () => void;
}) {
  return (
    <DataStudioPageFrame
      activeMode="OVERVIEW"
      description="任务运行详情不可用"
      title="任务详情"
    >
      <div className="flex h-full items-center justify-center p-ui-empty">
        <section
          className="w-full max-w-2xl rounded-panel border border-white/[0.07] bg-[#0b1120]/90 p-ui-empty"
          role={notFound ? 'status' : 'alert'}
        >
          <div
            className={cn(
              'mb-3 inline-flex rounded-control border p-2',
              notFound
                ? 'border-slate-500/20 bg-slate-500/10 text-slate-400'
                : 'border-rose-400/25 bg-rose-500/10 text-rose-300'
            )}
          >
            {notFound ? (
              <FileSearch className="h-5 w-5" />
            ) : (
              <AlertCircle className="h-5 w-5" />
            )}
          </div>
          <h1 className="text-ui-heading font-semibold text-slate-100">
            {notFound ? '未找到这次任务运行' : '任务详情加载失败'}
          </h1>
          <p className="mt-2 text-ui-body text-slate-400">{message}</p>
          <p className="mt-2 break-all font-mono text-ui-caption text-slate-600">
            {id}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => window.history.back()}
            >
              <ArrowLeft />
              返回
            </Button>
            <Button size="sm" onClick={onRetry}>
              <RefreshCw />
              重新加载
            </Button>
          </div>
        </section>
      </div>
    </DataStudioPageFrame>
  );
}

function LiveConnectionBadge({
  live,
  error,
}: {
  live: boolean;
  error?: Error;
}) {
  if (!live) {
    return (
      <span className="inline-flex h-6 items-center gap-1.5 rounded-control border border-slate-500/20 bg-slate-500/10 px-2 text-ui-caption font-semibold text-slate-400">
        <Terminal className="h-3.5 w-3.5" />
        历史日志
      </span>
    );
  }

  if (error) {
    return (
      <span
        className="inline-flex h-6 items-center gap-1.5 rounded-control border border-rose-400/25 bg-rose-500/10 px-2 text-ui-caption font-semibold text-rose-300"
        title={error.message}
      >
        <WifiOff className="h-3.5 w-3.5" />
        实时连接已断开
      </span>
    );
  }

  return (
    <span className="inline-flex h-6 items-center gap-1.5 rounded-control border border-emerald-400/20 bg-emerald-500/10 px-2 text-ui-caption font-semibold text-emerald-300">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 motion-safe:animate-pulse" />
      <Wifi className="h-3.5 w-3.5" />
      实时已连接
    </span>
  );
}

function LogPanel({
  logs,
  filteredLogs,
  searchQuery,
  levelFilter,
  live,
  subscriptionError,
  followingTail,
  logViewportRef,
  onSearchChange,
  onLevelFilterChange,
  onFollowTail,
  onScroll,
}: {
  logs: FlowRunLogRecord[];
  filteredLogs: FlowRunLogRecord[];
  searchQuery: string;
  levelFilter: FlowRunLogFilter;
  live: boolean;
  subscriptionError?: Error;
  followingTail: boolean;
  logViewportRef: React.RefObject<HTMLDivElement>;
  onSearchChange: (value: string) => void;
  onLevelFilterChange: (value: FlowRunLogFilter) => void;
  onFollowTail: () => void;
  onScroll: () => void;
}) {
  const latestMessage = logs.at(-1)?.message ?? '';

  return (
    <section
      aria-labelledby="flow-run-log-title"
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-panel border border-white/[0.07] bg-[#0B1728]"
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.07] px-ui-panel py-2">
        <div className="flex min-w-0 items-center gap-2">
          <Terminal className="h-4 w-4 text-blue-300" />
          <h2
            id="flow-run-log-title"
            className="text-ui-title font-semibold text-slate-100"
          >
            运行日志
          </h2>
          <span className="font-mono text-ui-caption text-slate-500">
            {filteredLogs.length === logs.length
              ? `${logs.length} 条`
              : `${filteredLogs.length} / ${logs.length} 条`}
          </span>
        </div>
        <LiveConnectionBadge live={live} error={subscriptionError} />

        <div className="ml-auto flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">
          <div className="relative w-full min-w-40 sm:w-48 xl:w-56">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <Input
              aria-label="搜索日志"
              className="h-control-compact border-white/10 bg-white/[0.03] pl-8 font-mono text-ui-caption"
              placeholder="搜索日志"
              value={searchQuery}
              onChange={event => onSearchChange(event.target.value)}
            />
          </div>
          <Select
            value={levelFilter}
            onValueChange={value => {
              if (isFlowRunLogFilter(value)) onLevelFilterChange(value);
            }}
          >
            <SelectTrigger
              aria-label="日志级别"
              className="h-control-compact w-28 border-white/10 bg-white/[0.03] text-ui-caption"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ALL">级别：全部</SelectItem>
              <SelectItem value="INFO">信息</SelectItem>
              <SelectItem value="WARN">警告</SelectItem>
              <SelectItem value="ERROR">错误</SelectItem>
            </SelectContent>
          </Select>
          <Button
            aria-pressed={followingTail}
            className={cn(
              'h-control-compact border-white/10 text-ui-caption',
              followingTail &&
                'border-blue-400/25 bg-blue-500/10 text-blue-300 hover:bg-blue-500/15'
            )}
            size="sm"
            variant="outline"
            onClick={onFollowTail}
          >
            <ArrowDownToLine />
            {followingTail ? '跟随尾部' : '回到最新'}
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-[72px_52px_minmax(0,1fr)] gap-3 border-b border-white/[0.06] bg-white/[0.02] px-ui-panel py-2 font-mono text-ui-caption text-slate-500">
        <span>时间</span>
        <span>级别</span>
        <span>消息</span>
      </div>

      <div
        ref={logViewportRef}
        aria-label="任务运行日志"
        className="execution-log-scrollbar min-h-0 flex-1 overflow-auto px-ui-panel py-2 font-mono text-ui-caption leading-relaxed"
        role="log"
        tabIndex={0}
        onScroll={onScroll}
      >
        {filteredLogs.length === 0 ? (
          <div className="flex h-full min-h-36 items-center justify-center">
            <div className="text-center">
              <FileSearch className="mx-auto h-7 w-7 text-slate-600" />
              <p className="mt-2 text-ui-body font-medium text-slate-400">
                {logs.length === 0 ? '暂时还没有运行日志' : '没有匹配的日志'}
              </p>
              <p className="mt-1 text-ui-caption text-slate-600">
                {logs.length === 0
                  ? live
                    ? '新日志到达后会显示在这里'
                    : '本次运行没有返回日志记录'
                  : '请调整关键词或日志级别'}
              </p>
            </div>
          </div>
        ) : (
          <div role="list">
            {filteredLogs.map((log, index) => {
              const level = getFlowRunLogLevel(log.level);
              return (
                <div
                  key={`${log.time ?? 'log'}-${log.level ?? 0}-${log.message ?? ''}-${index}`}
                  className="grid grid-cols-[72px_52px_minmax(0,1fr)] gap-3 border-b border-white/[0.045] py-1.5 last:border-b-0 hover:bg-white/[0.025]"
                  role="listitem"
                >
                  <time className="select-none text-slate-500">
                    {formatLogTime(log.time)}
                  </time>
                  <span
                    className={cn(
                      'font-semibold',
                      level === 'ERROR'
                        ? 'text-rose-300'
                        : level === 'WARN'
                          ? 'text-amber-300'
                          : 'text-blue-300'
                    )}
                  >
                    {level}
                  </span>
                  <span className="min-w-0 whitespace-pre-wrap break-words text-slate-300">
                    {log.message ?? ''}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <span className="sr-only" aria-live="polite">
        {live && latestMessage ? `最新日志：${latestMessage}` : ''}
      </span>
    </section>
  );
}

function InspectorRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
      <dt className="text-ui-label text-slate-500">{label}</dt>
      <dd
        className={cn(
          'min-w-0 text-right text-ui-label text-slate-300',
          mono && 'font-mono'
        )}
      >
        {value}
      </dd>
    </div>
  );
}

function OverviewInspector({
  flowRun,
  elapsed,
  logs,
  onCopyId,
}: {
  flowRun: FlowRunDetail;
  elapsed: string;
  logs: FlowRunLogRecord[];
  onCopyId: () => void;
}) {
  const status = getFlowRunStatusVisual(flowRun.state);
  const taskCount = flowRun.taskRuns?.length ?? 0;
  const latestLog = logs.at(-1);

  return (
    <div className="space-y-ui-section">
      <section className="rounded-panel border border-white/[0.06] bg-white/[0.025] p-ui-panel">
        <h3 className="text-ui-label font-semibold text-slate-400">运行状态</h3>
        <div
          className={cn(
            'mt-2 flex items-center gap-2 text-ui-title font-semibold',
            status.tone === 'info'
              ? 'text-blue-300'
              : status.tone === 'success'
                ? 'text-emerald-300'
                : status.tone === 'warning'
                  ? 'text-amber-300'
                  : status.tone === 'danger'
                    ? 'text-rose-300'
                    : 'text-slate-400'
          )}
        >
          <span
            className={cn(
              'h-2 w-2 rounded-full',
              STATUS_DOT_CLASSES[status.tone]
            )}
          />
          {status.label}
        </div>
      </section>

      <dl className="divide-y divide-white/[0.055]">
        <InspectorRow
          label="开始时间"
          mono
          value={formatDateTime(flowRun.startedAt)}
        />
        <InspectorRow label="运行时长" mono value={elapsed} />
        <InspectorRow
          label="结束时间"
          mono
          value={formatDateTime(flowRun.finishedAt)}
        />
        <InspectorRow label="日志条数" mono value={String(logs.length)} />
        <InspectorRow label="任务数量" mono value={String(taskCount)} />
      </dl>

      <section>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-ui-label font-semibold text-slate-400">
            运行 ID
          </h3>
          <Button
            aria-label="复制运行 ID"
            size="sm"
            variant="ghost"
            onClick={onCopyId}
          >
            <Copy />
          </Button>
        </div>
        <code className="mt-1 block break-all rounded-control border border-white/[0.06] bg-black/15 p-2 text-ui-caption text-slate-400">
          {flowRun.id}
        </code>
      </section>

      {latestLog && (
        <section>
          <h3 className="text-ui-label font-semibold text-slate-400">
            最近活动
          </h3>
          <div className="mt-2 rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
            <time className="font-mono text-ui-caption text-slate-500">
              {formatLogTime(latestLog.time)}
            </time>
            <p className="mt-1 break-words text-ui-label leading-relaxed text-slate-300">
              {latestLog.message}
            </p>
          </div>
        </section>
      )}
    </div>
  );
}

function TasksInspector({ tasks }: { tasks: FlowTaskRun[] }) {
  if (tasks.length === 0) {
    return (
      <div className="flex h-full min-h-36 items-center justify-center p-ui-panel">
        <div className="max-w-sm text-center">
          <FileSearch className="mx-auto h-8 w-8 text-slate-600" />
          <h3 className="mt-3 text-ui-body font-semibold text-slate-300">
            当前流程未产生独立任务记录
          </h3>
          <p className="mt-1 text-ui-caption text-slate-500">
            运行进度请以日志为准
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="divide-y divide-white/[0.055]">
      {tasks.map(task => {
        const status = getFlowRunStatusVisual(task.state);
        return (
          <article key={task.id} className="py-3 first:pt-0 last:pb-0">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h3 className="truncate text-ui-body font-semibold text-slate-200">
                  {task.name || '未命名任务'}
                </h3>
                <p className="mt-1 font-mono text-ui-caption text-slate-500">
                  {formatDateTime(task.startedAt)}
                </p>
              </div>
              <span
                className={cn(
                  'inline-flex h-6 shrink-0 items-center gap-1 rounded-control border px-2 text-ui-caption font-semibold',
                  STATUS_TONE_CLASSES[status.tone]
                )}
              >
                <StatusIcon state={task.state} className="h-3 w-3" />
                {status.label}
              </span>
            </div>
            <div className="mt-2 flex items-center gap-1.5 text-ui-caption text-slate-500">
              <Timer className="h-3.5 w-3.5" />
              <span className="font-mono">
                {formatFlowRunDuration(task.totalRunTime)}
              </span>
            </div>
            {task.taskInputs !== null && task.taskInputs !== undefined && (
              <details className="mt-2 rounded-control border border-white/[0.06] bg-black/10">
                <summary className="cursor-pointer px-2 py-1.5 text-ui-caption text-slate-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70">
                  查看任务输入
                </summary>
                <pre className="max-h-48 overflow-auto border-t border-white/[0.06] p-2 font-mono text-ui-caption text-slate-400">
                  {formatJson(task.taskInputs)}
                </pre>
              </details>
            )}
          </article>
        );
      })}
    </div>
  );
}

function ParametersInspector({
  parameters,
  onCopy,
}: {
  parameters: unknown;
  onCopy: () => void;
}) {
  const parsedParameters = safeParseFlowRunParameters(parameters);
  const entries = Object.entries(parsedParameters);

  if (entries.length === 0) {
    return (
      <div className="flex h-full min-h-36 items-center justify-center p-ui-panel text-center">
        <div>
          <Braces className="mx-auto h-8 w-8 text-slate-600" />
          <h3 className="mt-3 text-ui-body font-semibold text-slate-300">
            没有运行参数
          </h3>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <p className="text-ui-caption text-slate-500">
          {entries.length} 个参数
        </p>
        <Button size="sm" variant="ghost" onClick={onCopy}>
          <Copy />
          复制 JSON
        </Button>
      </div>
      <dl className="divide-y divide-white/[0.055] rounded-control border border-white/[0.06] bg-black/10 px-2">
        {entries.map(([key, value]) => (
          <div
            key={key}
            className="grid gap-1 py-2.5 first:pt-2 last:pb-2 sm:grid-cols-[minmax(112px,0.7fr)_minmax(0,1.3fr)] sm:gap-3"
          >
            <dt className="break-all font-mono text-ui-caption text-slate-500">
              {key}
            </dt>
            <dd className="min-w-0 whitespace-pre-wrap break-words font-mono text-ui-caption text-slate-300">
              {formatFlowRunParameterValue(value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function FlowRunInspector({
  flowRun,
  elapsed,
  logs,
  activeTab,
  onActiveTabChange,
  onCopyId,
  onCopyParameters,
}: {
  flowRun: FlowRunDetail;
  elapsed: string;
  logs: FlowRunLogRecord[];
  activeTab: InspectorTab;
  onActiveTabChange: (tab: InspectorTab) => void;
  onCopyId: () => void;
  onCopyParameters: () => void;
}) {
  const tasks = flowRun.taskRuns ?? [];
  const parameterCount = Object.keys(
    safeParseFlowRunParameters(flowRun.parameters)
  ).length;

  return (
    <Tabs
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-panel border border-white/[0.07] bg-[#0F1D30]"
      value={activeTab}
      onValueChange={value => onActiveTabChange(value as InspectorTab)}
    >
      <TabsList className="h-control-large w-full shrink-0 justify-start gap-1 rounded-none border-b border-white/[0.07] bg-transparent px-2 py-0">
        <TabsTrigger
          className="h-full rounded-none border-b-2 border-transparent bg-transparent px-3 text-slate-500 shadow-none data-[state=active]:border-blue-400 data-[state=active]:bg-transparent data-[state=active]:text-slate-100 data-[state=active]:shadow-none"
          value="overview"
        >
          概览
        </TabsTrigger>
        <TabsTrigger
          className="h-full gap-1.5 rounded-none border-b-2 border-transparent bg-transparent px-3 text-slate-500 shadow-none data-[state=active]:border-blue-400 data-[state=active]:bg-transparent data-[state=active]:text-slate-100 data-[state=active]:shadow-none"
          value="tasks"
        >
          任务
          <span className="rounded-control bg-white/[0.06] px-1.5 font-mono text-ui-caption">
            {tasks.length}
          </span>
        </TabsTrigger>
        <TabsTrigger
          className="h-full gap-1.5 rounded-none border-b-2 border-transparent bg-transparent px-3 text-slate-500 shadow-none data-[state=active]:border-blue-400 data-[state=active]:bg-transparent data-[state=active]:text-slate-100 data-[state=active]:shadow-none"
          value="parameters"
        >
          参数
          <span className="rounded-control bg-white/[0.06] px-1.5 font-mono text-ui-caption">
            {parameterCount}
          </span>
        </TabsTrigger>
      </TabsList>

      <TabsContent
        className="custom-scrollbar m-0 min-h-0 flex-1 overflow-auto p-ui-panel"
        value="overview"
      >
        <OverviewInspector
          elapsed={elapsed}
          flowRun={flowRun}
          logs={logs}
          onCopyId={onCopyId}
        />
      </TabsContent>
      <TabsContent
        className="custom-scrollbar m-0 min-h-0 flex-1 overflow-auto p-ui-panel"
        value="tasks"
      >
        <TasksInspector tasks={tasks} />
      </TabsContent>
      <TabsContent
        className="custom-scrollbar m-0 min-h-0 flex-1 overflow-auto p-ui-panel"
        value="parameters"
      >
        <ParametersInspector
          parameters={flowRun.parameters}
          onCopy={onCopyParameters}
        />
      </TabsContent>
    </Tabs>
  );
}

export function FlowRunDetailPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const { toast } = useToast();
  const isWideLayout = useWideInspectorLayout();
  const [{ data, fetching, error }, reexecuteQuery] = useQuery<
    FlowRunDetailQuery,
    { id: string }
  >({
    query: GET_FLOW_RUN_DETAIL,
    variables: { id },
    pause: !id,
    requestPolicy: 'cache-and-network',
  });

  const flowRun = data?.flowRun;
  const isLiveRun = isLiveFlowRunState(flowRun?.state);
  const [subscriptionResult] = useSubscription({
    query: TaskHistoryFlowRunLogsDocument,
    variables: { runId: id, includeHistory: true },
    pause: !id || !isLiveRun,
  });
  const [subscriptionLogs, setSubscriptionLogs] = React.useState<
    FlowRunLogRecord[]
  >([]);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [levelFilter, setLevelFilter] = React.useState<FlowRunLogFilter>('ALL');
  const [inspectorTab, setInspectorTab] =
    React.useState<InspectorTab>('overview');
  const [followingTail, setFollowingTail] = React.useState(true);
  const [nowMs, setNowMs] = React.useState(() => Date.now());
  const logViewportRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    setSubscriptionLogs([]);
    setSearchQuery('');
    setLevelFilter('ALL');
    setInspectorTab('overview');
    setFollowingTail(true);
  }, [id]);

  React.useEffect(() => {
    const incomingLog = subscriptionResult.data?.flowRunLogs;
    if (!incomingLog) return;
    setSubscriptionLogs(previous =>
      mergeFlowRunLogs(previous, [
        {
          time: incomingLog.time,
          level: incomingLog.level,
          message: incomingLog.message,
        },
      ])
    );
  }, [subscriptionResult.data?.flowRunLogs]);

  React.useEffect(() => {
    if (!isLiveRun) return;
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isLiveRun, id]);

  React.useEffect(() => {
    if (!isLiveRun) return;
    const timer = window.setInterval(() => {
      reexecuteQuery({ requestPolicy: 'network-only' });
    }, LIVE_RUN_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [id, isLiveRun, reexecuteQuery]);

  const logs = React.useMemo(
    () => mergeFlowRunLogs(flowRun?.detailedLogs ?? [], subscriptionLogs),
    [flowRun?.detailedLogs, subscriptionLogs]
  );
  const filteredLogs = React.useMemo(
    () => filterFlowRunLogs(logs, levelFilter, searchQuery),
    [levelFilter, logs, searchQuery]
  );

  React.useEffect(() => {
    const viewport = logViewportRef.current;
    if (!viewport || !followingTail || logs.length === 0) return;
    const frame = window.requestAnimationFrame(() => {
      viewport.scrollTop = viewport.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [followingTail, logs.length]);

  const handleLogScroll = React.useCallback(() => {
    const viewport = logViewportRef.current;
    if (!viewport) return;
    const distanceToBottom =
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    setFollowingTail(distanceToBottom < 48);
  }, []);

  const handleFollowTail = React.useCallback(() => {
    setFollowingTail(true);
    const viewport = logViewportRef.current;
    if (viewport) viewport.scrollTop = viewport.scrollHeight;
  }, []);

  const handleCopy = React.useCallback(
    async (text: string, label: string) => {
      try {
        await copyText(text);
        toast({ title: `${label}已复制`, variant: 'success' });
      } catch {
        toast({
          title: '复制失败',
          description: '当前浏览器无法访问剪贴板',
          variant: 'destructive',
        });
      }
    },
    [toast]
  );

  if (fetching && !flowRun) return <FlowRunLoadingState />;

  if (error && !flowRun) {
    return (
      <FlowRunErrorState
        id={id}
        message={error.message}
        onRetry={() => reexecuteQuery({ requestPolicy: 'network-only' })}
      />
    );
  }

  if (!flowRun) {
    return (
      <FlowRunErrorState
        id={id}
        message="该运行可能已被清理，或当前账号无权查看。"
        notFound
        onRetry={() => reexecuteQuery({ requestPolicy: 'network-only' })}
      />
    );
  }

  const status = getFlowRunStatusVisual(flowRun.state);
  const elapsedSeconds = resolveFlowRunElapsedSeconds({
    startedAt: flowRun.startedAt,
    finishedAt: flowRun.finishedAt,
    totalRunTime: flowRun.totalRunTime,
    live: isLiveRun,
    nowMs,
  });
  const elapsed = formatFlowRunDuration(elapsedSeconds);
  const parametersJson = formatJson(flowRun.parameters);
  const subscriptionError = subscriptionResult.error
    ? new Error(subscriptionResult.error.message)
    : undefined;

  const inspector = (
    <FlowRunInspector
      activeTab={inspectorTab}
      elapsed={elapsed}
      flowRun={flowRun}
      logs={logs}
      onActiveTabChange={setInspectorTab}
      onCopyId={() => void handleCopy(flowRun.id, '运行 ID')}
      onCopyParameters={() => void handleCopy(parametersJson, '运行参数')}
    />
  );
  const logPanel = (
    <div className="flex h-full min-h-0 flex-col">
      <MarketSyncEvidence key={id} runId={id} live={isLiveRun} />
      {logs.length >= 5000 && (
        <p className="px-ui-section text-ui-caption text-slate-500">
          当前保留最近 5,000 条日志；完整日志保存在 Prefect。
        </p>
      )}
      <div className="min-h-0 flex-1">
        <LogPanel
          filteredLogs={filteredLogs}
          followingTail={followingTail}
          levelFilter={levelFilter}
          live={isLiveRun}
          logViewportRef={logViewportRef}
          logs={logs}
          searchQuery={searchQuery}
          subscriptionError={subscriptionError}
          onFollowTail={handleFollowTail}
          onLevelFilterChange={setLevelFilter}
          onScroll={handleLogScroll}
          onSearchChange={setSearchQuery}
        />
      </div>
    </div>
  );

  return (
    <DataStudioPageFrame
      activeMode="OVERVIEW"
      description="任务运行监控与日志"
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                STATUS_DOT_CLASSES[status.tone]
              )}
            />
            {status.label}
          </span>
          <span className="text-slate-700">|</span>
          <span>{flowRun.flowName || '任务详情'}</span>
        </>
      }
      statusBarRight={
        <span>
          {isLiveRun
            ? subscriptionError
              ? '实时日志已降级'
              : '实时日志已连接'
            : `运行时长 ${elapsed}`}
        </span>
      }
      title={flowRun.flowName || '任务详情'}
    >
      <div className="flex h-full min-h-0 flex-col gap-ui-panel animate-fade-in">
        <header className="flex min-h-[76px] shrink-0 flex-wrap items-center gap-ui-panel rounded-panel border border-white/[0.06] bg-[#0b1120]/80 px-ui-panel py-2">
          <Button
            aria-label="返回上一页"
            className="shrink-0 border-white/10"
            size="icon"
            variant="outline"
            onClick={() => window.history.back()}
          >
            <ArrowLeft />
          </Button>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="min-w-0 truncate text-ui-page-title font-semibold text-slate-100">
                {flowRun.flowName || '未命名任务'}
              </h1>
              <RunStatusBadge state={flowRun.state} />
            </div>
            <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-ui-section gap-y-1 text-ui-caption text-slate-500">
              <span className="inline-flex items-center gap-1.5">
                <Clock3 className="h-3.5 w-3.5" />
                开始
                <time className="font-mono text-slate-400">
                  {formatDateTime(flowRun.startedAt)}
                </time>
              </span>
              <span className="inline-flex items-center gap-1.5">
                <Timer className="h-3.5 w-3.5" />
                运行时长
                <span className="font-mono text-slate-400">{elapsed}</span>
              </span>
              <span className="inline-flex min-w-0 items-center gap-1.5">
                <span>ID</span>
                <code
                  className="max-w-[360px] truncate text-slate-500"
                  title={flowRun.id}
                >
                  {flowRun.id}
                </code>
              </span>
            </div>
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Button
              className="border-white/10"
              size="sm"
              variant="outline"
              onClick={() => void handleCopy(flowRun.id, '运行 ID')}
            >
              <Copy />
              复制 ID
            </Button>
            <Button
              className="border-white/10"
              disabled={fetching}
              size="sm"
              variant="outline"
              onClick={() => reexecuteQuery({ requestPolicy: 'network-only' })}
            >
              <RefreshCw className={cn(fetching && 'animate-spin')} />
              刷新
            </Button>
          </div>
        </header>

        <div className="min-h-0 flex-1">
          <ResizablePanelGroup
            key={isWideLayout ? 'wide' : 'narrow'}
            autoSaveId={`flow-run-detail-${isWideLayout ? 'wide' : 'narrow'}`}
            className="min-h-0"
            direction={isWideLayout ? 'horizontal' : 'vertical'}
          >
            <ResizablePanel
              defaultSize={isWideLayout ? 72 : 70}
              minSize={isWideLayout ? 55 : 45}
              order={1}
            >
              {logPanel}
            </ResizablePanel>
            <ResizableHandle
              aria-label={
                isWideLayout ? '调整日志与检查器宽度' : '调整日志与检查器高度'
              }
              className={cn(
                'bg-transparent after:bg-white/[0.06] hover:after:bg-blue-400/40 focus-visible:ring-blue-400/70',
                isWideLayout ? 'mx-1' : 'my-1'
              )}
              withHandle
            />
            <ResizablePanel
              defaultSize={isWideLayout ? 28 : 30}
              maxSize={isWideLayout ? 45 : 55}
              minSize={isWideLayout ? 22 : 20}
              order={2}
            >
              {inspector}
            </ResizablePanel>
          </ResizablePanelGroup>
        </div>
      </div>
    </DataStudioPageFrame>
  );
}
