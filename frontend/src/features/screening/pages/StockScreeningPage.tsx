import { Activity, Filter, RotateCcw, Search } from 'lucide-react';
import { useState, useEffect, useMemo } from 'react';

import { DataStudioShell } from '@/features/system/components/DataStudioShell';
import { cn } from '@/utils/cn';

import { ScreeningResults } from '../components/ScreeningResults';
import { ScreeningTopBar } from '../components/ScreeningTopBar';
import { useStockScreening } from '../hooks/useStockScreening';
import { type ScreeningCriteria } from '../types';

export default function StockScreeningPage() {
  // 1. Data Logic
  const {
    screeningCriteria,
    results,
    meta,
    sort,
    applySort,
    error,
    isLoading,
    runScreening,
    resetCriteria,
    availableIndustries,
  } = useStockScreening();

  // 2. Initial criteria state
  const [localCriteria, setLocalCriteria] =
    useState<ScreeningCriteria>(screeningCriteria);

  // Sync local criteria when global criteria resets/changes
  useEffect(() => {
    setLocalCriteria(screeningCriteria);
  }, [screeningCriteria]);

  // Sync back to hook when user clicks "Run"
  const handleRunScreening = () => {
    runScreening(localCriteria);
  };

  const handleReset = () => {
    resetCriteria();
  };
  const activeStrategyCount = useMemo(() => {
    return [
      localCriteria.enableOversoldRebound,
      localCriteria.enableStrongTrend,
      localCriteria.enableKDJGoldenCross,
      localCriteria.enableVolumeBreakout,
      localCriteria.enableMACrossover,
      localCriteria.enableBollingerLowerRebound,
      localCriteria.enableBollingerUpperBreakout,
      localCriteria.enableRSIOversold,
      localCriteria.enableRSIStrong,
    ].filter(Boolean).length;
  }, [localCriteria]);
  const activeIndustryCount = localCriteria.includeIndustries?.length || 0;
  const snapshotTone = meta.snapshotDate
    ? meta.hasStaleData
      ? 'bg-amber-400'
      : 'bg-emerald-400'
    : 'bg-slate-500';

  return (
    <DataStudioShell
      activeMode="SCREENING"
      className="h-full min-h-0"
      showSidebar={false}
      content={
        <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#08101d]">
          <ScreeningTopBar
            screeningCriteria={localCriteria}
            setScreeningCriteria={setLocalCriteria}
            availableIndustries={availableIndustries}
            meta={meta}
            onRunScreening={handleRunScreening}
            screeningLoading={isLoading}
            onReset={handleReset}
          />

          <div className="relative min-h-0 flex-1 overflow-hidden bg-transparent p-1">
            <ScreeningResults
              screeningLoading={isLoading}
              results={results}
              meta={meta}
              sort={sort}
              onSortChange={applySort}
              error={error?.message}
            />
          </div>
        </div>
      }
      extraSidebar={
        <div className="space-y-3 px-1">
          <div className="px-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
            Filter State
          </div>

          <div className="grid grid-cols-2 gap-2">
            {[
              { label: '结果', value: meta.total || results?.length || 0 },
              { label: '策略', value: activeStrategyCount },
              { label: '行业', value: activeIndustryCount },
              { label: '警告', value: meta.warnings?.length || 0 },
            ].map(item => (
              <div
                key={item.label}
                className="rounded-md border border-white/5 bg-white/[0.03] px-2 py-2"
              >
                <div className="text-[9px] font-black uppercase tracking-wider text-slate-600">
                  {item.label}
                </div>
                <div className="mt-1 font-mono text-sm font-bold text-slate-200">
                  {item.value}
                </div>
              </div>
            ))}
          </div>

          <div className="rounded-md border border-white/5 bg-white/[0.03] p-2">
            <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-wider text-slate-500">
              <span className={cn('h-1.5 w-1.5 rounded-full', snapshotTone)} />
              快照状态
            </div>
            <div className="truncate font-mono text-[11px] text-slate-300">
              {meta.snapshotDate || '待计算'}
            </div>
            <div className="mt-1 text-[10px] text-slate-600">
              {meta.hasStaleData ? '历史快照' : '最新可用数据'}
            </div>
          </div>

          <div className="flex gap-2">
            <button
              type="button"
              onClick={handleRunScreening}
              disabled={isLoading}
              className="flex h-8 flex-1 items-center justify-center gap-2 rounded-md bg-red-500 text-[10px] font-black uppercase tracking-wider text-white transition-colors hover:bg-red-400 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <Search className="h-3.5 w-3.5" />
              运行
            </button>
            <button
              type="button"
              onClick={handleReset}
              className="flex h-8 w-8 items-center justify-center rounded-md border border-white/10 text-slate-500 transition-colors hover:border-red-500/40 hover:text-red-300"
              title="重置条件"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
      }
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className={cn('h-1.5 w-1.5 rounded-full', snapshotTone)} />
            股票筛选
          </span>
          <span className="text-slate-700">|</span>
          <span>命中 {meta.total || results?.length || 0}</span>
        </>
      }
      statusBarRight={
        <>
          <span className="inline-flex items-center gap-2">
            <Filter className="h-3 w-3 text-red-400" />
            策略 {activeStrategyCount}
          </span>
          <span className="text-slate-700">|</span>
          <span>行业 {activeIndustryCount}</span>
          <span className="text-slate-700">|</span>
          <span className="inline-flex items-center gap-2">
            <Activity className="h-3 w-3 text-emerald-300" />
            {isLoading ? '计算中' : '就绪'}
          </span>
        </>
      }
    />
  );
}
