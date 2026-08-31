import {
  AlertTriangle,
  BarChart3,
  Check,
  CircleDollarSign,
  FileCheck2,
  FlaskConical,
  Gauge,
  History,
  Hourglass,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
  TrendingUp,
  WalletCards,
} from 'lucide-react';
import * as React from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useMutation, useQuery, useSubscription } from 'urql';

import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { useGraphqlWsStatus } from '@/core/graphql/ws-status';
import { mapExecutionTraceView } from '@/features/strategies/domain/adapters';
import { DeleteStrategyRunMutation } from '@/features/strategies/hooks/strategyInstanceOperations';
import { useFragment as readFragment } from '@/generated/gql/fragment-masking';
import {
  TTradeReplayPortfolioSource,
  type TTradeReplayCycle,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { useTradingDays } from '@/hooks/useTradingDays';
import { cn } from '@/utils/cn';

import {
  CancelTTradeReplayMutation,
  StartTTradeReplayMutation,
  TTradeReplayCyclesQuery,
  TTradeReplayHistoryQuery,
  TTradeReplayPreparationQuery,
  TTradeReplayQuery,
  TTradeReplayUpdatesSubscription,
  TTradeSignalPolicyFieldsFragment,
} from '../../hooks/useTTradeGlobal';
import { useTTradeReplayEvidence } from '../../hooks/useTTradeReplayEvidence';

import {
  clearPersistedOperation,
  persistUncertainOperation,
  readUncertainOperation,
  type ClientOperationRef,
} from './operationPersistence';
import { replayEvidenceUnavailableMessage } from './replayEvidencePresentation';
import {
  costFormFromReplaySettings,
  replaySettingsDifferenceCount,
  replaySettingsInput,
  settingsFormFromReplaySettings,
  validateReplaySettings,
  type ReplayCostForm,
} from './replaySettings';
import {
  isNewerReplayRevision,
  replayFallbackPollInterval,
  replayNoticeRefreshTargets,
  stableValueByKey,
} from './replaySync';
import {
  canDeleteReplay,
  deleteReplayRunsSequentially,
  mapReplayCyclesToActivityBatches,
  mapReplayCyclesToActivityEvents,
  mapReplayCyclesToPositionBatches,
  mapReplayExecutionsToActivityEvents,
  replayProjectionActivityItems,
  replayStatusAfterDelete,
  replayStatusAfterDeleteMany,
} from './replayWorkspace';
import { TTradePanelBoundary } from './TTradePanelBoundary';
import { TTradeReplayDecisionAudit } from './TTradeReplayDecisionAudit';
import type {
  ReplayManualPositionDraft,
  ReplaySidebarContext,
} from './TTradeReplaySidebar';
import { TTradeReplaySignals } from './TTradeReplaySignals';
import {
  TTradeActivityView,
  TTradePositionsView,
  TTradeReplayAccountPanel,
  TTradeReplayFrozenSettings,
  TTradeReplaySettingsEditor,
} from './TTradeSecondaryViews';
import type {
  ReplayWorkspaceView,
  SettingsForm,
  SignalPolicyForm,
  SignalPolicyFormValue,
} from './types';
import {
  formatNumber,
  formatTime,
  replayDatePreset,
  replayIdempotencyKey,
  replayPhaseLabel,
  replayStatusLabel,
} from './utils';

const tTradePositionsFallback = (
  <div
    className="studio-workspace-surface flex h-full min-h-0 items-center justify-center text-ui-label text-slate-500"
    role="status"
  >
    <Loader2
      className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none"
      aria-hidden="true"
    />
    正在加载仓位与批次…
  </div>
);

const tTradeReplayAccountFallback = (
  <div
    className="flex min-h-64 items-center justify-center text-ui-label text-slate-500"
    role="status"
  >
    <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
    正在加载回测账户…
  </div>
);

const tTradeReplaySettingsFallback = (
  <div
    className="flex min-h-64 items-center justify-center text-ui-label text-slate-500"
    role="status"
  >
    <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
    正在加载回测参数…
  </div>
);

function MetricCard({
  icon: Icon,
  label,
  tone = 'slate',
  value,
}: {
  icon: React.ElementType;
  label: string;
  tone?:
    'amber' | 'emerald' | 'marketDown' | 'marketUp' | 'red' | 'sky' | 'slate';
  value: string | number;
}) {
  const tones = {
    amber: 'border-amber-400/15 bg-amber-400/[0.06] text-amber-200',
    emerald: 'border-emerald-400/15 bg-emerald-400/[0.06] text-emerald-200',
    marketDown: 'border-market-down/15 bg-market-down/[0.06] text-market-down',
    marketUp: 'border-market-up/15 bg-market-up/[0.06] text-market-up',
    red: 'border-red-400/15 bg-red-400/[0.06] text-red-200',
    sky: 'border-sky-300/15 bg-sky-300/[0.06] text-sky-200',
    slate: 'border-white/[0.07] bg-white/[0.025] text-slate-200',
  };
  return (
    <div className={cn('border p-2.5', tones[tone])}>
      <div className="flex items-center gap-2 text-ui-caption font-bold uppercase tracking-[0.12em] opacity-70">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1.5 font-mono text-ui-heading font-black tabular-nums">
        {value}
      </div>
    </div>
  );
}

function useStableValueByKey<T>(
  key: string,
  value: T | undefined,
  valueKey: string | undefined
) {
  const cache = React.useRef(new Map<string, T>());
  return stableValueByKey(cache.current, key, value, valueKey);
}

export function TTradeReplayPanel({
  accountId,
  activeView,
  baseCosts,
  baseForm,
  costs,
  form,
  liveConfigVersion,
  liveSettingsStale,
  onActiveViewChange,
  onCopySettings,
  onCostChange,
  onFieldChange,
  onRestoreSettings,
  onSidebarContextChange,
  onSignalPolicyChange,
  restoringSettings,
}: {
  accountId: string;
  activeView: ReplayWorkspaceView;
  baseCosts: ReplayCostForm;
  baseForm: SettingsForm;
  costs: ReplayCostForm;
  form: SettingsForm;
  liveConfigVersion: number;
  liveSettingsStale: boolean;
  onActiveViewChange: (view: ReplayWorkspaceView) => void;
  onCopySettings: (form: SettingsForm, costs: ReplayCostForm) => void;
  onCostChange: (field: keyof ReplayCostForm, value: string) => void;
  onFieldChange: <K extends keyof SettingsForm>(
    field: K,
    value: SettingsForm[K]
  ) => void;
  onRestoreSettings: () => Promise<boolean>;
  onSidebarContextChange: (context: ReplaySidebarContext | null) => void;
  onSignalPolicyChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
  restoringSettings: boolean;
}) {
  const { toast } = useToast();
  const { confirm: confirmDialog } = useAppDialog();
  const { tradingDays: replayTradingDays } = useTradingDays('SH', 60);
  const initialRange = React.useMemo(() => replayDatePreset(5), []);
  const [startDate, setStartDate] = React.useState(initialRange.start);
  const [endDate, setEndDate] = React.useState(initialRange.end);
  const appliedTradingCalendarRef = React.useRef(false);
  const [activeRunId, setActiveRunId] = React.useState('');
  const [portfolioSource, setPortfolioSource] = React.useState<
    'SNAPSHOT' | 'MANUAL'
  >('SNAPSHOT');
  const [portfolioDirty, setPortfolioDirty] = React.useState(false);
  const [manualCash, setManualCash] = React.useState('');
  const [manualPositions, setManualPositions] = React.useState<
    ReplayManualPositionDraft[]
  >([]);
  const [includeActivityDiagnostics, setIncludeActivityDiagnostics] =
    React.useState(true);
  const [activityBatchFilter, setActivityBatchFilter] = React.useState<
    string | null
  >(null);
  const [positionFocusBatchId, setPositionFocusBatchId] = React.useState<
    string | null
  >(null);
  const [cycleOffset, setCycleOffset] = React.useState(0);
  const [cycles, setCycles] = React.useState<TTradeReplayCycle[]>([]);
  const startTime = `${startDate}T09:30:00`;
  const endTime = `${endDate}T15:00:00`;

  const [preparationResult, _refreshPreparation] = useQuery({
    query: TTradeReplayPreparationQuery,
    variables: { accountId, startTime },
    pause: !accountId || !startDate,
    requestPolicy: 'network-only',
  });
  const [historyResult, refreshHistory] = useQuery({
    query: TTradeReplayHistoryQuery,
    variables: { accountId, limit: 20 },
    pause: !accountId,
    requestPolicy: 'network-only',
  });
  const [replayResult, refreshReplay] = useQuery({
    query: TTradeReplayQuery,
    variables: { runId: activeRunId },
    pause: !activeRunId,
    requestPolicy: 'network-only',
  });
  const [cyclesResult, refreshCycles] = useQuery({
    query: TTradeReplayCyclesQuery,
    variables: { runId: activeRunId, offset: cycleOffset, limit: 200 },
    pause: !activeRunId,
    requestPolicy: 'network-only',
  });
  const [startResult, startReplay] = useMutation(StartTTradeReplayMutation);
  const [cancelResult, cancelReplay] = useMutation(CancelTTradeReplayMutation);
  const [deleteResult, deleteStrategyRun] = useMutation(
    DeleteStrategyRunMutation
  );
  const replayOperationRef = React.useRef<ClientOperationRef | null>(null);
  React.useEffect(() => {
    replayOperationRef.current = readUncertainOperation(`replay:${accountId}`);
  }, [accountId]);
  const graphqlWsStatus = useGraphqlWsStatus();
  const [replayUpdateResult] = useSubscription({
    query: TTradeReplayUpdatesSubscription,
    variables: { accountId },
    pause: !accountId,
  });

  const stableHistory = useStableValueByKey(
    accountId,
    historyResult.data?.tTradeReplayHistory,
    String(historyResult.operation?.variables.accountId || '')
  );
  const history = React.useMemo(() => stableHistory || [], [stableHistory]);
  const replayValue = replayResult.data?.tTradeReplay;
  const replay = useStableValueByKey(
    activeRunId,
    replayValue,
    replayValue?.runId
  );
  const frozenSignalPolicy = readFragment(
    TTradeSignalPolicyFieldsFragment,
    replay?.settings.signalPolicy
  );
  const frozenForm = React.useMemo(
    () =>
      replay && frozenSignalPolicy
        ? settingsFormFromReplaySettings({
            ...replay.settings,
            signalPolicy: frozenSignalPolicy,
          })
        : null,
    [frozenSignalPolicy, replay]
  );
  const frozenCosts = React.useMemo(
    () => (replay ? costFormFromReplaySettings(replay.settings) : null),
    [replay]
  );
  const replaySettingsErrors = React.useMemo(
    () => validateReplaySettings(form, costs),
    [costs, form]
  );
  const replaySettingsDifference = React.useMemo(
    () => replaySettingsDifferenceCount(form, costs, baseForm, baseCosts),
    [baseCosts, baseForm, costs, form]
  );
  const frozenSettingsDifference = React.useMemo(
    () =>
      frozenForm && frozenCosts
        ? replaySettingsDifferenceCount(
            frozenForm,
            frozenCosts,
            baseForm,
            baseCosts
          )
        : 0,
    [baseCosts, baseForm, frozenCosts, frozenForm]
  );
  const replayEvidence = useTTradeReplayEvidence({
    runId: activeRunId,
    backtestId: replay?.backtestId,
    activeView,
    includeDiagnostics: includeActivityDiagnostics,
  });
  const replayExecutions = React.useMemo(
    () => [
      ...new Map(
        replayEvidence.auditRecords
          .flatMap(item => item.executions)
          .map(item => [item.intentId, mapExecutionTraceView(item)])
      ).values(),
    ],
    [replayEvidence.auditRecords]
  );
  const preparationValue = preparationResult.data?.tTradeReplayPreparation;
  const preparation = useStableValueByKey(
    startTime,
    preparationValue,
    preparationValue?.startTime
  );
  const cyclesPage = useStableValueByKey(
    activeRunId,
    cyclesResult.data?.tTradeReplayCycles,
    String(cyclesResult.operation?.variables.runId || '')
  );
  React.useEffect(() => {
    setCycleOffset(0);
    setCycles([]);
  }, [activeRunId]);
  React.useEffect(() => {
    if (!cyclesPage) return;
    setCycles(previous => {
      if (cyclesPage.offset === 0) return cyclesPage.items;
      const byId = new Map(previous.map(item => [item.batchId, item]));
      for (const item of cyclesPage.items) byId.set(item.batchId, item);
      return Array.from(byId.values());
    });
    if (cyclesPage.hasMore) {
      const nextOffset = cyclesPage.offset + cyclesPage.items.length;
      if (nextOffset > cycleOffset) setCycleOffset(nextOffset);
    }
  }, [cycleOffset, cyclesPage]);
  const replayPositionBatches = React.useMemo(
    () => mapReplayCyclesToPositionBatches(cycles, activeRunId),
    [activeRunId, cycles]
  );
  const replayActivityBatches = React.useMemo(
    () => mapReplayCyclesToActivityBatches(cycles, activeRunId),
    [activeRunId, cycles]
  );
  const replayActivityEvents = React.useMemo(
    () => [
      ...mapReplayCyclesToActivityEvents(cycles, activeRunId),
      ...mapReplayExecutionsToActivityEvents(
        replayExecutions,
        replay?.updatedAt || replay?.endTime || endTime
      ),
    ],
    [
      activeRunId,
      cycles,
      endTime,
      replay?.endTime,
      replay?.updatedAt,
      replayExecutions,
    ]
  );
  const replayActivityEvaluations = replayEvidence.evaluations;
  const replayActivitySupplementalItems = React.useMemo(
    () => (replay ? replayProjectionActivityItems(replay) : []),
    [replay]
  );
  const replayInstrumentNames = React.useMemo(() => {
    const names = new Map<string, string>();
    for (const position of replay?.initialPortfolio.positions || []) {
      if (position.instrumentName) {
        names.set(position.stockCode.toUpperCase(), position.instrumentName);
      }
    }
    for (const instrument of replay?.instruments || []) {
      if (instrument.instrumentName) {
        names.set(
          instrument.stockCode.toUpperCase(),
          instrument.instrumentName
        );
      }
    }
    return names;
  }, [replay?.initialPortfolio.positions, replay?.instruments]);
  const refreshEvidence = replayEvidence.refresh;
  const refreshReplayFacts = React.useCallback(() => {
    if (!activeRunId) return;
    refreshReplay({ requestPolicy: 'network-only' });
    refreshCycles({ requestPolicy: 'network-only' });
    if (replay?.backtestId) {
      refreshEvidence();
    }
  }, [
    activeRunId,
    refreshCycles,
    refreshEvidence,
    refreshReplay,
    replay?.backtestId,
  ]);
  const previousTradingDate = React.useMemo(
    () =>
      [...replayTradingDays]
        .filter(day => day < startDate)
        .sort()
        .at(-1) ||
      preparation?.snapshotDate ||
      '',
    [preparation?.snapshotDate, replayTradingDays, startDate]
  );
  const manualCashNumber = Number(manualCash);
  const manualRowsValid = manualPositions.every(
    item =>
      Boolean(item.stockCode) &&
      Number.isInteger(Number(item.volume)) &&
      Number(item.volume) > 0 &&
      Number.isFinite(Number(item.avgPrice)) &&
      Number(item.avgPrice) > 0
  );
  const manualPortfolioValid =
    previousTradingDate !== '' &&
    Number.isFinite(manualCashNumber) &&
    manualCashNumber >= 0 &&
    manualPositions.some(item => Number(item.volume) >= 100) &&
    manualRowsValid;
  const snapshotPortfolioValid = Boolean(
    preparation?.snapshotId && preparation.snapshotDate
  );
  const isRunning = ['PENDING', 'RUNNING', 'STARTING'].includes(
    String(replay?.status || '').toUpperCase()
  );
  const hasActiveReplay =
    isRunning ||
    history.some(item =>
      ['PENDING', 'RUNNING', 'STARTING'].includes(item.status.toUpperCase())
    );
  const handleReplayPortfolioSourceChange = React.useCallback(
    (source: 'MANUAL' | 'SNAPSHOT') => {
      setPortfolioSource(source);
      setPortfolioDirty(true);
    },
    []
  );
  const handleReplayCashChange = React.useCallback((value: string) => {
    setManualCash(value);
    setPortfolioDirty(true);
  }, []);
  const handleReplayPositionChange = React.useCallback(
    (index: number, field: 'avgPrice' | 'volume', value: string) => {
      setManualPositions(rows =>
        rows.map((row, rowIndex) =>
          rowIndex === index ? { ...row, [field]: value } : row
        )
      );
      setPortfolioDirty(true);
    },
    []
  );
  const handleReplayPositionRemove = React.useCallback((index: number) => {
    setManualPositions(rows =>
      rows.filter((_, rowIndex) => rowIndex !== index)
    );
    setPortfolioDirty(true);
  }, []);
  const handleReplayPositionAdd = React.useCallback(
    (stockCode: string, instrumentName: string, avgPrice: string) => {
      if (manualPositions.some(item => item.stockCode === stockCode)) {
        toast({
          title: '股票已经存在',
          description: `${stockCode} 已在初始组合中。`,
          variant: 'destructive',
        });
        return;
      }
      setManualPositions(rows => [
        ...rows,
        {
          stockCode,
          instrumentName,
          volume: '100',
          avgPrice,
        },
      ]);
      setPortfolioDirty(true);
    },
    [manualPositions, toast]
  );
  const deleteReplayRun = React.useCallback(
    async (runId: string) => {
      const result = await deleteStrategyRun({ runId });
      const payload = result.data?.deleteStrategyRun;
      if (!payload?.success) {
        throw new Error(
          payload?.message || result.error?.message || '删除回放失败'
        );
      }
      return payload.message || '回放及关联数据已删除。';
    },
    [deleteStrategyRun]
  );
  const handleDelete = React.useCallback(
    async (item: (typeof history)[number]) => {
      if (!canDeleteReplay(item.status)) {
        toast({
          title: '当前回放不能删除',
          description: '仅已完成、失败、已取消或已停止的回放可以删除。',
          variant: 'destructive',
        });
        return;
      }
      const confirmed = await confirmDialog({
        title: '删除这次回放？',
        description: `将同时删除 ${String(item.startTime).slice(0, 10)} 至 ${String(
          item.endTime
        ).slice(
          0,
          10
        )} 的回放记录、关联回测运行、回测版本、审计轨迹与结果文件。共享策略模板不会被删除。此操作不可撤销。`,
        confirmText: '删除回放及关联数据',
        cancelText: '取消',
        variant: 'destructive',
      });
      if (!confirmed) return;

      const nextRunId = replayStatusAfterDelete(
        history.map(historyItem => historyItem.runId),
        item.runId,
        activeRunId
      );
      try {
        const message = await deleteReplayRun(item.runId);
        setActiveRunId(nextRunId);
        if (!nextRunId) onActiveViewChange('OVERVIEW');
        toast({ title: '回放已删除', description: message });
        refreshHistory({ requestPolicy: 'network-only' });
      } catch (error) {
        toast({
          title: '无法删除回放',
          description: error instanceof Error ? error.message : '请求失败',
          variant: 'destructive',
        });
      }
    },
    [
      activeRunId,
      confirmDialog,
      deleteReplayRun,
      history,
      onActiveViewChange,
      refreshHistory,
      toast,
    ]
  );
  const handleDeleteMany = React.useCallback(
    async (runIds: readonly string[]) => {
      const requestedRunIds = new Set(runIds);
      const targets = history.filter(item => requestedRunIds.has(item.runId));
      if (targets.length === 0) return;

      if (targets.length !== requestedRunIds.size) {
        toast({
          title: '所选回放已经变化',
          description: '请刷新回测记录后重新选择。',
          variant: 'destructive',
        });
        return;
      }

      if (targets.some(item => !canDeleteReplay(item.status))) {
        toast({
          title: '所选回放不能批量删除',
          description: '选区包含仍在运行或尚未结束的回放，请调整选择。',
          variant: 'destructive',
        });
        return;
      }

      const confirmed = await confirmDialog({
        title: `删除选中的 ${targets.length} 次回放？`,
        description: `将批量删除选中的 ${targets.length} 次回放记录、关联回测运行、回测版本、审计轨迹与结果文件。共享策略模板不会被删除。此操作不可撤销。`,
        confirmText: `删除 ${targets.length} 次回放及关联数据`,
        cancelText: '取消',
        variant: 'destructive',
      });
      if (!confirmed) return;

      const { deletedRunIds, failedRunIds } =
        await deleteReplayRunsSequentially(
          targets.map(target => target.runId),
          deleteReplayRun
        );

      if (deletedRunIds.length > 0) {
        const nextRunId = replayStatusAfterDeleteMany(
          history.map(item => item.runId),
          deletedRunIds,
          activeRunId
        );
        setActiveRunId(nextRunId);
        if (!nextRunId) onActiveViewChange('OVERVIEW');
        refreshHistory({ requestPolicy: 'network-only' });
      }

      if (failedRunIds.length > 0) {
        toast({
          title: '批量删除未完全成功',
          description: `成功 ${deletedRunIds.length} 条，失败 ${failedRunIds.length} 条。失败记录：${failedRunIds
            .map(runId => runId.slice(0, 8))
            .join('、')}。`,
          variant: 'destructive',
        });
        return;
      }

      toast({
        title: `已删除 ${deletedRunIds.length} 次回放`,
        description: '关联回测版本、审计轨迹与结果文件已一并删除。',
      });
    },
    [
      activeRunId,
      confirmDialog,
      deleteReplayRun,
      history,
      onActiveViewChange,
      refreshHistory,
      toast,
    ]
  );
  const replaySidebarContext = React.useMemo<ReplaySidebarContext>(() => {
    const sidebarHistory = history.map(item => ({
      progressPct: item.progressPct,
      runId: item.runId,
      startTime: String(item.startTime),
      status: item.status,
      tNetProfit: item.summary?.tNetProfit ?? null,
    }));
    const historyControls = {
      activeRunId,
      deletingHistory: deleteResult.fetching,
      history: sidebarHistory,
      historyLoading: historyResult.fetching,
      onCreate: () => {
        setActiveRunId('');
        onActiveViewChange('OVERVIEW');
      },
      onDelete: (item: (typeof sidebarHistory)[number]) => {
        const target = history.find(row => row.runId === item.runId);
        if (target) void handleDelete(target);
      },
      onDeleteMany: (items: (typeof sidebarHistory)[number][]) => {
        void handleDeleteMany(items.map(item => item.runId));
      },
      onHistoryRefresh: () => refreshHistory({ requestPolicy: 'network-only' }),
      onSelectRun: (runId: string) => {
        setActiveRunId(runId);
        onActiveViewChange('OVERVIEW');
      },
    };
    if (activeRunId) {
      if (!replay) {
        return {
          ...historyControls,
          accountId,
          asOf: '',
          cashAvailable: 0,
          editor: null,
          frozen: true,
          loading: true,
          message: '正在读取该次回放冻结的初始账户…',
          mode: 'VIEW',
          positions: [],
          source: 'SNAPSHOT',
          totalAsset: 0,
        };
      }
      return {
        ...historyControls,
        accountId: replay.accountId,
        asOf: String(replay.initialPortfolio.asOf || '').slice(0, 10),
        cashAvailable: replay.initialPortfolio.cashAvailable,
        editor: null,
        frozen: true,
        loading: false,
        message:
          '该组合已随回放冻结，只用于复核本次结果，不会跟随当前实盘账户变化。',
        mode: 'VIEW',
        positions: replay.initialPortfolio.positions.map(item => ({
          avgPrice: item.avgPrice,
          availableVolume: item.availableVolume,
          instrumentName: item.instrumentName,
          marketValue: item.marketValue,
          stockCode: item.stockCode,
          volume: item.volume,
        })),
        source:
          String(replay.initialPortfolio.source).toUpperCase() === 'MANUAL'
            ? 'MANUAL'
            : 'SNAPSHOT',
        totalAsset: replay.initialPortfolio.totalAsset,
      };
    }

    if (portfolioSource === 'MANUAL') {
      const positions = manualPositions.map(item => {
        const volume = Number(item.volume) || 0;
        const avgPrice = Number(item.avgPrice) || 0;
        return {
          avgPrice,
          availableVolume: volume,
          instrumentName: item.instrumentName,
          marketValue: volume * avgPrice,
          stockCode: item.stockCode,
          volume,
        };
      });
      const cashAvailable = Number.isFinite(manualCashNumber)
        ? manualCashNumber
        : 0;
      return {
        ...historyControls,
        accountId,
        asOf: previousTradingDate,
        cashAvailable,
        editor: {
          manualCash,
          manualPositions,
          onAddPosition: handleReplayPositionAdd,
          onCashChange: handleReplayCashChange,
          onPositionChange: handleReplayPositionChange,
          onPositionRemove: handleReplayPositionRemove,
          onSourceChange: handleReplayPortfolioSourceChange,
          previousTradingDate,
          requiresManualPortfolio: Boolean(
            preparation?.requiresManualPortfolio
          ),
          snapshotAvailable: snapshotPortfolioValid,
        },
        frozen: false,
        loading: false,
        message: '手工组合会在启动时冻结；不足 100 股的持仓只计入账户权益。',
        mode: 'CREATE',
        positions,
        source: 'MANUAL',
        totalAsset:
          cashAvailable +
          positions.reduce((total, item) => total + item.marketValue, 0),
      };
    }

    return {
      ...historyControls,
      accountId,
      asOf: preparation?.snapshotDate || '',
      cashAvailable: preparation?.initialCash || 0,
      editor: {
        manualCash,
        manualPositions,
        onAddPosition: handleReplayPositionAdd,
        onCashChange: handleReplayCashChange,
        onPositionChange: handleReplayPositionChange,
        onPositionRemove: handleReplayPositionRemove,
        onSourceChange: handleReplayPortfolioSourceChange,
        previousTradingDate,
        requiresManualPortfolio: Boolean(preparation?.requiresManualPortfolio),
        snapshotAvailable: snapshotPortfolioValid,
      },
      frozen: false,
      loading: preparationResult.fetching && !preparation,
      message: preparation?.message || '选择日期后读取开始日前的账户日结快照。',
      mode: 'CREATE',
      positions: (preparation?.positions || []).map(item => ({
        avgPrice: item.avgPrice,
        availableVolume: item.availableVolume,
        instrumentName: item.instrumentName,
        marketValue: item.marketValue,
        stockCode: item.stockCode,
        volume: item.volume,
      })),
      source: 'SNAPSHOT',
      totalAsset: preparation?.initialTotalAsset || 0,
    };
  }, [
    accountId,
    activeRunId,
    deleteResult.fetching,
    handleReplayCashChange,
    handleDelete,
    handleDeleteMany,
    handleReplayPortfolioSourceChange,
    handleReplayPositionAdd,
    handleReplayPositionChange,
    handleReplayPositionRemove,
    manualCash,
    manualCashNumber,
    manualPositions,
    history,
    historyResult.fetching,
    onActiveViewChange,
    portfolioSource,
    preparation,
    preparationResult.fetching,
    previousTradingDate,
    replay,
    refreshHistory,
    snapshotPortfolioValid,
  ]);

  React.useEffect(() => {
    onSidebarContextChange(replaySidebarContext);
  }, [onSidebarContextChange, replaySidebarContext]);

  React.useEffect(
    () => () => onSidebarContextChange(null),
    [onSidebarContextChange]
  );

  const fallbackPollInterval = replayFallbackPollInterval(
    graphqlWsStatus,
    hasActiveReplay
  );
  const pendingRefreshRef = React.useRef({
    history: false,
    replay: false,
    cycles: false,
  });
  const refreshTimerRef = React.useRef<number | undefined>(undefined);
  const latestRevisionRef = React.useRef(new Map<string, string>());

  React.useEffect(() => {
    if (appliedTradingCalendarRef.current || replayTradingDays.length === 0) {
      return;
    }
    appliedTradingCalendarRef.current = true;
    const range = replayDatePreset(5, replayTradingDays);
    setStartDate(range.start);
    setEndDate(range.end);
  }, [replayTradingDays]);

  React.useEffect(() => {
    if (!preparation || portfolioDirty) return;
    if (!preparation.requiresManualPortfolio && preparation.snapshotId) {
      setPortfolioSource('SNAPSHOT');
      setManualCash(String(preparation.initialCash));
      setManualPositions(
        preparation.positions.map(item => ({
          stockCode: item.stockCode,
          instrumentName: item.instrumentName,
          volume: String(item.volume),
          avgPrice: String(item.avgPrice),
        }))
      );
      return;
    }
    setPortfolioSource('MANUAL');
    setManualCash('');
    setManualPositions([]);
  }, [portfolioDirty, preparation]);

  React.useEffect(() => {
    setPortfolioDirty(false);
  }, [startDate]);

  const scheduleRefresh = React.useCallback(
    (targets: { history: boolean; replay: boolean; cycles: boolean }) => {
      pendingRefreshRef.current.history ||= targets.history;
      pendingRefreshRef.current.replay ||= targets.replay;
      pendingRefreshRef.current.cycles ||= targets.cycles;
      if (refreshTimerRef.current !== undefined) return;
      refreshTimerRef.current = window.setTimeout(() => {
        const pending = pendingRefreshRef.current;
        pendingRefreshRef.current = {
          history: false,
          replay: false,
          cycles: false,
        };
        refreshTimerRef.current = undefined;
        if (pending.history) {
          refreshHistory({ requestPolicy: 'network-only' });
        }
        if (pending.replay && activeRunId) {
          refreshReplay({ requestPolicy: 'network-only' });
        }
        if (pending.cycles && activeRunId) {
          refreshCycles({ requestPolicy: 'network-only' });
        }
      }, 100);
    },
    [activeRunId, refreshCycles, refreshHistory, refreshReplay]
  );

  React.useEffect(
    () => () => {
      if (refreshTimerRef.current !== undefined) {
        window.clearTimeout(refreshTimerRef.current);
      }
    },
    []
  );

  React.useEffect(() => {
    const notice = replayUpdateResult.data?.tTradeReplayUpdates;
    if (!notice) return;
    const previousRevision = latestRevisionRef.current.get(notice.runId);
    if (!isNewerReplayRevision(previousRevision, notice.revision)) return;
    latestRevisionRef.current.set(notice.runId, notice.revision);
    scheduleRefresh(
      replayNoticeRefreshTargets(String(notice.kind), notice.runId, activeRunId)
    );
  }, [activeRunId, replayUpdateResult.data, scheduleRefresh]);

  React.useEffect(() => {
    if (!accountId || fallbackPollInterval === null) return;
    const poll = () => {
      if (document.visibilityState !== 'visible') return;
      refreshHistory({ requestPolicy: 'network-only' });
      if (hasActiveReplay && activeRunId) {
        refreshReplay({ requestPolicy: 'network-only' });
      }
    };
    poll();
    const timer = window.setInterval(poll, fallbackPollInterval);
    return () => window.clearInterval(timer);
  }, [
    accountId,
    activeRunId,
    fallbackPollInterval,
    hasActiveReplay,
    refreshHistory,
    refreshReplay,
  ]);

  const previousWsStatusRef = React.useRef(graphqlWsStatus);
  React.useEffect(() => {
    const reconnected =
      graphqlWsStatus === 'connected' &&
      previousWsStatusRef.current !== 'connected';
    previousWsStatusRef.current = graphqlWsStatus;
    if (reconnected) {
      scheduleRefresh({
        history: true,
        replay: Boolean(activeRunId),
        cycles: Boolean(activeRunId),
      });
    }
  }, [activeRunId, graphqlWsStatus, scheduleRefresh]);

  React.useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState !== 'visible') return;
      scheduleRefresh({
        history: true,
        replay: Boolean(activeRunId),
        cycles: Boolean(activeRunId && !hasActiveReplay),
      });
    };
    document.addEventListener('visibilitychange', handleVisibility);
    return () =>
      document.removeEventListener('visibilitychange', handleVisibility);
  }, [activeRunId, hasActiveReplay, scheduleRefresh]);

  const setPreset = (days: 1 | 5 | 20) => {
    const range = replayDatePreset(days, replayTradingDays);
    setStartDate(range.start);
    setEndDate(range.end);
  };

  const handleStart = async () => {
    if (replaySettingsErrors.length > 0) {
      onActiveViewChange('PARAMETERS');
      toast({
        title: '回测参数未通过校验',
        description: replaySettingsErrors[0],
        variant: 'destructive',
      });
      return;
    }
    if (portfolioSource === 'SNAPSHOT' && !snapshotPortfolioValid) {
      toast({
        title: '缺少 D-1 账户快照',
        description: '请选择手工组合，或先准备回放首日前的账户日结快照。',
        variant: 'destructive',
      });
      return;
    }
    if (portfolioSource === 'MANUAL' && !manualPortfolioValid) {
      toast({
        title: '初始回测账户不完整',
        description:
          '请填写非负可用资金，并至少配置一只不少于 100 股的有效持仓。',
        variant: 'destructive',
      });
      return;
    }
    const portfolio =
      portfolioSource === 'SNAPSHOT'
        ? {
            source: TTradeReplayPortfolioSource.Snapshot,
            asOf: `${preparation?.snapshotDate}T15:00:00`,
            snapshotId: preparation?.snapshotId,
            positions: [],
          }
        : {
            source: TTradeReplayPortfolioSource.Manual,
            asOf: `${previousTradingDate}T15:00:00`,
            cashAvailable: manualCashNumber,
            positions: manualPositions.map(item => ({
              stockCode: item.stockCode,
              volume: Number(item.volume),
              avgPrice: Number(item.avgPrice),
            })),
          };
    const input = {
      accountId,
      startTime,
      endTime,
      portfolio,
      ...replaySettingsInput(form, costs),
    };
    const identity = JSON.stringify(input);
    const previousOperation = replayOperationRef.current;
    if (previousOperation?.blocked) {
      toast({
        title: '回放操作不可恢复',
        description: '浏览器中的未决回放记录不可用，请清理后再发起操作。',
        variant: 'destructive',
      });
      return;
    }
    if (
      previousOperation?.uncertain &&
      previousOperation.identity !== identity
    ) {
      toast({
        title: '上一笔回放结果未知',
        description: '请先恢复原回放结果，不能用新的参数重复启动。',
        variant: 'destructive',
      });
      return;
    }
    const operation =
      previousOperation?.identity === identity
        ? previousOperation
        : {
            identity,
            idempotencyKey: replayIdempotencyKey(),
            uncertain: false,
          };
    const pendingOperation = { ...operation, uncertain: true };
    if (!persistUncertainOperation(`replay:${accountId}`, pendingOperation)) {
      replayOperationRef.current = { ...pendingOperation, blocked: true };
      toast({
        title: '无法安全记录回放操作',
        description: '未写入浏览器未决记录，本次回放未发送。',
        variant: 'destructive',
      });
      return;
    }
    replayOperationRef.current = pendingOperation;
    let responseReceived = false;
    try {
      const result = await startReplay({
        input: { ...input, idempotencyKey: operation.idempotencyKey },
      });
      responseReceived = true;
      const payload = result.data?.startTTradeReplay;
      // Keep the operation key while the Engine outcome is unknown, including
      // a transport error with no GraphQL payload. A terminal response marks
      // the next click as a new user action.
      const uncertain =
        !payload ||
        String(payload.code || '').endsWith('_COMMAND_PENDING') ||
        String(payload.code || '').endsWith('_OUTCOME_UNKNOWN');
      if (uncertain) {
        replayOperationRef.current = pendingOperation;
        persistUncertainOperation(`replay:${accountId}`, pendingOperation);
      } else {
        replayOperationRef.current = null;
        clearPersistedOperation(`replay:${accountId}`);
      }
      if (!payload?.success || !payload.replay?.runId) {
        throw new Error(
          payload?.message || result.error?.message || '启动失败'
        );
      }
      setActiveRunId(payload.replay.runId);
      toast({ title: '历史回放已启动', description: payload.message });
      refreshHistory({ requestPolicy: 'network-only' });
    } catch (error) {
      if (!responseReceived) {
        replayOperationRef.current = pendingOperation;
        persistUncertainOperation(`replay:${accountId}`, pendingOperation);
      }
      toast({
        title: '无法启动历史回放',
        description: error instanceof Error ? error.message : '请求失败',
        variant: 'destructive',
      });
    }
  };

  const handleCancel = async () => {
    if (!activeRunId) return;
    try {
      const result = await cancelReplay({ runId: activeRunId });
      const payload = result.data?.cancelTTradeReplay;
      if (!payload?.success) {
        throw new Error(
          payload?.message || result.error?.message || '取消失败'
        );
      }
      toast({ title: '回放已取消', description: payload.message });
      refreshReplay({ requestPolicy: 'network-only' });
      refreshHistory({ requestPolicy: 'network-only' });
    } catch (error) {
      toast({
        title: '无法取消回放',
        description: error instanceof Error ? error.message : '请求失败',
        variant: 'destructive',
      });
    }
  };

  const chartData = (replay?.curve || []).map(point => ({
    time: new Date(point.timestamp).toLocaleString('zh-CN', {
      hour: '2-digit',
      minute: '2-digit',
      month: '2-digit',
      day: '2-digit',
      hour12: false,
    }),
    账户收益: Number(point.returnPct.toFixed(4)),
    不做T基准: Number(point.passiveReturnPct.toFixed(4)),
    做T增量: Number(point.excessReturnPct.toFixed(4)),
  }));

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 flex-col">
        <TTradePanelBoundary
          name={
            {
              OVERVIEW: '回放总览',
              PARAMETERS: '回放参数',
              ACCOUNT: '回测账户',
              SIGNALS: '回放信号',
              AUDIT: '回放审计',
              POSITIONS: '回放仓位与批次',
              EVENTS: '回放运行动态',
            }[activeView]
          }
        >
          {activeView === 'OVERVIEW' ? (
            <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
              {!activeRunId && (
                <section className="border-b border-white/[0.06] bg-[#0a1728] p-ui-section">
                  <div className="flex flex-wrap items-end justify-between gap-ui-section">
                    <div>
                      <div className="flex items-center gap-2 text-ui-body font-black text-slate-100">
                        <FlaskConical className="h-4 w-4 text-cyan-300" />
                        历史回放测试
                        <span
                          aria-live="polite"
                          className={cn(
                            'border px-1.5 py-0.5 text-ui-micro font-bold',
                            graphqlWsStatus === 'connected'
                              ? 'border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-300'
                              : 'border-amber-400/20 bg-amber-400/[0.06] text-amber-300'
                          )}
                        >
                          {graphqlWsStatus === 'connected'
                            ? '实时推送'
                            : '轮询恢复'}
                        </span>
                      </div>
                      <p className="mt-1 text-ui-caption text-slate-500">
                        使用同一做 T
                        策略和交易风控；测试信号自动确认，不会提交实盘委托。
                      </p>
                    </div>
                    <div className="flex flex-wrap items-end gap-2">
                      <div>
                        <Label
                          htmlFor="replay-start"
                          className="text-ui-caption text-slate-500"
                        >
                          开始日期
                        </Label>
                        <Input
                          id="replay-start"
                          type="date"
                          value={startDate}
                          max={endDate}
                          onChange={event => setStartDate(event.target.value)}
                          className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-ui-label"
                        />
                      </div>
                      <div>
                        <Label
                          htmlFor="replay-end"
                          className="text-ui-caption text-slate-500"
                        >
                          结束日期
                        </Label>
                        <Input
                          id="replay-end"
                          type="date"
                          value={endDate}
                          min={startDate}
                          onChange={event => setEndDate(event.target.value)}
                          className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-ui-label"
                        />
                      </div>
                      <div className="flex h-8 overflow-hidden border border-white/10">
                        {([1, 5, 20] as const).map(days => (
                          <button
                            key={days}
                            type="button"
                            onClick={() => setPreset(days)}
                            className="cursor-pointer border-r border-white/10 px-2.5 text-ui-caption font-bold text-slate-400 transition-colors last:border-r-0 hover:bg-white/[0.06] hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/60"
                          >
                            {days}日
                          </button>
                        ))}
                      </div>
                      <Button
                        type="button"
                        size="sm"
                        onClick={handleStart}
                        disabled={
                          !accountId ||
                          !preparation ||
                          (portfolioSource === 'SNAPSHOT'
                            ? !snapshotPortfolioValid
                            : !manualPortfolioValid) ||
                          replaySettingsErrors.length > 0 ||
                          startResult.fetching ||
                          history.some(item =>
                            ['PENDING', 'RUNNING', 'STARTING'].includes(
                              item.status
                            )
                          )
                        }
                        className="h-8 rounded-sm bg-cyan-500 px-3 text-ui-caption font-black text-slate-950 hover:bg-cyan-400"
                      >
                        {startResult.fetching ? (
                          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                        ) : (
                          <Play className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        启动回放
                      </Button>
                    </div>
                  </div>

                  <div
                    className={cn(
                      'mt-3 flex items-start gap-2 border px-3 py-2 text-ui-caption',
                      preparation?.requiresManualPortfolio ||
                        preparationResult.error
                        ? 'border-amber-400/20 bg-amber-400/[0.06] text-amber-100'
                        : 'border-cyan-400/15 bg-cyan-400/[0.04] text-cyan-100'
                    )}
                  >
                    {preparationResult.fetching ? (
                      <Loader2 className="mt-0.5 h-3.5 w-3.5 shrink-0 animate-spin motion-reduce:animate-none" />
                    ) : preparation?.requiresManualPortfolio ||
                      preparationResult.error ? (
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    ) : (
                      <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    )}
                    <span>
                      {preparationResult.error?.message ||
                        preparation?.message ||
                        '正在读取回放开始日前的账户快照…'}
                      {preparation?.snapshotDate && (
                        <span className="ml-2 font-mono text-slate-400">
                          快照 {preparation.snapshotDate} ·{' '}
                          {preparation.positions.length} 只持仓 · 总资产 ¥
                          {formatNumber(preparation.initialTotalAsset)}
                        </span>
                      )}
                    </span>
                  </div>
                </section>
              )}

              {activeRunId && replay ? (
                <>
                  <section className="border-b border-white/[0.06] p-ui-section">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="flex items-center gap-3">
                        <span
                          className={cn(
                            'border px-2 py-1 text-ui-caption font-black',
                            replay.status === 'COMPLETED'
                              ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
                              : replay.status === 'ERROR'
                                ? 'border-rose-400/25 bg-rose-400/10 text-rose-200'
                                : 'border-cyan-400/25 bg-cyan-400/10 text-cyan-200'
                          )}
                        >
                          {replayStatusLabel(replay.status)}
                        </span>
                        <span className="font-mono text-ui-caption text-slate-500">
                          {replay.runId.slice(0, 8)} ·{' '}
                          {formatNumber(replay.progressPct, 1)}%
                        </span>
                        {replay.processedUntil && (
                          <span className="font-mono text-ui-caption text-slate-600">
                            已处理 {formatTime(replay.processedUntil)}
                          </span>
                        )}
                        <span className="text-ui-caption text-slate-600">
                          {replay.dataQualityMessage}
                        </span>
                      </div>
                      {isRunning && (
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          disabled={cancelResult.fetching}
                          onClick={handleCancel}
                          className="h-control-compact rounded-sm border-rose-400/20 bg-rose-400/[0.04] text-ui-caption text-rose-200 hover:bg-rose-400/10"
                        >
                          {cancelResult.fetching ? (
                            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                          ) : (
                            <Square className="mr-1.5 h-3 w-3" />
                          )}
                          取消回放
                        </Button>
                      )}
                    </div>
                    {replay.phase && (
                      <div className="mt-3 border border-cyan-400/15 bg-cyan-400/[0.035] px-3 py-2.5">
                        <div className="flex items-center justify-between gap-3 text-ui-caption">
                          <span className="font-black text-cyan-100">
                            {replayPhaseLabel(replay.phase)}
                          </span>
                          <span className="font-mono text-cyan-200/65">
                            {formatNumber(replay.phaseProgressPct, 0)}%
                          </span>
                        </div>
                        <Progress
                          value={replay.phaseProgressPct}
                          className="mt-2 h-1 bg-white/[0.06]"
                        />
                        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-ui-caption text-slate-500">
                          <span>
                            {replay.phaseMessage || '正在准备回测任务'}
                          </span>
                          {replay.dataPreparation?.currentInstrument && (
                            <span className="font-mono text-slate-600">
                              {replay.dataPreparation.currentInstrument}
                              {replay.dataPreparation.currentStartDate
                                ? ` · ${replay.dataPreparation.currentStartDate}~${
                                    replay.dataPreparation.currentEndDate ||
                                    replay.dataPreparation.currentStartDate
                                  }`
                                : ''}
                            </span>
                          )}
                        </div>
                      </div>
                    )}
                    {replay.errorMessage && (
                      <div className="mt-3 flex items-center gap-2 border border-rose-400/20 bg-rose-400/[0.06] px-3 py-2 text-ui-caption text-rose-100">
                        <AlertTriangle className="h-3.5 w-3.5" />
                        {replay.errorMessage}
                      </div>
                    )}
                  </section>

                  <section className="grid gap-2 border-b border-white/[0.06] p-ui-section sm:grid-cols-2 xl:grid-cols-4">
                    <MetricCard
                      icon={CircleDollarSign}
                      label="做 T 税费后增量"
                      tone={
                        (replay.summary?.tNetProfit || 0) >= 0
                          ? 'marketUp'
                          : 'marketDown'
                      }
                      value={
                        replay.summary
                          ? `¥${formatNumber(replay.summary.tNetProfit)}`
                          : '--'
                      }
                    />
                    <MetricCard
                      icon={TrendingUp}
                      label="相对不做 T 超额"
                      tone={
                        (replay.summary?.excessReturnPct || 0) >= 0
                          ? 'marketUp'
                          : 'marketDown'
                      }
                      value={
                        replay.summary
                          ? `${formatNumber(replay.summary.excessReturnPct)}%`
                          : '--'
                      }
                    />
                    <MetricCard
                      icon={Check}
                      label="完成批次 / 胜率"
                      tone="slate"
                      value={
                        replay.summary
                          ? `${replay.summary.completedCycles} / ${
                              replay.summary.completedCycles > 0
                                ? `${formatNumber(replay.summary.winRatePct, 1)}%`
                                : '无样本'
                            }`
                          : '--'
                      }
                    />
                    <MetricCard
                      icon={WalletCards}
                      label="交易税费"
                      tone="amber"
                      value={
                        replay.summary
                          ? `¥${formatNumber(replay.summary.totalFees)}`
                          : '--'
                      }
                    />
                  </section>

                  {replay.summary && (
                    <section className="border-b border-white/[0.06] p-ui-section">
                      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <h3 className="flex items-center gap-2 text-ui-label font-black text-slate-200">
                            <Gauge className="h-4 w-4 text-cyan-300" />
                            资金效率与期末清算
                          </h3>
                          <p className="mt-1 text-ui-caption text-slate-600">
                            资金利用率按 4
                            小时交易日折算并按实际买入资金加权；卖出等待越久，利用率越低。
                          </p>
                        </div>
                        <span
                          className={cn(
                            'border px-2 py-1 text-ui-caption font-black',
                            replay.summary.liquidationFailedCycles > 0
                              ? 'border-rose-400/25 bg-rose-400/10 text-rose-200'
                              : 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
                          )}
                        >
                          期末清算 {replay.summary.forcedExitCycles} 批 · 失败{' '}
                          {replay.summary.liquidationFailedCycles} 批
                        </span>
                      </div>
                      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                        <MetricCard
                          icon={Gauge}
                          label="等待折损后利用率"
                          tone="sky"
                          value={`${formatNumber(replay.summary.capitalUtilizationPct, 1)}%`}
                        />
                        <MetricCard
                          icon={WalletCards}
                          label="平均占用 / 可用率"
                          tone="slate"
                          value={`¥${formatNumber(replay.summary.averageOccupiedCapital)} / ${formatNumber(replay.summary.capitalAvailabilityPct, 1)}%`}
                        />
                        <MetricCard
                          icon={RefreshCw}
                          label="累计 / 日均周转"
                          tone="emerald"
                          value={`${formatNumber(replay.summary.capitalTurnoverTimes)}× / ${formatNumber(replay.summary.capitalTurnoverPerTradingDay)}×`}
                        />
                        <MetricCard
                          icon={Hourglass}
                          label="平均 / 最长等待"
                          tone="amber"
                          value={`${formatNumber(replay.summary.averageHoldingHours, 1)}h / ${formatNumber(replay.summary.maxHoldingHours, 1)}h`}
                        />
                      </div>
                    </section>
                  )}

                  {replay.report && (
                    <section className="border-b border-white/[0.06] bg-cyan-400/[0.025] p-ui-section">
                      <div className="flex items-start gap-3">
                        <FileCheck2 className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" />
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-ui-label font-black text-slate-100">
                              回放报告 · {replay.report.conclusionCode}
                            </h3>
                            <span className="border border-cyan-400/20 bg-cyan-400/[0.08] px-1.5 py-0.5 text-ui-micro font-black text-cyan-200">
                              {replay.report.status === 'GENERATED'
                                ? 'HTML / JSON 已生成'
                                : '报告生成失败'}
                            </span>
                          </div>
                          <p className="mt-1.5 text-ui-caption leading-5 text-slate-400">
                            {replay.report.conclusion}
                          </p>
                          <p className="mt-1 font-mono text-ui-micro text-slate-700">
                            {replay.report.generatedAt
                              ? formatTime(replay.report.generatedAt)
                              : '--'}{' '}
                            · {replay.report.htmlArtifact || '--'} ·{' '}
                            {replay.report.jsonArtifact || '--'}
                          </p>
                        </div>
                      </div>
                    </section>
                  )}

                  <section className="border-b border-white/[0.06] p-ui-section">
                    <div className="mb-3 flex items-center justify-between">
                      <div>
                        <h3 className="flex items-center gap-2 text-ui-label font-black text-slate-200">
                          <BarChart3 className="h-4 w-4 text-cyan-300" />
                          账户收益与不做 T 基准
                        </h3>
                        <p className="mt-1 text-ui-caption text-slate-600">
                          同一初始现金和持仓按历史价格估值，差值为做 T
                          税费后增量。
                        </p>
                      </div>
                    </div>
                    <div className="h-64 border border-white/[0.06] bg-[#07111f] p-2">
                      {chartData.length > 1 ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={chartData}>
                            <CartesianGrid
                              stroke="rgba(148,163,184,0.08)"
                              vertical={false}
                            />
                            <XAxis
                              dataKey="time"
                              minTickGap={40}
                              tick={{ fill: '#64748b', fontSize: 9 }}
                              axisLine={{ stroke: 'rgba(148,163,184,0.12)' }}
                              tickLine={false}
                            />
                            <YAxis
                              width={48}
                              tickFormatter={value => `${value}%`}
                              tick={{ fill: '#64748b', fontSize: 9 }}
                              axisLine={false}
                              tickLine={false}
                            />
                            <Tooltip
                              contentStyle={{
                                background: '#0b1628',
                                border: '1px solid rgba(148,163,184,0.18)',
                                borderRadius: 2,
                                fontSize: 11,
                              }}
                              formatter={value =>
                                `${formatNumber(Number(value), 3)}%`
                              }
                            />
                            <Legend wrapperStyle={{ fontSize: 10 }} />
                            <Line
                              type="monotone"
                              dataKey="账户收益"
                              stroke="#22d3ee"
                              dot={false}
                              strokeWidth={1.5}
                            />
                            <Line
                              type="monotone"
                              dataKey="不做T基准"
                              stroke="#94a3b8"
                              dot={false}
                              strokeWidth={1.2}
                            />
                            <Line
                              type="monotone"
                              dataKey="做T增量"
                              stroke="#fb7185"
                              dot={false}
                              strokeWidth={1.4}
                            />
                          </LineChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-full flex-col items-center justify-center text-center text-ui-caption text-slate-600">
                          <History className="mb-2 h-6 w-6 text-slate-700" />
                          回放产生数据后显示收益曲线
                        </div>
                      )}
                    </div>
                  </section>
                </>
              ) : activeRunId ? (
                <div
                  role="status"
                  className="flex min-h-[360px] items-center justify-center text-ui-label text-slate-500"
                >
                  <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
                  正在读取回放详情…
                </div>
              ) : (
                <div className="p-ui-section">
                  <React.Suspense fallback={tTradeReplayAccountFallback}>
                    <TTradeReplayAccountPanel context={replaySidebarContext} />
                  </React.Suspense>
                </div>
              )}
            </div>
          ) : activeView === 'PARAMETERS' ? (
            <React.Suspense fallback={tTradeReplaySettingsFallback}>
              {activeRunId && frozenForm && frozenCosts ? (
                <TTradeReplayFrozenSettings
                  costs={frozenCosts}
                  differenceCount={frozenSettingsDifference}
                  form={frozenForm}
                  onCopy={() => {
                    onCopySettings(frozenForm, frozenCosts);
                    setActiveRunId('');
                    toast({
                      title: '已复制历史参数',
                      description: '当前参数已成为下一次回测草稿。',
                    });
                  }}
                  onRestore={() => {
                    void onRestoreSettings().then(restored => {
                      if (restored) setActiveRunId('');
                    });
                  }}
                  restoring={restoringSettings}
                />
              ) : (
                <TTradeReplaySettingsEditor
                  costs={costs}
                  differenceCount={replaySettingsDifference}
                  errors={replaySettingsErrors}
                  form={form}
                  liveConfigVersion={liveConfigVersion}
                  liveSettingsStale={liveSettingsStale}
                  onCostChange={onCostChange}
                  onFieldChange={onFieldChange}
                  onRestore={() => void onRestoreSettings()}
                  onSignalPolicyChange={onSignalPolicyChange}
                  restoring={restoringSettings}
                />
              )}
            </React.Suspense>
          ) : activeView === 'ACCOUNT' ? (
            <div className="min-h-0 flex-1 overflow-y-auto p-ui-section custom-scrollbar">
              <React.Suspense fallback={tTradeReplayAccountFallback}>
                <TTradeReplayAccountPanel context={replaySidebarContext} />
              </React.Suspense>
            </div>
          ) : activeView === 'SIGNALS' ? (
            <div className="min-h-0 flex-1 overflow-hidden">
              <TTradeReplaySignals
                controller={replayEvidence}
                instrumentNames={replayInstrumentNames}
                hasReplay={Boolean(activeRunId && replay?.backtestId)}
                onViewAudit={eventKey => {
                  replayEvidence.setAuditFilters({ eventKey });
                  onActiveViewChange('AUDIT');
                }}
              />
            </div>
          ) : activeView === 'AUDIT' ? (
            <div className="min-h-0 flex-1 overflow-hidden">
              <TTradeReplayDecisionAudit
                controller={replayEvidence}
                instrumentNames={replayInstrumentNames}
                hasReplay={Boolean(activeRunId && replay?.backtestId)}
                onViewSignal={eventKey => {
                  replayEvidence.focusSignalEvent(eventKey);
                  onActiveViewChange('SIGNALS');
                }}
              />
            </div>
          ) : activeView === 'POSITIONS' ? (
            <div className="min-h-0 flex-1 overflow-hidden">
              <React.Suspense fallback={tTradePositionsFallback}>
                <TTradePositionsView
                  batches={replayPositionBatches}
                  error={cyclesResult.error?.message}
                  events={replayActivityEvents}
                  focusBatchId={positionFocusBatchId}
                  historyScopeKey={activeRunId}
                  instrumentNames={replayInstrumentNames}
                  loading={cyclesResult.fetching}
                  mode="REPLAY"
                  onFocusBatchHandled={() => setPositionFocusBatchId(null)}
                  onRefresh={refreshReplayFacts}
                  onViewActivity={batchId => {
                    setActivityBatchFilter(batchId);
                    onActiveViewChange('EVENTS');
                  }}
                />
              </React.Suspense>
            </div>
          ) : (
            <div className="min-h-0 flex-1 overflow-hidden">
              <TTradeActivityView
                backtestId={replay?.backtestId}
                batchError={cyclesResult.error?.message}
                batches={replayActivityBatches}
                eventError={
                  replayEvidence.auditError ||
                  (replayEvidence.auditPage?.evidence.availability ===
                  'UNAVAILABLE'
                    ? replayEvidenceUnavailableMessage(
                        replayEvidence.auditPage.evidence.reasonCode
                      )
                    : undefined)
                }
                events={replayActivityEvents}
                evaluations={replayActivityEvaluations}
                focusedBatchId={activityBatchFilter}
                hasMoreEvents={Boolean(
                  replayEvidence.auditPage?.pageInfo.hasNextPage
                )}
                hasMoreSignals={Boolean(
                  replayEvidence.signalPage?.pageInfo.hasNextPage
                )}
                includeDiagnostics={includeActivityDiagnostics}
                instrumentNames={replayInstrumentNames}
                isRunning={isRunning}
                loading={
                  replayResult.fetching ||
                  cyclesResult.fetching ||
                  replayEvidence.signalsLoading ||
                  replayEvidence.auditLoading
                }
                loadingMore={
                  replayEvidence.signalsLoading || replayEvidence.auditLoading
                }
                onIncludeDiagnosticsChange={setIncludeActivityDiagnostics}
                onFocusedBatchIdClear={() => setActivityBatchFilter(null)}
                onLoadMore={() => {
                  if (replayEvidence.signalPage?.pageInfo.hasNextPage)
                    replayEvidence.loadMoreSignals();
                  if (replayEvidence.auditPage?.pageInfo.hasNextPage)
                    replayEvidence.loadMoreAudit();
                }}
                onRefresh={refreshReplayFacts}
                onViewBatch={batchId => {
                  setPositionFocusBatchId(batchId);
                  onActiveViewChange('POSITIONS');
                }}
                onViewCurrent={() => onActiveViewChange('SIGNALS')}
                runId={activeRunId}
                runMode="BACKTEST"
                signalError={
                  replayEvidence.signalError ||
                  (replayEvidence.signalPage?.evidence.availability ===
                  'UNAVAILABLE'
                    ? replayEvidenceUnavailableMessage(
                        replayEvidence.signalPage.evidence.reasonCode
                      )
                    : undefined)
                }
                supplementalItems={replayActivitySupplementalItems}
                wsStatus={graphqlWsStatus}
              />
            </div>
          )}
        </TTradePanelBoundary>
      </div>
    </div>
  );
}
