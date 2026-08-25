import type { DocumentNode } from 'graphql';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  CalendarDays,
  Check,
  CircleDollarSign,
  Clock3,
  FileCheck2,
  FlaskConical,
  Gauge,
  History,
  Hourglass,
  ListChecks,
  Loader2,
  Plus,
  Play,
  Radar,
  RefreshCw,
  Save,
  Settings2,
  ShieldCheck,
  ShieldAlert,
  Square,
  TrendingUp,
  WalletCards,
  X,
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
import {
  useClient,
  useMutation,
  useQuery,
  useSubscription,
  type OperationContext,
} from 'urql';

import {
  StudioWorkbench,
  type StudioMode,
} from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';
import { getShanghaiDateKey } from '@/components/trading-chart/utils/time-utils';
import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useGraphqlWsStatus,
  type GraphqlWsStatus,
} from '@/core/graphql/ws-status';
import { StrategyInstrumentSelector } from '@/features/strategies/components/StrategyInstrumentSelector';
import { useTradingSafety } from '@/features/trading-safety';
import { useFragment as readFragment } from '@/generated/gql';
import {
  TTradeReplayPortfolioSource,
  TTradeRolloutTarget,
  TTradeTimeExitMode,
  type TTradeBatch,
  type TTradeBatchEvent,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { useTradingDays } from '@/hooks/useTradingDays';
import { tradingAccountConfig } from '@/shared/utils/env';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import { useLatestMarketQuotes } from '../hooks/useRealTimeHoldings';
import {
  ApproveTTradeEntryV3Mutation,
  CancelTTradeReplayMutation,
  CancelTTradeOrderMutation,
  ActivateTTradeLiveMutation,
  ImportTTradeExternalEntryMutation,
  ReconcileTTradeGlobalMonitorMutation,
  PauseTTradeEntriesMutation,
  PreviewTTradeSignalPolicyMutation,
  RecordTTradeClientTelemetryMutation,
  RejectTTradeEntryV3Mutation,
  SaveTTradeGlobalMonitorMutation,
  StartTTradeReplayMutation,
  SyncTTradeSourceOrdersMutation,
  TTradeGlobalMonitorQuery,
  TTradeInstrumentNameQuery,
  TTradeBatchesPageQuery,
  TTradeBatchEventsPageQuery,
  TTradeReplayCyclesQuery,
  TTradeReplayHistoryQuery,
  TTradeReplayPreparationQuery,
  TTradeReplayQuery,
  TTradeReplayUpdatesSubscription,
  TTradeCandidateTraceQuery,
  TTradeSignalDiagnosticsQuery,
  TTradeSignalEvaluationsQuery,
  TTradeSignalPolicyFieldsFragment,
  TTradeSignalSnapshotFieldsFragment,
  TTradeUpdatesSubscription,
  TTradeSourceOrdersQuery,
} from '../hooks/useTTradeGlobal';

import {
  createRollingDiagnosticRange,
  hasCandidateTraceIdentity,
} from './t-trade-global/clientTrust';
import {
  canApproveSnapshot,
  createSignalSnapshotRefreshCoordinator,
  createTTradeClientTelemetryReporter,
  type SignalSnapshot,
} from './t-trade-global/monitoring';
import {
  clearPersistedOperation,
  persistUncertainOperation,
  readUncertainOperation,
  type ClientOperationRef,
} from './t-trade-global/operationPersistence';
import { readinessStageLabel } from './t-trade-global/readiness';
import {
  isNewerReplayRevision,
  replayFallbackPollInterval,
  replayNoticeRefreshTargets,
  stableValueByKey,
} from './t-trade-global/replaySync';
import {
  isAppliedTTradeGlobalSave,
  tTradeGlobalSaveToastTitle,
} from './t-trade-global/saveOutcome';
import {
  createTTradeServerTruthRefreshPolicy,
  T_TRADE_SERVER_TRUTH_AUDIT_INTERVAL_MS,
} from './t-trade-global/serverTruthRecovery';
import {
  defaultSignalPolicyForm,
  localSignalPolicyErrors,
  signalPolicyForm,
  signalPolicyInput,
  type SignalPolicyLike,
} from './t-trade-global/signalPolicy';
import {
  TTradeHealthConsole,
  TTradeLiveBoard,
  type SignalEvaluationLike,
} from './t-trade-global/TTradeLiveMonitor';
import { TTradeSignalDiagnosticsPanel } from './t-trade-global/TTradeSignalDiagnostics';
import {
  TTradeSignalPolicyEditor,
  type SignalPolicyPreviewLike,
} from './t-trade-global/TTradeSignalPolicyEditor';
import {
  type CandidateTraceSelection,
  TTradeSignalsView,
} from './t-trade-global/TTradeSignalsView';
import type {
  SettingsForm,
  SignalPolicyForm,
  SignalPolicyFormValue,
  TTradeStudioMode,
} from './t-trade-global/types';
import { useLiveQuoteHistory } from './t-trade-global/useLiveQuoteHistory';
import {
  batchStatusLabels,
  formatNumber,
  formatSignedPercent,
  formatTime,
  hasInstrumentName,
  integerValue,
  numberValue,
  replayDatePreset,
  replayIdempotencyKey,
  replayPhaseLabel,
  replayStatusLabel,
  resolveInstrumentName,
} from './t-trade-global/utils';

const tTradeModes: StudioMode[] = [
  { id: 'MONITOR', icon: Radar, label: '总览' },
  { id: 'SIGNALS', icon: Activity, label: '信号' },
  { id: 'DIAGNOSTICS', icon: BarChart3, label: '诊断' },
  { id: 'POSITIONS', icon: WalletCards, label: '做T仓位' },
  { id: 'EVENTS', icon: ListChecks, label: '订单事件' },
  { id: 'SETTINGS', icon: Settings2, label: '参数' },
];

const defaultForm: SettingsForm = {
  mode: 'paper',
  acknowledged: false,
  targetTradeAmount: '10000',
  maxTradeAmount: '12000',
  maxConcurrentBatches: '3',
  maxTotalTExposurePct: '10',
  targetProfitPct: '2',
  baseFloorPct: '0.5',
  initialGapPct: '1.5',
  trailingGapSlope: '0.25',
  maxGapPct: '3',
  highProfitLockEnabled: true,
  highProfitArmPct: '4',
  highProfitMaxDrawdownPct: '1.2',
  rapidReversalEnabled: true,
  rapidReversalWindowSeconds: '15',
  rapidReversalDrawdownPct: '0.8',
  rapidReversalConfirmTicks: '2',
  hardStopEnabled: false,
  hardStopPct: '-0.8',
  signalPolicy: defaultSignalPolicyForm,
  maxPriceDeviationPct: '0.3',
  limitUpTouchExitEnabled: true,
  limitUpTouchToleranceTicks: '0',
  timeExitMode: TTradeTimeExitMode.Unlimited,
  timeExitTime: '14:50',
  maxHoldingTradingDays: '5',
  cooldownSeconds: '300',
};

function InstrumentNameLabel({
  className,
  knownName,
  stockCode,
}: {
  className?: string;
  knownName?: string | null;
  stockCode: string;
}) {
  const needsLookup = !hasInstrumentName(stockCode, knownName);
  const [result] = useQuery({
    query: TTradeInstrumentNameQuery,
    variables: { stockCode },
    pause: !needsLookup,
    requestPolicy: 'cache-first',
  });
  const instrumentName = resolveInstrumentName(
    stockCode,
    result.data?.instrument?.name,
    knownName
  );
  return <div className={className}>{instrumentName}</div>;
}

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
      <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-[0.12em] opacity-70">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="mt-1.5 font-mono text-lg font-black tabular-nums">
        {value}
      </div>
    </div>
  );
}

function NumericField({
  disabled = false,
  id,
  label,
  onChange,
  suffix,
  value,
}: {
  disabled?: boolean;
  id: string;
  label: string;
  onChange: (value: string) => void;
  suffix?: string;
  value: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-xs font-bold text-slate-400">
        {label}
      </Label>
      <div className="relative">
        <Input
          id={id}
          disabled={disabled}
          inputMode="decimal"
          value={value}
          onChange={event => onChange(event.target.value)}
          className="h-9 rounded-sm border-white/10 bg-[#07111f] pr-10 font-mono text-xs focus-visible:ring-red-500/60 disabled:cursor-not-allowed disabled:opacity-50"
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-[10px] font-bold text-slate-600">
            {suffix}
          </span>
        )}
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

function TTradeReplayPanel({
  accountId,
  form,
}: {
  accountId: string;
  form: SettingsForm;
}) {
  const { toast } = useToast();
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
    Array<{
      stockCode: string;
      instrumentName: string;
      volume: string;
      avgPrice: string;
    }>
  >([]);
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
    variables: { runId: activeRunId, offset: 0, limit: 100 },
    pause: !activeRunId,
    requestPolicy: 'network-only',
  });
  const [startResult, startReplay] = useMutation(StartTTradeReplayMutation);
  const [cancelResult, cancelReplay] = useMutation(CancelTTradeReplayMutation);
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
  const cycles = cyclesPage?.items || [];
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

  React.useEffect(() => {
    if (!activeRunId && history.length > 0) setActiveRunId(history[0].runId);
  }, [activeRunId, history]);

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
      targetTradeAmount: numberValue(form.targetTradeAmount, 10000),
      maxTradeAmount: numberValue(form.maxTradeAmount, 12000),
      maxConcurrentBatches: integerValue(form.maxConcurrentBatches, 3),
      maxTotalTExposurePct: numberValue(form.maxTotalTExposurePct, 10) / 100,
      signalPolicy: signalPolicyInput(form.signalPolicy),
      maxPriceDeviationPct: numberValue(form.maxPriceDeviationPct, 0.3),
      targetProfitPct: numberValue(form.targetProfitPct, 2),
      baseFloorPct: numberValue(form.baseFloorPct, 0.5),
      initialGapPct: numberValue(form.initialGapPct, 1.5),
      trailingGapSlope: numberValue(form.trailingGapSlope, 0.25),
      maxGapPct: numberValue(form.maxGapPct, 3),
      highProfitLockEnabled: form.highProfitLockEnabled,
      highProfitArmPct: numberValue(form.highProfitArmPct, 4),
      highProfitMaxDrawdownPct: numberValue(form.highProfitMaxDrawdownPct, 1.2),
      rapidReversalEnabled: form.rapidReversalEnabled,
      rapidReversalWindowSeconds: integerValue(
        form.rapidReversalWindowSeconds,
        15
      ),
      rapidReversalDrawdownPct: numberValue(form.rapidReversalDrawdownPct, 0.8),
      rapidReversalConfirmTicks: integerValue(
        form.rapidReversalConfirmTicks,
        2
      ),
      limitUpTouchExitEnabled: form.limitUpTouchExitEnabled,
      limitUpTouchToleranceTicks: integerValue(
        form.limitUpTouchToleranceTicks,
        0
      ),
      hardStopEnabled: form.hardStopEnabled,
      hardStopPct: numberValue(form.hardStopPct, -0.8),
      timeExitMode: form.timeExitMode,
      timeExitTime: form.timeExitTime,
      maxHoldingTradingDays: integerValue(form.maxHoldingTradingDays, 5),
      cooldownSeconds: integerValue(form.cooldownSeconds, 300),
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
    <div className="studio-workspace-surface grid h-full min-h-0 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-h-0 overflow-y-auto custom-scrollbar">
        <section className="border-b border-white/[0.06] bg-[#0a1728] p-4">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-sm font-black text-slate-100">
                <FlaskConical className="h-4 w-4 text-cyan-300" />
                历史回放测试
                <span
                  aria-live="polite"
                  className={cn(
                    'border px-1.5 py-0.5 text-[9px] font-bold',
                    graphqlWsStatus === 'connected'
                      ? 'border-emerald-400/20 bg-emerald-400/[0.06] text-emerald-300'
                      : 'border-amber-400/20 bg-amber-400/[0.06] text-amber-300'
                  )}
                >
                  {graphqlWsStatus === 'connected' ? '实时推送' : '轮询恢复'}
                </span>
              </div>
              <p className="mt-1 text-[11px] text-slate-500">
                使用同一做 T
                策略和交易风控；测试信号自动确认，不会提交实盘委托。
              </p>
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <div>
                <Label
                  htmlFor="replay-start"
                  className="text-[10px] text-slate-500"
                >
                  开始日期
                </Label>
                <Input
                  id="replay-start"
                  type="date"
                  value={startDate}
                  max={endDate}
                  onChange={event => setStartDate(event.target.value)}
                  className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-xs"
                />
              </div>
              <div>
                <Label
                  htmlFor="replay-end"
                  className="text-[10px] text-slate-500"
                >
                  结束日期
                </Label>
                <Input
                  id="replay-end"
                  type="date"
                  value={endDate}
                  min={startDate}
                  onChange={event => setEndDate(event.target.value)}
                  className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-xs"
                />
              </div>
              <div className="flex h-8 overflow-hidden border border-white/10">
                {([1, 5, 20] as const).map(days => (
                  <button
                    key={days}
                    type="button"
                    onClick={() => setPreset(days)}
                    className="cursor-pointer border-r border-white/10 px-2.5 text-[10px] font-bold text-slate-400 transition-colors last:border-r-0 hover:bg-white/[0.06] hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/60"
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
                  startResult.fetching ||
                  history.some(item =>
                    ['PENDING', 'RUNNING', 'STARTING'].includes(item.status)
                  )
                }
                className="h-8 rounded-sm bg-cyan-500 px-3 text-[10px] font-black text-slate-950 hover:bg-cyan-400"
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
              'mt-3 flex items-start gap-2 border px-3 py-2 text-[11px]',
              preparation?.requiresManualPortfolio || preparationResult.error
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

          <div className="mt-3 border border-white/[0.08] bg-[#07111f] p-3">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-[11px] font-black text-slate-100">
                  初始回测账户
                </p>
                <p className="mt-0.5 text-[10px] text-slate-500">
                  组合冻结后，系统只为其中的股票准备历史行情。
                </p>
              </div>
              <div className="flex h-8 overflow-hidden border border-white/10">
                <button
                  type="button"
                  disabled={!snapshotPortfolioValid}
                  onClick={() => {
                    setPortfolioSource('SNAPSHOT');
                    setPortfolioDirty(true);
                  }}
                  className={cn(
                    'cursor-pointer px-3 text-[10px] font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70 disabled:cursor-not-allowed disabled:opacity-35',
                    portfolioSource === 'SNAPSHOT'
                      ? 'bg-blue-500/15 text-blue-200'
                      : 'text-slate-500 hover:bg-white/[0.05] hover:text-slate-200'
                  )}
                >
                  D-1 快照
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setPortfolioSource('MANUAL');
                    setPortfolioDirty(true);
                  }}
                  className={cn(
                    'cursor-pointer border-l border-white/10 px-3 text-[10px] font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70',
                    portfolioSource === 'MANUAL'
                      ? 'bg-blue-500/15 text-blue-200'
                      : 'text-slate-500 hover:bg-white/[0.05] hover:text-slate-200'
                  )}
                >
                  手工组合
                </button>
              </div>
            </div>

            {portfolioSource === 'SNAPSHOT' ? (
              <div className="mt-3 grid gap-3 lg:grid-cols-[180px_1fr_auto] lg:items-end">
                <div>
                  <Label className="text-[9px] text-slate-500">可用资金</Label>
                  <div className="mt-1 font-mono text-sm font-black text-slate-100">
                    ¥{formatNumber(preparation?.initialCash || 0)}
                  </div>
                </div>
                <div className="text-[10px] leading-5 text-slate-500">
                  快照 {preparation?.snapshotDate || '--'} ·{' '}
                  {preparation?.positions.length || 0} 只持仓 · 可做 T{' '}
                  {preparation?.positions.filter(item => item.volume >= 100)
                    .length || 0}{' '}
                  只
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setPortfolioSource('MANUAL');
                    setPortfolioDirty(true);
                  }}
                  className="h-8 rounded-sm border-blue-400/25 text-[10px] text-blue-200 hover:bg-blue-500/10"
                >
                  导入并编辑
                </Button>
              </div>
            ) : (
              <div className="mt-3 space-y-3">
                <div className="grid gap-3 md:grid-cols-[180px_1fr] md:items-end">
                  <div>
                    <Label
                      htmlFor="replay-cash"
                      className="text-[9px] text-slate-500"
                    >
                      可用资金
                    </Label>
                    <Input
                      id="replay-cash"
                      type="number"
                      min="0"
                      value={manualCash}
                      onChange={event => {
                        setManualCash(event.target.value);
                        setPortfolioDirty(true);
                      }}
                      className="mt-1 h-8 rounded-sm border-white/10 bg-[#050b16] font-mono text-xs focus-visible:ring-blue-400/70"
                    />
                  </div>
                  <div className="text-[10px] leading-5 text-slate-500">
                    组合时点 {previousTradingDate || '--'}{' '}
                    收盘；开盘前持仓全部按已结算库存处理。
                  </div>
                </div>

                <div className="overflow-hidden border border-white/[0.07]">
                  <div className="grid grid-cols-[minmax(160px,1fr)_110px_120px_36px] gap-2 border-b border-white/[0.07] bg-white/[0.025] px-2 py-1.5 text-[9px] font-black uppercase tracking-[0.1em] text-slate-600">
                    <span>股票</span>
                    <span>持仓股数</span>
                    <span>平均成本</span>
                    <span />
                  </div>
                  {manualPositions.map((item, index) => (
                    <div
                      key={item.stockCode}
                      className="grid grid-cols-[minmax(160px,1fr)_110px_120px_36px] items-center gap-2 border-b border-white/[0.05] px-2 py-1.5 last:border-b-0"
                    >
                      <div className="min-w-0">
                        <div className="truncate text-[10px] font-bold text-slate-200">
                          {item.instrumentName || item.stockCode}
                        </div>
                        <div className="font-mono text-[9px] text-slate-600">
                          {item.stockCode}
                        </div>
                      </div>
                      <Input
                        aria-label={`${item.stockCode} 持仓股数`}
                        type="number"
                        min="1"
                        step="1"
                        value={item.volume}
                        onChange={event => {
                          const value = event.target.value;
                          setManualPositions(rows =>
                            rows.map((row, rowIndex) =>
                              rowIndex === index
                                ? { ...row, volume: value }
                                : row
                            )
                          );
                          setPortfolioDirty(true);
                        }}
                        className="h-7 rounded-sm border-white/10 bg-[#050b16] font-mono text-[10px] focus-visible:ring-blue-400/70"
                      />
                      <Input
                        aria-label={`${item.stockCode} 平均成本`}
                        type="number"
                        min="0.001"
                        step="0.001"
                        value={item.avgPrice}
                        onChange={event => {
                          const value = event.target.value;
                          setManualPositions(rows =>
                            rows.map((row, rowIndex) =>
                              rowIndex === index
                                ? { ...row, avgPrice: value }
                                : row
                            )
                          );
                          setPortfolioDirty(true);
                        }}
                        className="h-7 rounded-sm border-white/10 bg-[#050b16] font-mono text-[10px] focus-visible:ring-blue-400/70"
                      />
                      <button
                        type="button"
                        aria-label={`删除 ${item.stockCode}`}
                        onClick={() => {
                          setManualPositions(rows =>
                            rows.filter((_, rowIndex) => rowIndex !== index)
                          );
                          setPortfolioDirty(true);
                        }}
                        className="flex h-7 w-7 cursor-pointer items-center justify-center text-slate-600 hover:bg-rose-500/10 hover:text-rose-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ))}
                  <div className="p-2">
                    <StrategyInstrumentSelector
                      value=""
                      onChange={(stockCode, stock) => {
                        if (!stockCode) return;
                        if (
                          manualPositions.some(
                            item => item.stockCode === stockCode
                          )
                        ) {
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
                            instrumentName: stock?.name || stockCode,
                            volume: '100',
                            avgPrice: String(stock?.quote?.lastPrice || ''),
                          },
                        ]);
                        setPortfolioDirty(true);
                      }}
                      inputClassName="h-8 rounded-sm border-white/10 bg-[#050b16] text-[10px]"
                      placeholder="搜索股票代码或名称并加入持仓"
                    />
                  </div>
                </div>
                <div className="text-[10px] text-slate-500">
                  {manualPositions.length} 只持仓 ·{' '}
                  {
                    manualPositions.filter(item => Number(item.volume) >= 100)
                      .length
                  }{' '}
                  只可做 T；不足 100 股的持仓仅计入账户权益。
                </div>
              </div>
            )}
          </div>

          {preparation?.requiresManualPortfolio && (
            <div className="mt-2 border border-amber-400/15 bg-amber-400/[0.035] px-3 py-2">
              <div>
                <p className="text-[11px] font-bold text-amber-100">
                  缺少可审计的历史初始组合
                </p>
                <p className="mt-0.5 text-[10px] text-amber-200/55">
                  当前没有可采用的 D-1
                  日结快照，请使用上方手工组合配置回测账户。
                </p>
              </div>
            </div>
          )}
        </section>

        {replay ? (
          <>
            <section className="border-b border-white/[0.06] p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <span
                    className={cn(
                      'border px-2 py-1 text-[10px] font-black',
                      replay.status === 'COMPLETED'
                        ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
                        : replay.status === 'ERROR'
                          ? 'border-rose-400/25 bg-rose-400/10 text-rose-200'
                          : 'border-cyan-400/25 bg-cyan-400/10 text-cyan-200'
                    )}
                  >
                    {replayStatusLabel(replay.status)}
                  </span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {replay.runId.slice(0, 8)} ·{' '}
                    {formatNumber(replay.progressPct, 1)}%
                  </span>
                  {replay.processedUntil && (
                    <span className="font-mono text-[10px] text-slate-600">
                      已处理 {formatTime(replay.processedUntil)}
                    </span>
                  )}
                  <span className="text-[10px] text-slate-600">
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
                    className="h-8 rounded-sm border-rose-400/20 bg-rose-400/[0.04] text-[10px] text-rose-200 hover:bg-rose-400/10"
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
                  <div className="flex items-center justify-between gap-3 text-[10px]">
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
                  <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[10px] text-slate-500">
                    <span>{replay.phaseMessage || '正在准备回测任务'}</span>
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
                <div className="mt-3 flex items-center gap-2 border border-rose-400/20 bg-rose-400/[0.06] px-3 py-2 text-[11px] text-rose-100">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  {replay.errorMessage}
                </div>
              )}
            </section>

            <section className="grid gap-2 border-b border-white/[0.06] p-4 sm:grid-cols-2 xl:grid-cols-4">
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
              <section className="border-b border-white/[0.06] p-4">
                <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="flex items-center gap-2 text-xs font-black text-slate-200">
                      <Gauge className="h-4 w-4 text-cyan-300" />
                      资金效率与期末清算
                    </h3>
                    <p className="mt-1 text-[10px] text-slate-600">
                      资金利用率按 4
                      小时交易日折算并按实际买入资金加权；卖出等待越久，利用率越低。
                    </p>
                  </div>
                  <span
                    className={cn(
                      'border px-2 py-1 text-[10px] font-black',
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
              <section className="border-b border-white/[0.06] bg-cyan-400/[0.025] p-4">
                <div className="flex items-start gap-3">
                  <FileCheck2 className="mt-0.5 h-5 w-5 shrink-0 text-cyan-300" />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-xs font-black text-slate-100">
                        回放报告 · {replay.report.conclusionCode}
                      </h3>
                      <span className="border border-cyan-400/20 bg-cyan-400/[0.08] px-1.5 py-0.5 text-[9px] font-black text-cyan-200">
                        {replay.report.status === 'GENERATED'
                          ? 'HTML / JSON 已生成'
                          : '报告生成失败'}
                      </span>
                    </div>
                    <p className="mt-1.5 text-[11px] leading-5 text-slate-400">
                      {replay.report.conclusion}
                    </p>
                    <p className="mt-1 font-mono text-[9px] text-slate-700">
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

            <section className="border-b border-white/[0.06] p-4">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="flex items-center gap-2 text-xs font-black text-slate-200">
                    <BarChart3 className="h-4 w-4 text-cyan-300" />
                    账户收益与不做 T 基准
                  </h3>
                  <p className="mt-1 text-[10px] text-slate-600">
                    同一初始现金和持仓按历史价格估值，差值为做 T 税费后增量。
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
                  <div className="flex h-full flex-col items-center justify-center text-center text-[11px] text-slate-600">
                    <History className="mb-2 h-6 w-6 text-slate-700" />
                    回放产生数据后显示收益曲线
                  </div>
                )}
              </div>
            </section>

            <section className="p-4">
              <div className="mb-3 flex items-center justify-between">
                <h3 className="text-xs font-black text-slate-200">
                  T 批次明细
                </h3>
                <span className="font-mono text-[10px] text-slate-600">
                  {cyclesPage?.total || 0} 批
                </span>
              </div>
              <div className="overflow-x-auto border border-white/[0.06]">
                <table className="w-full min-w-[1080px] text-left text-[10px]">
                  <thead className="bg-white/[0.025] text-slate-500">
                    <tr>
                      <th className="px-3 py-2">标的 / 批次</th>
                      <th className="px-3 py-2">状态</th>
                      <th className="px-3 py-2 text-right">买入</th>
                      <th className="px-3 py-2 text-right">卖出</th>
                      <th className="px-3 py-2 text-right">税费</th>
                      <th className="px-3 py-2 text-right">
                        等待 / 资金利用率
                      </th>
                      <th className="px-3 py-2 text-right">净增量</th>
                      <th className="px-3 py-2">退出原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cycles.map(cycle => (
                      <tr
                        key={cycle.batchId}
                        className="border-t border-white/[0.05] text-slate-400"
                      >
                        <td className="px-3 py-2">
                          <div className="font-mono font-bold text-slate-200">
                            {cycle.stockCode}
                          </div>
                          <div className="mt-0.5 font-mono text-[9px] text-slate-700">
                            {cycle.batchId.slice(0, 12)}
                          </div>
                        </td>
                        <td className="px-3 py-2">
                          {cycle.status === 'COMPLETED'
                            ? '已完成'
                            : `未平 ${cycle.openVolume} 股`}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {cycle.entryVolume} @{' '}
                          {formatNumber(cycle.entryAvgPrice, 3)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {cycle.exitVolume} @{' '}
                          {formatNumber(cycle.exitAvgPrice, 3)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          ¥{formatNumber(cycle.totalFees)}
                        </td>
                        <td className="px-3 py-2 text-right font-mono">
                          {formatNumber(cycle.holdingHours, 1)}h /{' '}
                          {formatNumber(cycle.capitalUtilizationPct, 1)}%
                        </td>
                        <td
                          className={cn(
                            'px-3 py-2 text-right font-mono font-bold',
                            financialToneClass(cycle.netProfit)
                          )}
                        >
                          ¥{formatNumber(cycle.netProfit)}
                        </td>
                        <td className="px-3 py-2">
                          {cycle.exitReason || '--'}
                          {cycle.forcedExit && (
                            <span className="ml-1.5 border border-amber-400/20 bg-amber-400/[0.06] px-1 py-0.5 text-[8px] text-amber-200">
                              期末清算
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                    {cycles.length === 0 && (
                      <tr>
                        <td
                          colSpan={8}
                          className="px-3 py-8 text-center text-slate-600"
                        >
                          {cyclesResult.fetching
                            ? '正在读取成交批次…'
                            : '暂无成交批次'}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        ) : (
          <div className="flex min-h-[360px] flex-col items-center justify-center px-6 text-center">
            <FlaskConical className="h-10 w-10 text-slate-700" />
            <h2 className="mt-4 text-sm font-black text-slate-300">
              选择日期并启动第一次回放
            </h2>
            <p className="mt-2 max-w-md text-[11px] leading-5 text-slate-600">
              系统将读取开始日前最近的账户日结快照，并用历史 Tick
              数据按时间顺序重放全部合格持仓。
            </p>
          </div>
        )}
      </div>

      <aside
        aria-busy={historyResult.fetching}
        className="min-h-0 overflow-y-auto border-l border-white/[0.06] bg-[#091523] custom-scrollbar"
      >
        <div className="flex h-11 items-center justify-between border-b border-white/[0.06] px-3">
          <span className="text-[10px] font-black uppercase tracking-[0.14em] text-slate-500">
            回放记录
          </span>
          <button
            type="button"
            aria-label="刷新历史回放记录"
            onClick={() => refreshHistory({ requestPolicy: 'network-only' })}
            className="cursor-pointer text-slate-600 transition-colors hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
          >
            <RefreshCw
              className={cn(
                'h-3.5 w-3.5',
                historyResult.fetching &&
                  'animate-spin motion-reduce:animate-none'
              )}
            />
          </button>
        </div>
        {history.map(item => (
          <button
            key={item.runId}
            type="button"
            onClick={() => setActiveRunId(item.runId)}
            className={cn(
              'block w-full cursor-pointer border-b border-white/[0.05] px-3 py-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/60',
              activeRunId === item.runId
                ? 'bg-cyan-400/[0.07]'
                : 'hover:bg-white/[0.025]'
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-[10px] font-bold text-slate-300">
                <CalendarDays className="h-3.5 w-3.5 text-slate-600" />
                {String(item.startTime).slice(0, 10)}
              </span>
              <span className="text-[9px] font-black text-cyan-300">
                {replayStatusLabel(item.status)}
              </span>
            </div>
            <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-slate-600">
              <span>{item.runId.slice(0, 8)}</span>
              <span className={financialToneClass(item.summary?.tNetProfit)}>
                {item.summary
                  ? `¥${formatNumber(item.summary.tNetProfit)}`
                  : `${formatNumber(item.progressPct, 0)}%`}
              </span>
            </div>
          </button>
        ))}
        {history.length === 0 && (
          <div className="px-4 py-12 text-center text-[11px] text-slate-600">
            {historyResult.fetching ? '正在读取历史回放…' : '暂无历史回放'}
          </div>
        )}
      </aside>
    </div>
  );
}

export function TTradeGlobalPage() {
  const { toast } = useToast();
  const { confirm: confirmDialog, prompt: promptDialog } = useAppDialog();
  const openStudioTab = useStudioNavigate();
  const accountId = tradingAccountConfig.defaultAccountId;
  const { refreshSafety } = useTradingSafety();
  const [workspaceMode, setWorkspaceMode] = React.useState<
    'REALTIME' | 'REPLAY'
  >('REALTIME');
  const { tradingDays } = useTradingDays('SH', 3);
  const currentShanghaiDate = getShanghaiDateKey(new Date());
  const isCurrentTradingDay = tradingDays.length
    ? tradingDays.includes(currentShanghaiDate)
    : undefined;
  const [activeMode, setActiveMode] =
    React.useState<TTradeStudioMode>('MONITOR');
  const [form, setForm] = React.useState<SettingsForm>(defaultForm);
  const [ignoredCodes, setIgnoredCodes] = React.useState<string[]>([]);
  const [ignoreInput, setIgnoreInput] = React.useState('');
  const [lastMonitorRefreshAt, setLastMonitorRefreshAt] =
    React.useState<Date | null>(null);
  const [manualRefreshPending, setManualRefreshPending] = React.useState(false);
  const [draftDirty, setDraftDirty] = React.useState(false);
  const [policyPreview, setPolicyPreview] =
    React.useState<SignalPolicyPreviewLike | null>(null);
  const [configConflictVersion, setConfigConflictVersion] = React.useState<
    number | null
  >(null);
  const [configConflictPolicy, setConfigConflictPolicy] =
    React.useState<SignalPolicyLike | null>(null);
  const hydratedVersionRef = React.useRef('');
  const draftConfigVersionRef = React.useRef(0);
  const lastMonitorRefreshRequestRef = React.useRef(0);
  const autoSyncedSourceOrdersAccountRef = React.useRef('');
  const lastSourceOrdersSyncRequestRef = React.useRef(0);
  const reconcileOperationRef = React.useRef<
    (ClientOperationRef & { accountId: string }) | null
  >(null);
  const approveOperationRef = React.useRef(
    new Map<string, ClientOperationRef>()
  );
  const activateLiveOperationRef = React.useRef<ClientOperationRef | null>(
    null
  );
  React.useEffect(() => {
    const reconcile = readUncertainOperation(`reconcile:${accountId}`);
    reconcileOperationRef.current = reconcile
      ? { ...reconcile, accountId }
      : null;
    approveOperationRef.current.clear();
    // Keep one account-wide activation scope so a pending CANARY operation
    // also blocks a second LIVE mutation after a refresh or stage change.
    activateLiveOperationRef.current = readUncertainOperation(
      `activate-live:${accountId}`
    );
  }, [accountId]);
  const subscriptionRefreshTimerRef = React.useRef<number | null>(null);
  const graphqlWsStatus = useGraphqlWsStatus();
  const client = useClient();
  const previousWsStatusRef = React.useRef<GraphqlWsStatus | null>(null);
  const wsStatusRef = React.useRef(graphqlWsStatus);
  wsStatusRef.current = graphqlWsStatus;
  const monitorRefreshSequenceRef = React.useRef(0);
  const [signalSnapshotRefreshCoordinator] = React.useState(
    createSignalSnapshotRefreshCoordinator
  );
  const [serverTruthRefreshPolicy] = React.useState(
    createTTradeServerTruthRefreshPolicy
  );
  const [trustedSignalSnapshotEpoch, setTrustedSignalSnapshotEpoch] =
    React.useState<number | null>(null);
  const signalRefreshTelemetryRef = React.useRef<{
    evaluationsPending: boolean;
    evaluationsStarted: boolean;
    diagnosticsPending: boolean;
    diagnosticsStarted: boolean;
    failed: boolean;
  } | null>(null);

  const [monitorResult, refreshMonitor] = useQuery({
    query: TTradeGlobalMonitorQuery,
    variables: { accountId },
    pause: !accountId || workspaceMode !== 'REALTIME',
    requestPolicy: 'network-only',
  });
  const monitorPayload = monitorResult.data?.tTradeGlobalMonitor;
  const monitorSignalPolicy = readFragment(
    TTradeSignalPolicyFieldsFragment,
    monitorPayload?.signalPolicy
  );
  const freshMonitor = React.useMemo(() => {
    if (
      !monitorPayload ||
      monitorPayload.accountId !== accountId ||
      !monitorSignalPolicy
    ) {
      return undefined;
    }
    return {
      ...monitorPayload,
      signalPolicy: monitorSignalPolicy,
      holdings: monitorPayload.holdings.map(holding => ({
        ...holding,
        session: holding.session
          ? {
              ...holding.session,
              signalSnapshot: readFragment(
                TTradeSignalSnapshotFieldsFragment,
                holding.session.signalSnapshot
              ),
            }
          : null,
      })),
      sessions: monitorPayload.sessions.map(session => ({
        ...session,
        signalSnapshot: readFragment(
          TTradeSignalSnapshotFieldsFragment,
          session.signalSnapshot
        ),
      })),
    };
  }, [accountId, monitorPayload, monitorSignalPolicy]);
  const runMonitorEpochRefresh = React.useCallback(
    (epoch: number, expectedAccountId: string) => {
      void signalSnapshotRefreshCoordinator
        .refresh(epoch, expectedAccountId, async () => {
          const requestInstance =
            ++monitorRefreshSequenceRef.current as OperationContext['_instance'];
          const result = await client
            .query(
              TTradeGlobalMonitorQuery as DocumentNode,
              { accountId: expectedAccountId },
              {
                requestPolicy: 'network-only',
                // URQL uses this internal identity to keep this epoch's
                // promise from observing an older same-key result.
                _instance: requestInstance,
              }
            )
            .toPromise();
          return Boolean(
            result.data?.tTradeGlobalMonitor &&
            result.data.tTradeGlobalMonitor.accountId === expectedAccountId &&
            !result.error &&
            wsStatusRef.current === 'connected'
          );
        })
        .then(trusted => {
          if (
            trusted &&
            wsStatusRef.current === 'connected' &&
            signalSnapshotRefreshCoordinator.isTrusted(expectedAccountId)
          ) {
            setTrustedSignalSnapshotEpoch(epoch);
          }
        });
    },
    [client, signalSnapshotRefreshCoordinator]
  );
  const [lastTrustedMonitor, setLastTrustedMonitor] =
    React.useState<typeof freshMonitor>();
  React.useEffect(() => {
    if (freshMonitor && !monitorResult.error) {
      setLastTrustedMonitor(freshMonitor);
    }
  }, [freshMonitor, monitorResult.error]);
  React.useEffect(() => {
    setLastTrustedMonitor(undefined);
  }, [accountId]);
  React.useEffect(() => {
    const epoch = signalSnapshotRefreshCoordinator.beginEpoch(accountId);
    setTrustedSignalSnapshotEpoch(null);
    if (!accountId || workspaceMode !== 'REALTIME') return;
    serverTruthRefreshPolicy.noteNetworkRequest(accountId, Date.now());
    runMonitorEpochRefresh(epoch, accountId);
  }, [
    accountId,
    runMonitorEpochRefresh,
    signalSnapshotRefreshCoordinator,
    serverTruthRefreshPolicy,
    workspaceMode,
  ]);
  const monitor =
    freshMonitor ||
    (lastTrustedMonitor?.accountId === accountId
      ? lastTrustedMonitor
      : undefined);
  const signalSnapshotTrusted =
    graphqlWsStatus === 'connected' &&
    !monitorResult.error &&
    trustedSignalSnapshotEpoch != null &&
    trustedSignalSnapshotEpoch ===
      signalSnapshotRefreshCoordinator.currentEpoch() &&
    signalSnapshotRefreshCoordinator.isTrusted(accountId);
  const positionNamesByCode = React.useMemo(() => {
    const names = new Map<string, string>();
    for (const holding of monitor?.holdings || []) {
      const instrumentName = resolveInstrumentName(
        holding.stockCode,
        holding.instrumentName
      );
      if (hasInstrumentName(holding.stockCode, instrumentName)) {
        names.set(holding.stockCode.toUpperCase(), instrumentName);
      }
    }
    return names;
  }, [monitor?.holdings]);
  const quoteStockCodes = React.useMemo(
    () =>
      Array.from(
        new Set((monitor?.holdings || []).map(holding => holding.stockCode))
      ),
    [monitor?.holdings]
  );
  const liveQuoteState = useLatestMarketQuotes({
    stockCodes: quoteStockCodes,
    enabled: Boolean(accountId) && workspaceMode === 'REALTIME',
  });
  const realTimeQuotesByCode = liveQuoteState.quotes;
  const quoteHistoryByCode = useLiveQuoteHistory(
    realTimeQuotesByCode,
    workspaceMode === 'REALTIME' &&
      (activeMode === 'MONITOR' || activeMode === 'SIGNALS')
  );

  const [batchAfter, setBatchAfter] = React.useState<string | null>(null);
  const [eventAfter, setEventAfter] = React.useState<string | null>(null);
  const [signalAfter, setSignalAfter] = React.useState<string | null>(null);
  const [selectedTrace, setSelectedTrace] =
    React.useState<CandidateTraceSelection | null>(null);
  const selectedTraceForCurrentAccount =
    selectedTrace?.accountId === accountId ? selectedTrace : null;
  React.useEffect(() => {
    setSelectedTrace(null);
  }, [accountId]);
  const [batches, setBatches] = React.useState<TTradeBatch[]>([]);
  const [batchEvents, setBatchEvents] = React.useState<TTradeBatchEvent[]>([]);
  const [signalEvaluations, setSignalEvaluations] = React.useState<
    SignalEvaluationLike[]
  >([]);
  const accountBoundSignalEvaluations = signalEvaluations.filter(
    item => item.accountId === accountId
  );
  const [diagnosticRange, setDiagnosticRange] = React.useState(
    createRollingDiagnosticRange
  );
  const [batchesResult, refreshBatches] = useQuery({
    query: TTradeBatchesPageQuery,
    variables: {
      accountId,
      statusGroup: null,
      first: 30,
      after: batchAfter,
    },
    pause:
      !accountId || workspaceMode !== 'REALTIME' || activeMode !== 'POSITIONS',
    requestPolicy: 'network-only',
  });
  const [batchEventsResult, refreshBatchEvents] = useQuery({
    query: TTradeBatchEventsPageQuery,
    variables: { accountId, batchId: null, first: 30, after: eventAfter },
    pause:
      !accountId || workspaceMode !== 'REALTIME' || activeMode !== 'EVENTS',
    requestPolicy: 'network-only',
  });
  const [signalEvaluationsResult, refreshSignalEvaluations] = useQuery({
    query: TTradeSignalEvaluationsQuery,
    variables: {
      accountId,
      stockCode: null,
      eventKinds: null,
      startTime: diagnosticRange.startTime,
      endTime: diagnosticRange.endTime,
      first: 100,
      after: signalAfter,
    },
    pause:
      !accountId ||
      workspaceMode !== 'REALTIME' ||
      !['MONITOR', 'SIGNALS', 'DIAGNOSTICS'].includes(activeMode),
    requestPolicy: 'network-only',
  });
  const [signalDiagnosticsResult, refreshSignalDiagnostics] = useQuery({
    query: TTradeSignalDiagnosticsQuery,
    variables: {
      accountId,
      stockCode: null,
      startTime: diagnosticRange.startTime,
      endTime: diagnosticRange.endTime,
      mergeVersions: false,
    },
    pause:
      !accountId ||
      workspaceMode !== 'REALTIME' ||
      activeMode !== 'DIAGNOSTICS',
    requestPolicy: 'network-only',
  });
  const [candidateTraceResult, refreshCandidateTrace] = useQuery({
    query: TTradeCandidateTraceQuery,
    variables: {
      accountId: selectedTraceForCurrentAccount?.accountId || '',
      strategyRunId: selectedTraceForCurrentAccount?.strategyRunId || '',
      candidateId: selectedTraceForCurrentAccount?.candidateId || '',
    },
    pause:
      !accountId ||
      !selectedTraceForCurrentAccount ||
      workspaceMode !== 'REALTIME' ||
      activeMode !== 'SIGNALS',
    requestPolicy: 'network-only',
  });
  const signalEvaluationsPage = React.useMemo(() => {
    const page = signalEvaluationsResult.data?.tTradeSignalEvaluations;
    if (!page || page.items.some(item => item.accountId !== accountId)) {
      return undefined;
    }
    return page;
  }, [accountId, signalEvaluationsResult.data?.tTradeSignalEvaluations]);
  const diagnosticsPayload =
    signalDiagnosticsResult.data?.tTradeSignalDiagnostics;
  const diagnosticsForCurrentAccount =
    diagnosticsPayload?.accountId === accountId
      ? diagnosticsPayload
      : undefined;
  const candidateTracePayload = candidateTraceResult.data?.tTradeCandidateTrace;
  const candidateTraceMatchesSelection = Boolean(
    selectedTraceForCurrentAccount &&
    candidateTracePayload &&
    hasCandidateTraceIdentity(
      candidateTracePayload,
      selectedTraceForCurrentAccount
    )
  );
  const candidateTraceForUi = candidateTraceMatchesSelection
    ? candidateTracePayload
    : undefined;
  const candidateTraceIdentityMismatch = Boolean(
    selectedTraceForCurrentAccount &&
    candidateTracePayload &&
    !candidateTraceMatchesSelection
  );
  const [tTradeUpdateResult] = useSubscription({
    query: TTradeUpdatesSubscription,
    variables: { accountId },
    pause: !accountId || workspaceMode !== 'REALTIME',
  });
  const [, recordClientTelemetry] = useMutation(
    RecordTTradeClientTelemetryMutation
  );
  const reportClientTelemetry = React.useMemo(
    () =>
      createTTradeClientTelemetryReporter(event => {
        if (!accountId) return;
        return recordClientTelemetry({
          accountId,
          refreshSuccess: event === 'REFRESH_SUCCESS',
          refreshFailure: event === 'REFRESH_FAILURE',
          subscriptionReconnected: event === 'SUBSCRIPTION_RECONNECTED',
        }).then(() => undefined);
      }),
    [accountId, recordClientTelemetry]
  );
  const [saveResult, saveMonitor] = useMutation(
    SaveTTradeGlobalMonitorMutation
  );
  const [previewPolicyResult, previewSignalPolicy] = useMutation(
    PreviewTTradeSignalPolicyMutation
  );
  const [reconcileResult, reconcileMonitor] = useMutation(
    ReconcileTTradeGlobalMonitorMutation
  );
  const [approveResult, approveEntry] = useMutation(
    ApproveTTradeEntryV3Mutation
  );
  const [rejectResult, rejectEntry] = useMutation(RejectTTradeEntryV3Mutation);
  const [importResult, importExternalEntry] = useMutation(
    ImportTTradeExternalEntryMutation
  );
  const [syncSourceOrdersResult, syncSourceOrders] = useMutation(
    SyncTTradeSourceOrdersMutation
  );
  const [activateLiveResult, activateLive] = useMutation(
    ActivateTTradeLiveMutation
  );
  const [pauseEntriesResult, pauseEntries] = useMutation(
    PauseTTradeEntriesMutation
  );
  const [cancelOrderResult, cancelTTradeOrder] = useMutation(
    CancelTTradeOrderMutation
  );
  const [showExternalEntry, setShowExternalEntry] = React.useState(false);
  const [selectedOrderId, setSelectedOrderId] = React.useState('');
  const [sourceStartDate, setSourceStartDate] = React.useState(() => {
    const date = new Date();
    date.setDate(date.getDate() - 30);
    return date.toISOString().slice(0, 10);
  });
  const [sourceEndDate, setSourceEndDate] = React.useState(() =>
    new Date().toISOString().slice(0, 10)
  );
  const [externalAcknowledged, setExternalAcknowledged] = React.useState(false);
  const [sourceOrdersSyncError, setSourceOrdersSyncError] = React.useState('');
  const [sourceOrdersSyncedAt, setSourceOrdersSyncedAt] =
    React.useState<Date | null>(null);
  const readiness = monitor?.readiness;
  const [sourceOrdersResult, refreshSourceOrders] = useQuery({
    query: TTradeSourceOrdersQuery,
    variables: {
      accountId,
      startDate: sourceStartDate,
      endDate: sourceEndDate,
    },
    pause: !accountId || !showExternalEntry,
    requestPolicy: 'network-only',
  });
  const importedOrderIds = new Set(
    (sourceOrdersResult.data?.tTradeImportedEntries || []).flatMap(item =>
      item.sourceOrderId ? [item.sourceOrderId] : []
    )
  );
  const sourceBuyOrders = (sourceOrdersResult.data?.historyOrders || [])
    .filter(
      order =>
        String(order.type) === 'BUY' &&
        String(order.status) === 'SUCCEEDED' &&
        order.tradedVolume > 0
    )
    .sort((a, b) => Date.parse(b.time) - Date.parse(a.time));

  React.useEffect(() => {
    setBatchAfter(null);
    setEventAfter(null);
    setSignalAfter(null);
    setBatches([]);
    setBatchEvents([]);
    setSignalEvaluations([]);
    setDiagnosticRange(createRollingDiagnosticRange());
    signalRefreshTelemetryRef.current = null;
  }, [accountId]);

  React.useEffect(() => {
    const page = batchesResult.data?.tTradeBatchesPage;
    if (!page) return;
    if (page.items.some(item => item.accountId !== accountId)) {
      // Never render a page returned for a different account, even if a
      // gateway or stale cache serves it under the current operation key.
      setBatches([]);
      return;
    }
    setBatches(previous => {
      if (!batchAfter) return page.items;
      const byId = new Map(previous.map(item => [item.batchId, item]));
      for (const item of page.items) byId.set(item.batchId, item);
      return Array.from(byId.values());
    });
  }, [accountId, batchAfter, batchesResult.data?.tTradeBatchesPage]);

  React.useEffect(() => {
    const page = batchEventsResult.data?.tTradeBatchEventsPage;
    if (!page) return;
    setBatchEvents(previous => {
      if (!eventAfter) return page.items;
      const byId = new Map(previous.map(item => [item.eventId, item]));
      for (const item of page.items) byId.set(item.eventId, item);
      return Array.from(byId.values());
    });
  }, [batchEventsResult.data?.tTradeBatchEventsPage, eventAfter]);

  React.useEffect(() => {
    const page = signalEvaluationsResult.data?.tTradeSignalEvaluations;
    if (!page) return;
    if (page.items.some(item => item.accountId !== accountId)) {
      setSignalEvaluations([]);
      return;
    }
    const items: SignalEvaluationLike[] = page.items.map(item => ({
      ...item,
      id: String(item.id),
      signalSnapshot: readFragment(
        TTradeSignalSnapshotFieldsFragment,
        item.signalSnapshot
      ),
    }));
    setSignalEvaluations(previous => {
      if (!signalAfter) return items;
      const byId = new Map(previous.map(item => [item.id, item]));
      for (const item of items) byId.set(item.id, item);
      return Array.from(byId.values());
    });
  }, [
    accountId,
    signalAfter,
    signalEvaluationsResult.data?.tTradeSignalEvaluations,
  ]);

  const finishSignalRefreshTelemetry = React.useCallback(() => {
    const cycle = signalRefreshTelemetryRef.current;
    if (!cycle || cycle.evaluationsPending || cycle.diagnosticsPending) {
      return;
    }
    signalRefreshTelemetryRef.current = null;
    reportClientTelemetry(cycle.failed ? 'REFRESH_FAILURE' : 'REFRESH_SUCCESS');
  }, [reportClientTelemetry]);

  React.useEffect(() => {
    const cycle = signalRefreshTelemetryRef.current;
    if (!cycle?.evaluationsPending) return;
    if (signalEvaluationsResult.fetching) {
      cycle.evaluationsStarted = true;
      return;
    }
    if (!cycle.evaluationsStarted) return;
    cycle.evaluationsPending = false;
    cycle.failed ||= Boolean(signalEvaluationsResult.error);
    finishSignalRefreshTelemetry();
  }, [
    finishSignalRefreshTelemetry,
    signalEvaluationsResult.error,
    signalEvaluationsResult.fetching,
  ]);

  React.useEffect(() => {
    const cycle = signalRefreshTelemetryRef.current;
    if (!cycle?.diagnosticsPending) return;
    if (signalDiagnosticsResult.fetching) {
      cycle.diagnosticsStarted = true;
      return;
    }
    if (!cycle.diagnosticsStarted) return;
    cycle.diagnosticsPending = false;
    cycle.failed ||= Boolean(signalDiagnosticsResult.error);
    finishSignalRefreshTelemetry();
  }, [
    finishSignalRefreshTelemetry,
    signalDiagnosticsResult.error,
    signalDiagnosticsResult.fetching,
  ]);

  const refreshVisibleData = React.useCallback(() => {
    refreshMonitor({ requestPolicy: 'network-only' });
    if (activeMode === 'POSITIONS') {
      if (batchAfter) setBatchAfter(null);
      else refreshBatches({ requestPolicy: 'network-only' });
    }
    if (activeMode === 'EVENTS') {
      if (eventAfter) setEventAfter(null);
      else refreshBatchEvents({ requestPolicy: 'network-only' });
    }
    if (['MONITOR', 'SIGNALS', 'DIAGNOSTICS'].includes(activeMode)) {
      const includeDiagnostics = activeMode === 'DIAGNOSTICS';
      signalRefreshTelemetryRef.current = {
        evaluationsPending: true,
        evaluationsStarted: signalEvaluationsResult.fetching,
        diagnosticsPending: includeDiagnostics,
        diagnosticsStarted:
          includeDiagnostics && signalDiagnosticsResult.fetching,
        failed: false,
      };
      if (signalAfter) setSignalAfter(null);
      else refreshSignalEvaluations({ requestPolicy: 'network-only' });
      if (includeDiagnostics) {
        refreshSignalDiagnostics({ requestPolicy: 'network-only' });
      }
      if (activeMode === 'SIGNALS' && selectedTraceForCurrentAccount) {
        refreshCandidateTrace({ requestPolicy: 'network-only' });
      }
    }
  }, [
    activeMode,
    batchAfter,
    eventAfter,
    refreshBatchEvents,
    refreshBatches,
    refreshCandidateTrace,
    refreshMonitor,
    refreshSignalDiagnostics,
    refreshSignalEvaluations,
    signalAfter,
    signalDiagnosticsResult.fetching,
    signalEvaluationsResult.fetching,
    selectedTraceForCurrentAccount,
  ]);

  const requestAuthoritativeRefresh = React.useCallback(() => {
    if (!accountId || workspaceMode !== 'REALTIME') return;
    const epoch = signalSnapshotRefreshCoordinator.beginEpoch(accountId);
    setTrustedSignalSnapshotEpoch(null);
    serverTruthRefreshPolicy.noteNetworkRequest(accountId, Date.now());
    refreshVisibleData();
    runMonitorEpochRefresh(epoch, accountId);
  }, [
    accountId,
    refreshVisibleData,
    runMonitorEpochRefresh,
    serverTruthRefreshPolicy,
    signalSnapshotRefreshCoordinator,
    workspaceMode,
  ]);

  const handleSourceOrdersRefresh = React.useCallback(
    async (showSuccessToast = true) => {
      if (!accountId) return;
      const now = Date.now();
      if (now - lastSourceOrdersSyncRequestRef.current < 3_000) return;
      lastSourceOrdersSyncRequestRef.current = now;
      setSourceOrdersSyncError('');
      const result = await syncSourceOrders({ accountId });
      const payload = result.data?.syncTTradeSourceOrders;
      const errorMessage =
        payload?.message || result.error?.message || '同步当日委托失败';
      if (!payload?.success) {
        setSourceOrdersSyncError(errorMessage);
        toast({
          title: '当日委托同步失败',
          description: errorMessage,
          variant: 'destructive',
        });
        return;
      }
      setSourceOrdersSyncedAt(new Date());
      refreshSourceOrders({ requestPolicy: 'network-only' });
      if (showSuccessToast) {
        toast({
          title: '当日委托已同步',
          description: payload.message,
        });
      }
    },
    [accountId, refreshSourceOrders, syncSourceOrders, toast]
  );

  React.useEffect(() => {
    if (!showExternalEntry || !accountId) return;
    if (autoSyncedSourceOrdersAccountRef.current === accountId) return;
    autoSyncedSourceOrdersAccountRef.current = accountId;
    void handleSourceOrdersRefresh(false);
  }, [accountId, handleSourceOrdersRefresh, showExternalEntry]);

  React.useEffect(() => {
    const version = tTradeUpdateResult.data?.tTradeUpdates.version;
    if (
      !version ||
      !serverTruthRefreshPolicy.shouldRefreshForSubscriptionVersion(
        accountId,
        version
      )
    ) {
      return;
    }
    if (subscriptionRefreshTimerRef.current != null) {
      window.clearTimeout(subscriptionRefreshTimerRef.current);
    }
    subscriptionRefreshTimerRef.current = window.setTimeout(() => {
      subscriptionRefreshTimerRef.current = null;
      requestAuthoritativeRefresh();
    }, 250);
    return () => {
      if (subscriptionRefreshTimerRef.current != null) {
        window.clearTimeout(subscriptionRefreshTimerRef.current);
        subscriptionRefreshTimerRef.current = null;
      }
    };
  }, [
    accountId,
    requestAuthoritativeRefresh,
    serverTruthRefreshPolicy,
    tTradeUpdateResult.data?.tTradeUpdates.version,
  ]);

  React.useEffect(() => {
    const errorKey = tTradeUpdateResult.error?.message || null;
    if (!errorKey) {
      serverTruthRefreshPolicy.clearSubscriptionError(accountId);
      return;
    }
    if (
      serverTruthRefreshPolicy.shouldRefreshForSubscriptionError(
        accountId,
        errorKey
      )
    ) {
      requestAuthoritativeRefresh();
    }
  }, [
    accountId,
    requestAuthoritativeRefresh,
    serverTruthRefreshPolicy,
    tTradeUpdateResult.error?.message,
  ]);

  React.useEffect(() => {
    const previous = previousWsStatusRef.current;
    previousWsStatusRef.current = graphqlWsStatus;
    if (graphqlWsStatus !== 'connected') {
      // A connected flag alone is not evidence that the monitor snapshot was
      // refreshed after this transport interruption.
      setTrustedSignalSnapshotEpoch(null);
      serverTruthRefreshPolicy.resetForReconnect(accountId);
      if (previous === 'connected') {
        signalSnapshotRefreshCoordinator.beginEpoch(accountId);
      }
      return;
    }
    if (previous && previous !== 'connected') {
      requestAuthoritativeRefresh();
      reportClientTelemetry('SUBSCRIPTION_RECONNECTED');
    }
  }, [
    accountId,
    graphqlWsStatus,
    reportClientTelemetry,
    requestAuthoritativeRefresh,
    serverTruthRefreshPolicy,
    signalSnapshotRefreshCoordinator,
  ]);

  React.useEffect(() => {
    if (
      !accountId ||
      workspaceMode !== 'REALTIME' ||
      graphqlWsStatus !== 'connected'
    ) {
      return;
    }
    const auditServerTruth = () => {
      if (document.visibilityState !== 'visible') return;
      if (
        serverTruthRefreshPolicy.shouldRunAudit(
          accountId,
          graphqlWsStatus,
          Date.now()
        )
      ) {
        requestAuthoritativeRefresh();
      }
    };
    const timer = window.setInterval(
      auditServerTruth,
      T_TRADE_SERVER_TRUTH_AUDIT_INTERVAL_MS
    );
    return () => window.clearInterval(timer);
  }, [
    accountId,
    graphqlWsStatus,
    requestAuthoritativeRefresh,
    serverTruthRefreshPolicy,
    workspaceMode,
  ]);

  React.useEffect(() => {
    if (!accountId) return;
    const refreshIfVisible = () => {
      if (document.visibilityState !== 'visible') return;
      const now = Date.now();
      if (now - lastMonitorRefreshRequestRef.current < 5_000) return;
      lastMonitorRefreshRequestRef.current = now;
      requestAuthoritativeRefresh();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') refreshIfVisible();
    };
    window.addEventListener('focus', refreshIfVisible);
    window.addEventListener('online', refreshIfVisible);
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.removeEventListener('focus', refreshIfVisible);
      window.removeEventListener('online', refreshIfVisible);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [accountId, requestAuthoritativeRefresh]);

  React.useEffect(() => {
    if (monitorResult.fetching) return;
    setManualRefreshPending(false);
    if (monitorResult.data && !monitorResult.error) {
      setLastMonitorRefreshAt(new Date());
    }
  }, [monitorResult.data, monitorResult.error, monitorResult.fetching]);

  const handleMonitorRefresh = React.useCallback(() => {
    if (!accountId) return;
    lastMonitorRefreshRequestRef.current = Date.now();
    setManualRefreshPending(true);
    requestAuthoritativeRefresh();
  }, [accountId, requestAuthoritativeRefresh]);

  React.useEffect(() => {
    if (!monitor) return;
    const hydrationKey = `${monitor.accountId}:${monitor.configVersion}`;
    if (hydratedVersionRef.current === hydrationKey) return;
    if (
      draftDirty &&
      hydratedVersionRef.current.startsWith(`${monitor.accountId}:`)
    ) {
      setConfigConflictVersion(monitor.configVersion);
      return;
    }
    hydratedVersionRef.current = hydrationKey;
    draftConfigVersionRef.current = monitor.configVersion;
    setForm({
      mode: monitor.mode === 'live' ? 'live' : 'paper',
      acknowledged: monitor.autoExitAcknowledged,
      targetTradeAmount: String(monitor.targetTradeAmount),
      maxTradeAmount: String(monitor.maxTradeAmount),
      maxConcurrentBatches: String(monitor.maxConcurrentBatches),
      maxTotalTExposurePct: String(monitor.maxTotalTExposurePct * 100),
      targetProfitPct: String(monitor.targetProfitPct),
      baseFloorPct: String(monitor.baseFloorPct),
      initialGapPct: String(monitor.initialGapPct),
      trailingGapSlope: String(monitor.trailingGapSlope),
      maxGapPct: String(monitor.maxGapPct),
      highProfitLockEnabled: monitor.highProfitLockEnabled,
      highProfitArmPct: String(monitor.highProfitArmPct),
      highProfitMaxDrawdownPct: String(monitor.highProfitMaxDrawdownPct),
      rapidReversalEnabled: monitor.rapidReversalEnabled,
      rapidReversalWindowSeconds: String(monitor.rapidReversalWindowSeconds),
      rapidReversalDrawdownPct: String(monitor.rapidReversalDrawdownPct),
      rapidReversalConfirmTicks: String(monitor.rapidReversalConfirmTicks),
      hardStopEnabled: monitor.hardStopEnabled,
      hardStopPct: String(monitor.hardStopPct),
      signalPolicy: signalPolicyForm(monitor.signalPolicy),
      maxPriceDeviationPct: String(monitor.maxPriceDeviationPct),
      limitUpTouchExitEnabled: monitor.limitUpTouchExitEnabled,
      limitUpTouchToleranceTicks: String(monitor.limitUpTouchToleranceTicks),
      timeExitMode: monitor.timeExitMode,
      timeExitTime: monitor.timeExitTime,
      maxHoldingTradingDays: String(monitor.maxHoldingTradingDays),
      cooldownSeconds: String(monitor.cooldownSeconds),
    });
    setIgnoredCodes([...monitor.ignoredStockCodes]);
    setDraftDirty(false);
    setConfigConflictVersion(null);
    setConfigConflictPolicy(null);
    setPolicyPreview(null);
  }, [draftDirty, monitor]);

  const setField = React.useCallback(
    <K extends keyof SettingsForm>(key: K, value: SettingsForm[K]) => {
      setForm(current => ({ ...current, [key]: value }));
      setDraftDirty(true);
      setPolicyPreview(null);
    },
    []
  );

  const setSignalPolicyField = React.useCallback(
    (key: keyof SignalPolicyForm, value: SignalPolicyFormValue) => {
      setForm(current => ({
        ...current,
        signalPolicy: { ...current.signalPolicy, [key]: value },
      }));
      setDraftDirty(true);
      setPolicyPreview(null);
    },
    []
  );

  const persist = React.useCallback(
    async (
      enabled: boolean,
      nextIgnored = ignoredCodes,
      requirePolicyPreview = false
    ) => {
      if (!accountId) return false;
      if (draftDirty && !requirePolicyPreview) {
        toast({
          title: '当前有未保存草稿',
          description: '请先在参数页验证并保存，避免运行控制隐式带入新规则。',
          variant: 'destructive',
        });
        return false;
      }
      if (
        requirePolicyPreview &&
        (!policyPreview?.valid ||
          policyPreview.configVersion !== draftConfigVersionRef.current)
      ) {
        toast({
          title: '请先验证当前策略草稿',
          description: '保存只接受同一配置版本下已通过服务端预览的参数。',
          variant: 'destructive',
        });
        return false;
      }
      const result = await saveMonitor({
        input: {
          accountId,
          expectedConfigVersion: draftConfigVersionRef.current,
          signalPolicy: signalPolicyInput(form.signalPolicy),
          enabled,
          mode: form.mode,
          autoExitAcknowledged:
            form.mode === 'paper' ? false : form.acknowledged,
          ignoredStockCodes: nextIgnored,
          targetTradeAmount: numberValue(form.targetTradeAmount, 10000),
          maxTradeAmount: numberValue(form.maxTradeAmount, 12000),
          maxConcurrentBatches: integerValue(form.maxConcurrentBatches, 3),
          maxTotalTExposurePct:
            numberValue(form.maxTotalTExposurePct, 10) / 100,
          maxPriceDeviationPct: numberValue(form.maxPriceDeviationPct, 0.3),
          targetProfitPct: numberValue(form.targetProfitPct, 2),
          baseFloorPct: numberValue(form.baseFloorPct, 0.5),
          initialGapPct: numberValue(form.initialGapPct, 1.5),
          trailingGapSlope: numberValue(form.trailingGapSlope, 0.25),
          maxGapPct: numberValue(form.maxGapPct, 3),
          highProfitLockEnabled: form.highProfitLockEnabled,
          highProfitArmPct: numberValue(form.highProfitArmPct, 4),
          highProfitMaxDrawdownPct: numberValue(
            form.highProfitMaxDrawdownPct,
            1.2
          ),
          rapidReversalEnabled: form.rapidReversalEnabled,
          rapidReversalWindowSeconds: integerValue(
            form.rapidReversalWindowSeconds,
            15
          ),
          rapidReversalDrawdownPct: numberValue(
            form.rapidReversalDrawdownPct,
            0.8
          ),
          rapidReversalConfirmTicks: integerValue(
            form.rapidReversalConfirmTicks,
            2
          ),
          limitUpTouchExitEnabled: form.limitUpTouchExitEnabled,
          limitUpTouchToleranceTicks: integerValue(
            form.limitUpTouchToleranceTicks,
            0
          ),
          hardStopEnabled: form.hardStopEnabled,
          hardStopPct: numberValue(form.hardStopPct, -0.8),
          timeExitMode: form.timeExitMode,
          timeExitTime: form.timeExitTime,
          maxHoldingTradingDays: integerValue(form.maxHoldingTradingDays, 5),
          cooldownSeconds: integerValue(form.cooldownSeconds, 300),
        },
      });
      const payload = result.data?.saveTTradeGlobalMonitor;
      const success = isAppliedTTradeGlobalSave(payload);
      if (payload?.code === 'CONFIG_VERSION_CONFLICT') {
        setConfigConflictVersion(
          payload.monitor?.configVersion ?? monitor?.configVersion ?? 0
        );
        setConfigConflictPolicy(
          readFragment(
            TTradeSignalPolicyFieldsFragment,
            payload.monitor?.signalPolicy
          ) || null
        );
      }
      toast({
        title: tTradeGlobalSaveToastTitle(payload),
        description: payload?.message || result.error?.message || '请求失败',
        variant: success ? 'default' : 'destructive',
      });
      if (success) {
        const savedVersion =
          payload?.monitor?.configVersion ?? draftConfigVersionRef.current + 1;
        draftConfigVersionRef.current = savedVersion;
        hydratedVersionRef.current = `${accountId}:${savedVersion}`;
        setDraftDirty(false);
        setConfigConflictVersion(null);
        setConfigConflictPolicy(null);
        setPolicyPreview(null);
        refreshMonitor({ requestPolicy: 'network-only' });
      }
      return success;
    },
    [
      accountId,
      draftDirty,
      form,
      ignoredCodes,
      monitor?.configVersion,
      policyPreview,
      refreshMonitor,
      saveMonitor,
      toast,
    ]
  );

  const policyLocalErrors = React.useMemo(
    () => localSignalPolicyErrors(form.signalPolicy),
    [form.signalPolicy]
  );

  const handlePreviewPolicy = React.useCallback(async () => {
    if (!accountId || policyLocalErrors.length > 0) return;
    const expectedConfigVersion =
      configConflictVersion ?? draftConfigVersionRef.current;
    const result = await previewSignalPolicy({
      input: {
        accountId,
        expectedConfigVersion,
        signalPolicy: signalPolicyInput(form.signalPolicy),
      },
    });
    const payload = result.data?.previewTTradeSignalPolicy;
    if (!payload) {
      toast({
        title: '策略预览失败',
        description: result.error?.message || '服务端未返回校验结果',
        variant: 'destructive',
      });
      return;
    }
    const normalizedPolicy = readFragment(
      TTradeSignalPolicyFieldsFragment,
      payload.normalizedPolicy
    );
    setPolicyPreview({
      ...payload,
      normalizedPolicy,
    });
    if (payload.configVersion === expectedConfigVersion) {
      draftConfigVersionRef.current = expectedConfigVersion;
      setConfigConflictVersion(null);
    }
  }, [
    accountId,
    configConflictVersion,
    form.signalPolicy,
    policyLocalErrors.length,
    previewSignalPolicy,
    toast,
  ]);

  const handleIgnore = async (stockCode: string, ignored: boolean) => {
    const previous = ignoredCodes;
    const next = ignored
      ? Array.from(new Set([...ignoredCodes, stockCode]))
      : ignoredCodes.filter(code => code !== stockCode);
    setIgnoredCodes(next);
    if (!(await persist(Boolean(monitor?.enabled), next))) {
      setIgnoredCodes(previous);
    }
  };

  const handleAddIgnore = async () => {
    const value = ignoreInput.trim().toUpperCase();
    if (!value) return;
    const previous = ignoredCodes;
    const next = Array.from(new Set([...ignoredCodes, value]));
    setIgnoredCodes(next);
    setIgnoreInput('');
    if (!(await persist(Boolean(monitor?.enabled), next))) {
      setIgnoredCodes(previous);
    }
  };

  const handleReconcile = async () => {
    if (!accountId) return;
    if (reconcileOperationRef.current?.blocked) {
      toast({
        title: '同步记录不可恢复',
        description: '浏览器中的未决同步记录已损坏，请清理后再发起操作。',
        variant: 'destructive',
      });
      return;
    }
    if (!reconcileOperationRef.current) {
      const persisted = readUncertainOperation(`reconcile:${accountId}`);
      if (persisted) {
        reconcileOperationRef.current = { ...persisted, accountId };
      }
    }
    if (reconcileOperationRef.current?.blocked) {
      toast({
        title: '同步记录不可恢复',
        description: '浏览器中的未决同步记录已损坏，请清理后再发起操作。',
        variant: 'destructive',
      });
      return;
    }
    const activeOperation =
      reconcileOperationRef.current?.accountId === accountId
        ? reconcileOperationRef.current
        : {
            accountId,
            idempotencyKey: replayIdempotencyKey(),
            identity: accountId,
            uncertain: false,
          };
    const pendingOperation = { ...activeOperation, uncertain: true };
    if (
      !persistUncertainOperation(`reconcile:${accountId}`, pendingOperation)
    ) {
      reconcileOperationRef.current = { ...pendingOperation, blocked: true };
      toast({
        title: '无法安全记录同步操作',
        description: '未写入浏览器未决记录，本次同步未发送。',
        variant: 'destructive',
      });
      return;
    }
    reconcileOperationRef.current = pendingOperation;
    let result;
    try {
      result = await reconcileMonitor({
        accountId,
        idempotencyKey: activeOperation.idempotencyKey,
      });
    } catch (error) {
      reconcileOperationRef.current = pendingOperation;
      persistUncertainOperation(`reconcile:${accountId}`, pendingOperation);
      toast({
        title: '同步结果未知',
        description:
          error instanceof Error ? error.message : '请求结果未知，请重试原同步',
        variant: 'destructive',
      });
      refreshVisibleData();
      return;
    }
    const payload = result.data?.reconcileTTradeGlobalMonitor;
    const uncertain =
      !payload ||
      String(payload.code || '').endsWith('_COMMAND_PENDING') ||
      String(payload.code || '').endsWith('_OUTCOME_UNKNOWN');
    // Keep the same key only while the durable command outcome is unknown.
    // A terminal response represents a new user-action boundary.
    if (uncertain) {
      reconcileOperationRef.current = pendingOperation;
      persistUncertainOperation(`reconcile:${accountId}`, pendingOperation);
    } else {
      reconcileOperationRef.current = null;
      clearPersistedOperation(`reconcile:${accountId}`);
    }
    toast({
      title: payload?.success ? '持仓已同步' : '同步未完成',
      description: payload?.message || result.error?.message || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    refreshVisibleData();
  };

  const handleSignal = async (
    action: 'approve' | 'reject',
    runId: string,
    intentId: string,
    snapshot?: SignalSnapshot | null
  ) => {
    let payload:
      { code?: string; message: string; success: boolean } | undefined;
    let errorMessage = '';
    if (action === 'approve') {
      if (!signalSnapshotTrusted) {
        toast({
          title: '当前连接不可信，禁止确认',
          description: '请等待查询成功且订阅重连后的全量刷新。',
          variant: 'destructive',
        });
        return;
      }
      if (!canApproveSnapshot(snapshot)) {
        toast({
          title: '当前快照不能确认',
          description:
            '候选已过期、身份不完整或协议版本未知。请等待服务端刷新；服务端会在确认时重新校验交易资格。',
          variant: 'destructive',
        });
        return;
      }
      const expectation = {
        signalVersion: snapshot.signalVersion,
        candidateId: snapshot.candidateId!,
        candidateFingerprint: snapshot.candidateFingerprint!,
        candidateStateVersion: snapshot.candidateStateVersion,
        configVersion: snapshot.configVersion,
        policyVersion: snapshot.policyVersion,
      };
      const approvalIdentity = JSON.stringify({
        accountId,
        runId,
        intentId,
        expectation,
      });
      const approvalKey = `${runId}:${intentId}`;
      const existingOperation =
        approveOperationRef.current.get(approvalKey) ||
        readUncertainOperation(`approve:${accountId}:${approvalKey}`);
      if (existingOperation?.blocked) {
        toast({
          title: '审批操作不可恢复',
          description: '浏览器中的未决审批记录不可用，请清理后再发起操作。',
          variant: 'destructive',
        });
        return;
      }
      if (
        existingOperation?.uncertain &&
        existingOperation.identity !== approvalIdentity
      ) {
        toast({
          title: '上一笔审批结果未知',
          description: '请先恢复原审批结果，不能用新的候选身份重复确认。',
          variant: 'destructive',
        });
        return;
      }
      const operation =
        existingOperation?.identity === approvalIdentity
          ? existingOperation
          : {
              identity: approvalIdentity,
              idempotencyKey: replayIdempotencyKey(),
              uncertain: false,
            };
      approveOperationRef.current.set(approvalKey, operation);
      const pendingOperation = { ...operation, uncertain: true };
      if (
        !persistUncertainOperation(
          `approve:${accountId}:${approvalKey}`,
          pendingOperation
        )
      ) {
        approveOperationRef.current.set(approvalKey, {
          ...pendingOperation,
          blocked: true,
        });
        toast({
          title: '无法安全记录审批操作',
          description: '未写入浏览器未决记录，本次审批未发送。',
          variant: 'destructive',
        });
        return;
      }
      approveOperationRef.current.set(approvalKey, pendingOperation);
      let result;
      try {
        result = await approveEntry({
          runId,
          intentId,
          idempotencyKey: operation.idempotencyKey,
          expectation,
        });
      } catch (error) {
        approveOperationRef.current.set(approvalKey, pendingOperation);
        persistUncertainOperation(
          `approve:${accountId}:${approvalKey}`,
          pendingOperation
        );
        toast({
          title: '审批结果未知',
          description:
            error instanceof Error
              ? error.message
              : '请求结果未知，请重试原审批',
          variant: 'destructive',
        });
        return;
      }
      payload = result.data?.approveTTradeEntry;
      errorMessage = result.error?.message || '';
      const uncertain =
        !payload ||
        String(payload.code || '').endsWith('_COMMAND_PENDING') ||
        String(payload.code || '').endsWith('_OUTCOME_UNKNOWN');
      if (uncertain) {
        approveOperationRef.current.set(approvalKey, pendingOperation);
        persistUncertainOperation(
          `approve:${accountId}:${approvalKey}`,
          pendingOperation
        );
      } else {
        approveOperationRef.current.delete(approvalKey);
        clearPersistedOperation(`approve:${accountId}:${approvalKey}`);
      }
    } else {
      const result = await rejectEntry({ runId, intentId });
      payload = result.data?.rejectTTradeEntry;
      errorMessage = result.error?.message || '';
    }
    toast({
      title: payload?.success ? '信号已处理' : '信号未执行',
      description: payload?.message || errorMessage || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    refreshVisibleData();
  };

  const handleImportExternalEntry = async () => {
    if (!monitor?.strategyRunId || !selectedOrderId) return;
    const result = await importExternalEntry({
      input: {
        runId: monitor.strategyRunId,
        accountId,
        orderId: selectedOrderId,
      },
    });
    const payload = result.data?.importTTradeExternalEntry;
    toast({
      title: payload?.success ? '外部成交已纳入监控' : '外部成交未导入',
      description: payload?.message || result.error?.message || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    if (payload?.success) {
      setShowExternalEntry(false);
      setSelectedOrderId('');
      setExternalAcknowledged(false);
      refreshMonitor({ requestPolicy: 'network-only' });
    }
  };

  const refreshOperationalState = React.useCallback(() => {
    refreshVisibleData();
    refreshSafety();
  }, [refreshSafety, refreshVisibleData]);

  const handleActivateLive = async (targetStage: TTradeRolloutTarget) => {
    if (!accountId || !readiness?.canActivateLive || !readiness.snapshotId) {
      return;
    }
    let confirmation = '';
    if (targetStage === TTradeRolloutTarget.Live) {
      const expected = `LIVE:${accountId}`;
      const input = await promptDialog({
        title: '启用正式 LIVE 实盘',
        description:
          '此操作将授权当前账户执行正式实盘命令。请输入下方确认短语完成精确确认。',
        inputLabel: `确认短语：${expected}`,
        placeholder: expected,
        confirmText: '启用正式 LIVE',
        cancelText: '取消',
        variant: 'destructive',
        validate: value =>
          value === expected ? null : `必须完整输入 ${expected}`,
      });
      if (input === null) return;
      confirmation = input;
    } else {
      const confirmed = await confirmDialog({
        title: '进入严格 Canary 实盘',
        description:
          '买入仍需人工确认；买入真实成交后，止盈、止损和时间退出会自动提交卖单。',
        confirmText: '启用 Canary',
        cancelText: '取消',
        variant: 'warning',
      });
      if (!confirmed) return;
    }
    const identity = JSON.stringify({
      accountId,
      policyVersion: readiness.policyVersion,
      snapshotId: readiness.snapshotId,
      targetStage,
    });
    const operationScope = `activate-live:${accountId}`;
    const existingOperation =
      activateLiveOperationRef.current ||
      readUncertainOperation(operationScope);
    if (existingOperation?.blocked) {
      toast({
        title: '实盘提升操作不可恢复',
        description: '浏览器中的未决提升记录不可用，请清理后再发起操作。',
        variant: 'destructive',
      });
      return;
    }
    if (
      existingOperation?.uncertain &&
      existingOperation.identity !== identity
    ) {
      toast({
        title: '上一笔实盘提升结果未知',
        description: '请先恢复原提升结果，不能用新的门禁或确认再次提升。',
        variant: 'destructive',
      });
      return;
    }
    const operation =
      existingOperation?.identity === identity
        ? existingOperation
        : {
            identity,
            idempotencyKey: replayIdempotencyKey(),
            uncertain: false,
          };
    const pendingOperation = { ...operation, uncertain: true };
    if (!persistUncertainOperation(operationScope, pendingOperation)) {
      activateLiveOperationRef.current = {
        ...pendingOperation,
        blocked: true,
      };
      toast({
        title: '无法安全记录实盘提升操作',
        description: '未写入浏览器未决记录，本次实盘提升未发送。',
        variant: 'destructive',
      });
      return;
    }
    activateLiveOperationRef.current = pendingOperation;
    let result;
    try {
      result = await activateLive({
        accountId,
        policyVersion: readiness.policyVersion,
        snapshotId: readiness.snapshotId,
        idempotencyKey: operation.idempotencyKey,
        targetStage,
        confirmation,
      });
    } catch (error) {
      activateLiveOperationRef.current = pendingOperation;
      persistUncertainOperation(operationScope, pendingOperation);
      toast({
        title: '实盘提升结果未知',
        description:
          error instanceof Error ? error.message : '请求结果未知，请重试原操作',
        variant: 'destructive',
      });
      return;
    }
    const payload = result.data?.activateTTradeLive;
    const retryable =
      !payload ||
      String(payload.code || '').endsWith('_COMMAND_PENDING') ||
      String(payload.code || '').endsWith('_OUTCOME_UNKNOWN');
    if (retryable) {
      activateLiveOperationRef.current = pendingOperation;
      persistUncertainOperation(operationScope, pendingOperation);
    } else {
      activateLiveOperationRef.current = null;
      clearPersistedOperation(operationScope);
    }
    toast({
      title: payload?.success
        ? targetStage === TTradeRolloutTarget.Live
          ? '正式 LIVE 已启用'
          : 'Canary 已启用'
        : '实盘未启用',
      description: payload?.message || result.error?.message || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    refreshOperationalState();
  };

  const handlePauseEntries = async () => {
    if (!accountId) return;
    const result = await pauseEntries({
      accountId,
      reason: '用户从做 T 工作台暂停新买入',
    });
    const payload = result.data?.pauseTTradeEntries;
    toast({
      title: payload?.success ? '新买入已暂停' : '暂停失败',
      description: payload?.message || result.error?.message || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    refreshOperationalState();
  };

  const handleCancelOrder = async (clientOrderId: string) => {
    if (!accountId || !clientOrderId) return;
    const result = await cancelTTradeOrder({ accountId, clientOrderId });
    const payload = result.data?.cancelTTradeOrder;
    toast({
      title: payload?.success ? '撤单请求已提交' : '当前不能撤单',
      description: payload?.message || result.error?.message || '请求失败',
      variant: payload?.success ? 'default' : 'destructive',
    });
    refreshOperationalState();
  };

  const pendingSessions = (monitor?.sessions || []).filter(
    session =>
      session.signalSnapshot?.candidateStatus === 'AWAITING_APPROVAL' &&
      session.signalSnapshot.pendingEntryIntentId
  );
  const actionLoading =
    saveResult.fetching ||
    reconcileResult.fetching ||
    approveResult.fetching ||
    rejectResult.fetching ||
    importResult.fetching ||
    syncSourceOrdersResult.fetching ||
    activateLiveResult.fetching ||
    pauseEntriesResult.fetching ||
    cancelOrderResult.fetching ||
    previewPolicyResult.fetching;
  const openBatches = batches.filter(
    batch =>
      batch.activeVolume > 0 &&
      !['CLOSED', 'KILL_SWITCHED'].includes(batch.status)
  );
  const exitingBatches = batches.filter(batch =>
    [
      'EXIT_TRIGGERED',
      'EXIT_SUBMITTED',
      'EXIT_PARTIAL',
      'EXIT_REJECTED',
      'RECONCILE_REQUIRED',
      'KILL_SWITCHED',
    ].includes(batch.status)
  );

  const sidebar = (
    <TTradeHealthConsole
      accountId={accountId}
      actionLoading={actionLoading}
      isCurrentTradingDay={isCurrentTradingDay}
      loading={monitorResult.fetching}
      monitor={monitor}
      onRefresh={handleMonitorRefresh}
      onReconcile={handleReconcile}
      onToggleMonitoring={() => void persist(!monitor?.enabled)}
      quoteConnected={liveQuoteState.isConnected}
      quoteError={liveQuoteState.error}
      quotes={realTimeQuotesByCode}
      refreshing={manualRefreshPending || monitorResult.fetching}
      snapshotTrusted={signalSnapshotTrusted}
      toggleDisabled={
        draftDirty ||
        (!monitor?.enabled && form.mode === 'live' && !form.acknowledged)
      }
      wsStatus={graphqlWsStatus}
    />
  );

  const replaySidebar = (
    <aside className="studio-workspace-surface flex h-full min-h-0 flex-col">
      <div className="flex h-[68px] shrink-0 items-center border-b border-white/[0.05] px-4">
        <div>
          <div className="text-[10px] font-black uppercase tracking-[0.24em] text-cyan-300">
            Replay Lab
          </div>
          <h1 className="mt-1 text-base font-black text-slate-100">回放测试</h1>
        </div>
      </div>
      <div className="border-b border-white/[0.05] p-4">
        <div className="flex items-center gap-2 text-xs font-black text-cyan-100">
          <ShieldCheck className="h-4 w-4 text-cyan-300" />
          隔离回测环境
        </div>
        <p className="mt-2 text-[10px] leading-5 text-slate-600">
          回放使用 BACKTEST
          Broker，测试信号自动确认；实时监控保持原状态，不会提交实盘委托。
        </p>
      </div>
      <div className="space-y-3 p-4 text-[10px] text-slate-500">
        <div className="flex items-start gap-2">
          <History className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          以开始日前最近日结快照作为初始现金和持仓。
        </div>
        <div className="flex items-start gap-2">
          <BarChart3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          同时计算账户曲线与不做 T 被动基准。
        </div>
        <div className="flex items-start gap-2">
          <CircleDollarSign className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-600" />
          收益已扣佣金、过户费、卖出印花税与滑点。
        </div>
      </div>
      <div className="mt-auto shrink-0 border-t border-white/[0.06] bg-[#091322] p-3">
        <div className="mb-1.5 text-[10px] font-black uppercase tracking-[0.12em] text-slate-600">
          默认回放账户
        </div>
        <div className="flex h-10 items-center border border-white/[0.08] bg-white/[0.025] px-3 font-mono text-xs text-slate-300">
          {accountId || '未配置'}
        </div>
      </div>
    </aside>
  );

  const toolbar = (
    <div className="studio-workspace-surface flex h-12 shrink-0 items-center justify-between gap-3 overflow-x-auto border-b border-white/[0.05] px-4 custom-scrollbar">
      <nav
        className="flex h-full min-w-0 items-stretch"
        aria-label="做 T 工作区"
      >
        {(['REALTIME', 'REPLAY'] as const).map(mode => {
          const active = workspaceMode === mode;
          return (
            <button
              key={mode}
              type="button"
              onClick={() => setWorkspaceMode(mode)}
              className={cn(
                'relative flex h-full shrink-0 cursor-pointer items-center gap-1.5 px-3 text-[11px] font-black transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset',
                active
                  ? mode === 'REPLAY'
                    ? 'text-cyan-200 after:bg-cyan-400 focus-visible:ring-cyan-400/60'
                    : 'text-blue-200 after:bg-blue-400 focus-visible:ring-blue-400/70'
                  : 'text-slate-600 hover:text-slate-200'
              )}
            >
              {mode === 'REPLAY' ? (
                <FlaskConical className="h-3.5 w-3.5" />
              ) : (
                <Radar className="h-3.5 w-3.5" />
              )}
              {mode === 'REPLAY' ? '回放测试' : '实时监控'}
            </button>
          );
        })}
        {workspaceMode === 'REALTIME' && (
          <>
            <span className="mx-2 my-3 w-px bg-white/[0.08]" />
            {tTradeModes.map(mode => {
              const isActive = activeMode === mode.id;
              return (
                <button
                  key={mode.id}
                  type="button"
                  onClick={() => setActiveMode(mode.id as TTradeStudioMode)}
                  className={cn(
                    'relative h-full shrink-0 cursor-pointer px-3 text-xs font-bold transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70',
                    isActive
                      ? 'text-blue-200 after:bg-blue-400'
                      : 'text-slate-500 hover:text-slate-200'
                  )}
                >
                  {mode.label}
                  {mode.id === 'SIGNALS' && Boolean(pendingSessions.length) && (
                    <span className="ml-1.5 rounded-sm bg-amber-400/15 px-1.5 py-0.5 font-mono text-[9px] text-amber-200">
                      {pendingSessions.length}
                    </span>
                  )}
                </button>
              );
            })}
          </>
        )}
      </nav>

      <div className="flex shrink-0 items-center gap-2">
        {workspaceMode === 'REALTIME' && (
          <Button
            className="h-7 px-2 text-[10px]"
            onClick={() => openStudioTab('/liquidation')}
            size="sm"
            type="button"
            variant="outline"
          >
            <WalletCards className="h-3.5 w-3.5" />T 批次退出
          </Button>
        )}
        {workspaceMode === 'REPLAY' ? (
          <span className="hidden items-center gap-1.5 text-[10px] font-bold text-cyan-200 sm:inline-flex">
            <ShieldCheck className="h-3.5 w-3.5" />
            隔离回测 · 自动确认测试信号
          </span>
        ) : (
          <>
            <span
              className={cn(
                'hidden items-center gap-1.5 text-[10px] font-bold md:inline-flex',
                monitor?.enabled ? 'text-emerald-300' : 'text-slate-600'
              )}
            >
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  monitor?.enabled
                    ? 'bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.65)]'
                    : 'bg-slate-700'
                )}
              />
              {monitor?.enabled ? '全局监控运行中' : '全局监控已停止'}
            </span>
            <span className="hidden h-4 w-px bg-white/[0.08] sm:block" />
            <span className="hidden font-mono text-[9px] text-slate-600 sm:inline">
              行情 WS {graphqlWsStatus} · 策略投影约 10s
            </span>
          </>
        )}
      </div>
    </div>
  );

  const monitorView = (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      {!accountId && (
        <div className="flex shrink-0 items-center gap-2 border-b border-amber-400/15 bg-amber-400/[0.07] px-4 py-2.5 text-xs font-bold text-amber-100">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          未配置默认交易账户，请设置环境变量 VITE_DEFAULT_ACCOUNT_ID。
        </div>
      )}
      {(monitorResult.error || monitor?.lastError) && (
        <div className="flex shrink-0 items-center gap-2 border-b border-rose-400/15 bg-rose-500/[0.07] px-4 py-2.5 text-xs text-rose-100">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{monitorResult.error?.message || monitor?.lastError}</span>
        </div>
      )}
      {readiness && (
        <section
          aria-live="polite"
          className={cn(
            'flex shrink-0 flex-wrap items-center justify-between gap-3 border-b px-4 py-3',
            readiness.canApprove
              ? 'border-emerald-400/15 bg-emerald-400/[0.05]'
              : readiness.preparationReady
                ? 'border-sky-400/15 bg-sky-400/[0.05]'
                : 'border-amber-400/15 bg-amber-400/[0.05]'
          )}
        >
          <div className="flex min-w-0 items-start gap-2">
            {readiness.preparationReady ? (
              <ShieldCheck
                className={cn(
                  'mt-0.5 h-4 w-4 shrink-0',
                  readiness.canApprove ? 'text-emerald-400' : 'text-sky-300'
                )}
              />
            ) : (
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
            )}
            <div>
              <div className="text-xs font-black text-slate-100">
                {readinessStageLabel(readiness.status, readiness.stage)} ·
                Engine {readiness.engineStatus} · Agent {readiness.agentStatus}
              </div>
              <div className="mt-1 text-[10px] leading-4 text-slate-400">
                {readiness.preparationReady && !readiness.automationReady
                  ? `账户事实已收敛；做 T 自动执行仍关闭。账户实盘窗口${readiness.controlledWindowActive ? '已建立' : '未建立'}，当前快照识别手工委托 ${readiness.externalOrderCount} 笔、成交 ${readiness.externalTradeCount} 笔，窗口后新增 ${readiness.newExternalOrderCount + readiness.newExternalTradeCount} 笔，活动委托 ${readiness.workingExternalOrderCount} 笔。${readiness.blockedReasons[0] || ''}`
                  : readiness.automationReady && !readiness.canApprove
                    ? `账户实盘门禁已通过；做 T 当前处于 ${readiness.stage}，启用 Canary 或 LIVE 后才允许确认新买入。`
                    : readiness.blockedReasons.length
                      ? readiness.blockedReasons.join('；')
                      : '做 T 自动执行已启用，可按当前灰度阶段处理交易。'}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {readiness.stage === 'CANARY' || readiness.stage === 'LIVE' ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={actionLoading}
                onClick={handlePauseEntries}
                className="h-8 rounded-sm border-amber-400/20 text-[10px] text-amber-200"
              >
                暂停新买入
              </Button>
            ) : (
              <>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => openStudioTab('/settings/trading-safety')}
                  className="h-8 rounded-sm border-sky-400/20 text-[10px] text-sky-200"
                >
                  {readiness.controlledWindowActive
                    ? '查看账户交易安全'
                    : '前往建立账户实盘窗口'}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!readiness.canActivateLive || actionLoading}
                  onClick={() => handleActivateLive(TTradeRolloutTarget.Canary)}
                  className="h-8 rounded-sm border-emerald-400/20 text-[10px] text-emerald-200"
                >
                  启用严格 Canary
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={!readiness.canActivateLive || actionLoading}
                  onClick={() => handleActivateLive(TTradeRolloutTarget.Live)}
                  className="h-8 rounded-sm bg-emerald-500 px-3 text-[10px] font-black text-slate-950 hover:bg-emerald-400"
                >
                  启用正式 LIVE
                </Button>
              </>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => openStudioTab('/settings/trading-safety')}
              className="h-8 rounded-sm border-rose-400/20 text-[10px] text-rose-200"
            >
              账户紧急停止
            </Button>
          </div>
        </section>
      )}
      {pendingSessions.length > 0 && (
        <button
          type="button"
          onClick={() => setActiveMode('SIGNALS')}
          className="flex shrink-0 items-center justify-between border-b border-amber-400/15 bg-amber-400/[0.05] px-4 py-2.5 text-left transition-colors hover:bg-amber-400/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-amber-400/50"
        >
          <span className="inline-flex items-center gap-2 text-xs font-bold text-amber-100">
            <Activity className="h-4 w-4" />有 {pendingSessions.length}{' '}
            个买入机会等待人工确认
          </span>
          <span className="text-[10px] font-bold text-amber-300">
            查看信号 →
          </span>
        </button>
      )}

      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.05] px-4 py-3">
        <div>
          <h2 className="text-sm font-black text-slate-100">实时作战表</h2>
          <p className="mt-0.5 text-[10px] text-slate-600">
            行情流与策略流独立标时 · 默认按需要关注程度排序
          </p>
        </div>
        <div className="flex items-center gap-3 text-[10px] font-bold text-slate-600">
          <span>{monitor?.mode === 'live' ? '实盘执行' : '模拟观察'}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-8 rounded-sm border-white/10 px-2.5 text-[10px] text-slate-300"
            disabled={!accountId || actionLoading}
            onClick={() => setShowExternalEntry(value => !value)}
          >
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            从已成委托选择
          </Button>
          {monitorResult.fetching && !monitor ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none text-red-300" />
          ) : lastMonitorRefreshAt ? (
            <span
              className="font-mono font-normal text-slate-700"
              title={`最近同步：${lastMonitorRefreshAt.toLocaleString('zh-CN', {
                hour12: false,
              })}`}
            >
              {lastMonitorRefreshAt.toLocaleTimeString('zh-CN', {
                hour12: false,
              })}
            </span>
          ) : null}
        </div>
      </div>

      {showExternalEntry && (
        <section className="shrink-0 border-b border-amber-400/15 bg-[#0b1628] px-4 py-3">
          {!monitor?.enabled || !monitor.strategyRunId ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                <div>
                  <div className="text-xs font-black text-amber-100">
                    请先启动全局监控
                  </div>
                  <p className="mt-1 text-[10px] leading-4 text-slate-500">
                    外部成交需要加入一个正在运行的做 T
                    策略，才能持续读取行情并触发自动卖出。
                  </p>
                </div>
              </div>
              <Button
                type="button"
                size="sm"
                className="h-8 shrink-0 rounded-sm bg-primary px-3 text-[10px] font-black text-white hover:bg-primary/90"
                disabled={
                  actionLoading || (form.mode === 'live' && !form.acknowledged)
                }
                onClick={() => persist(true)}
              >
                <Play className="mr-1.5 h-3.5 w-3.5" />
                启动监控后添加
              </Button>
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs font-black text-slate-100">
                    选择已成交买入委托
                  </div>
                  <p className="mt-1 text-[10px] text-slate-500">
                    先同步 miniQMT
                    当日委托，再读取委托表；每个已成委托只能建立一次自动卖出批次。
                  </p>
                  {sourceOrdersSyncedAt && !sourceOrdersSyncError && (
                    <p className="mt-1 font-mono text-[9px] text-emerald-500/70">
                      当日委托已同步 ·{' '}
                      {sourceOrdersSyncedAt.toLocaleTimeString('zh-CN', {
                        hour12: false,
                      })}
                    </p>
                  )}
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 text-[10px] text-slate-400"
                  disabled={syncSourceOrdersResult.fetching}
                  onClick={() => void handleSourceOrdersRefresh()}
                >
                  <RefreshCw
                    className={cn(
                      'mr-1.5 h-3.5 w-3.5',
                      (syncSourceOrdersResult.fetching ||
                        sourceOrdersResult.fetching) &&
                        'animate-spin motion-reduce:animate-none'
                    )}
                  />
                  {syncSourceOrdersResult.fetching ? '同步中' : '同步并刷新'}
                </Button>
              </div>
              {sourceOrdersSyncError && (
                <div className="mt-3 flex items-start gap-2 border border-red-500/20 bg-red-500/[0.06] px-3 py-2 text-[10px] leading-4 text-red-200">
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-400" />
                  <span>
                    {sourceOrdersSyncError}。当前仍显示委托表中的已有记录。
                  </span>
                </div>
              )}
              <div className="mt-3 flex items-end gap-2">
                <div>
                  <Label
                    htmlFor="t-trade-source-start"
                    className="text-[10px] text-slate-500"
                  >
                    开始日期
                  </Label>
                  <Input
                    id="t-trade-source-start"
                    type="date"
                    value={sourceStartDate}
                    onChange={event => setSourceStartDate(event.target.value)}
                    className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-[10px]"
                  />
                </div>
                <div>
                  <Label
                    htmlFor="t-trade-source-end"
                    className="text-[10px] text-slate-500"
                  >
                    结束日期
                  </Label>
                  <Input
                    id="t-trade-source-end"
                    type="date"
                    value={sourceEndDate}
                    onChange={event => setSourceEndDate(event.target.value)}
                    className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-[10px]"
                  />
                </div>
              </div>
              <div className="mt-3 max-h-52 overflow-y-auto border border-white/[0.07] custom-scrollbar">
                {sourceBuyOrders.map(order => {
                  const holding = monitor.holdings.find(
                    item => item.stockCode === order.stockCode
                  );
                  const imported = importedOrderIds.has(order.id);
                  const unavailable =
                    imported ||
                    !holding ||
                    Boolean(holding.session?.activeVolume) ||
                    order.tradedVolume % 100 !== 0;
                  return (
                    <label
                      key={order.id}
                      className={cn(
                        'grid grid-cols-[24px_minmax(140px,1fr)_100px_100px_80px] items-center border-b border-white/[0.05] px-3 py-2 text-[10px] last:border-b-0',
                        unavailable
                          ? 'cursor-not-allowed opacity-45'
                          : 'cursor-pointer hover:bg-white/[0.03]'
                      )}
                    >
                      <input
                        type="radio"
                        name="t-trade-filled-buy-order"
                        value={order.id}
                        checked={selectedOrderId === order.id}
                        disabled={unavailable}
                        onChange={() => setSelectedOrderId(order.id)}
                        className="h-3.5 w-3.5 accent-amber-400"
                      />
                      <span>
                        <span className="font-bold text-slate-200">
                          {holding?.instrumentName || order.stockCode}
                        </span>
                        <span className="ml-2 font-mono text-slate-600">
                          {order.stockCode}
                        </span>
                      </span>
                      <span className="text-right font-mono text-slate-300">
                        {order.tradedVolume.toLocaleString()} 股
                      </span>
                      <span className="text-right font-mono text-slate-300">
                        ¥{formatNumber(order.tradedPrice, 3)}
                      </span>
                      <span
                        className={cn(
                          'text-right font-mono',
                          imported
                            ? 'font-bold text-emerald-400'
                            : 'text-slate-600'
                        )}
                      >
                        {imported
                          ? '已纳入'
                          : new Date(order.time).toLocaleDateString('zh-CN')}
                      </span>
                    </label>
                  );
                })}
                {!sourceOrdersResult.fetching &&
                  sourceBuyOrders.length === 0 && (
                    <div className="px-3 py-6 text-center text-[10px] text-slate-600">
                      所选日期范围内没有已成交买入委托
                    </div>
                  )}
              </div>
              <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <label className="flex cursor-pointer items-start gap-2 text-[10px] leading-4 text-amber-100">
                  <input
                    type="checkbox"
                    checked={externalAcknowledged}
                    onChange={event =>
                      setExternalAcknowledged(event.target.checked)
                    }
                    className="mt-0.5 h-3.5 w-3.5 accent-amber-400"
                  />
                  我确认将所选已成交买入委托纳入当前已启用的自动退出规则。
                </label>
                <Button
                  type="button"
                  className="h-9 rounded-sm bg-amber-500 px-4 text-xs font-black text-slate-950 hover:bg-amber-400"
                  disabled={
                    !selectedOrderId ||
                    !externalAcknowledged ||
                    importResult.fetching
                  }
                  onClick={handleImportExternalEntry}
                >
                  {importResult.fetching && (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                  )}
                  纳入自动卖出
                </Button>
              </div>
            </>
          )}
        </section>
      )}

      <TTradeLiveBoard
        evaluations={accountBoundSignalEvaluations}
        historyByCode={quoteHistoryByCode}
        loading={monitorResult.fetching}
        monitor={monitor}
        onIgnore={handleIgnore}
        quotes={realTimeQuotesByCode}
      />
    </div>
  );

  const renderBatchTable = (rows: typeof batches, exitView: boolean) => (
    <div className="min-h-0 overflow-auto custom-scrollbar">
      <table className="w-full min-w-[1040px] text-left text-xs">
        <thead className="sticky top-0 z-10 bg-[#0b1628] text-[10px] font-black uppercase tracking-[0.08em] text-slate-600">
          <tr>
            <th className="px-4 py-2.5">标的 / 批次</th>
            <th className="px-3 py-2.5">生命周期</th>
            <th className="px-3 py-2.5 text-right">买入成交</th>
            <th className="px-3 py-2.5 text-right">活跃仓</th>
            <th className="px-3 py-2.5 text-right">买入均价 / 最新价</th>
            <th className="px-3 py-2.5 text-right">净收益 / 峰值</th>
            <th className="px-3 py-2.5 text-right">
              {exitView ? '卖出成交 / 剩余' : '保护线'}
            </th>
            <th className="px-4 py-2.5 text-right">委托 / 操作</th>
          </tr>
        </thead>
        <tbody>
          {batchesResult.fetching && rows.length === 0 ? (
            <tr>
              <td
                colSpan={8}
                className="px-4 py-12 text-center text-xs text-slate-600"
                role="status"
              >
                <Loader2
                  className="mr-2 inline-block h-4 w-4 animate-spin motion-reduce:animate-none"
                  aria-hidden="true"
                />
                正在读取做 T 批次…
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={8}
                className="px-4 py-12 text-center text-xs text-slate-600"
              >
                {exitView
                  ? '当前没有待卖出或退出异常批次'
                  : '当前没有做 T 活跃仓位'}
              </td>
            </tr>
          ) : (
            rows.map(batch => {
              const clientOrderId = exitView
                ? batch.exitClientOrderId
                : batch.entryClientOrderId;
              const brokerOrderId = exitView
                ? batch.exitBrokerOrderId
                : batch.entryBrokerOrderId;
              const canCancel =
                Boolean(clientOrderId) &&
                [
                  'ENTRY_QUEUED',
                  'ENTRY_SUBMITTED',
                  'ENTRY_PARTIAL',
                  'EXIT_TRIGGERED',
                  'EXIT_SUBMITTED',
                  'EXIT_PARTIAL',
                ].includes(batch.status);
              return (
                <tr
                  key={`${exitView ? 'exit' : 'open'}-${batch.batchId}`}
                  className="border-b border-white/[0.04] hover:bg-white/[0.025]"
                >
                  <td className="px-4 py-3">
                    <InstrumentNameLabel
                      stockCode={batch.stockCode}
                      knownName={positionNamesByCode.get(
                        batch.stockCode.toUpperCase()
                      )}
                      className="font-black text-slate-100"
                    />
                    <div className="mt-1 font-mono text-[9px] text-slate-600">
                      {batch.batchId.slice(0, 12)}
                    </div>
                  </td>
                  <td className="px-3 py-3">
                    <span className="inline-flex border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] font-bold text-slate-300">
                      {batchStatusLabels[batch.status] || batch.status}
                    </span>
                    {(batch.exitReason || batch.exceptionReason) && (
                      <div className="mt-1 max-w-52 text-[9px] leading-4 text-amber-200/80">
                        {batch.exceptionReason || batch.exitReason}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-3 text-right font-mono tabular-nums text-slate-300">
                    {batch.entryFilledVolume.toLocaleString()} /{' '}
                    {batch.targetVolume.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right font-mono font-black tabular-nums text-cyan-200">
                    {batch.activeVolume.toLocaleString()}
                  </td>
                  <td className="px-3 py-3 text-right font-mono tabular-nums text-slate-300">
                    {formatNumber(batch.entryAvgPrice, 3)}
                    <span className="mx-1 text-slate-700">/</span>
                    {formatNumber(batch.lastPrice, 3)}
                  </td>
                  <td
                    className={cn(
                      'px-3 py-3 text-right font-mono tabular-nums',
                      financialToneClass(batch.lastNetProfitPct, 'holding')
                    )}
                  >
                    {formatSignedPercent(batch.lastNetProfitPct)}
                    <div className="mt-1 text-[9px] text-slate-600">
                      峰值 {formatSignedPercent(batch.peakNetProfitPct)}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-right font-mono tabular-nums text-slate-400">
                    {exitView ? (
                      <>
                        {batch.exitFilledVolume.toLocaleString()} /{' '}
                        {batch.activeVolume.toLocaleString()}
                      </>
                    ) : batch.trailingFloorPct == null ? (
                      '未武装'
                    ) : (
                      formatSignedPercent(batch.trailingFloorPct)
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="font-mono text-[9px] text-slate-600">
                      {brokerOrderId ||
                        clientOrderId?.slice(0, 12) ||
                        '尚未委托'}
                    </div>
                    {canCancel && clientOrderId && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={actionLoading}
                        onClick={() => handleCancelOrder(clientOrderId)}
                        className="mt-1.5 h-7 rounded-sm border-white/10 px-2 text-[9px]"
                      >
                        申请撤单
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );

  const positionsView = (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      {batchesResult.error && (
        <div
          role="alert"
          className="flex shrink-0 items-start justify-between gap-3 border-b border-rose-400/20 bg-rose-400/[0.06] px-4 py-2.5 text-[10px] leading-4 text-rose-100"
        >
          <span>做 T 批次读取失败；仍显示上次成功读取的结果。</span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-6 shrink-0 px-2 text-[9px] text-rose-100"
            onClick={() => refreshBatches({ requestPolicy: 'network-only' })}
          >
            重试
          </Button>
        </div>
      )}
      {!batchesResult.error && batchesResult.fetching && batches.length > 0 && (
        <div
          role="status"
          aria-busy="true"
          className="flex shrink-0 items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-4 py-2 text-[9px] text-cyan-100"
        >
          <Loader2
            className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在刷新做 T 批次，暂保留上次结果…
        </div>
      )}
      <div className="grid shrink-0 grid-cols-2 border-b border-white/[0.05]">
        <div className="border-r border-white/[0.05] px-4 py-3">
          <div className="text-[10px] font-black uppercase tracking-[0.1em] text-slate-600">
            做 T 活跃仓位
          </div>
          <div className="mt-1 font-mono text-xl font-black text-cyan-200">
            {openBatches.length}
          </div>
        </div>
        <div className="px-4 py-3">
          <div className="text-[10px] font-black uppercase tracking-[0.1em] text-slate-600">
            待卖出 / 退出中
          </div>
          <div className="mt-1 font-mono text-xl font-black text-amber-200">
            {exitingBatches.length}
          </div>
        </div>
      </div>
      <section className="flex min-h-0 flex-1 flex-col border-b border-white/[0.05]">
        <h2 className="shrink-0 px-4 py-2.5 text-xs font-black text-slate-200">
          做 T 仓位
        </h2>
        {renderBatchTable(openBatches, false)}
      </section>
      <section className="flex min-h-0 flex-1 flex-col">
        <h2 className="shrink-0 px-4 py-2.5 text-xs font-black text-slate-200">
          待卖出与退出异常
        </h2>
        {renderBatchTable(exitingBatches, true)}
      </section>
      {batchesResult.data?.tTradeBatchesPage.pageInfo.hasNextPage && (
        <div className="shrink-0 border-t border-white/[0.05] p-2 text-center">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={batchesResult.fetching}
            onClick={() =>
              setBatchAfter(
                batchesResult.data?.tTradeBatchesPage.pageInfo.endCursor ?? null
              )
            }
            className="h-8 text-[10px] text-slate-400"
          >
            {batchesResult.fetching ? '加载中…' : '加载更多批次'}
          </Button>
        </div>
      )}
    </div>
  );

  const eventsView = (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      {batchEventsResult.error && (
        <div
          role="alert"
          className="flex shrink-0 items-start justify-between gap-3 border-b border-rose-400/20 bg-rose-400/[0.06] px-4 py-2.5 text-[10px] leading-4 text-rose-100"
        >
          <span>订单事件读取失败；仍显示上次成功读取的事件。</span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-6 shrink-0 px-2 text-[9px] text-rose-100"
            onClick={() =>
              refreshBatchEvents({ requestPolicy: 'network-only' })
            }
          >
            重试
          </Button>
        </div>
      )}
      {!batchEventsResult.error &&
        batchEventsResult.fetching &&
        batchEvents.length > 0 && (
          <div
            role="status"
            aria-busy="true"
            className="flex shrink-0 items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-4 py-2 text-[9px] text-cyan-100"
          >
            <Loader2
              className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
              aria-hidden="true"
            />
            正在刷新订单事件，暂保留上次结果…
          </div>
        )}
      <div className="shrink-0 border-b border-white/[0.05] px-4 py-3">
        <h2 className="text-sm font-black text-slate-100">
          真实委托与成交事件
        </h2>
        <p className="mt-1 text-[10px] text-slate-600">
          仅展示已持久化事件；命令确认不会被当作券商成交。
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-auto custom-scrollbar">
        <table className="w-full min-w-[900px] text-left text-xs">
          <thead className="sticky top-0 bg-[#0b1628] text-[10px] font-black uppercase tracking-[0.08em] text-slate-600">
            <tr>
              <th className="px-4 py-2.5">时间</th>
              <th className="px-3 py-2.5">批次</th>
              <th className="px-3 py-2.5">事件</th>
              <th className="px-3 py-2.5">应用状态</th>
              <th className="px-3 py-2.5">Client Order</th>
              <th className="px-4 py-2.5">Broker Order / 异常</th>
            </tr>
          </thead>
          <tbody>
            {batchEventsResult.fetching && batchEvents.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-12 text-center text-slate-600"
                  role="status"
                >
                  <Loader2
                    className="mr-2 inline-block h-4 w-4 animate-spin motion-reduce:animate-none"
                    aria-hidden="true"
                  />
                  正在读取订单事件…
                </td>
              </tr>
            ) : batchEvents.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className="px-4 py-12 text-center text-slate-600"
                >
                  尚无持久化做 T 委托或成交事件
                </td>
              </tr>
            ) : (
              batchEvents.map(event => (
                <tr
                  key={event.eventId}
                  className="border-b border-white/[0.04] hover:bg-white/[0.025]"
                >
                  <td className="px-4 py-3 font-mono text-[10px] text-slate-400">
                    {formatTime(event.createdAt)}
                  </td>
                  <td className="px-3 py-3 font-mono text-[10px] text-slate-400">
                    {event.batchId?.slice(0, 12) || '--'}
                  </td>
                  <td className="px-3 py-3 font-black text-slate-200">
                    {event.eventType === 'TRADE' ? '真实成交' : '委托状态'}
                  </td>
                  <td className="px-3 py-3">
                    <span
                      className={cn(
                        'border px-2 py-1 text-[9px] font-black',
                        event.status === 'APPLIED'
                          ? 'border-emerald-400/20 text-emerald-200'
                          : 'border-amber-400/20 text-amber-200'
                      )}
                    >
                      {event.status}
                    </span>
                  </td>
                  <td className="px-3 py-3 font-mono text-[10px] text-slate-500">
                    {event.clientOrderId}
                  </td>
                  <td className="px-4 py-3 font-mono text-[10px] text-slate-500">
                    {event.brokerOrderId || '--'}
                    {event.error && (
                      <div className="mt-1 text-rose-300">{event.error}</div>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      {batchEventsResult.data?.tTradeBatchEventsPage.pageInfo.hasNextPage && (
        <div className="shrink-0 border-t border-white/[0.05] p-2 text-center">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={batchEventsResult.fetching}
            onClick={() =>
              setEventAfter(
                batchEventsResult.data?.tTradeBatchEventsPage.pageInfo
                  .endCursor ?? null
              )
            }
            className="h-8 text-[10px] text-slate-400"
          >
            {batchEventsResult.fetching ? '加载中…' : '加载更多事件'}
          </Button>
        </div>
      )}
    </div>
  );

  const signalsView = (
    <TTradeSignalsView
      actionLoading={actionLoading}
      accountId={accountId}
      canApproveAccount={Boolean(readiness?.canApprove)}
      candidateTrace={candidateTraceForUi}
      candidateTraceError={
        candidateTraceIdentityMismatch
          ? '候选追溯响应身份不一致，已阻止展示'
          : candidateTraceResult.error
            ? '候选追溯暂不可用，请稍后重试'
            : undefined
      }
      candidateTraceLoading={candidateTraceResult.fetching}
      dataTrusted={signalSnapshotTrusted}
      evaluations={accountBoundSignalEvaluations}
      evaluationsError={signalEvaluationsResult.error?.message}
      historyByCode={quoteHistoryByCode}
      hasMoreEvaluations={Boolean(signalEvaluationsPage?.pageInfo.hasNextPage)}
      loadingEvaluations={signalEvaluationsResult.fetching}
      loadingMonitor={monitorResult.fetching}
      monitorError={monitorResult.error?.message || monitor?.lastError}
      monitor={monitor}
      onApprove={(session, snapshot) =>
        void handleSignal(
          'approve',
          session.runId,
          snapshot.pendingEntryIntentId!,
          snapshot
        )
      }
      onLoadMoreEvaluations={() =>
        setSignalAfter(signalEvaluationsPage?.pageInfo.endCursor ?? null)
      }
      onRequestCandidateTrace={setSelectedTrace}
      onReject={(session, snapshot) =>
        void handleSignal(
          'reject',
          session.runId,
          snapshot.pendingEntryIntentId!,
          snapshot
        )
      }
      quotes={realTimeQuotesByCode}
      selectedTrace={selectedTraceForCurrentAccount}
    />
  );

  const diagnosticsView = (
    <div className="studio-workspace-surface h-full min-h-0">
      <TTradeSignalDiagnosticsPanel
        diagnostics={diagnosticsForCurrentAccount}
        evaluations={accountBoundSignalEvaluations}
        error={signalDiagnosticsResult.error?.message}
        loading={
          signalDiagnosticsResult.fetching || signalEvaluationsResult.fetching
        }
      />
    </div>
  );
  const settingsView = (
    <div className="studio-workspace-surface flex h-full min-h-0 flex-col">
      {monitorResult.error && (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-4 py-2.5 text-[10px] leading-4 text-rose-100"
        >
          <AlertTriangle
            className="mt-0.5 h-3.5 w-3.5 shrink-0"
            aria-hidden="true"
          />
          配置读取失败；当前表单可能是上次成功读取的草稿，保存已暂停，请先刷新。
        </div>
      )}
      {!monitorResult.error && monitorResult.fetching && (
        <div
          role="status"
          aria-busy="true"
          className="flex shrink-0 items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-4 py-2 text-[9px] text-cyan-100"
        >
          <Loader2
            className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在刷新配置版本…
        </div>
      )}
      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.05] px-4 py-3">
        <div>
          <h2 className="text-sm font-black text-slate-100">全局策略参数</h2>
          <p className="mt-0.5 text-[10px] text-slate-600">
            对账户内所有未忽略的合格持仓统一生效
          </p>
        </div>
        <span className="font-mono text-[10px] text-slate-600">
          配置版本 v{monitor?.configVersion ?? 0}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <div className="grid gap-px bg-white/[0.05] xl:grid-cols-2">
          <section className="bg-[#0a1424] p-4 xl:col-span-2">
            <div className="mb-4 border-b border-white/[0.05] pb-3">
              <div className="text-xs font-black text-slate-200">
                运行与资金约束
              </div>
              <div className="mt-1 text-[10px] text-slate-600">
                控制单次金额、全局并发与账户总暴露
              </div>
            </div>
            <div className="space-y-4">
              <div className="space-y-1.5">
                <Label
                  htmlFor="t-trade-mode"
                  className="text-xs font-bold text-slate-400"
                >
                  运行模式
                </Label>
                <Select
                  value={form.mode}
                  onValueChange={value =>
                    setField('mode', value === 'live' ? 'live' : 'paper')
                  }
                >
                  <SelectTrigger
                    id="t-trade-mode"
                    className="h-9 rounded-sm border-white/10 bg-[#07111f] text-xs focus:ring-red-500/60"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="paper">
                      模拟观察（推荐先验证）
                    </SelectItem>
                    <SelectItem value="live">实盘执行</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-2 2xl:grid-cols-4">
                <NumericField
                  id="t-trade-target-amount"
                  label="目标单次金额"
                  suffix="元"
                  value={form.targetTradeAmount}
                  onChange={value => setField('targetTradeAmount', value)}
                />
                <NumericField
                  id="t-trade-max-amount"
                  label="单次金额硬上限"
                  suffix="元"
                  value={form.maxTradeAmount}
                  onChange={value => setField('maxTradeAmount', value)}
                />
                <NumericField
                  id="t-trade-concurrency"
                  label="账户并发批次"
                  suffix="批"
                  value={form.maxConcurrentBatches}
                  onChange={value => setField('maxConcurrentBatches', value)}
                />
                <NumericField
                  id="t-trade-total-exposure"
                  label="账户总 T 暴露"
                  suffix="%"
                  value={form.maxTotalTExposurePct}
                  onChange={value => setField('maxTotalTExposurePct', value)}
                />
              </div>

              <div className="border-t border-white/[0.05] pt-4">
                <div className="mb-3 text-[10px] font-black uppercase tracking-[0.12em] text-slate-600">
                  动态退出
                </div>
                <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-2 2xl:grid-cols-4">
                  <NumericField
                    id="t-trade-target"
                    label="收益武装线"
                    suffix="%"
                    value={form.targetProfitPct}
                    onChange={value => setField('targetProfitPct', value)}
                  />
                  <NumericField
                    id="t-trade-floor"
                    label="初始保护线"
                    suffix="%"
                    value={form.baseFloorPct}
                    onChange={value => setField('baseFloorPct', value)}
                  />
                  <NumericField
                    id="t-trade-max-gap"
                    label="最大回撤宽度"
                    suffix="%"
                    value={form.maxGapPct}
                    onChange={value => setField('maxGapPct', value)}
                  />
                  <NumericField
                    id="t-trade-initial-gap"
                    label="初始回撤宽度"
                    suffix="%"
                    value={form.initialGapPct}
                    onChange={value => setField('initialGapPct', value)}
                  />
                  <NumericField
                    id="t-trade-gap-slope"
                    label="放宽斜率"
                    value={form.trailingGapSlope}
                    onChange={value => setField('trailingGapSlope', value)}
                  />
                </div>
              </div>

              <div className="border border-emerald-400/15 bg-emerald-400/[0.03] p-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <Label
                      htmlFor="t-trade-high-profit-lock-enabled"
                      className="text-xs font-bold text-slate-300"
                    >
                      高利润保护
                    </Label>
                    <p className="mt-1 text-[10px] text-slate-600">
                      按可执行买一计算峰值；进入高利润区后限制最大利润回吐
                    </p>
                  </div>
                  <input
                    id="t-trade-high-profit-lock-enabled"
                    type="checkbox"
                    checked={form.highProfitLockEnabled}
                    onChange={event =>
                      setField('highProfitLockEnabled', event.target.checked)
                    }
                    className="h-4 w-4 cursor-pointer accent-emerald-500 focus-visible:ring-2 focus-visible:ring-emerald-500/60"
                  />
                </div>
                {form.highProfitLockEnabled && (
                  <div className="mt-3 grid grid-cols-2 gap-3 border-t border-white/[0.05] pt-3">
                    <NumericField
                      id="t-trade-high-profit-arm"
                      label="高利润武装线"
                      suffix="%"
                      value={form.highProfitArmPct}
                      onChange={value => setField('highProfitArmPct', value)}
                    />
                    <NumericField
                      id="t-trade-high-profit-drawdown"
                      label="峰值最大回吐"
                      suffix="%"
                      value={form.highProfitMaxDrawdownPct}
                      onChange={value =>
                        setField('highProfitMaxDrawdownPct', value)
                      }
                    />
                  </div>
                )}
              </div>

              <div className="border border-amber-400/15 bg-amber-400/[0.03] p-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <Label
                      htmlFor="t-trade-rapid-reversal-enabled"
                      className="text-xs font-bold text-slate-300"
                    >
                      极速反转退出
                    </Label>
                    <p className="mt-1 text-[10px] text-slate-600">
                      高利润峰值形成后，短时间内连续确认买一收益快速回落即紧急退出
                    </p>
                  </div>
                  <input
                    id="t-trade-rapid-reversal-enabled"
                    type="checkbox"
                    checked={form.rapidReversalEnabled}
                    onChange={event =>
                      setField('rapidReversalEnabled', event.target.checked)
                    }
                    className="h-4 w-4 cursor-pointer accent-amber-500 focus-visible:ring-2 focus-visible:ring-amber-500/60"
                  />
                </div>
                {form.rapidReversalEnabled && (
                  <div className="mt-3 grid grid-cols-3 gap-3 border-t border-white/[0.05] pt-3">
                    <NumericField
                      id="t-trade-rapid-reversal-window"
                      label="反转窗口"
                      suffix="秒"
                      value={form.rapidReversalWindowSeconds}
                      onChange={value =>
                        setField('rapidReversalWindowSeconds', value)
                      }
                    />
                    <NumericField
                      id="t-trade-rapid-reversal-drawdown"
                      label="回吐阈值"
                      suffix="%"
                      value={form.rapidReversalDrawdownPct}
                      onChange={value =>
                        setField('rapidReversalDrawdownPct', value)
                      }
                    />
                    <NumericField
                      id="t-trade-rapid-reversal-confirm"
                      label="连续确认"
                      suffix="Tick"
                      value={form.rapidReversalConfirmTicks}
                      onChange={value =>
                        setField('rapidReversalConfirmTicks', value)
                      }
                    />
                  </div>
                )}
              </div>

              <div className="border border-white/[0.07] bg-[#07111f]/60 p-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <Label
                      htmlFor="t-trade-limit-up-touch-enabled"
                      className="text-xs font-bold text-slate-300"
                    >
                      涨停触达退出
                    </Label>
                    <p className="mt-1 text-[10px] text-slate-600">
                      活跃 T
                      批次的可执行买一达到涨停价时，用昨日老仓完成等量退出
                    </p>
                  </div>
                  <input
                    id="t-trade-limit-up-touch-enabled"
                    type="checkbox"
                    checked={form.limitUpTouchExitEnabled}
                    onChange={event =>
                      setField('limitUpTouchExitEnabled', event.target.checked)
                    }
                    className="h-4 w-4 cursor-pointer accent-red-500 focus-visible:ring-2 focus-visible:ring-red-500/60"
                  />
                </div>
                {form.limitUpTouchExitEnabled && (
                  <div className="mt-3 max-w-48 border-t border-white/[0.05] pt-3">
                    <NumericField
                      id="t-trade-limit-up-touch-tolerance"
                      label="涨停容差"
                      suffix="Tick"
                      value={form.limitUpTouchToleranceTicks}
                      onChange={value =>
                        setField('limitUpTouchToleranceTicks', value)
                      }
                    />
                  </div>
                )}
              </div>

              <div className="border border-white/[0.07] bg-[#07111f]/60 p-3">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <Label
                      htmlFor="t-trade-hard-stop-enabled"
                      className="text-xs font-bold text-slate-300"
                    >
                      硬止损保护
                    </Label>
                    <p className="mt-1 text-[10px] text-slate-600">
                      可选风险底线；关闭后不会因亏损比例自动卖出
                    </p>
                  </div>
                  <input
                    id="t-trade-hard-stop-enabled"
                    type="checkbox"
                    checked={form.hardStopEnabled}
                    onChange={event =>
                      setField('hardStopEnabled', event.target.checked)
                    }
                    className="h-4 w-4 cursor-pointer accent-red-500 focus-visible:ring-2 focus-visible:ring-red-500/60"
                  />
                </div>
                {form.hardStopEnabled && (
                  <div className="mt-3 max-w-48 border-t border-white/[0.05] pt-3">
                    <NumericField
                      id="t-trade-hard-stop"
                      label="硬止损线"
                      suffix="%"
                      value={form.hardStopPct}
                      onChange={value => setField('hardStopPct', value)}
                    />
                  </div>
                )}
              </div>

              <div className="border border-white/[0.07] bg-[#07111f]/60 p-3">
                <Label
                  htmlFor="t-trade-time-exit-mode"
                  className="text-xs font-bold text-slate-300"
                >
                  时间退出策略
                </Label>
                <p className="mt-1 text-[10px] text-slate-600">
                  默认无限期保护，仅在明确选择后按时间自动卖出
                </p>
                <Select
                  value={form.timeExitMode}
                  onValueChange={value =>
                    setField(
                      'timeExitMode',
                      value as SettingsForm['timeExitMode']
                    )
                  }
                >
                  <SelectTrigger
                    id="t-trade-time-exit-mode"
                    className="mt-3 h-9 rounded-sm border-white/10 bg-[#07111f] text-xs focus:ring-primary/60"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={TTradeTimeExitMode.Unlimited}>
                      无限期保护
                    </SelectItem>
                    <SelectItem value={TTradeTimeExitMode.EndOfDay}>
                      当日收盘前退出
                    </SelectItem>
                    <SelectItem value={TTradeTimeExitMode.MaxHoldingDays}>
                      持有 N 个交易日退出
                    </SelectItem>
                  </SelectContent>
                </Select>

                {form.timeExitMode !== TTradeTimeExitMode.Unlimited && (
                  <div className="mt-3 grid grid-cols-2 gap-3 border-t border-white/[0.05] pt-3">
                    {form.timeExitMode ===
                      TTradeTimeExitMode.MaxHoldingDays && (
                      <NumericField
                        id="t-trade-max-holding-days"
                        label="最长持有"
                        suffix="交易日"
                        value={form.maxHoldingTradingDays}
                        onChange={value =>
                          setField('maxHoldingTradingDays', value)
                        }
                      />
                    )}
                    <div className="space-y-1.5">
                      <Label
                        htmlFor="t-trade-time-exit-time"
                        className="text-xs font-bold text-slate-400"
                      >
                        退出时刻
                      </Label>
                      <Input
                        id="t-trade-time-exit-time"
                        type="time"
                        value={form.timeExitTime}
                        onChange={event =>
                          setField('timeExitTime', event.target.value)
                        }
                        className="h-9 rounded-sm border-white/10 bg-[#07111f] font-mono text-xs focus-visible:ring-primary/60"
                      />
                    </div>
                  </div>
                )}

                {!form.hardStopEnabled &&
                  form.timeExitMode === TTradeTimeExitMode.Unlimited && (
                    <div className="mt-3 flex items-start gap-2 border-t border-amber-400/10 pt-3 text-[10px] leading-4 text-amber-200/80">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      未达到收益武装线的批次可能长期持有，仍可通过人工操作结束。
                    </div>
                  )}
              </div>
            </div>
          </section>

          <section className="bg-[#0a1424] p-4 xl:col-span-2">
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.05] pb-3">
              <div>
                <div className="text-xs font-black text-slate-200">
                  V3 有状态信号规则
                </div>
                <div className="mt-1 text-[10px] text-slate-600">
                  因果窗口、双 FSM、可解释评分、硬门禁与 episode 防重复
                </div>
              </div>
              <div className="font-mono text-[9px] text-slate-600">
                {monitor?.signalPolicy.policyVersion || '等待策略版本'} ·
                feature {monitor?.signalPolicy.featureSchemaVersion || '--'}
              </div>
            </div>

            <div className="mb-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <NumericField
                id="t-trade-deviation"
                label="确认价偏离"
                suffix="%"
                value={form.maxPriceDeviationPct}
                onChange={value => setField('maxPriceDeviationPct', value)}
              />
              <NumericField
                id="t-trade-cooldown"
                label="批次冷却时间"
                suffix="秒"
                value={form.cooldownSeconds}
                onChange={value => setField('cooldownSeconds', value)}
              />
            </div>

            <TTradeSignalPolicyEditor
              conflictPolicy={configConflictPolicy}
              conflictVersion={configConflictVersion}
              form={form.signalPolicy}
              localErrors={policyLocalErrors}
              onChange={setSignalPolicyField}
              onPreview={() => void handlePreviewPolicy()}
              preview={policyPreview}
              previewLoading={previewPolicyResult.fetching}
              serverConfigVersion={draftConfigVersionRef.current}
            />

            <div className="mt-5 border-t border-white/[0.05] pt-4">
              <Label
                htmlFor="t-trade-ignore-code"
                className="text-xs font-bold text-slate-300"
              >
                忽略股票代码
              </Label>
              <p className="mt-1 text-[10px] text-slate-600">
                忽略名单属于外部发意图门禁，不改变服务端三层信号状态。
              </p>
              <div className="mt-3 flex gap-2">
                <Input
                  id="t-trade-ignore-code"
                  value={ignoreInput}
                  onChange={event => setIgnoreInput(event.target.value)}
                  onKeyDown={event => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      handleAddIgnore();
                    }
                  }}
                  placeholder="例如 600000 或 600000.SH"
                  className="h-9 rounded-sm border-white/10 bg-[#07111f] font-mono text-xs focus-visible:ring-red-500/60"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-9 rounded-sm border-white/10"
                  disabled={!ignoreInput.trim() || actionLoading}
                  onClick={handleAddIgnore}
                >
                  添加
                </Button>
              </div>
              <div className="mt-3 flex min-h-8 flex-wrap gap-1.5">
                {ignoredCodes.length === 0 ? (
                  <span className="text-[10px] text-slate-700">
                    当前未忽略任何股票
                  </span>
                ) : (
                  ignoredCodes.map(code => (
                    <button
                      key={code}
                      type="button"
                      disabled={actionLoading}
                      onClick={() => handleIgnore(code, false)}
                      className="inline-flex items-center gap-1 border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-[10px] text-slate-400 outline-none transition-colors hover:border-rose-400/30 hover:text-rose-200 focus-visible:ring-2 focus-visible:ring-red-500/60"
                      aria-label={'从忽略名单移除 ' + code}
                    >
                      {code}
                      <X className="h-3 w-3" />
                    </button>
                  ))
                )}
              </div>
            </div>
          </section>
        </div>

        {form.mode === 'live' && (
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-amber-400/15 bg-amber-400/[0.06] px-4 py-3 text-xs font-bold leading-5 text-amber-100">
            <input
              type="checkbox"
              checked={form.acknowledged}
              onChange={event => setField('acknowledged', event.target.checked)}
              className="mt-0.5 h-4 w-4 shrink-0 accent-amber-400"
            />
            我确认所有持仓形成 T
            批次后，当前已启用的退出规则可自动提交实盘卖单。
          </label>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-white/[0.06] bg-[#091322] px-4 py-3">
        <div className="text-[10px] text-slate-600">
          保存后立即应用于当前账户的单一 T 策略运行
        </div>
        <Button
          type="button"
          className="h-9 rounded-sm bg-red-500 px-5 text-xs text-white hover:bg-red-400"
          disabled={
            !accountId ||
            !monitor ||
            Boolean(monitorResult.error) ||
            actionLoading ||
            !policyPreview?.valid ||
            policyPreview.configVersion !== draftConfigVersionRef.current ||
            policyLocalErrors.length > 0 ||
            (form.mode === 'live' && !form.acknowledged)
          }
          onClick={() => persist(Boolean(monitor?.enabled), ignoredCodes, true)}
        >
          {saveResult.fetching ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          保存全局设置
        </Button>
      </div>
    </div>
  );

  const content = (
    <div className="flex h-full min-h-0 flex-col">
      {toolbar}
      <div className="min-h-0 flex-1">
        {workspaceMode === 'REPLAY' ? (
          <TTradeReplayPanel accountId={accountId} form={form} />
        ) : activeMode === 'MONITOR' ? (
          monitorView
        ) : activeMode === 'SIGNALS' ? (
          signalsView
        ) : activeMode === 'DIAGNOSTICS' ? (
          diagnosticsView
        ) : activeMode === 'POSITIONS' ? (
          positionsView
        ) : activeMode === 'EVENTS' ? (
          eventsView
        ) : (
          settingsView
        )}
      </div>
    </div>
  );

  return (
    <StudioWorkbench
      activeMode={activeMode}
      className="h-full min-h-0"
      content={content}
      isPage
      modes={workspaceMode === 'REALTIME' ? tTradeModes : []}
      onModeChange={mode => setActiveMode(mode as TTradeStudioMode)}
      sidebar={workspaceMode === 'REPLAY' ? replaySidebar : sidebar}
      sidebarSizing={{
        defaultWidth: 312,
        maxWidth: 420,
        minWidth: 260,
        storageScope: 't-trade-studio',
      }}
      showSidebar
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                workspaceMode === 'REPLAY'
                  ? 'bg-cyan-400'
                  : monitor?.enabled
                    ? 'bg-emerald-400'
                    : 'bg-slate-600'
              )}
            />
            {workspaceMode === 'REPLAY'
              ? '历史回放测试模式'
              : monitor?.enabled
                ? '全局监控运行中'
                : '全局监控已停止'}
          </span>
          <span className="text-slate-700">|</span>
          <span className="font-mono">{accountId || '未配置账户'}</span>
          {workspaceMode === 'REALTIME' && (
            <>
              <span className="text-slate-700">|</span>
              <span className="inline-flex items-center gap-1.5">
                <Clock3 className="h-3 w-3" />
                最近同步 {formatTime(monitor?.lastReconciledAt)}
              </span>
            </>
          )}
        </>
      }
      statusBarRight={
        workspaceMode === 'REPLAY' ? (
          <>
            <span>BACKTEST Broker</span>
            <span className="text-slate-700">|</span>
            <span>最长 20 个交易日</span>
            <span className="text-slate-700">|</span>
            <span>实时监控互不影响</span>
          </>
        ) : (
          <>
            <span className="font-mono">
              运行 {monitor?.strategyRunId?.slice(0, 8) || '--'}
            </span>
            <span className="text-slate-700">|</span>
            <span>
              标的 v{monitor?.universeRevision ?? 0} · 配置 v
              {monitor?.configVersion ?? 0}
            </span>
            <span className="text-slate-700">|</span>
            <span>
              待确认 {monitor?.pendingSignalCount ?? 0} · 活跃{' '}
              {monitor?.activeBatchCount ?? 0}
            </span>
          </>
        )
      }
      theme={{
        icon: workspaceMode === 'REPLAY' ? FlaskConical : Radar,
        name: 'blue',
        title: workspaceMode === 'REPLAY' ? '做T回放测试' : '做T助手',
      }}
    />
  );
}
