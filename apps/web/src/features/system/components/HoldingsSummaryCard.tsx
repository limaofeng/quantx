import { Briefcase, Clock, ChevronRight } from 'lucide-react';
import React from 'react';
import { useLocation } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';

interface HoldingsSummaryCardProps {
  holdings: Array<{
    status: string;
    completeness: number;
  }>;
}

export function HoldingsSummaryCard({
  holdings = [],
}: HoldingsSummaryCardProps) {
  const [, setLocation] = useLocation();
  const safeHoldings = Array.isArray(holdings) ? holdings : [];
  const total = safeHoldings.length;
  const synced = safeHoldings.filter(
    h => h?.status === 'success' || h?.completeness > 90
  ).length;
  const syncRate = Math.round((synced / total) * 100) || 0;

  return (
    <div
      className="h-full flex flex-col p-ui-section rounded-panel border border-slate-200/40 dark:border-slate-800/40 bg-gradient-to-br from-purple-50/50 to-pink-50/50 dark:from-purple-900/10 dark:to-pink-900/10 overflow-hidden relative group cursor-pointer hover:bg-purple-50/80 dark:hover:bg-purple-900/20 transition-all shadow-sm hover:shadow-md"
      onClick={() => setLocation('/settings/data/holdings')}
    >
      <div className="flex items-start justify-between mb-4 z-10">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-panel bg-purple-500/10 text-purple-600 dark:text-purple-400 ring-1 ring-inset ring-black/5 dark:ring-white/10">
            <Briefcase className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-bold text-ui-title text-slate-800 dark:text-slate-100">
              持仓数据监控
            </h3>
            <p className="text-ui-caption text-slate-500 dark:text-slate-400 font-medium mt-0.5">
              Holdings Sync
            </p>
          </div>
        </div>

        <Badge
          variant="outline"
          className="bg-purple-500/5 text-purple-600 border-purple-500 gap-1 flex items-center border-opacity-20"
        >
          <div className="w-1.5 h-1.5 rounded-full bg-purple-500 animate-pulse" />
          <span className="text-ui-caption">{syncRate}% 就绪</span>
        </Badge>
      </div>

      <div className="flex-1 flex flex-col justify-end z-10">
        <div className="flex items-baseline justify-between mb-4">
          <div className="flex flex-col">
            <span className="text-ui-display-lg font-black text-slate-800 dark:text-slate-100 line-height-1">
              {total}
            </span>
            <span className="text-ui-caption font-bold text-slate-500 uppercase tracking-wider">
              支监控标的
            </span>
          </div>

          <div className="flex flex-col items-end">
            <div className="text-ui-caption text-slate-500 mb-1 font-semibold uppercase tracking-tight">
              同步进度
            </div>
            <div className="flex items-center gap-2">
              <Progress
                value={syncRate}
                className="h-1.5 w-16 bg-purple-100 dark:bg-purple-950"
              />
              <span className="text-ui-caption font-mono text-slate-400">
                {synced}/{total}
              </span>
            </div>
          </div>
        </div>

        <div className="pt-3 border-t border-purple-200/50 dark:border-purple-800/50 flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
            <Clock size={12} />
            <span className="text-ui-caption font-mono whitespace-nowrap">
              最近同步: 10 mins ago
            </span>
          </div>
          <div className="flex items-center gap-1 text-ui-caption font-semibold text-purple-600 dark:text-purple-400 opacity-0 group-hover:opacity-100 transition-all -translate-x-2 group-hover:translate-x-0">
            管理持仓
            <ChevronRight size={12} />
          </div>
        </div>
      </div>

      {/* Decorative Background */}
      <div className="absolute -right-8 -bottom-8 w-32 h-32 bg-purple-500/10 rounded-full blur-3xl group-hover:bg-purple-500/20 transition-all duration-500 opacity-20" />
    </div>
  );
}
