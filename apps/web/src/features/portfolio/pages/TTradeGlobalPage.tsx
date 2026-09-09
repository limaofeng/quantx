import {
  Activity,
  AlertTriangle,
  BarChart3,
  Clock3,
  FlaskConical,
  GitBranch,
  ListChecks,
  Loader2,
  Play,
  Plus,
  Radar,
  RefreshCw,
  Save,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  WalletCards,
  X,
} from 'lucide-react';
import * as React from 'react';
import { useClient, useMutation, useQuery, useSubscription } from 'urql';

import { StudioWorkbench } from '@/components/studio-workbench/StudioWorkbench';
import type { StudioMode } from '@/components/studio-workbench/types';
import { useStudioNavigate } from '@/components/studio-workspace/useStudioNavigate';
import { getShanghaiDateKey } from '@/components/trading-chart/utils/time-utils';
import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  useGraphqlWsStatus,
  type GraphqlWsStatus,
} from '@/core/graphql/ws-status';
import {
  mapExecutionTraceView,
  mapStrategyDecisionView,
  type ExecutionTraceView,
  type StrategyDecision,
} from '@/features/strategies/domain';
import {
  StrategyDecisionHistoryQuery,
  StrategyExecutionTraceQuery,
} from '@/features/strategies/hooks/strategyInstanceOperations';
import { useTradingSafety } from '@/features/trading-safety/trading-safety-context';
import { useFragment as readFragment } from '@/generated/gql/fragment-masking';
import {
  TTradeBatchScope,
  TTradeRolloutTarget,
  TTradeSignalEvaluationKind,
  TTradeTimeExitMode,
  type TTradeBatchEvent,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { useTradingDays } from '@/hooks/useTradingDays';
import { tradingAccountConfig } from '@/shared/utils/env';
import { cn } from '@/utils/cn';

import { useLatestMarketQuotes } from '../hooks/useRealTimeHoldings';
import {
  ActivateTTradeLiveMutation,
  ApproveTTradeEntryV3Mutation,
  CancelTTradeOrderMutation,
  ImportTTradeExternalEntryMutation,
  PauseTTradeEntriesMutation,
  PreviewTTradeSignalPolicyMutation,
  ReconcileTTradeGlobalMonitorMutation,
  RecordTTradeClientTelemetryMutation,
  RejectTTradeEntryV3Mutation,
  SaveTTradeGlobalMonitorMutation,
  SyncTTradeSourceOrdersMutation,
  TTradeBatchesPageQuery,
  TTradeBatchEventsPageQuery,
  TTradeCandidateTraceQuery,
  TTradeGlobalMonitorQuery,
  TTradeSignalDiagnosticsQuery,
  TTradeSignalEvaluationDetailQuery,
  TTradeSignalEvaluationsQuery,
  TTradeSignalPolicyFieldsFragment,
  TTradeSignalSnapshotFieldsFragment,
  TTradeSourceOrdersQuery,
  TTradeUpdatesSubscription,
} from '../hooks/useTTradeGlobal';

import type { ActivitySignalEvaluation } from './t-trade-global/activity';
import {
  createRollingDiagnosticRange,
  hasCandidateTraceIdentity,
} from './t-trade-global/clientTrust';
import {
  adaptLiveBatch,
  adaptLiveBatchSummary,
} from './t-trade-global/liveBatchAdapter';
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
  cloneReplayCostForm,
  cloneSettingsForm,
  defaultReplayCostForm,
  replaySettingsDifferenceCount,
  settingsFormFromReplaySettings,
  updateSignalPolicyValue,
  type ReplayCostForm,
} from './t-trade-global/replaySettings';
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
import { TTradePanelBoundary } from './t-trade-global/TTradePanelBoundary';
import type {
  TTradeExecutionMode,
  TTradePositionBatch,
} from './t-trade-global/TTradePositionsView';
import type { ReplaySidebarContext } from './t-trade-global/TTradeReplaySidebar';
import {
  TAssistantPaperPanel,
  TAssistantLivePanel,
  TTradeActivityView,
  TTradeExecutionSettingsPanel,
  TTradeLiveDecisionAudit,
  TTradePositionsView,
  TTradeReplaySidebar,
  TTradeSignalDiagnosticsPanel,
  TTradeSignalPolicyEditor,
  TTradeSignalsView,
} from './t-trade-global/TTradeSecondaryViews';
import type { SignalPolicyPreviewLike } from './t-trade-global/TTradeSignalPolicyEditor';
import type { CandidateTraceSelection } from './t-trade-global/TTradeSignalsView';
import type {
  ReplayWorkspaceView,
  SettingsForm,
  SignalPolicyForm,
  SignalPolicyFormValue,
  TTradeStudioMode,
} from './t-trade-global/types';
import { useLiveQuoteHistory } from './t-trade-global/useLiveQuoteHistory';
import {
  formatNumber,
  formatTime,
  hasInstrumentName,
  integerValue,
  numberValue,
  replayIdempotencyKey,
  resolveInstrumentName,
} from './t-trade-global/utils';

const TTradeReplayPanel = React.lazy(() =>
  import('./t-trade-global/TTradeReplayPanel').then(module => ({
    default: module.TTradeReplayPanel,
  }))
);

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

const tTradeModes: StudioMode[] = [
  { id: 'MONITOR', icon: Radar, label: '总览' },
  { id: 'SIGNALS', icon: Activity, label: '信号' },
  { id: 'AUDIT', icon: GitBranch, label: '决策审计' },
  { id: 'DIAGNOSTICS', icon: BarChart3, label: '诊断' },
  { id: 'POSITIONS', icon: WalletCards, label: '仓位与批次' },
  { id: 'EVENTS', icon: ListChecks, label: '运行动态' },
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
      <Label htmlFor={id} className="text-ui-label font-bold text-slate-400">
        {label}
      </Label>
      <div className="relative">
        <Input
          id={id}
          disabled={disabled}
          inputMode="decimal"
          value={value}
          onChange={event => onChange(event.target.value)}
          className="h-9 rounded-sm border-white/10 bg-[#07111f] pr-10 font-mono text-ui-label focus-visible:ring-red-500/60 disabled:cursor-not-allowed disabled:opacity-50"
        />
        {suffix && (
          <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-ui-caption font-bold text-slate-600">
            {suffix}
          </span>
        )}
      </div>
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
    'REALTIME' | 'REPLAY' | 'PAPER' | 'LIVE_ASSISTANT'
  >('REALTIME');
  const [activeReplayView, setActiveReplayView] =
    React.useState<ReplayWorkspaceView>('OVERVIEW');
  const [replaySidebarContext, setReplaySidebarContext] =
    React.useState<ReplaySidebarContext | null>(null);
  const { tradingDays } = useTradingDays('SH', 3);
  const currentShanghaiDate = getShanghaiDateKey(new Date());
  const isCurrentTradingDay = tradingDays.length
    ? tradingDays.includes(currentShanghaiDate)
    : undefined;
  const [activeMode, setActiveMode] =
    React.useState<TTradeStudioMode>('MONITOR');
  const [form, setForm] = React.useState<SettingsForm>(defaultForm);
  const [replayForm, setReplayForm] = React.useState<SettingsForm>(() =>
    cloneSettingsForm(defaultForm)
  );
  const [replayBaseForm, setReplayBaseForm] = React.useState<SettingsForm>(() =>
    cloneSettingsForm(defaultForm)
  );
  const [replayCosts, setReplayCosts] = React.useState<ReplayCostForm>(() =>
    cloneReplayCostForm(defaultReplayCostForm)
  );
  const [replayBaseCosts, setReplayBaseCosts] = React.useState<ReplayCostForm>(
    () => cloneReplayCostForm(defaultReplayCostForm)
  );
  const [replayConfigVersion, setReplayConfigVersion] = React.useState(0);
  const [replaySettingsAccountId, setReplaySettingsAccountId] =
    React.useState('');
  const [replaySettingsRestoring, setReplaySettingsRestoring] =
    React.useState(false);
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
          const result = await client
            .query(
              TTradeGlobalMonitorQuery,
              { accountId: expectedAccountId },
              {
                requestPolicy: 'network-only',
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
          } else if (
            signalSnapshotRefreshCoordinator.isCurrent(epoch, expectedAccountId)
          ) {
            setTrustedSignalSnapshotEpoch(null);
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
  const replayDraftDirty = React.useMemo(
    () =>
      replaySettingsDifferenceCount(
        replayForm,
        replayCosts,
        replayBaseForm,
        replayBaseCosts
      ) > 0,
    [replayBaseCosts, replayBaseForm, replayCosts, replayForm]
  );
  React.useEffect(() => {
    if (!monitor) return;
    const shouldInitialize = replaySettingsAccountId !== monitor.accountId;
    const shouldAdvanceCleanDraft =
      !replayDraftDirty && replayConfigVersion !== monitor.configVersion;
    if (!shouldInitialize && !shouldAdvanceCleanDraft) return;
    const nextForm = settingsFormFromReplaySettings(monitor);
    setReplayForm(cloneSettingsForm(nextForm));
    setReplayBaseForm(cloneSettingsForm(nextForm));
    setReplayCosts(cloneReplayCostForm(defaultReplayCostForm));
    setReplayBaseCosts(cloneReplayCostForm(defaultReplayCostForm));
    setReplayConfigVersion(monitor.configVersion);
    setReplaySettingsAccountId(monitor.accountId);
  }, [monitor, replayConfigVersion, replayDraftDirty, replaySettingsAccountId]);
  const replayLiveSettingsStale = Boolean(
    monitor && replayConfigVersion !== monitor.configVersion
  );
  const setReplayField = React.useCallback(
    <K extends keyof SettingsForm>(field: K, value: SettingsForm[K]) => {
      setReplayForm(current => ({ ...current, [field]: value }));
    },
    []
  );
  const setReplaySignalPolicyField = React.useCallback(
    (field: keyof SignalPolicyForm, value: SignalPolicyFormValue) => {
      setReplayForm(current => updateSignalPolicyValue(current, field, value));
    },
    []
  );
  const setReplayCostField = React.useCallback(
    (field: keyof ReplayCostForm, value: string) => {
      setReplayCosts(current => ({ ...current, [field]: value }));
    },
    []
  );
  const copyReplaySettings = React.useCallback(
    (nextForm: SettingsForm, nextCosts: ReplayCostForm) => {
      setReplayForm(cloneSettingsForm(nextForm));
      setReplayCosts(cloneReplayCostForm(nextCosts));
    },
    []
  );
  const restoreReplaySettings = React.useCallback(async () => {
    if (!accountId) return false;
    setReplaySettingsRestoring(true);
    try {
      const result = await client
        .query(
          TTradeGlobalMonitorQuery,
          { accountId },
          { requestPolicy: 'network-only' }
        )
        .toPromise();
      const payload = result.data?.tTradeGlobalMonitor;
      const policy = readFragment(
        TTradeSignalPolicyFieldsFragment,
        payload?.signalPolicy
      );
      if (!payload || !policy || result.error) {
        throw new Error(result.error?.message || '读取当前实盘参数失败');
      }
      const nextForm = settingsFormFromReplaySettings({
        ...payload,
        signalPolicy: policy,
      });
      setReplayForm(cloneSettingsForm(nextForm));
      setReplayBaseForm(cloneSettingsForm(nextForm));
      setReplayCosts(cloneReplayCostForm(defaultReplayCostForm));
      setReplayBaseCosts(cloneReplayCostForm(defaultReplayCostForm));
      setReplayConfigVersion(payload.configVersion);
      setReplaySettingsAccountId(payload.accountId);
      toast({
        title: '已还原当前实盘参数',
        description: `回测草稿已更新为实盘配置 v${payload.configVersion}，未修改实盘运行。`,
      });
      return true;
    } catch (error) {
      toast({
        title: '无法还原实盘参数',
        description: error instanceof Error ? error.message : '请求失败',
        variant: 'destructive',
      });
      return false;
    } finally {
      setReplaySettingsRestoring(false);
    }
  }, [accountId, client, toast]);
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

  const [currentBatchAfter, setCurrentBatchAfter] = React.useState<
    string | null
  >(null);
  const [historyBatchAfter, setHistoryBatchAfter] = React.useState<
    string | null
  >(null);
  const [eventAfter, setEventAfter] = React.useState<string | null>(null);
  const [signalAfter, setSignalAfter] = React.useState<string | null>(null);
  const [requestedSignalDetailId, setRequestedSignalDetailId] = React.useState<
    string | null
  >(null);
  const [activitySignalAfter, setActivitySignalAfter] = React.useState<
    string | null
  >(null);
  const [includeActivityDiagnostics, setIncludeActivityDiagnostics] =
    React.useState(false);
  const [selectedTrace, setSelectedTrace] =
    React.useState<CandidateTraceSelection | null>(null);
  const [focusedSignalStockCode, setFocusedSignalStockCode] = React.useState<
    string | null
  >(null);
  const selectedTraceForCurrentAccount =
    selectedTrace?.accountId === accountId ? selectedTrace : null;
  React.useEffect(() => {
    setSelectedTrace(null);
    setFocusedSignalStockCode(null);
  }, [accountId]);
  const [currentBatches, setCurrentBatches] = React.useState<
    TTradePositionBatch[]
  >([]);
  const [historyBatches, setHistoryBatches] = React.useState<
    TTradePositionBatch[]
  >([]);
  const batches = React.useMemo(
    () => [...currentBatches, ...historyBatches],
    [currentBatches, historyBatches]
  );
  const activityBatches = React.useMemo(
    () => batches.map(batch => ({ ...batch, version: batch.version ?? 0 })),
    [batches]
  );
  const [batchEvents, setBatchEvents] = React.useState<TTradeBatchEvent[]>([]);
  const [inspectedBatchId, setInspectedBatchId] = React.useState<string | null>(
    null
  );
  const [activityBatchFilter, setActivityBatchFilter] = React.useState<
    string | null
  >(null);
  const [positionFocusBatchId, setPositionFocusBatchId] = React.useState<
    string | null
  >(null);
  const [signalEvaluations, setSignalEvaluations] = React.useState<
    SignalEvaluationLike[]
  >([]);
  const [activitySignalEvaluations, setActivitySignalEvaluations] =
    React.useState<ActivitySignalEvaluation[]>([]);
  const accountBoundSignalEvaluations = signalEvaluations.filter(
    item => item.accountId === accountId
  );
  const [diagnosticRange, setDiagnosticRange] = React.useState(
    createRollingDiagnosticRange
  );
  const batchQueriesPaused =
    !accountId ||
    workspaceMode !== 'REALTIME' ||
    !['POSITIONS', 'EVENTS'].includes(activeMode);
  const [currentBatchesResult, refreshCurrentBatches] = useQuery({
    query: TTradeBatchesPageQuery,
    variables: {
      accountId,
      filter: { scope: TTradeBatchScope.Current },
      first: 100,
      after: currentBatchAfter,
    },
    pause: batchQueriesPaused,
    requestPolicy: 'network-only',
  });
  const [historyBatchesResult, refreshHistoryBatches] = useQuery({
    query: TTradeBatchesPageQuery,
    variables: {
      accountId,
      filter: { scope: TTradeBatchScope.Terminal },
      first: 100,
      after: historyBatchAfter,
    },
    pause: batchQueriesPaused,
    requestPolicy: 'network-only',
  });
  const [batchEventsResult, refreshBatchEvents] = useQuery({
    query: TTradeBatchEventsPageQuery,
    variables: {
      accountId,
      batchId:
        activeMode === 'POSITIONS'
          ? inspectedBatchId
          : activeMode === 'EVENTS'
            ? activityBatchFilter
            : null,
      first: 100,
      after: eventAfter,
    },
    pause:
      !accountId ||
      workspaceMode !== 'REALTIME' ||
      !['POSITIONS', 'EVENTS'].includes(activeMode) ||
      (activeMode === 'POSITIONS' && !inspectedBatchId),
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
  const activityDayStart = `${getShanghaiDateKey(new Date())}T00:00:00+08:00`;
  const [activitySignalsResult, refreshActivitySignals] = useQuery({
    query: TTradeSignalEvaluationsQuery,
    variables: {
      accountId,
      stockCode: null,
      eventKinds: includeActivityDiagnostics
        ? [
            TTradeSignalEvaluationKind.Material,
            TTradeSignalEvaluationKind.CoalescedDiagnostic,
          ]
        : [TTradeSignalEvaluationKind.Material],
      startTime: activityDayStart,
      endTime: null,
      first: 100,
      after: activitySignalAfter,
    },
    pause:
      !accountId || workspaceMode !== 'REALTIME' || activeMode !== 'EVENTS',
    requestPolicy: 'network-only',
  });
  const [signalDetailResult] = useQuery({
    query: TTradeSignalEvaluationDetailQuery,
    variables: {
      accountId,
      evaluationId: requestedSignalDetailId || '',
    },
    pause:
      !accountId ||
      !requestedSignalDetailId ||
      workspaceMode !== 'REALTIME' ||
      !['SIGNALS', 'EVENTS'].includes(activeMode),
    requestPolicy: 'cache-first',
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
  const liveDecisionRunId = monitor?.strategyRunId || '';
  const [liveDecisionResult, refreshLiveDecisions] = useQuery({
    query: StrategyDecisionHistoryQuery,
    variables: {
      instanceId: liveDecisionRunId,
      cursor: null,
      limit: 200,
      backtestId: null,
    },
    pause:
      !liveDecisionRunId ||
      workspaceMode !== 'REALTIME' ||
      activeMode !== 'AUDIT',
    requestPolicy: 'cache-and-network',
  });
  const [liveExecutionResult, refreshLiveExecutions] = useQuery({
    query: StrategyExecutionTraceQuery,
    variables: {
      instanceId: liveDecisionRunId,
      decisionId: null,
      backtestId: null,
      cursor: null,
      limit: 200,
    },
    pause:
      !liveDecisionRunId ||
      workspaceMode !== 'REALTIME' ||
      activeMode !== 'AUDIT',
    requestPolicy: 'cache-and-network',
  });
  const liveDecisions = React.useMemo<StrategyDecision[]>(
    () =>
      ((liveDecisionResult.data?.strategyDecisionHistory || []) as unknown[])
        .map(mapStrategyDecisionView)
        .filter(decision => decision.instanceId === liveDecisionRunId),
    [liveDecisionResult.data, liveDecisionRunId]
  );
  const liveExecutions = React.useMemo<ExecutionTraceView[]>(
    () =>
      (
        (liveExecutionResult.data?.strategyExecutionTrace || []) as unknown[]
      ).map(mapExecutionTraceView),
    [liveExecutionResult.data]
  );
  const signalEvaluationsPage = React.useMemo(() => {
    const page = signalEvaluationsResult.data?.tTradeSignalEvaluations;
    if (!page || page.items.some(item => item.accountId !== accountId)) {
      return undefined;
    }
    return page;
  }, [accountId, signalEvaluationsResult.data?.tTradeSignalEvaluations]);
  const activitySignalsPage = React.useMemo(() => {
    const page = activitySignalsResult.data?.tTradeSignalEvaluations;
    if (!page || page.items.some(item => item.accountId !== accountId)) {
      return undefined;
    }
    return page;
  }, [accountId, activitySignalsResult.data?.tTradeSignalEvaluations]);
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
    setCurrentBatchAfter(null);
    setHistoryBatchAfter(null);
    setEventAfter(null);
    setSignalAfter(null);
    setActivitySignalAfter(null);
    setCurrentBatches([]);
    setHistoryBatches([]);
    setBatchEvents([]);
    setInspectedBatchId(null);
    setActivityBatchFilter(null);
    setPositionFocusBatchId(null);
    setSignalEvaluations([]);
    setActivitySignalEvaluations([]);
    setRequestedSignalDetailId(null);
    setDiagnosticRange(createRollingDiagnosticRange());
    signalRefreshTelemetryRef.current = null;
  }, [accountId]);

  React.useEffect(() => {
    const page = currentBatchesResult.data?.tTradeBatchesPage;
    if (!page) return;
    if (page.items.some(item => item.accountId !== accountId)) {
      // Never render a page returned for a different account, even if a
      // gateway or stale cache serves it under the current operation key.
      setCurrentBatches([]);
      return;
    }
    const items = page.items.map(adaptLiveBatch);
    setCurrentBatches(previous => {
      if (!currentBatchAfter) return items;
      const byId = new Map(previous.map(item => [item.batchId, item]));
      for (const item of items) byId.set(item.batchId, item);
      return Array.from(byId.values());
    });
    if (
      page.pageInfo.hasNextPage &&
      page.pageInfo.endCursor &&
      page.pageInfo.endCursor !== currentBatchAfter
    ) {
      setCurrentBatchAfter(page.pageInfo.endCursor);
    }
  }, [
    accountId,
    currentBatchAfter,
    currentBatchesResult.data?.tTradeBatchesPage,
  ]);

  React.useEffect(() => {
    const page = historyBatchesResult.data?.tTradeBatchesPage;
    if (!page) return;
    if (page.items.some(item => item.accountId !== accountId)) {
      setHistoryBatches([]);
      return;
    }
    const items = page.items.map(adaptLiveBatch);
    setHistoryBatches(previous => {
      if (!historyBatchAfter) return items;
      const byId = new Map(previous.map(item => [item.batchId, item]));
      for (const item of items) byId.set(item.batchId, item);
      return Array.from(byId.values());
    });
    if (
      page.pageInfo.hasNextPage &&
      page.pageInfo.endCursor &&
      page.pageInfo.endCursor !== historyBatchAfter
    ) {
      setHistoryBatchAfter(page.pageInfo.endCursor);
    }
  }, [
    accountId,
    historyBatchAfter,
    historyBatchesResult.data?.tTradeBatchesPage,
  ]);

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
      signalSnapshot: item.signalSummary,
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

  React.useEffect(() => {
    const page = activitySignalsResult.data?.tTradeSignalEvaluations;
    if (!page) return;
    if (page.items.some(item => item.accountId !== accountId)) {
      setActivitySignalEvaluations([]);
      return;
    }
    const items: ActivitySignalEvaluation[] = page.items.map(item => ({
      ...item,
      id: String(item.id),
      signalSnapshot: item.signalSummary,
    }));
    setActivitySignalEvaluations(previous => {
      if (!activitySignalAfter) return items;
      const byId = new Map(previous.map(item => [item.id, item]));
      for (const item of items) byId.set(item.id, item);
      return Array.from(byId.values());
    });
  }, [
    accountId,
    activitySignalAfter,
    activitySignalsResult.data?.tTradeSignalEvaluations,
  ]);

  const signalEvaluationDetail = React.useMemo(() => {
    const item = signalDetailResult.data?.tTradeSignalEvaluation;
    if (
      !item ||
      item.accountId !== accountId ||
      String(item.id) !== requestedSignalDetailId
    ) {
      return null;
    }
    return {
      id: String(item.id),
      signalSnapshot: readFragment(
        TTradeSignalSnapshotFieldsFragment,
        item.signalSnapshot
      ),
    };
  }, [
    accountId,
    requestedSignalDetailId,
    signalDetailResult.data?.tTradeSignalEvaluation,
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

  const refreshVisibleData = React.useCallback(
    (options: { includeMonitor?: boolean } = {}) => {
      if (options.includeMonitor !== false) {
        refreshMonitor({ requestPolicy: 'network-only' });
      }
      if (activeMode === 'POSITIONS') {
        if (currentBatchAfter) setCurrentBatchAfter(null);
        else refreshCurrentBatches({ requestPolicy: 'network-only' });
        if (historyBatchAfter) setHistoryBatchAfter(null);
        else refreshHistoryBatches({ requestPolicy: 'network-only' });
        if (inspectedBatchId) {
          if (eventAfter) setEventAfter(null);
          else refreshBatchEvents({ requestPolicy: 'network-only' });
        }
      }
      if (activeMode === 'EVENTS') {
        if (eventAfter) setEventAfter(null);
        else refreshBatchEvents({ requestPolicy: 'network-only' });
        if (currentBatchAfter) setCurrentBatchAfter(null);
        else refreshCurrentBatches({ requestPolicy: 'network-only' });
        if (historyBatchAfter) setHistoryBatchAfter(null);
        else refreshHistoryBatches({ requestPolicy: 'network-only' });
        if (activitySignalAfter) setActivitySignalAfter(null);
        else refreshActivitySignals({ requestPolicy: 'network-only' });
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
      if (activeMode === 'AUDIT' && liveDecisionRunId) {
        refreshLiveDecisions({ requestPolicy: 'network-only' });
        refreshLiveExecutions({ requestPolicy: 'network-only' });
      }
    },
    [
      activeMode,
      inspectedBatchId,
      activitySignalAfter,
      currentBatchAfter,
      eventAfter,
      historyBatchAfter,
      refreshActivitySignals,
      refreshBatchEvents,
      refreshCurrentBatches,
      refreshCandidateTrace,
      refreshHistoryBatches,
      refreshLiveDecisions,
      refreshLiveExecutions,
      refreshMonitor,
      refreshSignalDiagnostics,
      refreshSignalEvaluations,
      signalAfter,
      signalDiagnosticsResult.fetching,
      signalEvaluationsResult.fetching,
      liveDecisionRunId,
      selectedTraceForCurrentAccount,
    ]
  );

  React.useEffect(() => {
    if (
      workspaceMode !== 'REALTIME' ||
      activeMode !== 'AUDIT' ||
      !liveDecisionRunId ||
      !monitor?.enabled
    ) {
      return;
    }
    const refreshAudit = () => {
      if (document.visibilityState !== 'visible') return;
      refreshLiveDecisions({ requestPolicy: 'network-only' });
      refreshLiveExecutions({ requestPolicy: 'network-only' });
    };
    const interval = window.setInterval(refreshAudit, 5_000);
    document.addEventListener('visibilitychange', refreshAudit);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', refreshAudit);
    };
  }, [
    activeMode,
    liveDecisionRunId,
    monitor?.enabled,
    refreshLiveDecisions,
    refreshLiveExecutions,
    workspaceMode,
  ]);

  const requestAuthoritativeMonitorRefresh = React.useCallback(() => {
    if (!accountId || workspaceMode !== 'REALTIME') return;
    const epoch = signalSnapshotRefreshCoordinator.beginEpoch(accountId, {
      preserveTrust: true,
    });
    setTrustedSignalSnapshotEpoch(
      signalSnapshotRefreshCoordinator.isTrusted(accountId) ? epoch : null
    );
    serverTruthRefreshPolicy.noteNetworkRequest(accountId, Date.now());
    runMonitorEpochRefresh(epoch, accountId);
  }, [
    accountId,
    runMonitorEpochRefresh,
    serverTruthRefreshPolicy,
    signalSnapshotRefreshCoordinator,
    workspaceMode,
  ]);
  const requestAuthoritativeRefresh = React.useCallback(() => {
    refreshVisibleData({ includeMonitor: false });
    requestAuthoritativeMonitorRefresh();
  }, [refreshVisibleData, requestAuthoritativeMonitorRefresh]);

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
      requestAuthoritativeMonitorRefresh();
    }, 250);
    return () => {
      if (subscriptionRefreshTimerRef.current != null) {
        window.clearTimeout(subscriptionRefreshTimerRef.current);
        subscriptionRefreshTimerRef.current = null;
      }
    };
  }, [
    accountId,
    requestAuthoritativeMonitorRefresh,
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
      requestAuthoritativeMonitorRefresh();
    }
  }, [
    accountId,
    requestAuthoritativeMonitorRefresh,
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
        requestAuthoritativeMonitorRefresh();
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
    requestAuthoritativeMonitorRefresh,
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
    <TTradePanelBoundary name="回测记录">
      <React.Suspense
        fallback={
          <aside className="studio-workspace-surface flex h-full min-h-0 flex-col">
            <div className="h-[68px] shrink-0 border-b border-white/[0.05] px-ui-section py-3">
              <div className="text-ui-caption font-black uppercase tracking-[0.18em] text-cyan-300">
                Replay Lab
              </div>
              <div className="mt-1 text-ui-title font-black text-slate-100">
                回测记录
              </div>
            </div>
            <div className="flex items-center gap-2 p-ui-section text-ui-caption text-slate-500">
              <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
              正在载入回测记录…
            </div>
          </aside>
        }
      >
        <TTradeReplaySidebar context={replaySidebarContext} />
      </React.Suspense>
    </TTradePanelBoundary>
  );

  const toolbar = (
    <div className="studio-workspace-surface flex h-12 shrink-0 items-center justify-between gap-3 overflow-x-auto border-b border-white/[0.05] px-ui-section custom-scrollbar">
      <nav
        className="flex h-full shrink-0 items-stretch"
        aria-label="做 T 工作区"
      >
        {(['REALTIME', 'REPLAY', 'PAPER', 'LIVE_ASSISTANT'] as const).map(
          mode => {
            const active = workspaceMode === mode;
            return (
              <button
                key={mode}
                type="button"
                onClick={() => {
                  setWorkspaceMode(mode);
                  if (mode === 'REPLAY') setActiveReplayView('OVERVIEW');
                }}
                className={cn(
                  'relative flex h-full shrink-0 cursor-pointer items-center gap-1.5 px-3 text-ui-caption font-black transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset',
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
                {mode === 'LIVE_ASSISTANT'
                  ? 'LIVE 人工确认'
                  : mode === 'PAPER'
                    ? 'PAPER 执行'
                    : mode === 'REPLAY'
                      ? '回放测试'
                      : '实时监控'}
              </button>
            );
          }
        )}
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
                    'relative h-full shrink-0 cursor-pointer px-3 text-ui-label font-bold transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70',
                    isActive
                      ? 'text-blue-200 after:bg-blue-400'
                      : 'text-slate-500 hover:text-slate-200'
                  )}
                >
                  {mode.label}
                  {mode.id === 'SIGNALS' && Boolean(pendingSessions.length) && (
                    <span className="ml-1.5 rounded-sm bg-amber-400/15 px-1.5 py-0.5 font-mono text-ui-micro text-amber-200">
                      {pendingSessions.length}
                    </span>
                  )}
                </button>
              );
            })}
          </>
        )}
        {workspaceMode === 'REPLAY' && (
          <>
            <span className="mx-2 my-3 w-px bg-white/[0.08]" />
            {(replaySidebarContext?.activeRunId
              ? [
                  ['OVERVIEW', '总览'],
                  ['SIGNALS', '信号'],
                  ['AUDIT', '决策审计'],
                  ['POSITIONS', '仓位与批次'],
                  ['EVENTS', '运行动态'],
                  ['PARAMETERS', '参数'],
                  ['ACCOUNT', '账户'],
                ]
              : [
                  ['OVERVIEW', '总览'],
                  ['PARAMETERS', '参数'],
                  ['ACCOUNT', '账户'],
                ]
            ).map(([view, label]) => {
              const replayView = view as ReplayWorkspaceView;
              const active = activeReplayView === view;
              return (
                <button
                  key={view}
                  type="button"
                  onClick={() => setActiveReplayView(replayView)}
                  className={cn(
                    'relative h-full shrink-0 cursor-pointer px-3 text-ui-label font-bold transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-transparent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70',
                    active
                      ? 'text-blue-200 after:bg-blue-400'
                      : 'text-slate-500 hover:text-slate-200'
                  )}
                >
                  {label}
                </button>
              );
            })}
          </>
        )}
      </nav>

      <div className="flex shrink-0 items-center gap-2">
        {workspaceMode === 'REALTIME' && (
          <Button
            className="h-control-compact px-2 text-ui-caption"
            onClick={() => openStudioTab('/liquidation')}
            size="sm"
            type="button"
            variant="outline"
          >
            <WalletCards className="h-3.5 w-3.5" />T 批次退出
          </Button>
        )}
        {workspaceMode === 'LIVE_ASSISTANT' ? (
          <span className="text-ui-caption text-slate-400">
            LIVE · 设备确认后重新分配
          </span>
        ) : workspaceMode === 'PAPER' ? (
          <span className="text-ui-caption text-slate-400">
            PAPER · 执行事实只读
          </span>
        ) : workspaceMode === 'REPLAY' ? (
          <span className="hidden items-center gap-1.5 text-ui-caption font-bold text-cyan-200 sm:inline-flex">
            <ShieldCheck className="h-3.5 w-3.5" />
            隔离回测 · 自动确认测试信号
          </span>
        ) : (
          <>
            <span
              className={cn(
                'hidden border px-2 py-1 text-ui-micro font-black lg:inline-flex',
                (readiness?.stage || monitor?.rolloutStage) === 'LIVE'
                  ? 'border-cyan-400/25 bg-cyan-400/[0.06] text-cyan-200'
                  : (readiness?.stage || monitor?.rolloutStage) === 'CANARY'
                    ? 'border-amber-400/25 bg-amber-400/[0.06] text-amber-200'
                    : 'border-white/[0.08] bg-white/[0.025] text-slate-500'
              )}
            >
              {(readiness?.stage || monitor?.rolloutStage) === 'LIVE'
                ? 'LIVE · 自动执行'
                : (readiness?.stage || monitor?.rolloutStage) === 'CANARY'
                  ? 'CANARY · 人工确认'
                  : `${readiness?.stage || monitor?.rolloutStage || 'SHADOW'} · 新买入关闭`}
            </span>
            {(readiness?.stage || monitor?.rolloutStage) === 'LIVE' && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={actionLoading}
                onClick={handlePauseEntries}
                className="hidden h-control-compact rounded-sm border-amber-400/20 px-2 text-ui-caption text-amber-200 xl:inline-flex"
              >
                暂停自动执行
              </Button>
            )}
            <span
              className={cn(
                'hidden items-center gap-1.5 text-ui-caption font-bold md:inline-flex',
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
            <span className="hidden font-mono text-ui-micro text-slate-600 sm:inline">
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
        <div className="flex shrink-0 items-center gap-2 border-b border-amber-400/15 bg-amber-400/[0.07] px-ui-section py-2.5 text-ui-label font-bold text-amber-100">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          未配置默认交易账户，请设置环境变量 VITE_DEFAULT_ACCOUNT_ID。
        </div>
      )}
      {(monitorResult.error || monitor?.lastError) && (
        <div className="flex shrink-0 items-center gap-2 border-b border-rose-400/15 bg-rose-500/[0.07] px-ui-section py-2.5 text-ui-label text-rose-100">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>{monitorResult.error?.message || monitor?.lastError}</span>
        </div>
      )}
      {readiness && (
        <section
          aria-live="polite"
          className={cn(
            'flex shrink-0 flex-wrap items-center justify-between gap-3 border-b px-ui-section py-3',
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
              <div className="text-ui-label font-black text-slate-100">
                {readinessStageLabel(readiness.status, readiness.stage)} ·
                Engine {readiness.engineStatus} · Agent {readiness.agentStatus}
              </div>
              <div className="mt-1 text-ui-caption leading-4 text-slate-400">
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
                className="h-control-compact rounded-sm border-amber-400/20 text-ui-caption text-amber-200"
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
                  className="h-8 rounded-sm border-sky-400/20 text-ui-caption text-sky-200"
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
                  className="h-8 rounded-sm border-emerald-400/20 text-ui-caption text-emerald-200"
                >
                  启用严格 Canary
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={!readiness.canActivateLive || actionLoading}
                  onClick={() => handleActivateLive(TTradeRolloutTarget.Live)}
                  className="h-8 rounded-sm bg-emerald-500 px-3 text-ui-caption font-black text-slate-950 hover:bg-emerald-400"
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
              className="h-8 rounded-sm border-rose-400/20 text-ui-caption text-rose-200"
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
          className="flex shrink-0 items-center justify-between border-b border-amber-400/15 bg-amber-400/[0.05] px-ui-section py-2.5 text-left transition-colors hover:bg-amber-400/[0.08] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-amber-400/50"
        >
          <span className="inline-flex items-center gap-2 text-ui-label font-bold text-amber-100">
            <Activity className="h-4 w-4" />有 {pendingSessions.length}{' '}
            个买入机会等待人工确认
          </span>
          <span className="text-ui-caption font-bold text-amber-300">
            查看信号 →
          </span>
        </button>
      )}

      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.05] px-ui-section py-3">
        <div>
          <h2 className="text-ui-body font-black text-slate-100">实时作战表</h2>
          <p className="mt-0.5 text-ui-caption text-slate-600">
            行情流与策略流独立标时 · 默认按需要关注程度排序
          </p>
        </div>
        <div className="flex items-center gap-3 text-ui-caption font-bold text-slate-600">
          <span>{monitor?.mode === 'live' ? '实盘执行' : '模拟观察'}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-control-compact rounded-sm border-white/10 px-2.5 text-ui-caption text-slate-300"
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
        <section className="shrink-0 border-b border-amber-400/15 bg-[#0b1628] px-ui-section py-3">
          {!monitor?.enabled || !monitor.strategyRunId ? (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-2.5">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                <div>
                  <div className="text-ui-label font-black text-amber-100">
                    请先启动全局监控
                  </div>
                  <p className="mt-1 text-ui-caption leading-4 text-slate-500">
                    外部成交需要加入一个正在运行的做 T
                    策略，才能持续读取行情并触发自动卖出。
                  </p>
                </div>
              </div>
              <Button
                type="button"
                size="sm"
                className="h-control-compact shrink-0 rounded-sm bg-primary px-3 text-ui-caption font-black text-white hover:bg-primary/90"
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
                  <div className="text-ui-label font-black text-slate-100">
                    选择已成交买入委托
                  </div>
                  <p className="mt-1 text-ui-caption text-slate-500">
                    先同步 miniQMT
                    当日委托，再读取委托表；每个已成委托只能建立一次自动卖出批次。
                  </p>
                  {sourceOrdersSyncedAt && !sourceOrdersSyncError && (
                    <p className="mt-1 font-mono text-ui-micro text-emerald-500/70">
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
                  className="h-control-compact text-ui-caption text-slate-400"
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
                <div className="mt-3 flex items-start gap-2 border border-red-500/20 bg-red-500/[0.06] px-3 py-2 text-ui-caption leading-4 text-red-200">
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
                    className="text-ui-caption text-slate-500"
                  >
                    开始日期
                  </Label>
                  <Input
                    id="t-trade-source-start"
                    type="date"
                    value={sourceStartDate}
                    onChange={event => setSourceStartDate(event.target.value)}
                    className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-ui-caption"
                  />
                </div>
                <div>
                  <Label
                    htmlFor="t-trade-source-end"
                    className="text-ui-caption text-slate-500"
                  >
                    结束日期
                  </Label>
                  <Input
                    id="t-trade-source-end"
                    type="date"
                    value={sourceEndDate}
                    onChange={event => setSourceEndDate(event.target.value)}
                    className="mt-1 h-8 w-36 rounded-sm border-white/10 bg-[#07111f] text-ui-caption"
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
                        'grid grid-cols-[24px_minmax(140px,1fr)_100px_100px_80px] items-center border-b border-white/[0.05] px-3 py-2 text-ui-caption last:border-b-0',
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
                    <div className="px-3 py-ui-panel text-center text-ui-caption text-slate-600">
                      所选日期范围内没有已成交买入委托
                    </div>
                  )}
              </div>
              <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <label className="flex cursor-pointer items-start gap-2 text-ui-caption leading-4 text-amber-100">
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
                  className="h-control-default rounded-sm bg-amber-500 px-ui-section text-ui-label font-black text-slate-950 hover:bg-amber-400"
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

  const positionExecutionMode: TTradeExecutionMode =
    String(monitor?.mode || '').toUpperCase() === 'PAPER' ? 'PAPER' : 'LIVE';
  const batchesError =
    currentBatchesResult.error?.message || historyBatchesResult.error?.message;
  const batchesLoading =
    currentBatchesResult.fetching || historyBatchesResult.fetching;
  const batchesLoadingMore =
    (Boolean(currentBatchAfter) && currentBatchesResult.fetching) ||
    (Boolean(historyBatchAfter) && historyBatchesResult.fetching);
  const batchesHaveMore = Boolean(
    currentBatchesResult.data?.tTradeBatchesPage.pageInfo.hasNextPage ||
    historyBatchesResult.data?.tTradeBatchesPage.pageInfo.hasNextPage
  );
  const historyBatchSummary = historyBatchesResult.data?.tTradeBatchesPage
    .summary
    ? adaptLiveBatchSummary(historyBatchesResult.data.tTradeBatchesPage.summary)
    : null;
  const loadMoreBatches = () => {
    const currentPage = currentBatchesResult.data?.tTradeBatchesPage;
    if (currentPage?.pageInfo.hasNextPage) {
      setCurrentBatchAfter(currentPage.pageInfo.endCursor ?? null);
    }
    const historyPage = historyBatchesResult.data?.tTradeBatchesPage;
    if (historyPage?.pageInfo.hasNextPage) {
      setHistoryBatchAfter(historyPage.pageInfo.endCursor ?? null);
    }
  };
  const positionsView = (
    <React.Suspense fallback={tTradePositionsFallback}>
      <TTradePositionsView
        actionLoading={actionLoading}
        batches={batches}
        defaultExecutionMode={positionExecutionMode}
        error={batchesError}
        events={batchEvents}
        focusBatchId={positionFocusBatchId}
        hasMore={batchesHaveMore}
        historyScopeKey={accountId}
        instrumentNames={positionNamesByCode}
        loading={batchesLoading}
        loadingMore={batchesLoadingMore}
        mode="LIVE"
        onCancelOrder={handleCancelOrder}
        onFocusBatchHandled={() => setPositionFocusBatchId(null)}
        onInspectBatch={batchId => {
          if (batchId !== inspectedBatchId) {
            setEventAfter(null);
            setBatchEvents([]);
          }
          setInspectedBatchId(batchId);
        }}
        onLoadMore={loadMoreBatches}
        onRefresh={requestAuthoritativeRefresh}
        onViewActivity={batchId => {
          setInspectedBatchId(null);
          setActivityBatchFilter(batchId);
          setEventAfter(null);
          setBatchEvents([]);
          setActiveMode('EVENTS');
        }}
        summary={historyBatchSummary}
      />
    </React.Suspense>
  );

  const eventsView = (
    <TTradeActivityView
      batchError={batchesError}
      batches={activityBatches}
      eventError={batchEventsResult.error?.message}
      events={batchEvents}
      evaluationDetail={signalEvaluationDetail}
      evaluationDetailError={signalDetailResult.error?.message}
      evaluationDetailLoading={signalDetailResult.fetching}
      evaluations={activitySignalEvaluations}
      focusedBatchId={activityBatchFilter}
      hasMoreEvents={Boolean(
        batchEventsResult.data?.tTradeBatchEventsPage.pageInfo.hasNextPage
      )}
      hasMoreSignals={Boolean(activitySignalsPage?.pageInfo.hasNextPage)}
      includeDiagnostics={includeActivityDiagnostics}
      instrumentNames={positionNamesByCode}
      isRunning={Boolean(monitor?.enabled && monitor.strategyRunId)}
      loading={
        batchesLoading ||
        batchEventsResult.fetching ||
        activitySignalsResult.fetching
      }
      loadingMore={
        Boolean(
          currentBatchAfter ||
          historyBatchAfter ||
          eventAfter ||
          activitySignalAfter
        ) &&
        (batchesLoading ||
          batchEventsResult.fetching ||
          activitySignalsResult.fetching)
      }
      onIncludeDiagnosticsChange={value => {
        setIncludeActivityDiagnostics(value);
        setActivitySignalAfter(null);
        setActivitySignalEvaluations([]);
      }}
      onFocusedBatchIdClear={() => {
        setActivityBatchFilter(null);
        setEventAfter(null);
        setBatchEvents([]);
      }}
      onLoadMore={() => {
        if (
          batchEventsResult.data?.tTradeBatchEventsPage.pageInfo.hasNextPage
        ) {
          setEventAfter(
            batchEventsResult.data.tTradeBatchEventsPage.pageInfo.endCursor ??
              null
          );
        }
        if (activitySignalsPage?.pageInfo.hasNextPage) {
          setActivitySignalAfter(
            activitySignalsPage.pageInfo.endCursor ?? null
          );
        }
        loadMoreBatches();
      }}
      onRefresh={requestAuthoritativeRefresh}
      onRequestEvaluationDetail={setRequestedSignalDetailId}
      onViewBatch={batchId => {
        setPositionFocusBatchId(batchId);
        setInspectedBatchId(batchId);
        setEventAfter(null);
        setBatchEvents([]);
        setActiveMode('POSITIONS');
      }}
      onViewCurrent={stockCode => {
        setFocusedSignalStockCode(stockCode);
        setActiveMode('SIGNALS');
      }}
      runId={monitor?.strategyRunId}
      runMode={monitor?.mode}
      signalError={activitySignalsResult.error?.message}
      wsStatus={graphqlWsStatus}
    />
  );
  const signalsView = (
    <React.Suspense
      fallback={
        <div
          className="studio-workspace-surface flex h-full min-h-0 items-center justify-center text-ui-label text-slate-500"
          role="status"
        >
          <Loader2
            className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在加载真实信号…
        </div>
      }
    >
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
        evaluationDetail={signalEvaluationDetail}
        evaluationDetailError={signalDetailResult.error?.message}
        evaluationDetailLoading={signalDetailResult.fetching}
        evaluations={accountBoundSignalEvaluations}
        evaluationsError={signalEvaluationsResult.error?.message}
        focusStockCode={focusedSignalStockCode}
        hasMoreEvaluations={Boolean(
          signalEvaluationsPage?.pageInfo.hasNextPage
        )}
        loadingEvaluations={signalEvaluationsResult.fetching}
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
        onFocusHandled={() => setFocusedSignalStockCode(null)}
        onRequestCandidateTrace={setSelectedTrace}
        onRequestEvaluationDetail={setRequestedSignalDetailId}
        onReject={(session, snapshot) =>
          void handleSignal(
            'reject',
            session.runId,
            snapshot.pendingEntryIntentId!,
            snapshot
          )
        }
        selectedTrace={selectedTraceForCurrentAccount}
      />
    </React.Suspense>
  );

  const auditView = (
    <React.Suspense
      fallback={
        <div
          className="studio-workspace-surface flex h-full min-h-0 items-center justify-center text-ui-label text-slate-500"
          role="status"
        >
          <Loader2
            className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在加载决策审计…
        </div>
      }
    >
      <TTradeLiveDecisionAudit
        decisions={liveDecisions}
        error={
          liveDecisionResult.error?.message ||
          liveExecutionResult.error?.message
        }
        executions={liveExecutions}
        instrumentNames={positionNamesByCode}
        loading={liveDecisionResult.fetching || liveExecutionResult.fetching}
        onRefresh={requestAuthoritativeRefresh}
        runId={liveDecisionRunId}
      />
    </React.Suspense>
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
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-ui-section py-2.5 text-ui-caption leading-4 text-rose-100"
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
          className="flex shrink-0 items-center gap-2 border-b border-cyan-400/15 bg-cyan-400/[0.04] px-ui-section py-2 text-ui-micro text-cyan-100"
        >
          <Loader2
            className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在刷新配置版本…
        </div>
      )}
      <div className="flex shrink-0 items-center justify-between border-b border-white/[0.05] px-ui-section py-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-ui-body font-black text-slate-100">
              全局策略参数
            </h2>
            {draftDirty && (
              <span
                role="status"
                className="rounded-sm border border-primary/20 bg-primary/10 px-1.5 py-0.5 text-ui-micro font-semibold text-blue-200"
              >
                已修改
              </span>
            )}
          </div>
          <p className="mt-0.5 text-ui-caption text-slate-600">
            对账户内所有未忽略的合格持仓统一生效
          </p>
        </div>
        <span className="font-mono text-ui-caption text-slate-600">
          配置版本 v{monitor?.configVersion ?? 0}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto custom-scrollbar">
        <div className="grid gap-px bg-white/[0.05] xl:grid-cols-2">
          <TTradeExecutionSettingsPanel form={form} onFieldChange={setField} />
          <section className="bg-[#0a1424] p-ui-section xl:col-span-2">
            <div className="mb-4 flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.05] pb-3">
              <div>
                <div className="text-ui-label font-black text-slate-200">
                  V3 有状态信号规则
                </div>
                <div className="mt-1 text-ui-caption text-slate-600">
                  因果窗口、双 FSM、可解释评分、硬门禁与 episode 防重复
                </div>
              </div>
              <div className="font-mono text-ui-micro text-slate-600">
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
                className="text-ui-label font-bold text-slate-300"
              >
                忽略股票代码
              </Label>
              <p className="mt-1 text-ui-caption text-slate-600">
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
                  className="h-9 rounded-sm border-white/10 bg-[#07111f] font-mono text-ui-label focus-visible:ring-red-500/60"
                />
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="h-control-default rounded-sm border-white/10"
                  disabled={!ignoreInput.trim() || actionLoading}
                  onClick={handleAddIgnore}
                >
                  添加
                </Button>
              </div>
              <div className="mt-3 flex min-h-8 flex-wrap gap-1.5">
                {ignoredCodes.length === 0 ? (
                  <span className="text-ui-caption text-slate-700">
                    当前未忽略任何股票
                  </span>
                ) : (
                  ignoredCodes.map(code => (
                    <button
                      key={code}
                      type="button"
                      disabled={actionLoading}
                      onClick={() => handleIgnore(code, false)}
                      className="inline-flex items-center gap-1 border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-ui-caption text-slate-400 outline-none transition-colors hover:border-rose-400/30 hover:text-rose-200 focus-visible:ring-2 focus-visible:ring-red-500/60"
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
          <label className="flex cursor-pointer items-start gap-2.5 border-t border-amber-400/15 bg-amber-400/[0.06] px-ui-section py-3 text-ui-label font-bold leading-5 text-amber-100">
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

      <div className="flex shrink-0 items-center justify-between border-t border-white/[0.06] bg-[#091322] px-ui-section py-3">
        <div className="text-ui-caption text-slate-600">
          保存后立即应用于当前账户的单一 T 策略运行
        </div>
        <Button
          type="button"
          className="h-control-default rounded-sm bg-primary px-ui-section text-ui-label text-primary-foreground hover:bg-primary/90"
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
        <TTradePanelBoundary
          name={
            workspaceMode === 'LIVE_ASSISTANT'
              ? 'LIVE 人工确认'
              : workspaceMode === 'PAPER'
                ? 'PAPER 执行'
                : workspaceMode === 'REPLAY'
                  ? '回放测试'
                  : (tTradeModes.find(mode => mode.id === activeMode)?.label ??
                    '做 T 面板')
          }
        >
          {workspaceMode === 'LIVE_ASSISTANT' ? (
            <TAssistantLivePanel accountId={accountId} />
          ) : workspaceMode === 'PAPER' ? (
            <TAssistantPaperPanel accountId={accountId} />
          ) : workspaceMode === 'REPLAY' ? (
            <TTradeReplayPanel
              accountId={accountId}
              activeView={activeReplayView}
              baseCosts={replayBaseCosts}
              baseForm={replayBaseForm}
              costs={replayCosts}
              form={replayForm}
              liveConfigVersion={replayConfigVersion}
              liveSettingsStale={replayLiveSettingsStale}
              onActiveViewChange={setActiveReplayView}
              onCopySettings={copyReplaySettings}
              onCostChange={setReplayCostField}
              onFieldChange={setReplayField}
              onRestoreSettings={restoreReplaySettings}
              onSidebarContextChange={setReplaySidebarContext}
              onSignalPolicyChange={setReplaySignalPolicyField}
              restoringSettings={replaySettingsRestoring}
            />
          ) : activeMode === 'MONITOR' ? (
            monitorView
          ) : activeMode === 'SIGNALS' ? (
            signalsView
          ) : activeMode === 'AUDIT' ? (
            auditView
          ) : activeMode === 'DIAGNOSTICS' ? (
            diagnosticsView
          ) : activeMode === 'POSITIONS' ? (
            positionsView
          ) : activeMode === 'EVENTS' ? (
            eventsView
          ) : (
            settingsView
          )}
        </TTradePanelBoundary>
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
      showSidebar={workspaceMode === 'REALTIME' || workspaceMode === 'REPLAY'}
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                workspaceMode === 'PAPER'
                  ? 'bg-blue-400'
                  : workspaceMode === 'REPLAY'
                    ? 'bg-cyan-400'
                    : monitor?.enabled
                      ? 'bg-emerald-400'
                      : 'bg-slate-600'
              )}
            />
            {workspaceMode === 'LIVE_ASSISTANT'
              ? 'LIVE 人工确认'
              : workspaceMode === 'PAPER'
                ? 'PAPER 执行 · 只读'
                : workspaceMode === 'REPLAY'
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
        workspaceMode === 'LIVE_ASSISTANT' ? (
          <span>确认提交后等待 Engine 重新分配与风控</span>
        ) : workspaceMode === 'PAPER' ? (
          <span>PAPER Broker · 原始行情时间与本地受理时间分别记录</span>
        ) : workspaceMode === 'REPLAY' ? (
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
