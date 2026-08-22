import { Activity, Filter, RotateCcw, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from 'urql';
import { useLocation } from 'wouter';

import { DataStudioShell } from '@/features/system/components/DataStudioShell';
import { gql } from '@/generated/gql';
import { useToast } from '@/hooks/use-toast';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

import { ScreeningResults } from '../components/ScreeningResults';
import { ScreeningTopBar } from '../components/ScreeningTopBar';
import { useStockScreening } from '../hooks/useStockScreening';
import { buildSnapshotBackfillParameters } from '../snapshotBackfill';
import { type ScreeningCriteria } from '../types';

const SCREENING_BACKFILL_RUNS_QUERY = gql(`
  query ScreeningBackfillRuns(
    $deploymentId: String!
    $limit: Int
    $offset: Int
  ) {
    flowRuns(
      deploymentId: $deploymentId
      limit: $limit
      offset: $offset
    ) {
      items {
        id
        state
      }
    }
  }
`);

const TERMINAL_RUN_STATES = new Set([
  'COMPLETED',
  'FAILED',
  'CRASHED',
  'CANCELLED',
]);

export default function StockScreeningPage() {
  const [, setLocation] = useLocation();
  const { toast } = useToast();
  const snapshotSync = useDeploymentSync('daily-market-data-sync', {
    successMessage: '选股快照补算已提交',
  });
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
    refreshDailyData,
    isSnapshotStatusLoading,
  } = useStockScreening();
  const [snapshotRunId, setSnapshotRunId] = useState<string | null>(null);
  const [snapshotLogRunId, setSnapshotLogRunId] = useState<string | null>(null);
  const [snapshotRunState, setSnapshotRunState] = useState<string | null>(null);
  const [verificationAttempt, setVerificationAttempt] = useState(0);
  const handledTerminalRun = useRef<string | null>(null);

  const [{ data: backfillRunsData }, refreshBackfillRuns] = useQuery({
    query: SCREENING_BACKFILL_RUNS_QUERY,
    variables: {
      deploymentId: snapshotSync.deployment?.id || '',
      limit: 12,
      offset: 0,
    },
    pause: !snapshotSync.deployment?.id || !snapshotRunId,
    requestPolicy: 'network-only',
  });
  const trackedRun = backfillRunsData?.flowRuns?.items.find(
    run => run.id === snapshotRunId
  );

  useEffect(() => {
    if (!snapshotRunId || TERMINAL_RUN_STATES.has(snapshotRunState || '')) {
      return;
    }
    const intervalId = window.setInterval(() => {
      refreshBackfillRuns({ requestPolicy: 'network-only' });
    }, 2000);
    return () => window.clearInterval(intervalId);
  }, [refreshBackfillRuns, snapshotRunId, snapshotRunState]);

  useEffect(() => {
    if (!trackedRun || trackedRun.id !== snapshotRunId) return;
    const state = (trackedRun.state || '').toUpperCase();
    setSnapshotRunState(state);
    if (!TERMINAL_RUN_STATES.has(state)) return;
    const handledKey = `${trackedRun.id}:${state}`;
    if (handledTerminalRun.current === handledKey) return;
    handledTerminalRun.current = handledKey;

    if (state === 'COMPLETED') {
      refreshDailyData();
      setVerificationAttempt(1);
      return;
    }

    setSnapshotRunId(null);
    refreshDailyData();
    toast({
      title: '选股快照补算失败',
      description: `运行状态：${state}。旧快照结果已保留，可查看日志后重试。`,
      variant: 'destructive',
    });
  }, [refreshDailyData, snapshotRunId, toast, trackedRun]);

  useEffect(() => {
    if (verificationAttempt <= 0) return;
    if (meta.isComplete) {
      toast({
        title: '选股快照已补齐',
        description: `应有快照 ${meta.expectedSnapshotDate} 已可用，筛选结果已刷新。`,
        variant: 'success',
      });
      setVerificationAttempt(0);
      setSnapshotRunId(null);
      return;
    }
    if (verificationAttempt >= 6) {
      toast({
        title: '任务完成，但快照仍不完整',
        description: '数据库中尚未出现应有日期的成功快照，请查看运行日志。',
        variant: 'destructive',
      });
      setVerificationAttempt(0);
      setSnapshotRunId(null);
      return;
    }
    const timeoutId = window.setTimeout(() => {
      refreshDailyData();
      setVerificationAttempt(current => current + 1);
    }, 1500);
    return () => window.clearTimeout(timeoutId);
  }, [
    meta.expectedSnapshotDate,
    meta.isComplete,
    refreshDailyData,
    toast,
    verificationAttempt,
  ]);

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

  const handleBackfillSnapshot = async () => {
    if (snapshotRunId || snapshotSync.isSyncing) return;
    const dates = [...meta.missingSnapshotDates].sort();
    if (dates.length === 0) {
      toast({
        title: '快照已是最新',
        description: `应有快照 ${meta.expectedSnapshotDate || '--'} 已可用。`,
        variant: 'success',
      });
      return;
    }
    const parameters = buildSnapshotBackfillParameters(dates);
    if (!parameters) return;
    const runId = await snapshotSync.triggerSync(parameters);
    if (!runId) return;
    handledTerminalRun.current = null;
    setSnapshotRunId(runId);
    setSnapshotLogRunId(runId);
    setSnapshotRunState('PENDING');
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
  const isIntradayMode =
    (localCriteria.screeningMode ?? 'DAILY') === 'INTRADAY';
  const snapshotTone = isIntradayMode
    ? meta.calculatedAt
      ? 'bg-cyan-400'
      : 'bg-slate-500'
    : meta.snapshotDate
      ? meta.hasStaleData
        ? 'bg-amber-400'
        : 'bg-emerald-400'
      : 'bg-slate-500';
  const dataStateLabel = isIntradayMode ? '盘中状态' : '快照状态';
  const dataStateValue = isIntradayMode
    ? meta.calculatedAt
      ? new Date(meta.calculatedAt).toLocaleTimeString()
      : '待接入'
    : meta.snapshotDate || '待计算';
  const dataStateHint = isIntradayMode
    ? meta.isComplete
      ? '全市场扫描中'
      : '等待实时行情'
    : meta.isComplete
      ? '最新可用数据'
      : meta.snapshotDate
        ? `缺少 ${meta.missingSnapshotDates.length} 个交易日`
        : '等待生成';
  const snapshotBackfillLoading =
    Boolean(snapshotRunId) ||
    snapshotSync.isSyncing ||
    meta.latestRunStatus === 'running' ||
    isSnapshotStatusLoading;

  return (
    <DataStudioShell
      activeMode="SCREENING"
      className="h-full min-h-0"
      showSidebar={false}
      content={
        <div className="studio-workspace-surface flex h-full min-h-0 flex-col overflow-hidden">
          <ScreeningTopBar
            screeningCriteria={localCriteria}
            setScreeningCriteria={setLocalCriteria}
            availableIndustries={availableIndustries}
            meta={meta}
            onRunScreening={handleRunScreening}
            screeningLoading={isLoading}
            onReset={handleReset}
            onBackfillSnapshot={() => void handleBackfillSnapshot()}
            onOpenAdvancedData={() => setLocation('/settings/data/market-data')}
            onOpenSnapshotRun={
              snapshotLogRunId
                ? () => setLocation(`/system/flow-runs/${snapshotLogRunId}`)
                : undefined
            }
            snapshotBackfillLoading={snapshotBackfillLoading}
            snapshotRunState={snapshotRunState}
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
              {dataStateLabel}
            </div>
            <div className="truncate font-mono text-[11px] text-slate-300">
              {dataStateValue}
            </div>
            <div className="mt-1 text-[10px] text-slate-600">
              {dataStateHint}
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
            {isIntradayMode ? '盘中筛选' : '股票筛选'}
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
