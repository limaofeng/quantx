import {
  Database,
  Loader2,
  RefreshCw,
  Box,
  CandlestickChart,
  FileText,
  CheckCircle2,
  Percent,
} from 'lucide-react';
import React, { useState } from 'react';
import { useLocation } from 'wouter';

import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

export function GlobalSyncCard() {
  const { toast } = useToast();
  const [, setLocation] = useLocation();
  const [syncing, setSyncing] = useState(false);
  const [progress, setProgress] = useState(0);

  const handleSync = () => {
    setSyncing(true);
    setProgress(0);
    toast({
      title: '全市场数据',
      description: '正在执行全量数据初始化...',
    });

    const interval = setInterval(() => {
      setProgress(prev => {
        if (prev >= 100) {
          clearInterval(interval);
          setSyncing(false);
          toast({
            title: '同步完成',
            description: '基础数据、K线与财务数据已更新。',
            className:
              'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800',
          });
          return 100;
        }
        return prev + 5;
      });
    }, 200);
  };

  return (
    <div
      className="h-full flex flex-row items-center p-ui-panel rounded-panel border border-slate-200/40 dark:border-slate-800/40 bg-gradient-to-br from-indigo-50/50 to-blue-50/50 dark:from-indigo-900/20 dark:to-blue-900/20 overflow-hidden relative group cursor-pointer transition-all hover:bg-indigo-50/80 dark:hover:bg-indigo-900/30"
      onClick={e => {
        if ((e.target as HTMLElement).closest('button')) return;
        setLocation('/settings/data/market');
      }}
    >
      {/* Left Section: Icon & Identity */}
      <div className="flex items-center gap-ui-section z-10 w-[300px] shrink-0">
        <div className="p-3 rounded-panel bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 ring-1 ring-inset ring-black/5 dark:ring-white/10">
          <Database className="w-8 h-8" />
        </div>
        <div>
          <h3 className="font-extrabold text-ui-heading text-slate-800 dark:text-slate-100">
            全市场综合同步
          </h3>
          <p className="text-ui-label text-slate-500 dark:text-slate-400 font-medium">
            Global Market Data Sync
          </p>
        </div>
      </div>

      {/* Middle Section: Description & Progress */}
      <div className="flex-1 px-ui-panel z-10 flex flex-col justify-center gap-3">
        {!syncing ? (
          <p className="text-ui-label text-slate-600 dark:text-slate-400 leading-relaxed max-w-[400px]">
            一键同步全 A 股与 ETF 基础信息、历史日/分钟 K
            线数据以及最新的财务报告指标，确保分析引擎处于最新状态。
          </p>
        ) : (
          <div className="w-full max-w-[400px]">
            <div className="flex justify-between text-ui-caption text-indigo-600 dark:text-indigo-400 mb-2 font-bold uppercase tracking-wider">
              <span className="flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在初始化核心数据库...
              </span>
              <span>{progress}%</span>
            </div>
            <Progress
              value={progress}
              className="h-2 bg-indigo-100 dark:bg-indigo-950"
            />
          </div>
        )}

        <div className="flex gap-ui-section">
          <div className="flex items-center gap-1.5 text-ui-caption font-bold text-slate-500/80 dark:text-slate-400/80 bg-white/40 dark:bg-black/20 px-2 py-0.5 rounded border border-slate-200/50 dark:border-white/5">
            <Box className="w-3 h-3" /> A股/ETF 列表
          </div>
          <div className="flex items-center gap-1.5 text-ui-caption font-bold text-slate-500/80 dark:text-slate-400/80 bg-white/40 dark:bg-black/20 px-2 py-0.5 rounded border border-slate-200/50 dark:border-white/5">
            <CandlestickChart className="w-3 h-3" /> K线全时间框架
          </div>
          <div className="flex items-center gap-1.5 text-ui-caption font-bold text-slate-500/80 dark:text-slate-400/80 bg-white/40 dark:bg-black/20 px-2 py-0.5 rounded border border-slate-200/50 dark:border-white/5">
            <FileText className="w-3 h-3" /> 多维财务指标
          </div>
          <div className="flex items-center gap-1.5 text-ui-caption font-bold text-slate-500/80 dark:text-slate-400/80 bg-white/40 dark:bg-black/20 px-2 py-0.5 rounded border border-slate-200/50 dark:border-white/5">
            <Percent className="w-3 h-3" /> 除权数据
          </div>
        </div>
      </div>

      {/* Right Section: Actions */}
      <div className="w-[180px] shrink-0 flex flex-col items-end gap-3 z-10">
        <Button
          onClick={handleSync}
          disabled={syncing}
          size="lg"
          className={cn(
            'w-full h-control-large text-ui-body font-black shadow-lg shadow-indigo-500/20 dark:shadow-none transition-all active:scale-95',
            syncing
              ? 'bg-slate-100 text-slate-400 dark:bg-slate-800'
              : 'bg-indigo-600 hover:bg-indigo-700 text-white'
          )}
        >
          {syncing ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              同步中
            </>
          ) : (
            <>
              <RefreshCw className="w-4 h-4 mr-2" />
              立即开始
            </>
          )}
        </Button>
        <div className="flex items-center gap-1.5 text-ui-caption font-bold text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="w-3.5 h-3.5" />
          系统当前已是最新
        </div>
      </div>

      {/* Decorative Background */}
      <div className="absolute -right-10 -top-10 w-48 h-48 bg-indigo-500/10 rounded-full blur-3xl group-hover:bg-indigo-500/20 transition-all duration-700 opacity-50" />
      <div className="absolute left-1/4 -bottom-12 w-32 h-32 bg-blue-500/5 rounded-full blur-2xl group-hover:bg-blue-500/10 transition-all duration-700" />
    </div>
  );
}
