import { formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import {
  Activity,
  AlertCircle,
  ChevronRight,
  Clock,
  FileSearch,
  Megaphone,
  RefreshCw,
} from 'lucide-react';
import React, { useMemo } from 'react';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

export function AnnouncementSyncCard() {
  const [, setLocation] = useLocation();
  const { deployment, isSyncing } = useDeploymentSync('announcement-sync');

  const isHealthy =
    deployment?.status === 'Ready' ||
    deployment?.status === 'Scheduled' ||
    !deployment?.status;
  const isStale = deployment?.isStale || deployment?.status === 'Stale';
  const isError =
    deployment?.status === 'Failed' ||
    deployment?.status === 'Crashed' ||
    isStale;

  const theme = useMemo(() => {
    if (isStale) {
      return {
        accent: 'text-red-600 dark:text-red-400',
        bg: 'bg-gradient-to-br from-red-50/70 to-rose-50/60 dark:from-red-950/20 dark:to-rose-950/10',
        border: 'border-red-300/40 dark:border-red-600/20',
        iconBg: 'bg-red-600/10',
        iconText: 'text-red-700 dark:text-red-400',
        shadow: 'hover:shadow-red-600/10',
      };
    }

    if (isSyncing) {
      return {
        accent: 'text-violet-600 dark:text-violet-400',
        bg: 'bg-gradient-to-br from-violet-50/70 to-fuchsia-50/60 dark:from-violet-950/20 dark:to-fuchsia-950/10',
        border: 'border-violet-300/40 dark:border-violet-600/20',
        iconBg: 'bg-violet-600/10',
        iconText: 'text-violet-700 dark:text-violet-400',
        shadow: 'hover:shadow-violet-600/10',
      };
    }

    if (isError) {
      return {
        accent: 'text-red-600 dark:text-red-400',
        bg: 'bg-gradient-to-br from-red-50/70 to-rose-50/60 dark:from-red-950/20 dark:to-rose-950/10',
        border: 'border-red-300/40 dark:border-red-600/20',
        iconBg: 'bg-red-600/10',
        iconText: 'text-red-700 dark:text-red-400',
        shadow: 'hover:shadow-red-600/10',
      };
    }

    return {
      accent: 'text-violet-700 dark:text-violet-400',
      bg: 'bg-gradient-to-br from-violet-50/70 to-slate-50/70 dark:from-violet-950/20 dark:to-slate-950/10',
      border: 'border-violet-300/40 dark:border-violet-600/20',
      iconBg: 'bg-violet-600/10',
      iconText: 'text-violet-700 dark:text-violet-400',
      shadow: 'hover:shadow-violet-600/10',
    };
  }, [isError, isStale, isSyncing]);

  return (
    <button
      type="button"
      className={cn(
        'group relative flex h-full min-h-[150px] cursor-pointer flex-col overflow-hidden rounded-xl border p-5 text-left shadow-sm transition-colors hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/60',
        theme.bg,
        theme.border,
        theme.shadow
      )}
      onClick={() => setLocation('/settings/data/announcements')}
    >
      <div className="relative z-10 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div
            className={cn(
              'rounded-xl p-2 ring-1 ring-inset ring-black/5 dark:ring-white/10',
              theme.iconBg,
              theme.iconText
            )}
          >
            <Megaphone
              className={cn('h-5 w-5', isSyncing && 'animate-pulse')}
            />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-base font-bold text-slate-800 dark:text-slate-100">
              公告与回购同步
            </h3>
            <p className="mt-0.5 text-[10px] font-medium text-slate-500 dark:text-slate-400">
              announcement-sync
            </p>
          </div>
        </div>

        <Badge
          variant="outline"
          className={cn(
            'shrink-0 gap-1 border-opacity-30 pr-2 text-[10px]',
            isSyncing
              ? isStale
                ? 'border-red-500 bg-red-500/5 text-red-600'
                : 'border-violet-500 bg-violet-500/5 text-violet-600'
              : isError
                ? 'border-red-500 bg-red-500/5 text-red-600'
                : 'border-emerald-500 bg-emerald-500/5 text-emerald-600'
          )}
        >
          {isStale ? (
            <AlertCircle className="h-2.5 w-2.5" />
          ) : isSyncing ? (
            <Activity className="h-2.5 w-2.5 animate-spin" />
          ) : isError ? (
            <AlertCircle className="h-2.5 w-2.5" />
          ) : (
            <Activity className="h-2.5 w-2.5" />
          )}
          {isStale
            ? '运行卡住'
            : isSyncing
              ? '运行中'
              : isHealthy
                ? '就绪'
                : deployment?.status}
        </Badge>
      </div>

      <p className="relative z-10 mt-4 max-w-[360px] text-xs font-medium leading-relaxed text-slate-600 dark:text-slate-400">
        同步持仓股和自选股的上市公司公告、回购事件，也支持指定标的强制刷新。
      </p>

      <div className="relative z-10 mt-auto flex flex-wrap gap-2 pt-4">
        <span className="inline-flex items-center gap-1.5 rounded border border-slate-200/60 bg-white/50 px-2 py-1 text-[10px] font-bold text-slate-500 dark:border-white/5 dark:bg-black/20">
          <FileSearch className="h-3 w-3" />
          公告解析
        </span>
        <span className="inline-flex items-center gap-1.5 rounded border border-slate-200/60 bg-white/50 px-2 py-1 text-[10px] font-bold text-slate-500 dark:border-white/5 dark:bg-black/20">
          <RefreshCw className="h-3 w-3" />
          强制刷新
        </span>
        <span className="inline-flex items-center gap-1.5 rounded border border-slate-200/60 bg-white/50 px-2 py-1 text-[10px] font-bold text-slate-500 dark:border-white/5 dark:bg-black/20">
          <Clock className="h-3 w-3" />
          {deployment?.lastRunTime
            ? formatDistanceToNow(new Date(deployment.lastRunTime), {
                addSuffix: true,
                locale: zhCN,
              })
            : '15:45 自动'}
        </span>
      </div>

      <div
        className={cn(
          'relative z-10 mt-4 flex items-center gap-1 text-[10px] font-black opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100',
          theme.accent
        )}
      >
        管理同步任务
        <ChevronRight className="h-3.5 w-3.5" />
      </div>
    </button>
  );
}
