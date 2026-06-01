import { format, formatDistanceToNow, intervalToDuration } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import {
  Activity,
  RefreshCw,
  Clock,
  Calendar,
  CheckCircle2,
  XCircle,
  PlayCircle,
  Timer,
  ChevronRight,
  Sparkles,
  Layers,
  List,
  Box,
} from 'lucide-react';
import React, { useState } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

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

const GET_FLOW_RUNS = gql(`
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
`);

interface TaskHistoryProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  deploymentId: string | undefined;
  deploymentName?: string;
  workPoolName?: string;
  workQueueName?: string;
}

export function TaskHistory({
  open,
  onOpenChange,
  deploymentId,
  deploymentName = 'sector-data-sync',
  workPoolName = 'process-pool',
  workQueueName = 'default',
}: TaskHistoryProps) {
  const [, setLocation] = useLocation();
  const [historyPage, setHistoryPage] = useState(1);
  const [historyPageSize] = useState(10);
  const [scheduledExpanded, setScheduledExpanded] = useState(false);

  const safeFormatDistanceToNow = (dateStr: string | null | undefined) => {
    if (!dateStr) return '-';
    try {
      const date = new Date(dateStr);
      if (isNaN(date.getTime())) return '-';
      return formatDistanceToNow(date, { addSuffix: true, locale: zhCN });
    } catch (e) {
      return '-';
    }
  };

  const formatDuration = (seconds: number): string => {
    if (seconds <= 0) return '-';

    const duration = intervalToDuration({ start: 0, end: seconds * 1000 });

    const parts: string[] = [];
    if (duration.hours && duration.hours > 0)
      parts.push(`${duration.hours}小时`);
    if (duration.minutes && duration.minutes > 0)
      parts.push(`${duration.minutes}分`);
    if (
      duration.seconds !== undefined &&
      duration.seconds >= 0 &&
      parts.length === 0
    ) {
      // 只有秒时显示小数
      parts.push(`${seconds.toFixed(1)}秒`);
    } else if (duration.seconds && duration.seconds > 0) {
      parts.push(`${duration.seconds}秒`);
    }

    return parts.length > 0 ? parts.join('') : '-';
  };

  // Fetch flow runs history
  const [{ data: historyData, fetching: fetchingHistory }, reexecuteHistory] =
    useQuery({
      query: GET_FLOW_RUNS as any,
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

  const getStatusConfig = (state: string) => {
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
  };

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
                任务历史记录
              </SheetTitle>
              <SheetDescription className="text-slate-500 dark:text-slate-400 text-xs mt-0.5">
                {deploymentName} 的执行历史
              </SheetDescription>
            </div>
          </div>
        </SheetHeader>

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
          ) : !historyData?.flowRuns?.items ||
            historyData.flowRuns.items.length === 0 ? (
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
              {(historyData?.flowRuns?.items ?? []).filter(
                (r: any) => r.state?.toUpperCase() === 'SCHEDULED'
              ).length > 0 && (
                <div className="space-y-3">
                  <div
                    className="flex items-center gap-1.5 px-1 cursor-pointer group"
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
                  </div>
                  <div
                    className={cn(
                      'relative min-h-[120px] transition-all duration-500 ease-in-out',
                      scheduledExpanded && 'pb-4'
                    )}
                  >
                    {(historyData?.flowRuns?.items ?? [])
                      .filter(
                        (r: any) => r.state?.toUpperCase() === 'SCHEDULED'
                      )
                      .sort(
                        (a: any, b: any) =>
                          new Date(a.expectedStartTime).getTime() -
                          new Date(b.expectedStartTime).getTime()
                      )
                      .map((run: any, idx: number) => (
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
                    {
                      (historyData?.flowRuns?.items ?? []).filter(
                        (r: any) => r.state?.toUpperCase() !== 'SCHEDULED'
                      ).length
                    }{' '}
                    条记录
                  </Badge>
                </div>

                <div className="relative space-y-2 pl-3">
                  {/* Timeline Line */}
                  <div className="absolute left-[20px] top-4 bottom-4 w-px bg-gradient-to-b from-slate-200 to-transparent dark:from-slate-800" />

                  {(historyData?.flowRuns?.items ?? [])
                    .filter((r: any) => r.state?.toUpperCase() !== 'SCHEDULED')
                    .map((run: any) => {
                      const config = getStatusConfig(run.state);
                      const Icon = config.icon;
                      const isRunning = run.state === 'Running';

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
                                  <Icon
                                    size={14}
                                    className={cn(
                                      config.text,
                                      isRunning && 'animate-spin-slow'
                                    )}
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
                                  {run.totalRunTime > 0
                                    ? formatDuration(run.totalRunTime)
                                    : '-'}
                                </span>
                              </div>
                              <div className="flex items-center gap-1.5">
                                <span className="text-[10px] text-slate-500">
                                  参数:
                                </span>
                                <span className="text-[11px] font-medium text-slate-900 dark:text-white font-mono">
                                  {run.parameters
                                    ? Object.keys(JSON.parse(run.parameters))
                                        .length
                                    : 0}
                                </span>
                              </div>
                            </div>

                            {/* Clickable Footer Area */}
                            <div
                              className="flex items-center gap-3 pt-3 border-t border-slate-200 dark:border-white/5 cursor-pointer hover:bg-slate-50 dark:hover:bg-white/5 -mx-3 -mb-3 px-3 py-2 transition-colors"
                              onClick={() =>
                                setLocation(`/system/flow-runs/${run.id}`)
                              }
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
                                <Layers size={12} className="text-purple-500" />
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
                            </div>
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
                (historyData?.flowRuns?.items?.length || 0) < historyPageSize ||
                fetchingHistory
              }
              onClick={() => setHistoryPage(p => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
