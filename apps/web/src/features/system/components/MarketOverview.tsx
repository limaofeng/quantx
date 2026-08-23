import { CheckCircle2, Server, Terminal, BarChart4 } from 'lucide-react';
import React from 'react';

import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';

import { MarketStatsCards } from './MarketStatsCards';

const INDICES_PREVIEW = [
  {
    name: '上证指数',
    code: '000001.SH',
    close: '3,050.12',
    pct: '+0.54%',
    status: 'Synced',
  },
  {
    name: '深证成指',
    code: '399001.SZ',
    close: '9,850.33',
    pct: '-0.12%',
    status: 'Synced',
  },
  {
    name: '创业板指',
    code: '399006.SZ',
    close: '1,950.45',
    pct: '+0.88%',
    status: 'Synced',
  },
  {
    name: '科创50',
    code: '000688.SH',
    close: '890.11',
    pct: '+1.20%',
    status: 'Synced',
  },
];

const STEPS = [
  {
    title: '交易日历同步',
    status: 'completed',
    duration: '0.5s',
    detail: '242 Valid Days',
  },
  {
    title: '股票列表更新',
    status: 'completed',
    duration: '2.1s',
    detail: '5324 Securities',
  },
  {
    title: 'ETF列表更新',
    status: 'completed',
    duration: '1.2s',
    detail: '890 Funds',
  },
  {
    title: '全量日K线数据',
    status: 'processing',
    duration: 'In Progress',
    detail: 'Batch 4/50',
  },
  { title: '实时分钟K线', status: 'pending', duration: '-', detail: 'Waiting' },
  {
    title: '财务指标数据',
    status: 'pending',
    duration: '-',
    detail: 'Waiting',
  },
  {
    title: '除权数据',
    status: 'pending',
    duration: '-',
    detail: 'Waiting',
  },
];

export function MarketOverview() {
  return (
    <div className="flex flex-col gap-6 h-full min-h-0">
      {/* Stats Cards Row */}
      <MarketStatsCards />

      <div className="flex flex-col lg:flex-row gap-6 flex-1 min-h-0">
        {/* Left Column: Charts & Indices */}
        <div className="flex-1 flex flex-col gap-6 min-h-0">
          <Card className="flex-1 flex flex-col border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-sm overflow-hidden shadow-sm rounded-[24px]">
            <CardHeader className="pb-2 border-b border-slate-100 dark:border-white/5">
              <CardTitle className="text-base font-bold text-slate-800 dark:text-slate-200">
                市场动态
              </CardTitle>
              <CardDescription>
                Real-time updates from synced data
              </CardDescription>
            </CardHeader>
            <CardContent className="flex-1 flex flex-col p-0 min-h-0">
              <div className="h-48 bg-slate-50/50 dark:bg-white/[0.02] flex items-center justify-center border-b border-slate-100 dark:border-white/5">
                <p className="text-slate-400 flex items-center gap-2">
                  <BarChart4 className="w-8 h-8 opacity-50" />
                  <span className="text-sm font-medium">
                    Market Trend Visualization
                  </span>
                </p>
              </div>
              <div className="p-6 flex-1 overflow-auto">
                <h3 className="text-[11px] font-black uppercase tracking-widest text-slate-500 mb-4">
                  Top Indices Status
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {INDICES_PREVIEW.map((idx, k) => (
                    <div
                      key={k}
                      className="flex items-center justify-between p-3 rounded-xl border border-slate-200/50 dark:border-white/5 bg-white/60 dark:bg-slate-800/30 hover:bg-white dark:hover:bg-slate-800/50 transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <div
                          className={`w-1 h-8 rounded-full ${idx.pct.startsWith('+') ? 'bg-market-up' : 'bg-market-down'}`}
                        />
                        <div>
                          <div className="font-bold text-sm text-slate-700 dark:text-slate-200">
                            {idx.name}
                          </div>
                          <div className="text-[10px] font-mono text-slate-400">
                            {idx.code}
                          </div>
                        </div>
                      </div>
                      <div className="text-right">
                        <div className="font-mono font-bold text-slate-700 dark:text-slate-200">
                          {idx.close}
                        </div>
                        <div
                          className={`text-xs font-bold ${idx.pct.startsWith('+') ? 'text-market-up' : 'text-market-down'}`}
                        >
                          {idx.pct}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right Column: Sync Process & Logs */}
        <div className="w-full lg:w-96 flex flex-col gap-6 shrink-0 min-h-0">
          <Card className="border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-sm shadow-sm relative overflow-hidden rounded-[24px]">
            <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-indigo-500 via-purple-500 to-pink-500" />
            <CardHeader className="pb-3 pt-5">
              <div className="flex items-center justify-between">
                <CardTitle className="text-sm font-bold flex items-center gap-2 text-slate-800 dark:text-slate-200">
                  <Server className="w-4 h-4 text-slate-500" />
                  同步进程
                </CardTitle>
                <Badge
                  variant="outline"
                  className="text-[10px] font-bold bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-500/30 uppercase tracking-wider px-2"
                >
                  Running
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-1">
              {STEPS.map((step, i) => (
                <div
                  key={i}
                  className="group flex items-start justify-between p-2 rounded-lg hover:bg-slate-100 dark:hover:bg-white/5 transition-colors"
                >
                  <div className="flex items-start gap-3">
                    <div className="mt-1">
                      {step.status === 'completed' && (
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                      )}
                      {step.status === 'processing' && (
                        <div className="w-3.5 h-3.5 rounded-full border-2 border-indigo-500 border-t-transparent animate-spin" />
                      )}
                      {step.status === 'pending' && (
                        <div className="w-3.5 h-3.5 rounded-full border-2 border-slate-300 dark:border-slate-700" />
                      )}
                    </div>
                    <div>
                      <div
                        className={`text-xs font-bold ${step.status === 'processing' ? 'text-indigo-600 dark:text-indigo-400' : 'text-slate-700 dark:text-slate-300'}`}
                      >
                        {step.title}
                      </div>
                      <div className="text-[10px] text-slate-400 font-mono mt-0.5">
                        {step.detail}
                      </div>
                    </div>
                  </div>
                  <span className="text-[10px] font-mono text-slate-400 tabular-nums opacity-60 group-hover:opacity-100 transition-opacity">
                    {step.duration}
                  </span>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="flex-1 flex flex-col border-slate-200/60 dark:border-white/5 bg-slate-950 text-slate-300 overflow-hidden shadow-inner min-h-[250px] rounded-[24px]">
            <CardHeader className="py-2.5 px-3 border-b border-white/10 bg-white/5 backdrop-blur">
              <div className="flex items-center justify-between">
                <div className="text-[10px] font-black uppercase tracking-widest flex items-center gap-2 text-slate-400">
                  <Terminal className="w-3 h-3" />
                  Console Output
                </div>
                <Badge
                  variant="secondary"
                  className="h-4 px-1.5 text-[9px] bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 uppercase tracking-wider animate-pulse"
                >
                  Live
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="flex-1 p-0 min-h-0 relative">
              <ScrollArea className="absolute inset-0">
                <div className="p-3 font-mono text-[10px] leading-relaxed space-y-1.5">
                  <div className="text-emerald-400">
                    [15:30:01] Flow started: comprehensive-market-sync
                  </div>
                  <div className="text-slate-500">
                    [15:30:01] Initializing data connections...
                  </div>
                  <div className="text-slate-500">
                    [15:30:01] Fetching trading calendar for 2026...
                  </div>
                  <div className="text-emerald-400">
                    [15:30:02] Calendar synced successfully.
                  </div>
                  <div className="text-slate-500">
                    [15:30:04] Stock list updated. (5324 total)
                  </div>
                  <div className="text-emerald-400">
                    [15:30:05] ETF list updated.
                  </div>
                  <div className="text-blue-400 font-bold">
                    [15:30:05] Starting Batch K-Line Sync...
                  </div>
                  <div className="text-slate-600 pl-2">
                    Batch 1/50: 600000.SH ~ 600100.SH OK
                  </div>
                  <div className="text-slate-600 pl-2">
                    Batch 2/50: 600101.SH ~ 600200.SH OK
                  </div>
                  <div className="text-slate-600 pl-2">
                    Batch 3/50: 600201.SH ~ 600300.SH OK
                  </div>
                  <div className="text-indigo-400 pl-2 animate-pulse">
                    Batch 4/50: Processing...
                  </div>
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
