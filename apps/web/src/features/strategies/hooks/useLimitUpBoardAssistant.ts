import { useCallback, useEffect, useMemo } from 'react';
import { useMutation, useQuery, useSubscription } from 'urql';

import {
  ApproveStrategyTradeIntentDocument,
  FirstBoardPromotionUpdatesDocument,
  LimitUpBoardAssistantDeskDocument,
  LimitUpBoardAssistantRuntimeDocument,
  ReconcileLimitUpBoardAssistantDocument,
  RejectStrategyTradeIntentDocument,
  SaveFirstBoardAssistantDocument,
  SetFirstBoardCandidatePreferenceDocument,
  type LimitUpBoardAssistantSettingsInput,
} from '@/generated/gql/graphql';

const DEFAULT_SETTINGS: Omit<
  LimitUpBoardAssistantSettingsInput,
  'accountId'
> = {
  approvalTtlMs: 15_000,
  autoExitAcknowledged: false,
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
  maxDailyExposurePct: 0.06,
  maxOpenPositions: 2,
  maxRankedCandidates: 5,
  maxSinglePositionPct: 0.02,
  mode: 'paper',
  plannedTailLossPct: 0.0015,
  promotionModelMode: 'SHADOW',
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
    query: FirstBoardPromotionUpdatesDocument,
    variables: { accountId: accountId || '' },
    pause: !accountId,
  });
  const [, saveMutation] = useMutation(SaveFirstBoardAssistantDocument);
  const [, reconcileMutation] = useMutation(
    ReconcileLimitUpBoardAssistantDocument
  );
  const [, preferenceMutation] = useMutation(
    SetFirstBoardCandidatePreferenceDocument
  );
  const [, approveMutation] = useMutation(
    ApproveStrategyTradeIntentDocument
  );
  const [, rejectMutation] = useMutation(RejectStrategyTradeIntentDocument);

  const refresh = useCallback(() => {
    refreshAssistant({ requestPolicy: 'network-only' });
    if (runId) refreshRuntime({ requestPolicy: 'network-only' });
  }, [refreshAssistant, refreshRuntime, runId]);

  useEffect(() => {
    if (!updatesResult.data?.firstBoardPromotionUpdates.version) return;
    refresh();
  }, [refresh, updatesResult.data?.firstBoardPromotionUpdates.version]);

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
      maxDailyExposurePct:
        assistant?.maxDailyExposurePct ??
        DEFAULT_SETTINGS.maxDailyExposurePct,
      plannedTailLossPct:
        assistant?.plannedTailLossPct ?? DEFAULT_SETTINGS.plannedTailLossPct,
      maxOpenPositions:
        assistant?.maxOpenPositions ?? DEFAULT_SETTINGS.maxOpenPositions,
      maxRankedCandidates:
        assistant?.maxRankedCandidates ?? DEFAULT_SETTINGS.maxRankedCandidates,
      promotionModelMode:
        assistant?.promotionModelMode ?? DEFAULT_SETTINGS.promotionModelMode,
      mode: assistant?.mode ?? DEFAULT_SETTINGS.mode,
    }),
    [accountId, assistant]
  );

  const save = useCallback(
    async (patch: Partial<LimitUpBoardAssistantSettingsInput>) => {
      if (!accountId) throw new Error('未选择交易账户');
      const result = await saveMutation({
        input: { ...currentSettings, ...patch, accountId },
      });
      const payload = result.data?.saveFirstBoardAssistant;
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

  const setPreference = useCallback(
    async (instrumentCode: string, preference: 'PREFER' | 'IGNORE') => {
      if (!accountId) throw new Error('未选择交易账户');
      const result = await preferenceMutation({
        input: {
          accountId,
          instrumentCode,
          preference,
          idempotencyKey: idempotencyKey(
            `preference:${preference}`,
            accountId,
            instrumentCode
          ),
        },
      });
      const payload = result.data?.setFirstBoardCandidatePreference;
      if (result.error || !payload?.success) {
        throw new Error(result.error?.message || payload?.message || '偏好保存失败');
      }
      refresh();
      return payload;
    },
    [accountId, preferenceMutation, refresh]
  );

  const arm = useCallback(
    (instrumentCode: string) => setPreference(instrumentCode, 'PREFER'),
    [setPreference]
  );

  const disarm = useCallback(
    (instrumentCode: string) => setPreference(instrumentCode, 'IGNORE'),
    [setPreference]
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
