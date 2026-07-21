import { format, formatDistanceToNow, intervalToDuration } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import {
  ArrowLeft,
  Activity,
  RefreshCw,
  Clock,
  Calendar,
  CheckCircle2,
  XCircle,
  AlertCircle,
  PlayCircle,
  Timer,
  ChevronRight,
  Terminal,
  Sparkles,
  Layers,
  List,
  Box,
} from 'lucide-react';
import React, { useState } from 'react';
import { useQuery, useSubscription } from 'urql';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import { gql } from '@/generated/gql';
import { cn } from '@/utils/cn';

const GET_FLOW_RUNS = `
  query GetFlowRuns($flowName: String, $flowId: String, $deploymentId: String, $limit: Int, $offset: Int) {
    flowRuns(flowName: $flowName, flowId: $flowId, deploymentId: $deploymentId, limit: $limit, offset: $offset) {
      items {
        id
        flowName
        state
        startedAt
        expectedStartTime
        created
        finishedAt
        totalRunTime
        parameters
      }
      total
    }
  }
`;

const GET_FLOW_RUN_DETAIL = `
  query GetTaskHistoryFlowRunDetail($id: String!) {
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

const FLOW_RUN_LOGS_SUBSCRIPTION = gql(`
  subscription TaskHistoryFlowRunLogs($runId: String!, $includeHistory: Boolean) {
    flowRunLogs(runId: $runId, includeHistory: $includeHistory) {
      time
      level
      message
    }
  }
`);

interface TaskHistoryProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  deploymentId: string | undefined;
  deploymentName?: string;
  workPoolName?: string;
  workQueueName?: string;
}

type FlowRunSummary = {
  id: string;
  flowName?: string | null;
  state?: string | null;
  startedAt?: string | null;
  expectedStartTime?: string | null;
  created?: string | null;
  finishedAt?: string | null;
  totalRunTime?: number | null;
  parameters?: unknown;
};

type FlowRunsQueryData = {
  flowRuns?: {
    items?: FlowRunSummary[] | null;
    total?: number | null;
  } | null;
};

type FlowRunsQueryVariables = {
  flowName?: string;
  flowId?: string;
  deploymentId?: string;
  limit: number;
  offset: number;
};

type FlowRunTask = {
  id: string;
  name?: string | null;
  state?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  totalRunTime?: number | null;
  taskInputs?: unknown;
};

type FlowRunLog = {
  time?: string | null;
  level?: number | null;
  message?: string | null;
};

type FlowRunDetail = {
  id: string;
  flowName?: string | null;
  state?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  totalRunTime?: number | null;
  parameters?: unknown;
  taskRuns?: FlowRunTask[] | null;
  detailedLogs?: FlowRunLog[] | null;
};

const LIVE_FLOW_RUN_STATES = new Set([
  'PENDING',
  'SCHEDULED',
  'RUNNING',
  'LATE',
  'PAUSED',
  'CANCELLING',
]);

function isLiveFlowRunState(state: string | null | undefined) {
  return LIVE_FLOW_RUN_STATES.has(state?.toUpperCase() ?? '');
}

function getFlowRunLogKey(log: FlowRunLog) {
  return `${log.time ?? ''}::${log.level ?? ''}::${log.message ?? ''}`;
}

function getFlowRunLogTime(log: FlowRunLog) {
  if (!log.time) return 0;
  const timestamp = new Date(log.time).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function mergeFlowRunLogs(...logGroups: FlowRunLog[][]) {
  const seen = new Set<string>();
  const merged: FlowRunLog[] = [];

  logGroups.flat().forEach(log => {
    const key = getFlowRunLogKey(log);
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(log);
  });

  return merged.sort(
    (left, right) => getFlowRunLogTime(left) - getFlowRunLogTime(right)
  );
}

function safeParseParameters(parameters: unknown): Record<string, unknown> {
  if (!parameters) return {};

  if (typeof parameters === 'string') {
    try {
      const parsed = JSON.parse(parameters);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {};
    }
  }

  if (typeof parameters === 'object' && !Array.isArray(parameters)) {
    return parameters as Record<string, unknown>;
  }

  return {};
}

function getParameterCount(parameters: unknown) {
  return Object.keys(safeParseParameters(parameters)).length;
}

function formatParameters(parameters: unknown) {
  return JSON.stringify(safeParseParameters(parameters), null, 2);
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || seconds <= 0) return '-';

  const duration = intervalToDuration({ start: 0, end: seconds * 1000 });

  const parts: string[] = [];
  if (duration.hours && duration.hours > 0) parts.push(`${duration.hours}小时`);
  if (duration.minutes && duration.minutes > 0)
    parts.push(`${duration.minutes}分`);
  if (
    duration.seconds !== undefined &&
    duration.seconds >= 0 &&
    parts.length === 0
  ) {
    parts.push(`${seconds.toFixed(1)}秒`);
  } else if (duration.seconds && duration.seconds > 0) {
    parts.push(`${duration.seconds}秒`);
  }

  return parts.length > 0 ? parts.join('') : '-';
}

function getStatusConfig(state: string | null | undefined) {
  const normalizedState = state?.toUpperCase();
  switch (normalizedState) {
    case 'COMPLETED':
      return {
        color: 'bg-emerald-500',
        text: 'text-emerald-400',
        bg: 'bg-emerald-500/10',
        border: 'border-emerald-500/20',
        shadow: 'shadow-emerald-500/10',
        icon: CheckCircle2,
        gradient: 'from-emerald-500/20 to-transparent',
      };
    case 'FAILED':
    case 'CRASHED':
    case 'STALE':
      return {
        color: 'bg-red-500',
        text: 'text-red-400',
        bg: 'bg-red-500/10',
        border: 'border-red-500/20',
        shadow: 'shadow-red-500/10',
        icon: XCircle,
        gradient: 'from-red-500/20 to-transparent',
      };
    case 'CANCELLED':
      return {
        color: 'bg-slate-400',
        text: 'text-slate-400',
        bg: 'bg-slate-400/10',
        border: 'border-slate-400/20',
        shadow: 'shadow-slate-500/10',
        icon: XCircle,
        gradient: 'from-slate-500/20 to-transparent',
      };
    case 'RUNNING':
      return {
        color: 'bg-blue-500',
        text: 'text-blue-400',
        bg: 'bg-blue-500/10',
        border: 'border-blue-500/20',
        shadow: 'shadow-blue-500/10',
        icon: PlayCircle,
        gradient: 'from-blue-500/20 to-transparent',
      };
    case 'PENDING':
      return {
        color: 'bg-amber-500',
        text: 'text-amber-400',
        bg: 'bg-amber-500/10',
        border: 'border-amber-500/20',
        shadow: 'shadow-amber-500/10',
        icon: Timer,
        gradient: 'from-amber-500/20 to-transparent',
      };
    case 'SCHEDULED':
      return {
        color: 'bg-purple-500',
        text: 'text-purple-400',
        bg: 'bg-purple-500/10',
        border: 'border-purple-500/20',
        shadow: 'shadow-purple-500/10',
        icon: Calendar,
        gradient: 'from-purple-500/20 to-transparent',
      };
    default:
      return {
        color: 'bg-slate-500',
        text: 'text-slate-400',
        bg: 'bg-slate-500/10',
        border: 'border-slate-500/20',
        shadow: 'shadow-slate-500/10',
        icon: Activity,
        gradient: 'from-slate-500/20 to-transparent',
      };
  }
}

export function TaskHistory({
  open,
  onOpenChange,
  deploymentId,
  deploymentName = 'sector-data-sync',
  workPoolName = 'process-pool',
  workQueueName = 'default',
}: TaskHistoryProps) {
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize] = useState(10);
  const [scheduledExpanded, setScheduledExpanded] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const safeFormatDistanceToNow = (dateStr: string | null | undefined) => {
    if (!dateStr) return '-';
    try {
      const date = new Date(dateStr);
      if (isNaN(date.getTime())) return '-';
      return formatDistanceToNow(date, { addSuffix: true, locale: zhCN });
    } catch {
      return '-';
    }
  };

  // Fetch flow runs history
  const [{ data: historyData, fetching: fetchingHistory }, reexecuteHistory] =
    useQuery<FlowRunsQueryData, FlowRunsQueryVariables>({
      query: GET_FLOW_RUNS,
      variables: {
        deploymentId,
        limit: historyPageSize,
        offset: (historyPage - 1) * historyPageSize,
      },
      pause: !open || !deploymentId,
      requestPolicy: 'cache-and-network', // Always fetch fresh data
    });

  // Force refresh when sheet opens
  React.useEffect(() => {
    if (open && deploymentId) {
      reexecuteHistory({ requestPolicy: 'network-only' });
    }
  }, [open, deploymentId, reexecuteHistory]);

  React.useEffect(() => {
    if (!open) setSelectedRunId(null);
  }, [open]);

  React.useEffect(() => {
    setSelectedRunId(null);
  }, [deploymentId]);

  const historyRuns = historyData?.flowRuns?.items ?? [];
  const scheduledRuns = historyRuns
    .filter(run => run.state?.toUpperCase() === 'SCHEDULED')
    .sort(
      (left, right) =>
        new Date(left.expectedStartTime || 0).getTime() -
        new Date(right.expectedStartTime || 0).getTime()
    );
  const pastRuns = historyRuns.filter(
    run => run.state?.toUpperCase() !== 'SCHEDULED'
  );

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-[85vw] sm:max-w-[700px] flex flex-col p-0 border-l border-slate-200/50 dark:border-white/5 shadow-2xl bg-white dark:bg-slate-950"
      >
        {/* Header */}
        {/* Header - Glassmorphism */}
        <SheetHeader className="sticky top-0 z-50 px-5 py-4 border-b border-slate-200/50 dark:border-white/5 bg-white/80 dark:bg-slate-950/80 backdrop-blur-md">
          <div className="flex items-center gap-2.5">
            <div className="p-2 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-xl shadow-lg shadow-blue-500/20">
              <Activity size={16} className="text-white" />
            </div>
            <div>
              <SheetTitle className="text-base font-bold text-slate-900 dark:text-white">
                {selectedRunId ? '任务日志详情' : '任务历史记录'}
              </SheetTitle>
              <SheetDescription className="text-slate-500 dark:text-slate-400 text-xs mt-0.5">
                {selectedRunId
                  ? `${deploymentName} 的单次执行明细`
                  : `${deploymentName} 的执行历史`}
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

        {selectedRunId ? (
          <FlowRunInlineDetail
            runId={selectedRunId}
            deploymentName={deploymentName}
            onBack={() => setSelectedRunId(null)}
          />
        ) : (
          <>
            {/* Content */}
            <ScrollArea className="flex-1 bg-slate-50/50 dark:bg-[#0A0B0E]">
              {fetchingHistory ? (
                <div className="flex flex-col items-center justify-center p-12 space-y-4">
                  <div className="relative">
                    <div className="absolute inset-0 bg-blue-500/20 rounded-full blur-lg animate-pulse" />
                    <RefreshCw className="relative w-10 h-10 text-blue-500 animate-spin" />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                      正在加载...
                    </p>
                  </div>
                </div>
              ) : historyRuns.length === 0 ? (
                <div className="flex flex-col items-center justify-center p-12 space-y-4">
                  <div className="p-4 bg-slate-100 dark:bg-white/5 rounded-2xl">
                    <Clock
                      size={32}
                      className="text-slate-400 dark:text-slate-600"
                    />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-medium text-slate-600 dark:text-slate-400">
                      暂无执行记录
                    </p>
                  </div>
                </div>
              ) : (
                <div className="p-4 space-y-4">
                  {/* Scheduled Tasks Section */}
                  {scheduledRuns.length > 0 && (
                    <div className="space-y-3">
                      <button
                        type="button"
                        className="group flex w-full items-center gap-1.5 px-1 text-left"
                        onClick={() => setScheduledExpanded(!scheduledExpanded)}
                      >
                        <Sparkles size={14} className="text-purple-400" />
                        <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                          即将运行
                        </h4>
                        <div className="h-px flex-1 bg-gradient-to-r from-purple-500/20 to-transparent" />
                        <ChevronRight
                          size={14}
                          className={cn(
                            'text-slate-400 transition-transform duration-300',
                            scheduledExpanded && 'rotate-90'
                          )}
                        />
                      </button>
                      <div
                        className={cn(
                          'relative min-h-[120px] transition-all duration-500 ease-in-out',
                          scheduledExpanded && 'pb-4'
                        )}
                      >
                        {scheduledRuns.map((run, idx) => (
                          <div
                            key={run.id}
                            className={cn(
                              'w-full bg-white dark:bg-slate-900 rounded-xl border border-purple-200 dark:border-purple-500/20 overflow-hidden transition-all duration-500 ease-[bezier(0.25,0.1,0.25,1)] cursor-pointer',
                              // Stack positioning logic: ALL relative now
                              'relative',
                              // Negative margin to pull items up into a stack
                              idx > 0 && !scheduledExpanded && '-mt-[105px]',
                              idx > 0 && scheduledExpanded && 'mt-3',
                              // Z-index to ensure correct layering (top one is highest)
                              idx === 0 ? 'z-30' : 'z-20',
                              // Visual stacking effect (scale & opacity) when collapsed
                              idx > 0 &&
                                !scheduledExpanded &&
                                'scale-95 opacity-0',
                              idx > 0 &&
                                scheduledExpanded &&
                                'opacity-100 scale-100',
                              // Hover state specific tweaks
                              idx > 0 && 'shadow-sm hover:shadow-purple-500/10'
                            )}
                            style={{
                              // Custom style for the 'stacked' peek effect
                              transform:
                                idx > 0 && !scheduledExpanded
                                  ? `scale(${1 - idx * 0.04}) translateY(${idx * 10}px)`
                                  : 'none',
                              zIndex: 30 - idx,
                            }}
                          >
                            {/* Gradient Background */}
                            <div className="absolute inset-0 bg-gradient-to-br from-purple-500/5 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300" />

                            {/* Status Bar - only show on hover for stacked items, or always if it looks good */}
                            <div className="absolute top-0 bottom-0 left-0 w-0.5 bg-gradient-to-b from-purple-500 to-purple-600" />

                            <div className="relative p-3.5">
                              <div className="flex items-start justify-between mb-3">
                                <div className="flex items-center gap-2">
                                  <div className="p-1.5 bg-purple-500/10 rounded-lg">
                                    <Calendar
                                      size={14}
                                      className="text-purple-500"
                                    />
                                  </div>
                                  <div>
                                    <h5 className="font-semibold text-sm text-slate-900 dark:text-white">
                                      {run.flowName || 'Sector Data Sync'}
                                    </h5>
                                    <div className="flex items-center gap-1.5 mt-0.5">
                                      <span className="text-[10px] font-mono text-slate-500">
                                        {run.id.substring(0, 8)}
                                      </span>
                                    </div>
                                  </div>
                                </div>
                                <Badge className="bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/20 px-2 py-0.5 text-[10px] font-semibold shadow-sm">
                                  已调度
                                </Badge>
                              </div>

                              <div className="flex items-center gap-4">
                                <div className="flex items-center gap-2">
                                  <p className="text-[9px] font-medium text-slate-500 uppercase tracking-wider">
                                    计划时间
                                  </p>
                                  <p className="text-xs font-semibold text-slate-900 dark:text-white">
                                    {run.expectedStartTime
                                      ? format(
                                          new Date(run.expectedStartTime),
                                          'MM-dd HH:mm'
                                        )
                                      : '-'}
                                  </p>
                                </div>
                                <div className="flex items-center gap-2">
                                  <p className="text-[9px] font-medium text-slate-500 uppercase tracking-wider">
                                    距离开始
                                  </p>
                                  <p className="text-xs font-semibold text-purple-600 dark:text-purple-400">
                                    {safeFormatDistanceToNow(
                                      run.expectedStartTime
                                    )}
                                  </p>
                                </div>
                              </div>
                            </div>
                          </div>
                        ))}
                        {/* Shadow/Stack indicators for the collapsed state */}
                        <div className="absolute bottom-1 left-3 right-3 h-2 bg-purple-500/5 rounded-b-xl -z-10 group-hover/stack:opacity-0 transition-all duration-300" />
                        <div className="absolute bottom-0 left-5 right-5 h-2 bg-purple-500/5 rounded-b-xl -z-20 group-hover/stack:opacity-0 transition-all duration-300" />
                      </div>
                    </div>
                  )}

                  {/* Past Runs Section */}
                  <div className="space-y-3">
                    <div className="flex items-center justify-between px-1">
                      <div className="flex items-center gap-1.5">
                        <Clock size={14} className="text-blue-400" />
                        <h4 className="text-xs font-semibold text-slate-700 dark:text-slate-300">
                          历史运行
                        </h4>
                      </div>
                      <Badge
                        variant="outline"
                        className="border-slate-200 dark:border-white/10 text-slate-500 text-[10px] bg-transparent"
                      >
                        {pastRuns.length} 条记录
                      </Badge>
                    </div>

                    <div className="relative space-y-2 pl-3">
                      {/* Timeline Line */}
                      <div className="absolute left-[20px] top-4 bottom-4 w-px bg-gradient-to-b from-slate-200 to-transparent dark:from-slate-800" />

                      {pastRuns.map(run => {
                        const config = getStatusConfig(run.state);
                        const Icon = config.icon;
                        const isRunning =
                          run.state?.toUpperCase() === 'RUNNING';

                        return (
                          <div
                            key={run.id}
                            className="group relative bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-white/5 overflow-hidden transition-all duration-300"
                          >
                            {/* Hover Gradient */}
                            <div className="absolute inset-0 bg-gradient-to-br from-blue-500/5 via-transparent to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none" />

                            {/* Status Bar */}
                            <div
                              className={cn(
                                'absolute top-0 bottom-0 left-0 w-0.5 transition-all duration-300 group-hover:w-1',
                                config.color
                              )}
                            />

                            <div className="relative p-3">
                              <div className="flex items-start justify-between mb-2.5">
                                <div className="flex items-center gap-2">
                                  <div
                                    className={cn(
                                      'relative p-1.5 rounded-lg transition-all duration-300 z-10',
                                      config.bg
                                    )}
                                  >
                                    {isRunning && (
                                      <span className="absolute inset-0 rounded-lg bg-blue-500 opacity-20 animate-ping" />
                                    )}
                                    <Icon size={14} className={config.text} />
                                  </div>
                                  <div>
                                    <h5 className="font-semibold text-sm text-slate-900 dark:text-white">
                                      {run.flowName || 'Sector Data Sync'}
                                    </h5>
                                    <div className="flex items-center gap-1.5 mt-0.5">
                                      <span className="text-[10px] font-mono text-slate-500">
                                        {run.id.substring(0, 8)}
                                      </span>
                                    </div>
                                  </div>
                                </div>
                                <Badge
                                  className={cn(
                                    'flex items-center gap-1 px-2 py-0.5 text-[10px] font-semibold border shadow-sm',
                                    config.bg,
                                    config.text,
                                    config.border,
                                    config.shadow
                                  )}
                                >
                                  <Icon size={10} />
                                  {run.state}
                                </Badge>
                              </div>

                              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-2.5">
                                <div className="flex items-center gap-1.5">
                                  <span className="text-[10px] text-slate-500">
                                    开始:
                                  </span>
                                  <span className="text-[11px] font-medium text-slate-900 dark:text-white font-mono">
                                    {run.startedAt
                                      ? format(
                                          new Date(run.startedAt),
                                          'MM-dd HH:mm'
                                        )
                                      : '-'}
                                  </span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                  <span className="text-[10px] text-slate-500">
                                    时长:
                                  </span>
                                  <span className="text-[11px] font-medium text-slate-900 dark:text-white font-mono">
                                    {formatDuration(run.totalRunTime)}
                                  </span>
                                </div>
                                <div className="flex items-center gap-1.5">
                                  <span className="text-[10px] text-slate-500">
                                    参数:
                                  </span>
                                  <span className="text-[11px] font-medium text-slate-900 dark:text-white font-mono">
                                    {getParameterCount(run.parameters)}
                                  </span>
                                </div>
                              </div>

                              {/* Clickable Footer Area */}
                              <button
                                type="button"
                                className="flex w-full items-center gap-3 pt-3 border-t border-slate-200 dark:border-white/5 cursor-pointer hover:bg-slate-50 dark:hover:bg-white/5 -mx-3 -mb-3 px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"
                                aria-label={`查看 ${
                                  run.flowName || deploymentName
                                } 的日志详情`}
                                onClick={() => setSelectedRunId(run.id)}
                              >
                                <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-white/5 px-2 py-1 rounded-md">
                                  <Box size={12} className="text-blue-500" />
                                  <span
                                    className="truncate max-w-[80px]"
                                    title={deploymentName}
                                  >
                                    {deploymentName}
                                  </span>
                                </div>
                                <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-white/5 px-2 py-1 rounded-md">
                                  <Layers
                                    size={12}
                                    className="text-purple-500"
                                  />
                                  <span
                                    className="truncate max-w-[80px]"
                                    title={workPoolName}
                                  >
                                    {workPoolName}
                                  </span>
                                </div>
                                <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 bg-slate-100 dark:bg-white/5 px-2 py-1 rounded-md">
                                  <List size={12} className="text-amber-500" />
                                  <span
                                    className="truncate max-w-[80px]"
                                    title={workQueueName}
                                  >
                                    {workQueueName}
                                  </span>
                                </div>
                                <div className="flex-1" />
                                <ChevronRight
                                  size={14}
                                  className="text-slate-400 group-hover:text-blue-500 group-hover:translate-x-0.5 transition-all duration-300"
                                />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}
            </ScrollArea>

            {/* Pagination Footer - Glassmorphism */}
            <div className="sticky bottom-0 z-50 px-4 py-3 border-t border-slate-200 dark:border-white/5 bg-white/80 dark:bg-slate-950/80 backdrop-blur-md flex items-center justify-between">
              <div className="text-[10px] text-slate-500 dark:text-slate-400">
                第{' '}
                <span className="font-semibold text-slate-700 dark:text-slate-300">
                  {historyPage}
                </span>{' '}
                页
              </div>
              <div className="flex gap-1.5">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-[10px] border-slate-200 dark:border-white/10 bg-white dark:bg-white/5 hover:bg-slate-100 dark:hover:bg-white/10 text-slate-700 dark:text-slate-300 transition-all"
                  disabled={historyPage === 1 || fetchingHistory}
                  onClick={() => setHistoryPage(p => Math.max(1, p - 1))}
                >
                  上一页
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-[10px] border-slate-200 dark:border-white/10 bg-white dark:bg-white/5 hover:bg-slate-100 dark:hover:bg-white/10 text-slate-700 dark:text-slate-300 transition-all"
                  disabled={
                    historyRuns.length < historyPageSize || fetchingHistory
                  }
                  onClick={() => setHistoryPage(p => p + 1)}
                >
                  下一页
                </Button>
              </div>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}

function FlowRunInlineDetail({
  runId,
  deploymentName,
  onBack,
}: {
  runId: string;
  deploymentName: string;
  onBack: () => void;
}) {
  const [{ data, fetching: detailFetching, error }] = useQuery<
    { flowRun?: FlowRunDetail | null },
    { id: string }
  >({
    query: GET_FLOW_RUN_DETAIL,
    variables: { id: runId },
    pause: !runId,
    requestPolicy: 'cache-and-network',
  });

  const [
    {
      data: logSubscriptionData,
      fetching: logSubscriptionFetching,
      error: logSubscriptionError,
    },
  ] = useSubscription({
    query: FLOW_RUN_LOGS_SUBSCRIPTION,
    variables: {
      runId,
      includeHistory: true,
    },
    pause: !runId,
  });

  const flowRun = data?.flowRun;
  const taskRuns = flowRun?.taskRuns ?? [];
  const [subscriptionLogs, setSubscriptionLogs] = React.useState<FlowRunLog[]>(
    []
  );
  const incomingLog = logSubscriptionData?.flowRunLogs;
  const logs = React.useMemo(
    () => mergeFlowRunLogs(flowRun?.detailedLogs ?? [], subscriptionLogs),
    [flowRun?.detailedLogs, subscriptionLogs]
  );
  const isLiveRun = isLiveFlowRunState(flowRun?.state);
  const logViewportRef = React.useRef<HTMLDivElement | null>(null);
  const shouldFollowLogTailRef = React.useRef(true);

  React.useEffect(() => {
    shouldFollowLogTailRef.current = true;
    setSubscriptionLogs([]);
  }, [runId]);

  React.useEffect(() => {
    if (!incomingLog) return;

    setSubscriptionLogs(previousLogs =>
      mergeFlowRunLogs(previousLogs, [
        {
          time: incomingLog.time,
          level: incomingLog.level,
          message: incomingLog.message,
        },
      ])
    );
  }, [incomingLog]);

  React.useEffect(() => {
    const viewport = logViewportRef.current;
    if (!viewport || logs.length === 0 || !shouldFollowLogTailRef.current) {
      return;
    }

    requestAnimationFrame(() => {
      viewport.scrollTop = viewport.scrollHeight;
    });
  }, [logs.length, runId]);

  const handleLogScroll = () => {
    const viewport = logViewportRef.current;
    if (!viewport) return;

    const distanceToBottom =
      viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
    shouldFollowLogTailRef.current = distanceToBottom < 48;
  };

  if (detailFetching && !flowRun) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 bg-slate-50/50 p-12 dark:bg-[#0A0B0E]">
        <RefreshCw className="h-9 w-9 animate-spin text-blue-500" />
        <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
          正在加载日志详情...
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-1 flex-col gap-4 bg-slate-50/50 p-4 dark:bg-[#0A0B0E]">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit gap-1.5 text-xs"
          onClick={onBack}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          返回历史
        </Button>
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 p-4 text-sm text-red-400">
          日志详情加载失败：{error.message}
        </div>
      </div>
    );
  }

  if (!flowRun) {
    return (
      <div className="flex flex-1 flex-col gap-4 bg-slate-50/50 p-4 dark:bg-[#0A0B0E]">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit gap-1.5 text-xs"
          onClick={onBack}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          返回历史
        </Button>
        <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm text-slate-500 dark:border-white/5 dark:bg-white/[0.03] dark:text-slate-400">
          未找到这次任务运行。
        </div>
      </div>
    );
  }

  const config = getStatusConfig(flowRun.state);
  const Icon = config.icon;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-slate-50/50 dark:bg-[#0A0B0E]">
      <div className="border-b border-slate-200/60 bg-white/70 px-4 py-3 backdrop-blur dark:border-white/5 dark:bg-slate-950/70">
        <Button
          variant="ghost"
          size="sm"
          className="mb-3 h-7 gap-1.5 px-2 text-xs text-slate-600 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
          onClick={onBack}
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          返回历史
        </Button>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-base font-black text-slate-900 dark:text-white">
              {flowRun.flowName || deploymentName}
            </h3>
            <p className="mt-1 break-all font-mono text-[10px] text-slate-500">
              {flowRun.id}
            </p>
          </div>
          <Badge
            className={cn(
              'shrink-0 items-center gap-1 border px-2 py-0.5 text-[10px] font-semibold shadow-sm',
              config.bg,
              config.text,
              config.border,
              config.shadow
            )}
          >
            <Icon size={10} />
            {flowRun.state || 'UNKNOWN'}
          </Badge>
        </div>

        <div className="mt-3 grid grid-cols-2 gap-2 text-[10px] text-slate-500 sm:grid-cols-3">
          <span className="inline-flex items-center gap-1.5">
            <Clock className="h-3 w-3" />
            {flowRun.startedAt
              ? format(new Date(flowRun.startedAt), 'MM-dd HH:mm')
              : '-'}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <Timer className="h-3 w-3" />
            {formatDuration(flowRun.totalRunTime)}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <List className="h-3 w-3" />
            参数 {getParameterCount(flowRun.parameters)}
          </span>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-4 p-4">
          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-white/5 dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3 dark:border-white/5">
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                任务执行
              </h4>
              <Badge
                variant="outline"
                className="border-slate-200 bg-transparent text-[10px] text-slate-500 dark:border-white/10"
              >
                {taskRuns.length} 个任务
              </Badge>
            </div>
            {taskRuns.length === 0 ? (
              <div className="p-6 text-center text-xs text-slate-400">
                暂无任务执行明细
              </div>
            ) : (
              <div className="divide-y divide-slate-100 dark:divide-white/5">
                {taskRuns.map(task => {
                  const taskConfig = getStatusConfig(task.state);
                  return (
                    <div
                      key={task.id}
                      className="flex items-center justify-between gap-3 px-4 py-3"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-slate-900 dark:text-white">
                          {task.name || '未命名任务'}
                        </p>
                        <p className="mt-1 font-mono text-[10px] text-slate-500">
                          {task.startedAt
                            ? format(new Date(task.startedAt), 'HH:mm:ss')
                            : '-'}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <span className="font-mono text-[10px] text-slate-500">
                          {formatDuration(task.totalRunTime)}
                        </span>
                        <Badge
                          variant="outline"
                          className={cn(
                            'border px-2 py-0.5 text-[10px] font-semibold',
                            taskConfig.bg,
                            taskConfig.text,
                            taskConfig.border
                          )}
                        >
                          {task.state || 'UNKNOWN'}
                        </Badge>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section className="flex min-h-[320px] flex-col overflow-hidden rounded-xl border border-slate-800 bg-slate-950 text-slate-200 shadow-sm">
            <div className="flex items-center justify-between border-b border-white/10 bg-white/5 px-4 py-3">
              <h4 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-slate-400">
                <Terminal className="h-3.5 w-3.5" />
                运行日志
              </h4>
              <div className="flex items-center gap-2">
                {(isLiveRun || logSubscriptionError) && (
                  <span
                    className={cn(
                      'inline-flex h-5 items-center gap-1.5 rounded-full border px-2 text-[10px] font-semibold',
                      logSubscriptionError
                        ? 'border-red-400/20 bg-red-400/10 text-red-300'
                        : 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'
                    )}
                    title={logSubscriptionError?.message}
                  >
                    <span
                      className={cn(
                        'h-1.5 w-1.5 rounded-full',
                        logSubscriptionError ? 'bg-red-300' : 'bg-emerald-300',
                        logSubscriptionFetching && 'animate-pulse'
                      )}
                    />
                    {logSubscriptionError ? '实时断开' : '实时'}
                  </span>
                )}
                <Badge className="border border-white/10 bg-white/5 text-[10px] text-slate-400">
                  {logs.length} 条
                </Badge>
              </div>
            </div>
            <div
              ref={logViewportRef}
              className="max-h-[420px] overflow-auto scroll-smooth p-4 font-mono text-xs"
              aria-live={logSubscriptionFetching ? 'polite' : undefined}
              onScroll={handleLogScroll}
            >
              {logs.length === 0 ? (
                <div className="flex items-center gap-2 text-slate-600">
                  <AlertCircle className="h-3.5 w-3.5" />
                  暂无日志
                </div>
              ) : (
                <div className="space-y-1">
                  {logs.map((log, index) => {
                    const level = Number(log.level ?? 0);
                    return (
                      <div
                        key={`${log.time ?? 'log'}-${index}`}
                        className="flex gap-3 rounded px-2 py-0.5 hover:bg-white/5"
                      >
                        <span className="w-20 shrink-0 select-none text-slate-500">
                          {log.time
                            ? format(new Date(log.time), 'HH:mm:ss')
                            : '--:--:--'}
                        </span>
                        <span
                          className={cn(
                            'w-16 shrink-0 font-bold',
                            level >= 40
                              ? 'text-red-400'
                              : level >= 30
                                ? 'text-yellow-400'
                                : 'text-blue-400'
                          )}
                        >
                          {level >= 40
                            ? 'ERROR'
                            : level >= 30
                              ? 'WARN'
                              : 'INFO'}
                        </span>
                        <span className="break-all whitespace-pre-wrap text-slate-300">
                          {log.message || ''}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </section>

          <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm dark:border-white/5 dark:bg-slate-900">
            <div className="border-b border-slate-100 px-4 py-3 dark:border-white/5">
              <h4 className="text-xs font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                参数
              </h4>
            </div>
            <pre className="max-h-64 overflow-auto p-4 text-xs text-slate-600 dark:text-slate-300">
              {formatParameters(flowRun.parameters)}
            </pre>
          </section>
        </div>
      </ScrollArea>
    </div>
  );
}
