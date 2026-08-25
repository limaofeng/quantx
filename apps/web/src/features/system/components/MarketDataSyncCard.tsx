import { formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import {
  Activity,
  AlertCircle,
  CandlestickChart,
  ChevronRight,
  Clock,
  Database,
  Layers,
} from 'lucide-react';
import React, { useMemo } from 'react';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

export function MarketDataSyncCard() {
  const [, setLocation] = useLocation();
  const { deployment, isSyncing } = useDeploymentSync('daily-market-data-sync');

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
        accent: 'text-blue-600 dark:text-blue-400',
        bg: 'bg-gradient-to-br from-sky-50/70 to-blue-50/60 dark:from-sky-950/20 dark:to-blue-950/10',
        border: 'border-blue-300/40 dark:border-blue-600/20',
        iconBg: 'bg-blue-600/10',
        iconText: 'text-blue-700 dark:text-blue-400',
        shadow: 'hover:shadow-blue-600/10',
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
      accent: 'text-sky-700 dark:text-sky-400',
      bg: 'bg-gradient-to-br from-sky-50/70 to-cyan-50/60 dark:from-sky-950/20 dark:to-cyan-950/10',
      border: 'border-sky-300/40 dark:border-sky-600/20',
      iconBg: 'bg-sky-600/10',
      iconText: 'text-sky-700 dark:text-sky-400',
      shadow: 'hover:shadow-sky-600/10',
    };
  }, [isError, isStale, isSyncing]);

  return (
    <button
      type="button"
      className={cn(
        'group relative flex h-full min-h-[150px] cursor-pointer flex-col overflow-hidden rounded-panel border p-ui-section text-left shadow-sm transition-colors hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/60',
        theme.bg,
        theme.border,
        theme.shadow
      )}
      onClick={() => setLocation('/settings/data/market-data')}
    >
      <div className="relative z-10 flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <div
            className={cn(
              'rounded-panel p-2 ring-1 ring-inset ring-black/5 dark:ring-white/10',
              theme.iconBg,
              theme.iconText
            )}
          >
            <CandlestickChart
              className={cn('h-5 w-5', isSyncing && 'animate-pulse')}
            />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-ui-title font-bold text-slate-800 dark:text-slate-100">
              K线批量同步
            </h3>
            <p className="mt-0.5 text-ui-caption font-medium text-slate-500 dark:text-slate-400">
              daily-market-data-sync
            </p>
          </div>
        </div>

        <Badge
          variant="outline"
          className={cn(
            'shrink-0 gap-1 border-opacity-30 pr-2 text-ui-caption',
            isSyncing
              ? isStale
                ? 'border-red-500 bg-red-500/5 text-red-600'
                : 'border-blue-500 bg-blue-500/5 text-blue-600'
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

      <p className="relative z-10 mt-4 max-w-[360px] text-ui-label font-medium leading-relaxed text-slate-600 dark:text-slate-400">
        独立管理 A 股、ETF、指数的 1d / 1m / tick
        历史行情下载，支持指定日期范围和指定标的列表。
      </p>

      <div className="relative z-10 mt-auto flex flex-wrap gap-2 pt-4">
        <span className="inline-flex items-center gap-1.5 rounded border border-slate-200/60 bg-white/50 px-2 py-1 text-ui-caption font-bold text-slate-500 dark:border-white/5 dark:bg-black/20">
          <Layers className="h-3 w-3" />
          分片下载
        </span>
        <span className="inline-flex items-center gap-1.5 rounded border border-slate-200/60 bg-white/50 px-2 py-1 text-ui-caption font-bold text-slate-500 dark:border-white/5 dark:bg-black/20">
          <Database className="h-3 w-3" />
          K线缓存
        </span>
        <span className="inline-flex items-center gap-1.5 rounded border border-slate-200/60 bg-white/50 px-2 py-1 text-ui-caption font-bold text-slate-500 dark:border-white/5 dark:bg-black/20">
          <Clock className="h-3 w-3" />
          {deployment?.lastRunTime
            ? formatDistanceToNow(new Date(deployment.lastRunTime), {
                addSuffix: true,
                locale: zhCN,
              })
            : '15:05 自动'}
        </span>
      </div>

      <div
        className={cn(
          'relative z-10 mt-4 flex items-center gap-1 text-ui-caption font-black opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100',
          theme.accent
        )}
      >
        管理同步任务
        <ChevronRight className="h-3.5 w-3.5" />
      </div>
    </button>
  );
}
