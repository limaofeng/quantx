import { LayoutGrid, Activity, PieChart, Layers } from 'lucide-react';
import React from 'react';

import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/utils/cn';

interface SectorStatsCardsProps {
  totalCount: number;
  statsCounts: Record<string, number>;
}

export function SectorStatsCards({
  totalCount,
  statsCounts,
}: SectorStatsCardsProps) {
  const stats = [
    {
      label: 'All Sectors',
      value: totalCount,
      icon: LayoutGrid,
      color: 'text-indigo-600 dark:text-indigo-400',
      bg: 'bg-indigo-500/10',
      gradient:
        'from-indigo-50/50 via-white to-white dark:from-indigo-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
    },
    {
      label: 'Shenwan (SW)',
      value:
        (statsCounts['SW1'] || 0) +
        (statsCounts['SW2'] || 0) +
        (statsCounts['SW3'] || 0),
      icon: Layers,
      color: 'text-blue-600 dark:text-blue-400',
      bg: 'bg-blue-500/10',
      gradient:
        'from-blue-50/50 via-white to-white dark:from-blue-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
    },
    {
      label: 'Thematic (GN)',
      value: (statsCounts['GN'] || 0) + (statsCounts['TGN'] || 0),
      icon: PieChart,
      color: 'text-emerald-600 dark:text-emerald-400',
      bg: 'bg-emerald-500/10',
      gradient:
        'from-emerald-50/50 via-white to-white dark:from-emerald-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
    },
    {
      label: 'Regulatory (CSRC)',
      value: (statsCounts['CSRC'] || 0) + (statsCounts['CSRC1'] || 0),
      icon: Activity,
      color: 'text-rose-600 dark:text-rose-400',
      bg: 'bg-rose-500/10',
      gradient:
        'from-rose-50/50 via-white to-white dark:from-rose-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 shrink-0">
      {stats.map(stat => (
        <Card
          key={stat.label}
          className={cn(
            'relative overflow-hidden border-slate-200/60 dark:border-white/5 shadow-sm transition-all duration-300 hover:shadow-md bg-gradient-to-br',
            stat.gradient
          )}
        >
          <div className="absolute top-0 right-0 p-2 opacity-5 pointer-events-none">
            <stat.icon className={cn('w-16 h-16 rotate-12', stat.color)} />
          </div>

          <CardContent className="p-3">
            <div className="flex items-center gap-2 mb-2">
              <div className={cn('p-1 rounded-md', stat.bg)}>
                <stat.icon className={cn('w-3.5 h-3.5', stat.color)} />
              </div>
              <span className="text-[10px] font-black text-slate-400 dark:text-slate-500 uppercase tracking-wider">
                {stat.label}
              </span>
            </div>

            <div className="flex items-baseline gap-1 relative z-10">
              <span
                className={cn(
                  'text-2xl font-black tracking-tighter tabular-nums',
                  stat.color
                )}
              >
                {stat.value.toLocaleString()}
              </span>
              <span className="text-[10px] font-bold text-slate-400">
                items
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
