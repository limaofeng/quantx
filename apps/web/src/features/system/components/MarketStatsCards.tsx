import { TrendingUp, Database, BarChart4, Activity } from 'lucide-react';
import React from 'react';

import { Card, CardContent } from '@/components/ui/card';

interface MarketStatsCardsProps {
  stats?: {
    totalStocks: number;
    dataCoverage: number;
    marketVolume: number;
    latency: number;
  };
}

export function MarketStatsCards({ stats }: MarketStatsCardsProps) {
  const dynamicStats = [
    {
      label: 'Total Stocks',
      value: stats?.totalStocks.toLocaleString() || '5,324',
      change: '+12',
      icon: TrendingUp,
      color: 'text-emerald-500',
    },
    {
      label: 'Data Coverage',
      value: stats ? `${stats.dataCoverage}%` : '98.5%',
      change: 'Optimal',
      icon: Database,
      color: 'text-blue-500',
    },
    {
      label: 'Market Volume',
      value: stats ? `${stats.marketVolume}B` : '1.2T',
      change: 'Daily',
      icon: BarChart4,
      color: 'text-indigo-500',
    },
    {
      label: 'System Latency',
      value: stats ? `${stats.latency}ms` : '24ms',
      change: 'Good',
      icon: Activity,
      color: 'text-emerald-500',
    },
  ];

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 shrink-0">
      {dynamicStats.map((stat, i) => (
        <Card
          key={i}
          className="border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-slate-900/40 backdrop-blur-sm hover:shadow-md hover:shadow-slate-200/50 dark:hover:shadow-black/50 transition-all rounded-panel"
        >
          <CardContent className="p-3">
            <div className="flex items-center gap-2 mb-2">
              <div
                className={`p-1 rounded-md bg-slate-50 dark:bg-white/5 ${stat.color}`}
              >
                <stat.icon className="w-3.5 h-3.5" />
              </div>
              <p className="text-ui-caption font-black uppercase tracking-widest text-slate-500 dark:text-slate-400">
                {stat.label}
              </p>
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-ui-display font-black text-slate-900 dark:text-white tracking-tight">
                {stat.value}
              </span>
              <span className="text-ui-caption font-bold text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded-full">
                {stat.change}
              </span>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
