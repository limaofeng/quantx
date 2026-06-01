import { format } from 'date-fns';
import { Activity, Timer, RefreshCw, BarChart4 } from 'lucide-react';
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
}

interface SyncControlPanelProps {
  deployment: DeploymentStatus | undefined;
  isSyncing: boolean;
  defaultFlowName?: string;
  onShowHistory: () => void;
  onSync: () => void;
}

export function SyncControlPanel({
  deployment,
  isSyncing,
  defaultFlowName = '数据同步',
  onShowHistory,
  onSync,
}: SyncControlPanelProps) {
  return (
    <div className="flex flex-col md:flex-row items-stretch md:items-center gap-2">
      <div className="flex items-center gap-2 bg-white/80 dark:bg-slate-900/40 backdrop-blur-sm p-1 rounded-xl border border-slate-200/60 dark:border-slate-700/60 shadow-sm">
        {/* Compact Sync Info */}
        <div className="hidden lg:flex items-center gap-3 px-3 py-0.5">
          <div className="flex flex-col justify-center border-r border-slate-200/60 dark:border-white/10 pr-3">
            <span className="text-[9px] font-black text-slate-400 uppercase tracking-widest leading-none mb-0.5">
              任务来源
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className="text-xs font-bold text-slate-800 dark:text-slate-200 truncate max-w-[100px]"
                title={deployment?.description || undefined}
              >
                {deployment?.flowName || defaultFlowName}
              </span>
              {deployment?.workPoolName && (
                <span className="text-[8px] px-1 py-0 bg-slate-100 dark:bg-white/5 text-slate-500 rounded-sm font-mono uppercase border border-slate-200/50">
                  {deployment.workPoolName}
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
              <span className="text-[8px] font-black text-slate-400/80 uppercase tracking-tighter leading-none">
                上次同步
              </span>
              <span className="text-[10px] font-bold text-slate-600 dark:text-slate-300 leading-tight font-mono">
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
              <span className="text-[8px] font-black text-slate-400/80 uppercase tracking-tighter leading-none">
                下次同步
              </span>
              <span className="text-[10px] font-bold text-slate-600 dark:text-slate-300 leading-tight font-mono">
                {deployment?.nextRunTime
                  ? format(new Date(deployment.nextRunTime), 'MM-dd HH:mm')
                  : '--'}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2.5 rounded-lg hover:bg-slate-100/80 dark:hover:bg-white/10 transition-all gap-1.5"
            onClick={onShowHistory}
          >
            <BarChart4 className="w-3.5 h-3.5 text-slate-500" />
            <span className="font-bold text-[10px] text-slate-600 dark:text-slate-300">
              历史记录
            </span>
          </Button>

          <Button
            variant="default"
            size="sm"
            className="h-7 px-3 rounded-lg shadow-sm shadow-indigo-500/20 hover:shadow-indigo-500/30 active:scale-95 transition-all gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white border border-indigo-500/50"
            disabled={isSyncing}
            onClick={onSync}
          >
            <RefreshCw className={cn('w-3 h-3', isSyncing && 'animate-spin')} />
            <span className="font-bold text-[10px]">
              {isSyncing ? '同步中...' : '同步'}
            </span>
          </Button>
        </div>
      </div>
    </div>
  );
}
