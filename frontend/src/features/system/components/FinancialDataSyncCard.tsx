import { formatDistanceToNow } from 'date-fns';
import { zhCN } from 'date-fns/locale';
import {
  Banknote,
  FileText,
  Clock,
  ChevronRight,
  Activity,
  AlertCircle,
  PieChart,
} from 'lucide-react';
import React, { useMemo } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { gql } from '@/generated/gql';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

const FINANCIAL_SYNC_CARD_QUERY = gql(`
  query FinancialDataSyncCard {
    financialOverview {
      reportCount
      instrumentCount
    }
  }
`);

export function FinancialDataSyncCard() {
  const [, setLocation] = useLocation();

  // Use the hook for deployment status
  const { deployment, isSyncing } = useDeploymentSync('financial-sync');
  const [{ data }] = useQuery({ query: FINANCIAL_SYNC_CARD_QUERY });

  const stats = {
    reportsCount: data?.financialOverview.reportCount ?? 0,
    instrumentsCount: data?.financialOverview.instrumentCount ?? 0,
  };

  const isHealthy =
    deployment?.status === 'Ready' ||
    deployment?.status === 'Scheduled' ||
    !deployment;
  const isError =
    deployment?.status === 'Failed' || deployment?.status === 'Crashed';
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
    if (isSyncing)
      return {
        bg: 'bg-gradient-to-br from-blue-50/50 to-teal-50/50 dark:from-blue-950/10 dark:to-teal-900/10',
        border: 'border-blue-300/40 dark:border-blue-900/40',
        iconBg: 'bg-blue-600/10',
        iconText: 'text-blue-700 dark:text-blue-500',
        text: 'text-slate-800 dark:text-slate-100',
        hover: 'hover:bg-blue-50/80 dark:hover:bg-blue-950/20',
        accent: 'text-blue-700 dark:text-blue-500',
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
      // Default (Healthy/Ready) - Cyan/Teal theme
      bg: 'bg-gradient-to-br from-blue-50/50 to-emerald-50/50 dark:from-blue-950/10 dark:to-emerald-900/10',
      border: 'border-blue-300/40 dark:border-blue-600/10',
      iconBg: 'bg-blue-600/10',
      iconText: 'text-blue-700 dark:text-blue-600',
      text: 'text-slate-800 dark:text-slate-100',
      hover: 'hover:bg-blue-50/80 dark:hover:bg-blue-950/20',
      accent: 'text-blue-700 dark:text-blue-600',
    };
  }, [isInternalOffline, isSyncing, isError]);

  return (
    <div
      className={cn(
        'h-full flex flex-col p-5 rounded-xl border overflow-hidden relative group cursor-pointer transition-all shadow-sm hover:shadow-md hover:shadow-blue-600/5',
        theme.bg,
        theme.border,
        theme.hover
      )}
      onClick={() => setLocation('/settings/data/financial')}
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
            <Banknote className={cn('w-5 h-5', isSyncing && 'animate-pulse')} />
          </div>
          <div>
            <h3
              className={cn(
                'font-bold text-base transition-colors',
                theme.text
              )}
            >
              财务数据
            </h3>
            <p className="text-[10px] text-slate-500 dark:text-slate-400 font-medium mt-0.5">
              Financial Reports
            </p>
          </div>
        </div>

        {deployment ? (
          <Badge
            variant="outline"
            className={cn(
              'gap-1 flex items-center pr-2 border-opacity-20',
              isHealthy
                ? 'bg-emerald-500/5 text-emerald-600 border-emerald-500'
                : isSyncing
                  ? 'bg-blue-500/5 text-blue-600 border-blue-500'
                  : isError
                    ? 'bg-red-500/5 text-red-600 border-red-500'
                    : 'bg-slate-500/5 text-slate-500 border-slate-500'
            )}
          >
            {isSyncing ? (
              <Activity size={10} className="animate-spin" />
            ) : isHealthy ? (
              <Activity size={10} />
            ) : isError ? (
              <AlertCircle size={10} />
            ) : (
              <Activity size={10} className="opacity-50" />
            )}
            <span className="text-[10px]">
              {isSyncing
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
            <span className="text-[10px]">就绪</span>
          </Badge>
        )}
      </div>

      {/* Main Stats Grid */}
      <div className="grid grid-cols-2 gap-4 z-10 mb-4">
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-white/60 dark:bg-black/20 border border-slate-200/50 dark:border-white/5">
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 uppercase tracking-wider font-semibold">
            <FileText className="w-3 h-3" />
            已入库报表
          </div>
          <div className={cn('text-2xl font-black', theme.text)}>
            {stats.reportsCount.toLocaleString()}
            <span className="text-[10px] sm:text-xs font-normal text-slate-400 ml-1">
              份
            </span>
          </div>
        </div>
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-white/60 dark:bg-black/20 border border-slate-200/50 dark:border-white/5">
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 uppercase tracking-wider font-semibold">
            <PieChart className="w-3 h-3" />
            覆盖标的
          </div>
          <div className={cn('text-2xl font-black', theme.text)}>
            {stats.instrumentsCount.toLocaleString()}
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
            {isSyncing
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
            'flex items-center gap-1 text-[10px] font-semibold opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0',
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
          isSyncing ? 'bg-blue-600' : isError ? 'bg-red-500' : 'bg-emerald-500'
        )}
      />
    </div>
  );
}
