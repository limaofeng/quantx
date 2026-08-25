import { format } from 'date-fns';
import {
  Activity,
  AlertTriangle,
  Timer,
  RefreshCw,
  BarChart4,
  PauseCircle,
  PlayCircle,
  Square,
} from 'lucide-react';
import React from 'react';

import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { cn } from '@/utils/cn';

export interface DeploymentStatus {
  id: string;
  name: string;
  flowName: string;
  description: string;
  workPoolName: string;
  isScheduleActive: boolean;
  lastRunTime: string | null;
  nextRunTime: string | null;
  status: string | null;
  activeRunId: string | null;
  activeRunStatus: string | null;
  isStale: boolean;
  staleReason: string | null;
  latestActivityTime: string | null;
}

interface SyncControlPanelProps {
  deployment: DeploymentStatus | undefined;
  isSyncing: boolean;
  isRunCancelling?: boolean;
  isScheduleUpdating?: boolean;
  defaultFlowName?: string;
  onCancelRun?: () => void;
  onShowHistory: () => void;
  onSync: () => void;
  onToggleSchedule?: () => void;
  syncDisabled?: boolean;
  syncDisabledReason?: string;
}

export function SyncControlPanel({
  deployment,
  isSyncing,
  isRunCancelling = false,
  isScheduleUpdating = false,
  defaultFlowName = '数据同步',
  onCancelRun,
  onShowHistory,
  onSync,
  onToggleSchedule,
  syncDisabled = false,
  syncDisabledReason,
}: SyncControlPanelProps) {
  const isScheduleActive = deployment?.isScheduleActive ?? false;
  const runtimeStatus = getRuntimeStatus(deployment?.status);
  const isStale = deployment?.isStale ?? false;
  const canCancelRun = Boolean(onCancelRun && deployment?.activeRunId);
  const syncButtonCancels = isSyncing && canCancelRun;
  const scheduleActionLabel = !deployment
    ? '调度'
    : isScheduleActive
      ? '暂停调度'
      : '恢复调度';
  const syncLabel = isSyncing ? '同步中...' : '同步';
  const syncButtonTitle = syncDisabled
    ? syncDisabledReason
    : syncButtonCancels
      ? isStale
        ? deployment?.staleReason || '点击停止疑似卡死的运行'
        : '点击停止当前运行'
      : undefined;

  const handleSyncButtonClick = () => {
    if (syncButtonCancels && onCancelRun) {
      onCancelRun();
      return;
    }

    onSync();
  };

  return (
    <div className="flex flex-col md:flex-row items-stretch md:items-center gap-2">
      <div className="flex items-center gap-2 bg-white/80 dark:bg-slate-900/40 backdrop-blur-sm p-1 rounded-panel border border-slate-200/60 dark:border-slate-700/60 shadow-sm">
        {/* Compact Sync Info */}
        <div className="hidden lg:flex items-center gap-3 px-3 py-0.5">
          <div className="flex flex-col justify-center border-r border-slate-200/60 dark:border-white/10 pr-3">
            <span className="text-ui-micro font-black text-slate-400 uppercase tracking-widest leading-none mb-0.5">
              任务来源
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className="text-ui-label font-bold text-slate-800 dark:text-slate-200 truncate max-w-[100px]"
                title={deployment?.description || undefined}
              >
                {deployment?.flowName || defaultFlowName}
              </span>
              {deployment?.workPoolName && (
                <span className="text-ui-micro px-1 py-0 bg-slate-100 dark:bg-white/5 text-slate-500 rounded-sm font-mono uppercase border border-slate-200/50">
                  {deployment.workPoolName}
                </span>
              )}
              {deployment && (
                <span
                  className={cn(
                    'text-ui-micro px-1 py-0 rounded-sm font-mono uppercase border',
                    isScheduleActive
                      ? 'bg-emerald-500/10 text-emerald-600 border-emerald-500/20'
                      : 'bg-amber-500/10 text-amber-600 border-amber-500/20'
                  )}
                >
                  {isScheduleActive ? 'AUTO' : 'PAUSED'}
                </span>
              )}
              {runtimeStatus && (
                <span
                  className={cn(
                    'inline-flex items-center gap-1 text-ui-micro px-1 py-0 rounded-sm font-mono uppercase border',
                    runtimeStatus.className
                  )}
                  title={runtimeStatus.title}
                >
                  {runtimeStatus.icon}
                  {runtimeStatus.label}
                </span>
              )}
              {isStale && (
                <span
                  className="inline-flex items-center gap-1 rounded-sm border border-red-500/20 bg-red-500/10 px-1 py-0 font-mono text-ui-micro uppercase text-red-500"
                  title={deployment?.staleReason || '运行中但长时间无活动'}
                >
                  <AlertTriangle className="h-2.5 w-2.5" />
                  疑似卡死
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            <div className="p-1 bg-emerald-500/10 rounded-md">
              <Timer
                size={10}
                className="text-emerald-600 dark:text-emerald-500"
              />
            </div>
            <div className="flex flex-col">
              <span className="text-ui-micro font-black text-slate-400/80 uppercase tracking-tighter leading-none">
                上次同步
              </span>
              <span className="text-ui-caption font-bold text-slate-600 dark:text-slate-300 leading-tight font-mono">
                {deployment?.lastRunTime
                  ? format(new Date(deployment.lastRunTime), 'MM-dd HH:mm')
                  : '--'}
              </span>
            </div>
          </div>

          <Separator
            orientation="vertical"
            className="h-5 bg-slate-200/60 dark:bg-white/10"
          />

          <div className="flex items-center gap-1.5">
            <div className="p-1 bg-blue-500/10 rounded-md">
              <Activity
                size={10}
                className="text-blue-600 dark:text-blue-500"
              />
            </div>
            <div className="flex flex-col">
              <span className="text-ui-micro font-black text-slate-400/80 uppercase tracking-tighter leading-none">
                下次同步
              </span>
              <span className="text-ui-caption font-bold text-slate-600 dark:text-slate-300 leading-tight font-mono">
                {deployment?.nextRunTime
                  ? format(new Date(deployment.nextRunTime), 'MM-dd HH:mm')
                  : '--'}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1">
          {onToggleSchedule && (
            <Button
              variant="ghost"
              size="sm"
              className={cn(
                'h-control-compact px-2.5 rounded-lg hover:bg-slate-100/80 dark:hover:bg-white/10 transition-all gap-1.5',
                !isScheduleActive && 'text-amber-600 dark:text-amber-400'
              )}
              disabled={!deployment || isScheduleUpdating}
              title={scheduleActionLabel}
              onClick={onToggleSchedule}
            >
              {isScheduleActive ? (
                <PauseCircle className="w-3.5 h-3.5 text-slate-500" />
              ) : (
                <PlayCircle className="w-3.5 h-3.5 text-amber-500" />
              )}
              <span className="font-bold text-ui-caption">
                {isScheduleUpdating ? '更新中...' : scheduleActionLabel}
              </span>
            </Button>
          )}

          <Button
            variant="ghost"
            size="sm"
            className="h-control-compact px-2.5 rounded-lg hover:bg-slate-100/80 dark:hover:bg-white/10 transition-all gap-1.5"
            onClick={onShowHistory}
          >
            <BarChart4 className="w-3.5 h-3.5 text-slate-500" />
            <span className="font-bold text-ui-caption text-slate-600 dark:text-slate-300">
              历史记录
            </span>
          </Button>

          <Button
            variant="default"
            size="sm"
            className={cn(
              'group h-control-compact px-3 rounded-lg shadow-sm active:scale-95 transition-all gap-1.5 text-white',
              syncButtonCancels
                ? 'w-[88px] bg-indigo-600 border border-indigo-500/50 shadow-indigo-500/20 hover:bg-red-600 hover:border-red-500/50 hover:shadow-red-500/25 focus-visible:bg-red-600 focus-visible:border-red-500/50'
                : 'bg-indigo-600 hover:bg-indigo-700 border border-indigo-500/50 shadow-indigo-500/20 hover:shadow-indigo-500/30'
            )}
            disabled={
              syncDisabled || isRunCancelling || (isSyncing && !canCancelRun)
            }
            title={syncButtonTitle}
            aria-label={
              syncButtonCancels
                ? isRunCancelling
                  ? '停止中'
                  : '停止当前运行'
                : syncLabel
            }
            onClick={handleSyncButtonClick}
          >
            {syncButtonCancels ? (
              <span className="relative block h-3.5 w-full">
                <span className="absolute inset-0 flex items-center justify-center gap-1.5 transition-opacity duration-150 group-hover:opacity-0 group-focus-visible:opacity-0">
                  <Activity className="!h-3 !w-3" />
                  <span className="whitespace-nowrap font-bold text-ui-caption leading-none">
                    {isRunCancelling ? '停止中...' : syncLabel}
                  </span>
                </span>
                <span className="absolute inset-0 flex items-center justify-center gap-1.5 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100">
                  <Square className="!h-3 !w-3" />
                  <span className="whitespace-nowrap font-bold text-ui-caption leading-none">
                    {isRunCancelling ? '停止中...' : '停止'}
                  </span>
                </span>
              </span>
            ) : (
              <>
                {isSyncing ? (
                  <Activity className="w-3 h-3" />
                ) : (
                  <RefreshCw className="w-3 h-3" />
                )}
                <span className="font-bold text-ui-caption">{syncLabel}</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  );
}

function getRuntimeStatus(status?: string | null) {
  switch (status) {
    case 'Running':
    case 'Pending':
    case 'Cancelling':
    case 'Scheduled':
    case 'Late':
      return {
        label: status === 'Running' ? '运行中' : status,
        title: status,
        className: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
        icon: <Activity className="h-2.5 w-2.5" />,
      };
    default:
      return null;
  }
}
