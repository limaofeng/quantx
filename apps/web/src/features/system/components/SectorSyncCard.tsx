import { formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import {
  Layers,
  Factory,
  Zap,
  Clock,
  ChevronRight,
  Activity,
  AlertCircle,
} from 'lucide-react';
import React, { useMemo } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { gql } from '@/generated/gql';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn'; // Assuming cn utility is available here

const GET_SECTOR_STATS = gql(`
  query GetSectorStatsCard {
    sectorStats {
      classification
      count
    }
  }
`);

export function SectorSyncCard() {
  const [, setLocation] = useLocation();

  // Fetch Stats
  const [{ data: statsData }] = useQuery({ query: GET_SECTOR_STATS });

  const { deployment, isSyncing } = useDeploymentSync('sector-data-sync');
  const isHealthy =
    deployment?.status === 'Ready' || deployment?.status === 'Scheduled';

  // Process Stats
  const counts = useMemo(() => {
    const c: Record<string, number> = { SW1: 0, GN: 0 };
    statsData?.sectorStats?.forEach(s => {
      if (s.classification === 'SW1' || s.classification === 'GN') {
        c[s.classification] = s.count;
      }
    });
    return c;
  }, [statsData]);

  // Determine State
  const isInternalOffline = !deployment;
  const isStale = deployment?.isStale || deployment?.status === 'Stale';
  const isError =
    deployment?.status === 'Failed' ||
    deployment?.status === 'Crashed' ||
    isStale;
  const isEmpty = counts.SW1 === 0 && counts.GN === 0;

  // Visual Config based on State
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
        bg: 'bg-gradient-to-br from-blue-50/50 to-indigo-50/50 dark:from-blue-900/10 dark:to-indigo-900/10',
        border: 'border-blue-200/40 dark:border-blue-800/40',
        iconBg: 'bg-blue-500/10',
        iconText: 'text-blue-600 dark:text-blue-400',
        text: 'text-slate-800 dark:text-slate-100',
        hover: 'hover:bg-blue-50/80 dark:hover:bg-blue-900/20',
        accent: 'text-blue-600 dark:text-blue-400',
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
      // Default (Healthy/Ready)
      bg: 'bg-gradient-to-br from-amber-50/50 to-orange-50/50 dark:from-amber-900/10 dark:to-orange-900/10',
      border: 'border-amber-200/40 dark:border-amber-500/10',
      iconBg: 'bg-amber-500/10',
      iconText: 'text-amber-600 dark:text-amber-500',
      text: 'text-slate-800 dark:text-slate-100',
      hover: 'hover:bg-amber-50/80 dark:hover:bg-amber-900/20',
      accent: 'text-amber-600 dark:text-amber-500',
    };
  }, [isInternalOffline, isStale, isSyncing, isError]);

  return (
    <div
      className={cn(
        'h-full flex flex-col p-5 rounded-xl border overflow-hidden relative group cursor-pointer transition-all shadow-sm hover:shadow-md hover:shadow-slate-500/5',
        theme.bg,
        theme.border,
        theme.hover
      )}
      onClick={() => setLocation('/settings/data/sectors')}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4 z-10">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'p-2 rounded-xl ring-1 ring-inset ring-black/5 dark:ring-white/10',
              theme.iconBg,
              theme.iconText
            )}
          >
            <Layers className={cn('w-5 h-5', isSyncing && 'animate-pulse')} />
          </div>
          <div>
            <h3
              className={cn(
                'font-bold text-base transition-colors',
                theme.text
              )}
            >
              板块数据
            </h3>
            <p className="text-[10px] text-slate-500 dark:text-slate-400 font-medium mt-0.5">
              Sector Management
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
            <span className="text-[10px]">
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
            className="bg-slate-500/5 text-slate-500 border-slate-500/20"
          >
            <span className="text-[10px]">离线</span>
          </Badge>
        )}
      </div>

      {/* Main Stats Grid */}
      <div className="grid grid-cols-2 gap-4 z-10 mb-4">
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-white/60 dark:bg-black/20 border border-slate-200/50 dark:border-white/5">
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 uppercase tracking-wider font-semibold">
            <Factory className="w-3 h-3" />
            行业板块
          </div>
          <div className={cn('text-2xl font-black', theme.text)}>
            {isEmpty ? (
              <span className="text-sm text-slate-400 font-normal">
                未初始化
              </span>
            ) : (
              counts.SW1 || '-'
            )}
            {!isEmpty && (
              <span className="text-[10px] sm:text-xs font-normal text-slate-400 ml-1">
                个
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-white/60 dark:bg-black/20 border border-slate-200/50 dark:border-white/5">
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 uppercase tracking-wider font-semibold">
            <Zap className="w-3 h-3" />
            概念板块
          </div>
          <div className={cn('text-2xl font-black', theme.text)}>
            {isEmpty ? (
              <span className="text-sm text-slate-400 font-normal">
                未初始化
              </span>
            ) : (
              counts.GN || '-'
            )}
            {!isEmpty && (
              <span className="text-[10px] sm:text-xs font-normal text-slate-400 ml-1">
                个
              </span>
            )}
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
          <span className="text-[10px] font-mono">
            {isStale
              ? deployment?.staleReason || '运行中但长时间无活动'
              : isSyncing
                ? '正在同步数据...'
                : isError
                  ? '上次同步失败'
                  : deployment?.lastRunTime
                    ? `上次同步: ${formatDistanceToNow(new Date(deployment.lastRunTime), { locale: zhCN, addSuffix: true })}`
                    : '暂无同步记录'}
          </span>
        </div>

        <div
          className={cn(
            'flex items-center gap-1 text-[10px] font-semibold opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0',
            theme.accent
          )}
        >
          {isEmpty ? '去初始化' : '查看详情'}
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
              ? 'bg-blue-500'
              : isError
                ? 'bg-red-500'
                : isInternalOffline
                  ? 'bg-slate-500'
                  : 'bg-amber-500'
        )}
      />
    </div>
  );
}
