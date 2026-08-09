import {
  Calendar,
  Clock,
  ChevronRight,
  Activity,
  AlertCircle,
} from 'lucide-react';
import React from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { gql } from '@/generated/gql';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

const GET_HOLIDAYS_COUNT = gql(`
  query GetHolidaysCount($market: String!, $year: Int!) {
    holidays(market: $market, year: $year) {
      total
    }
  }
`);

export function TradingCalendarCard() {
  const [, setLocation] = useLocation();
  const currentYear = new Date().getFullYear();

  // Fetch Holidays Count
  const [{ data: holidaysData }] = useQuery({
    query: GET_HOLIDAYS_COUNT,
    variables: { market: 'SH', year: currentYear },
  });

  const { deployment, isSyncing } = useDeploymentSync('holiday-sync', {
    successMessage: '交易日历同步已启动',
  });

  const isStale = deployment?.isStale || deployment?.status === 'Stale';
  const isError =
    deployment?.status === 'Failed' ||
    deployment?.status === 'Crashed' ||
    isStale;
  const holidayCount = holidaysData?.holidays?.total ?? 0;

  return (
    <div
      className="h-full flex flex-col p-5 rounded-xl border border-slate-200/40 dark:border-slate-800/40 bg-gradient-to-br from-rose-50/50 to-orange-50/50 dark:from-rose-900/10 dark:to-orange-900/10 overflow-hidden relative group cursor-pointer transition-all hover:bg-rose-50/80 dark:hover:bg-rose-900/20 shadow-sm hover:shadow-md"
      onClick={e => {
        if ((e.target as HTMLElement).closest('button')) return;
        setLocation('/settings/data/calendar');
      }}
    >
      {/* Header */}
      <div className="flex items-start justify-between mb-4 z-10">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-rose-500/10 text-rose-600 dark:text-rose-400 ring-1 ring-inset ring-black/5 dark:ring-white/10">
            <Calendar className={cn('w-5 h-5', isSyncing && 'animate-pulse')} />
          </div>
          <div>
            <h3 className="font-bold text-base text-slate-800 dark:text-slate-100">
              交易日历
            </h3>
            <p className="text-[10px] text-slate-500 dark:text-slate-400 font-medium mt-0.5">
              Trading Calendar
            </p>
          </div>
        </div>

        <Badge
          variant="outline"
          className={cn(
            'gap-1 flex items-center pr-2 border-opacity-20',
            isStale
              ? 'bg-red-500/5 text-red-600 border-red-500'
              : isSyncing
                ? 'bg-blue-500/5 text-blue-600 border-blue-500'
                : isError
                  ? 'bg-red-500/5 text-red-600 border-red-500'
                  : 'bg-emerald-500/5 text-emerald-600 border-emerald-500'
          )}
        >
          {isStale ? (
            <AlertCircle size={10} />
          ) : isSyncing ? (
            <Activity size={10} className="animate-spin" />
          ) : isError ? (
            <AlertCircle size={10} />
          ) : (
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
          )}
          <span className="text-[10px]">
            {isStale
              ? '运行卡住'
              : isSyncing
                ? '同步中'
                : isError
                  ? '任务异常'
                  : '系统就绪'}
          </span>
        </Badge>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 gap-4 z-10 mb-4">
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-white/60 dark:bg-black/20 border border-slate-200/50 dark:border-white/5">
          <div className="flex items-center gap-1.5 text-[10px] text-slate-500 dark:text-slate-400 uppercase tracking-wider font-semibold">
            <Calendar className="w-3 h-3" />
            {currentYear}年度休市天数
          </div>
          <div className="text-2xl font-black text-rose-600 dark:text-rose-400">
            {holidayCount}
            <span className="text-[10px] sm:text-xs font-normal text-slate-400 ml-1">
              天
            </span>
          </div>
        </div>
      </div>

      {/* Footer Info */}
      <div className="mt-3 flex items-center justify-between z-10">
        <div className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
          <Clock size={12} />
          <span className="text-[10px] font-mono whitespace-nowrap">
            {deployment?.lastRunTime
              ? `上次同步: ${new Date(deployment.lastRunTime).toLocaleDateString()}`
              : '暂无记录'}
          </span>
        </div>
        <div className="flex items-center gap-1 text-[10px] font-semibold text-rose-600 dark:text-rose-400 opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0">
          管理日历
          <ChevronRight size={12} />
        </div>
      </div>

      {/* Decorative Background */}
      <div className="absolute -right-8 -bottom-8 w-32 h-32 bg-rose-500/10 rounded-full blur-3xl group-hover:bg-rose-500/20 transition-all duration-500 opacity-20" />
    </div>
  );
}
