import { formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import {
  ArrowLeftRight,
  Clock,
  ChevronRight,
  Activity,
  AlertCircle,
  TrendingUp,
  BarChart2,
} from 'lucide-react';
import React, { useMemo } from 'react';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

export function TransactionDataSyncCard() {
  const [, setLocation] = useLocation();

  // Use the hook for deployment status
  // Assuming a flow name 'batch_transaction_sync_flow' for now, can be adjusted later
  const { deployment, isSyncing } = useDeploymentSync(
    'batch_transaction_sync_flow'
  );

  // Mock Data
  const stats = {
    transactionsCount: 12580,
    volume: '28.5亿',
  };

  const isHealthy =
    deployment?.status === 'Ready' ||
    deployment?.status === 'Scheduled' ||
    !deployment; // Default to healthy if no deployment found for mock
  const isStale = deployment?.isStale || deployment?.status === 'Stale';
  const isError =
    deployment?.status === 'Failed' ||
    deployment?.status === 'Crashed' ||
    isStale;
  const isInternalOffline = false; // Assume online for now

  // Visual Config
  const theme = useMemo(() => {
    if (isInternalOffline)
      return {
        bg: 'bg-slate-50 dark:bg-slate-900/50',
        border: 'border-slate-200 dark:border-slate-800',
        iconBg: 'bg-slate-100 dark:bg-slate-800',
        iconText: 'text-slate-500',
        text: 'text-slate-600 dark:text-slate-400',
        hover: 'hover:bg-slate-100 dark:hover:bg-slate-800/50',
        accent: 'text-slate-500',
      };
    if (isStale)
      return {
        bg: 'bg-gradient-to-br from-red-50/50 to-rose-50/50 dark:from-red-900/10 dark:to-rose-900/10',
        border: 'border-red-200/40 dark:border-red-800/40',
        iconBg: 'bg-red-500/10',
        iconText: 'text-red-600 dark:text-red-400',
        text: 'text-slate-800 dark:text-slate-100',
        hover: 'hover:bg-red-50/80 dark:hover:bg-red-900/20',
        accent: 'text-red-600 dark:text-red-400',
      };
    if (isSyncing)
      return {
        bg: 'bg-gradient-to-br from-indigo-50/50 to-violet-50/50 dark:from-indigo-900/10 dark:to-violet-900/10',
        border: 'border-indigo-200/40 dark:border-indigo-800/40',
        iconBg: 'bg-indigo-500/10',
        iconText: 'text-indigo-600 dark:text-indigo-400',
        text: 'text-slate-800 dark:text-slate-100',
        hover: 'hover:bg-indigo-50/80 dark:hover:bg-indigo-900/20',
        accent: 'text-indigo-600 dark:text-indigo-400',
      };
    if (isError)
      return {
        bg: 'bg-gradient-to-br from-red-50/50 to-rose-50/50 dark:from-red-900/10 dark:to-rose-900/10',
        border: 'border-red-200/40 dark:border-red-800/40',
        iconBg: 'bg-red-500/10',
        iconText: 'text-red-600 dark:text-red-400',
        text: 'text-slate-800 dark:text-slate-100',
        hover: 'hover:bg-red-50/80 dark:hover:bg-red-900/20',
        accent: 'text-red-600 dark:text-red-400',
      };
    return {
      // Default (Healthy/Ready) - Indigo/Violet theme
      bg: 'bg-gradient-to-br from-indigo-50/50 to-purple-50/50 dark:from-indigo-900/10 dark:to-purple-900/10',
      border: 'border-indigo-200/40 dark:border-indigo-500/10',
      iconBg: 'bg-indigo-500/10',
      iconText: 'text-indigo-600 dark:text-indigo-500',
      text: 'text-slate-800 dark:text-slate-100',
      hover: 'hover:bg-indigo-50/80 dark:hover:bg-indigo-900/20',
      accent: 'text-indigo-600 dark:text-indigo-500',
    };
  }, [isInternalOffline, isStale, isSyncing, isError]);

  return (
    <div
      className={cn(
        'h-full flex flex-col p-ui-section rounded-panel border overflow-hidden relative group cursor-pointer transition-all shadow-sm hover:shadow-md hover:shadow-indigo-500/5',
        theme.bg,
        theme.border,
        theme.hover
      )}
      onClick={() => setLocation('/settings/data/transactions')}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4 z-10">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'p-2 rounded-panel ring-1 ring-inset ring-black/5 dark:ring-white/10',
              theme.iconBg,
              theme.iconText
            )}
          >
            <ArrowLeftRight
              className={cn('w-5 h-5', isSyncing && 'animate-pulse')}
            />
          </div>
          <div>
            <h3
              className={cn(
                'font-bold text-ui-title transition-colors',
                theme.text
              )}
            >
              交易数据
            </h3>
            <p className="text-ui-caption text-slate-500 dark:text-slate-400 font-medium mt-0.5">
              Transactions & Usage
            </p>
          </div>
        </div>

        {deployment ? (
          <Badge
            variant="outline"
            className={cn(
              'gap-1 flex items-center pr-2 border-opacity-20',
              isStale
                ? 'bg-red-500/5 text-red-600 border-red-500'
                : isHealthy
                  ? 'bg-emerald-500/5 text-emerald-600 border-emerald-500'
                  : isSyncing
                    ? 'bg-blue-500/5 text-blue-600 border-blue-500'
                    : isError
                      ? 'bg-red-500/5 text-red-600 border-red-500'
                      : 'bg-slate-500/5 text-slate-500 border-slate-500'
            )}
          >
            {isStale ? (
              <AlertCircle size={10} />
            ) : isSyncing ? (
              <Activity size={10} className="animate-spin" />
            ) : isHealthy ? (
              <Activity size={10} />
            ) : isError ? (
              <AlertCircle size={10} />
            ) : (
              <Activity size={10} className="opacity-50" />
            )}
            <span className="text-ui-caption">
              {isStale
                ? '运行卡住'
                : isSyncing
                  ? '同步中'
                  : isHealthy
                    ? '系统就绪'
                    : isError
                      ? '任务异常'
                      : deployment.status}
            </span>
          </Badge>
        ) : (
          <Badge
            variant="outline"
            className="bg-emerald-500/5 text-emerald-600 border-emerald-500/20"
          >
            <span className="text-ui-caption">就绪</span>
          </Badge>
        )}
      </div>

      {/* Main Stats Grid */}
      <div className="grid grid-cols-2 gap-ui-section z-10 mb-4">
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-white/60 dark:bg-black/20 border border-slate-200/50 dark:border-white/5">
          <div className="flex items-center gap-1.5 text-ui-caption text-slate-500 dark:text-slate-400 uppercase tracking-wider font-semibold">
            <TrendingUp className="w-3 h-3" />
            总交易数
          </div>
          <div className={cn('text-ui-display font-black', theme.text)}>
            {stats.transactionsCount.toLocaleString()}
            <span className="text-ui-caption sm:text-ui-label font-normal text-slate-400 ml-1">
              笔
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-white/60 dark:bg-black/20 border border-slate-200/50 dark:border-white/5">
          <div className="flex items-center gap-1.5 text-ui-caption text-slate-500 dark:text-slate-400 uppercase tracking-wider font-semibold">
            <BarChart2 className="w-3 h-3" />
            成交额
          </div>
          <div className={cn('text-ui-display font-black', theme.text)}>
            {stats.volume}
          </div>
        </div>
      </div>

      {/* Footer Info */}
      <div
        className={cn(
          'mt-auto flex items-center justify-between z-10 pt-3',
          theme.border
        )}
      >
        <div className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
          <Clock size={12} />
          <span className="text-ui-caption font-mono">
            {isStale
              ? deployment?.staleReason || '运行中但长时间无活动'
              : isSyncing
                ? '正在同步数据...'
                : isError
                  ? '上次同步失败'
                  : deployment?.lastRunTime
                    ? `上次同步: ${formatDistanceToNow(new Date(deployment.lastRunTime), { locale: zhCN, addSuffix: true })}`
                    : '自动同步'}
          </span>
        </div>

        <div
          className={cn(
            'flex items-center gap-1 text-ui-caption font-semibold opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0',
            theme.accent
          )}
        >
          查看详情
          <ChevronRight size={12} />
        </div>
      </div>

      {/* Decorative Background - Dynamic Color */}
      <div
        className={cn(
          'absolute -right-8 -bottom-8 w-32 h-32 rounded-full blur-3xl transition-all duration-500 opacity-10 group-hover:opacity-20',
          isStale
            ? 'bg-red-500'
            : isSyncing
              ? 'bg-indigo-500'
              : isError
                ? 'bg-red-500'
                : 'bg-purple-500'
        )}
      />
    </div>
  );
}
