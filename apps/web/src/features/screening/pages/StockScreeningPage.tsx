import { Activity, Filter } from 'lucide-react';
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
    activeMode,
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
    retry,
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

  const hasPendingChanges =
    JSON.stringify(localCriteria) !== JSON.stringify(screeningCriteria);

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
  const isIntradayMode = activeMode === 'INTRADAY';
  const loadedCount = meta.loadedCount ?? results?.length ?? 0;
  const snapshotTone = isIntradayMode
    ? meta.calculatedAt
      ? 'bg-cyan-400'
      : 'bg-slate-500'
    : meta.snapshotDate
      ? meta.hasStaleData
        ? 'bg-amber-400'
        : 'bg-emerald-400'
      : 'bg-slate-500';
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
          <div className="grid min-h-0 flex-1 grid-cols-[296px_minmax(0,1fr)] overflow-hidden min-[1440px]:grid-cols-[320px_minmax(0,1fr)]">
            <ScreeningTopBar
              screeningCriteria={localCriteria}
              setScreeningCriteria={setLocalCriteria}
              availableIndustries={availableIndustries}
              meta={meta}
              onRunScreening={handleRunScreening}
              screeningLoading={isLoading}
              onReset={handleReset}
              onBackfillSnapshot={() => void handleBackfillSnapshot()}
              onOpenAdvancedData={() =>
                setLocation('/settings/data/market-data')
              }
              onOpenSnapshotRun={
                snapshotLogRunId
                  ? () => setLocation(`/system/flow-runs/${snapshotLogRunId}`)
                  : undefined
              }
              snapshotBackfillLoading={snapshotBackfillLoading}
              snapshotRunState={snapshotRunState}
              hasPendingChanges={hasPendingChanges}
            />

            <div className="studio-workspace-surface relative min-h-0 min-w-0 overflow-hidden p-1">
              <ScreeningResults
                screeningLoading={isLoading}
                results={results}
                meta={meta}
                sort={sort}
                onSortChange={applySort}
                activeMode={activeMode}
                onRetry={retry}
                error={error?.message}
              />
            </div>
          </div>
        </div>
      }
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className={cn('h-1.5 w-1.5 rounded-full', snapshotTone)} />
            {isIntradayMode ? '盘中选股' : '选股'}
          </span>
          <span className="text-slate-700">|</span>
          <span>
            已加载 {loadedCount} / 共 {meta.total ?? loadedCount}
          </span>
        </>
      }
      statusBarRight={
        isIntradayMode ? (
          <>
            <span className="inline-flex items-center gap-2">
              <Activity className="h-3 w-3 text-cyan-300" />
              {meta.intradayScannerRunning ? '扫描器运行中' : '扫描器已停止'}
            </span>
            <span className="text-slate-700">|</span>
            <span>5 秒刷新</span>
          </>
        ) : (
          <>
            <span className="inline-flex items-center gap-2">
              <Filter className="h-3 w-3 text-blue-300" />
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
        )
      }
    />
  );
}
