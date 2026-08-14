import { useCallback, useEffect, useMemo } from 'react';
import { useMutation, useQuery, useSubscription } from 'urql';

import {
  ApproveStrategyTradeIntentDocument,
  ArmLimitUpBoardCandidateDocument,
  DisarmLimitUpBoardCandidateDocument,
  LimitUpBoardAssistantDeskDocument,
  LimitUpBoardAssistantRuntimeDocument,
  LimitUpBoardAssistantUpdatesDocument,
  ReconcileLimitUpBoardAssistantDocument,
  RejectStrategyTradeIntentDocument,
  SaveLimitUpBoardAssistantDocument,
  type LimitUpBoardAssistantSettingsInput,
} from '@/generated/gql/graphql';

const DEFAULT_SETTINGS: Omit<
  LimitUpBoardAssistantSettingsInput,
  'accountId'
> = {
  approvalTtlMs: 15_000,
  autoExitAcknowledged: false,
  autoSignalMinScore: 70,
  enabled: false,
  entryDistanceTicks: 1,
  entryEndTime: '14:50',
  entryOrderTtlMs: 15_000,
  entryStartTime: '09:30',
  executionQuoteMaxAgeSeconds: 3,
  exitLimitBreakTicks: 1,
  exitMaxSlippageBps: 50,
  exitMinSealSeconds: 3,
  exitTrailingArmProfitPct: 2,
  exitTrailingDrawdownPct: 3,
  exitTrailingPercent: 50,
  maxEntryAttemptsPerDay: 1,
  maxHoldingExitTime: '14:50',
  maxHoldingTradingDays: 2,
  maxPriceDeviationBps: 20,
  maxSinglePositionPct: 0.05,
  mode: 'paper',
  targetEntryAmount: 10_000,
};

function idempotencyKey(action: string, accountId: string, code: string) {
  const suffix =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${action}:${accountId}:${code}:${suffix}`;
}

export function useLimitUpBoardAssistant(accountId?: string) {
  const [assistantResult, refreshAssistant] = useQuery({
    query: LimitUpBoardAssistantDeskDocument,
    variables: { accountId: accountId || '' },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });
  const assistant = assistantResult.data?.limitUpBoardAssistant;
  const runId = assistant?.strategyRunId || '';
  const [runtimeResult, refreshRuntime] = useQuery({
    query: LimitUpBoardAssistantRuntimeDocument,
    variables: { runId },
    pause: !runId,
    requestPolicy: 'cache-and-network',
  });
  const [updatesResult] = useSubscription({
    query: LimitUpBoardAssistantUpdatesDocument,
    variables: { accountId: accountId || '' },
    pause: !accountId,
  });
  const [, saveMutation] = useMutation(SaveLimitUpBoardAssistantDocument);
  const [, reconcileMutation] = useMutation(
    ReconcileLimitUpBoardAssistantDocument
  );
  const [, armMutation] = useMutation(ArmLimitUpBoardCandidateDocument);
  const [, disarmMutation] = useMutation(DisarmLimitUpBoardCandidateDocument);
  const [, approveMutation] = useMutation(
    ApproveStrategyTradeIntentDocument
  );
  const [, rejectMutation] = useMutation(RejectStrategyTradeIntentDocument);

  const refresh = useCallback(() => {
    refreshAssistant({ requestPolicy: 'network-only' });
    if (runId) refreshRuntime({ requestPolicy: 'network-only' });
  }, [refreshAssistant, refreshRuntime, runId]);

  useEffect(() => {
    if (!updatesResult.data?.limitUpBoardAssistantUpdates.version) return;
    refresh();
  }, [refresh, updatesResult.data?.limitUpBoardAssistantUpdates.version]);

  useEffect(() => {
    if (!accountId) return;
    const poll = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    const timer = window.setInterval(poll, 3_000);
    return () => window.clearInterval(timer);
  }, [accountId, refresh]);

  const currentSettings = useMemo<LimitUpBoardAssistantSettingsInput>(
    () => ({
      accountId: accountId || '',
      approvalTtlMs: assistant?.approvalTtlMs ?? DEFAULT_SETTINGS.approvalTtlMs,
      autoExitAcknowledged:
        assistant?.autoExitAcknowledged ??
        DEFAULT_SETTINGS.autoExitAcknowledged,
      autoSignalMinScore:
        assistant?.autoSignalMinScore ?? DEFAULT_SETTINGS.autoSignalMinScore,
      enabled: assistant?.enabled ?? DEFAULT_SETTINGS.enabled,
      entryDistanceTicks:
        assistant?.entryDistanceTicks ?? DEFAULT_SETTINGS.entryDistanceTicks,
      entryEndTime: assistant?.entryEndTime ?? DEFAULT_SETTINGS.entryEndTime,
      entryOrderTtlMs:
        assistant?.entryOrderTtlMs ?? DEFAULT_SETTINGS.entryOrderTtlMs,
      entryStartTime:
        assistant?.entryStartTime ?? DEFAULT_SETTINGS.entryStartTime,
      executionQuoteMaxAgeSeconds:
        assistant?.executionQuoteMaxAgeSeconds ??
        DEFAULT_SETTINGS.executionQuoteMaxAgeSeconds,
      exitLimitBreakTicks:
        assistant?.exitLimitBreakTicks ?? DEFAULT_SETTINGS.exitLimitBreakTicks,
      exitMaxSlippageBps:
        assistant?.exitMaxSlippageBps ?? DEFAULT_SETTINGS.exitMaxSlippageBps,
      exitMinSealSeconds:
        assistant?.exitMinSealSeconds ?? DEFAULT_SETTINGS.exitMinSealSeconds,
      exitTrailingArmProfitPct:
        assistant?.exitTrailingArmProfitPct ??
        DEFAULT_SETTINGS.exitTrailingArmProfitPct,
      exitTrailingDrawdownPct:
        assistant?.exitTrailingDrawdownPct ??
        DEFAULT_SETTINGS.exitTrailingDrawdownPct,
      exitTrailingPercent:
        assistant?.exitTrailingPercent ?? DEFAULT_SETTINGS.exitTrailingPercent,
      maxEntryAttemptsPerDay:
        assistant?.maxEntryAttemptsPerDay ??
        DEFAULT_SETTINGS.maxEntryAttemptsPerDay,
      maxHoldingExitTime:
        assistant?.maxHoldingExitTime ?? DEFAULT_SETTINGS.maxHoldingExitTime,
      maxHoldingTradingDays:
        assistant?.maxHoldingTradingDays ??
        DEFAULT_SETTINGS.maxHoldingTradingDays,
      maxPriceDeviationBps:
        assistant?.maxPriceDeviationBps ??
        DEFAULT_SETTINGS.maxPriceDeviationBps,
      maxSinglePositionPct:
        assistant?.maxSinglePositionPct ??
        DEFAULT_SETTINGS.maxSinglePositionPct,
      mode: assistant?.mode ?? DEFAULT_SETTINGS.mode,
      targetEntryAmount:
        assistant?.targetEntryAmount ?? DEFAULT_SETTINGS.targetEntryAmount,
    }),
    [accountId, assistant]
  );

  const save = useCallback(
    async (patch: Partial<LimitUpBoardAssistantSettingsInput>) => {
      if (!accountId) throw new Error('未选择交易账户');
      const result = await saveMutation({
        input: { ...currentSettings, ...patch, accountId },
      });
      const payload = result.data?.saveLimitUpBoardAssistant;
      if (result.error || !payload?.success) {
        throw new Error(result.error?.message || payload?.message || '保存失败');
      }
      refresh();
      return payload;
    },
    [accountId, currentSettings, refresh, saveMutation]
  );

  const reconcile = useCallback(async () => {
    if (!accountId) throw new Error('未选择交易账户');
    const result = await reconcileMutation({ accountId });
    const payload = result.data?.reconcileLimitUpBoardAssistant;
    if (result.error || !payload?.success) {
      throw new Error(result.error?.message || payload?.message || '同步失败');
    }
    refresh();
    return payload;
  }, [accountId, reconcileMutation, refresh]);

  const arm = useCallback(
    async (instrumentCode: string) => {
      if (!accountId) throw new Error('未选择交易账户');
      const result = await armMutation({
        input: {
          accountId,
          instrumentCode,
          idempotencyKey: idempotencyKey('arm', accountId, instrumentCode),
        },
      });
      const payload = result.data?.armLimitUpBoardCandidate;
      if (result.error || !payload?.success) {
        throw new Error(result.error?.message || payload?.message || '布防失败');
      }
      refresh();
      return payload;
    },
    [accountId, armMutation, refresh]
  );

  const disarm = useCallback(
    async (instrumentCode: string) => {
      if (!accountId) throw new Error('未选择交易账户');
      const result = await disarmMutation({
        input: {
          accountId,
          instrumentCode,
          idempotencyKey: idempotencyKey('disarm', accountId, instrumentCode),
        },
      });
      const payload = result.data?.disarmLimitUpBoardCandidate;
      if (result.error || !payload?.success) {
        throw new Error(result.error?.message || payload?.message || '取消布防失败');
      }
      refresh();
      return payload;
    },
    [accountId, disarmMutation, refresh]
  );

  const approve = useCallback(
    async (intentId: string) => {
      if (!runId) throw new Error('助手尚未运行');
      const result = await approveMutation({ runId, intentId });
      const payload = result.data?.approveStrategyTradeIntent;
      if (result.error || !payload?.success) {
        throw new Error(result.error?.message || payload?.message || '确认失败');
      }
      refresh();
      return payload;
    },
    [approveMutation, refresh, runId]
  );

  const reject = useCallback(
    async (intentId: string) => {
      if (!runId) throw new Error('助手尚未运行');
      const result = await rejectMutation({
        runId,
        intentId,
        reason: 'USER_REJECTED',
      });
      const payload = result.data?.rejectStrategyTradeIntent;
      if (result.error || !payload?.success) {
        throw new Error(result.error?.message || payload?.message || '忽略失败');
      }
      refresh();
      return payload;
    },
    [refresh, rejectMutation, runId]
  );

  return {
    approve,
    arm,
    assistant,
    currentSettings,
    disarm,
    error: assistantResult.error || runtimeResult.error,
    fetching: assistantResult.fetching || runtimeResult.fetching,
    pendingIntents: runtimeResult.data?.strategyPendingTradeIntents ?? [],
    reconcile,
    refresh,
    reject,
    runId,
    save,
    exitPlans: runtimeResult.data?.strategyExitPlans ?? [],
  };
}
