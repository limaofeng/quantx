import { Layers, Activity, PieChart, TrendingUp } from 'lucide-react';
import React from 'react';

import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/utils/cn';

interface HoldingsStatsCardsProps {
  totalCount: number;
  syncHealth: number;
  sectorCount: number;
}

export function HoldingsStatsCards({
  totalCount,
  syncHealth,
  sectorCount,
}: HoldingsStatsCardsProps) {
  const stats = [
    {
      label: '全部持仓',
      value: totalCount,
      detail: '活跃监控中',
      icon: Layers,
      color: 'text-indigo-600 dark:text-indigo-400',
      bg: 'bg-indigo-500/10',
      gradient:
        'from-indigo-50/50 via-white to-white dark:from-indigo-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
    },
    {
      label: '数据健康度',
      value: `${syncHealth}%`,
      detail: '实时状态良好',
      icon: Activity,
      color: 'text-emerald-600 dark:text-emerald-400',
      bg: 'bg-emerald-500/10',
      gradient:
        'from-emerald-50/50 via-white to-white dark:from-emerald-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
    },
    {
      label: '板块覆盖',
      value: sectorCount,
      detail: '行业分布',
      icon: PieChart,
      color: 'text-orange-600 dark:text-orange-400',
      bg: 'bg-orange-500/10',
      gradient:
        'from-orange-50/50 via-white to-white dark:from-orange-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {stats.map((stat, i) => (
        <Card
          key={i}
          className={cn(
            'relative overflow-hidden border-slate-200/60 dark:border-white/5 shadow-sm bg-gradient-to-br transition-all hover:shadow-md',
            stat.gradient
          )}
        >
          <div className="absolute top-0 right-0 p-2 opacity-5 pointer-events-none">
            <stat.icon className={cn('w-24 h-24 rotate-12', stat.color)} />
          </div>
          <CardContent className="p-4 flex items-center justify-between relative z-10">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <div className={cn('p-1 rounded-md', stat.bg)}>
                  <stat.icon className={cn('w-3.5 h-3.5', stat.color)} />
                </div>
                <span className="text-[10px] font-black text-slate-400 dark:text-slate-500 uppercase tracking-widest">
                  {stat.label}
                </span>
              </div>
              <div className="flex items-baseline gap-2 mt-1">
                <span
                  className={cn(
                    'text-3xl font-black tracking-tighter tabular-nums',
                    stat.color
                  )}
                >
                  {stat.value}
                </span>
              </div>
            </div>
            <div className="text-right">
              <span className="text-[10px] font-bold text-slate-400 dark:text-slate-500 bg-white/50 dark:bg-black/10 px-2 py-1 rounded-full backdrop-blur-sm">
                {stat.detail}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
