import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useSubscription } from 'urql';

import { useGraphqlWsStatus } from '@/core/graphql/ws-status';
import {
  type FragmentType,
  useFragment as getFragmentData,
} from '@/generated/gql';
import {
  CancelLimitUpBoardReplayDocument,
  LimitUpBoardReplayCoverageFieldsFragmentDoc,
  LimitUpBoardReplayCurveDocument,
  LimitUpBoardReplayDataQualityFieldsFragmentDoc,
  LimitUpBoardReplayDetailDocument,
  LimitUpBoardReplayFieldsFragmentDoc,
  LimitUpBoardReplayHistoryDocument,
  LimitUpBoardReplayPreparationDocument,
  LimitUpBoardReplayScenarioFieldsFragmentDoc,
  LimitUpBoardReplayTradesDocument,
  LimitUpBoardReplayUpdatesDocument,
  StartLimitUpBoardReplayDocument,
} from '@/generated/gql/graphql';

const ACTIVE_STATUSES = new Set(['PENDING', 'STARTING', 'RUNNING']);
const SELECTION_KEY = 'quantx:limit-up-board-replay:selected-job';

function createIdempotencyKey(accountId: string) {
  const suffix =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `limit-up-board-replay:${accountId}:${suffix}`;
}

function isNewerRevision(previous: string | undefined, next: string) {
  if (!/^\d+$/.test(next)) return false;
  if (!previous || !/^\d+$/.test(previous)) return true;
  return BigInt(next) > BigInt(previous);
}

function replayFields(
  value:
    FragmentType<typeof LimitUpBoardReplayFieldsFragmentDoc> | null | undefined
) {
  const replay = getFragmentData(LimitUpBoardReplayFieldsFragmentDoc, value);
  if (!replay) return replay;
  const quality = getFragmentData(
    LimitUpBoardReplayDataQualityFieldsFragmentDoc,
    replay.dataQuality
  );
  return {
    ...replay,
    dataQuality: {
      ...quality,
      coverage: getFragmentData(
        LimitUpBoardReplayCoverageFieldsFragmentDoc,
        quality.coverage
      ),
    },
    scenarios: getFragmentData(
      LimitUpBoardReplayScenarioFieldsFragmentDoc,
      replay.scenarios
    ),
  };
}

type ReplayFields = NonNullable<ReturnType<typeof replayFields>>;

function isReplayFields(
  value: ReturnType<typeof replayFields>
): value is ReplayFields {
  return value != null;
}

export function useLimitUpBoardReplay(
  accountId: string | undefined,
  startTime: string,
  endTime: string,
  scenarioId: string | undefined
) {
  const wsStatus = useGraphqlWsStatus();
  const [selectedJobId, setSelectedJobIdState] = useState(() =>
    typeof window === 'undefined'
      ? ''
      : window.sessionStorage.getItem(SELECTION_KEY) || ''
  );
  const revisionsRef = useRef(new Map<string, string>());

  const [preparationResult, refreshPreparation] = useQuery({
    query: LimitUpBoardReplayPreparationDocument,
    variables: {
      accountId: accountId || '',
      startTime,
      endTime,
    },
    pause: !accountId || !startTime || !endTime,
    requestPolicy: 'cache-and-network',
  });
  const [historyResult, refreshHistory] = useQuery({
    query: LimitUpBoardReplayHistoryDocument,
    variables: { accountId: accountId || '', limit: 20 },
    pause: !accountId,
    requestPolicy: 'cache-and-network',
  });
  const [detailResult, refreshDetail] = useQuery({
    query: LimitUpBoardReplayDetailDocument,
    variables: { jobId: selectedJobId },
    pause: !selectedJobId,
    requestPolicy: 'cache-and-network',
  });
  const [tradesResult, refreshTrades] = useQuery({
    query: LimitUpBoardReplayTradesDocument,
    variables: {
      jobId: selectedJobId,
      scenarioId: scenarioId || '',
      offset: 0,
      limit: 100,
    },
    pause: !selectedJobId || !scenarioId,
    requestPolicy: 'cache-and-network',
  });
  const [curveResult, refreshCurve] = useQuery({
    query: LimitUpBoardReplayCurveDocument,
    variables: {
      jobId: selectedJobId,
      scenarioId: scenarioId || '',
      offset: 0,
      limit: 2_000,
    },
    pause: !selectedJobId || !scenarioId,
    requestPolicy: 'cache-and-network',
  });
  const [updateResult] = useSubscription({
    query: LimitUpBoardReplayUpdatesDocument,
    variables: { accountId: accountId || '' },
    pause: !accountId,
  });
  const [startResult, startMutation] = useMutation(
    StartLimitUpBoardReplayDocument
  );
  const [cancelResult, cancelMutation] = useMutation(
    CancelLimitUpBoardReplayDocument
  );

  const history = (historyResult.data?.limitUpBoardReplayHistory ?? [])
    .map(item => replayFields(item))
    .filter(isReplayFields);
  const detail = replayFields(detailResult.data?.limitUpBoardReplay);
  const selectedReplay =
    detail ?? history.find(job => job.jobId === selectedJobId);
  const activeReplay = history.find(job => ACTIVE_STATUSES.has(job.status));

  const setSelectedJobId = useCallback((jobId: string) => {
    setSelectedJobIdState(jobId);
    if (typeof window === 'undefined') return;
    if (jobId) window.sessionStorage.setItem(SELECTION_KEY, jobId);
    else window.sessionStorage.removeItem(SELECTION_KEY);
  }, []);

  useEffect(() => {
    if (!history.length) return;
    if (selectedJobId && history.some(job => job.jobId === selectedJobId))
      return;
    setSelectedJobId(activeReplay?.jobId || history[0]?.jobId || '');
  }, [activeReplay?.jobId, history, selectedJobId, setSelectedJobId]);

  const refresh = useCallback(() => {
    refreshHistory({ requestPolicy: 'network-only' });
    refreshPreparation({ requestPolicy: 'network-only' });
    if (selectedJobId) refreshDetail({ requestPolicy: 'network-only' });
  }, [refreshDetail, refreshHistory, refreshPreparation, selectedJobId]);

  useEffect(() => {
    const notice = updateResult.data?.limitUpBoardReplayUpdates;
    if (!notice) return;
    const previous = revisionsRef.current.get(notice.jobId);
    if (!isNewerRevision(previous, notice.revision)) return;
    revisionsRef.current.set(notice.jobId, notice.revision);
    refreshHistory({ requestPolicy: 'network-only' });
    if (notice.jobId === selectedJobId) {
      refreshDetail({ requestPolicy: 'network-only' });
      if (notice.kind === 'RESULT_READY' && scenarioId) {
        refreshTrades({ requestPolicy: 'network-only' });
        refreshCurve({ requestPolicy: 'network-only' });
      }
    }
  }, [
    refreshCurve,
    refreshDetail,
    refreshHistory,
    refreshTrades,
    scenarioId,
    selectedJobId,
    updateResult.data?.limitUpBoardReplayUpdates,
  ]);

  useEffect(() => {
    if (!accountId) return;
    const isActive = Boolean(
      activeReplay ||
      (selectedReplay && ACTIVE_STATUSES.has(selectedReplay.status))
    );
    const interval =
      wsStatus === 'connected'
        ? isActive
          ? 15_000
          : 30_000
        : isActive
          ? 2_500
          : 30_000;
    const poll = () => {
      if (document.visibilityState === 'visible') refresh();
    };
    const timer = window.setInterval(poll, interval);
    return () => window.clearInterval(timer);
  }, [accountId, activeReplay, refresh, selectedReplay, wsStatus]);

  const start = useCallback(
    async (initialCash?: number, initialTotalAsset?: number) => {
      if (!accountId) throw new Error('未选择交易账户');
      const result = await startMutation({
        input: {
          accountId,
          idempotencyKey: createIdempotencyKey(accountId),
          startTime,
          endTime,
          ...(initialCash != null ? { initialCash } : {}),
          ...(initialTotalAsset != null ? { initialTotalAsset } : {}),
        },
      });
      const payload = result.data?.startLimitUpBoardReplay;
      if (result.error || !payload?.success) {
        throw new Error(
          result.error?.message || payload?.message || '历史回放启动失败'
        );
      }
      const startedReplay = replayFields(payload.replay);
      if (startedReplay?.jobId) setSelectedJobId(startedReplay.jobId);
      refresh();
      return payload;
    },
    [accountId, endTime, refresh, setSelectedJobId, startMutation, startTime]
  );

  const cancel = useCallback(async () => {
    if (!selectedJobId) throw new Error('未选择历史回放任务');
    const result = await cancelMutation({ jobId: selectedJobId });
    const payload = result.data?.cancelLimitUpBoardReplay;
    if (result.error || !payload?.success) {
      throw new Error(
        result.error?.message || payload?.message || '取消历史回放失败'
      );
    }
    refresh();
    return payload;
  }, [cancelMutation, refresh, selectedJobId]);

  return useMemo(
    () => ({
      activeReplay,
      cancel,
      curve: curveResult.data?.limitUpBoardReplayCurve,
      detailError: detailResult.error,
      fetching:
        historyResult.fetching ||
        detailResult.fetching ||
        preparationResult.fetching,
      history,
      preparation: preparationResult.data?.limitUpBoardReplayPreparation,
      preparationError: preparationResult.error,
      refresh,
      selectedJobId,
      selectedReplay,
      setSelectedJobId,
      start,
      starting: startResult.fetching,
      cancelling: cancelResult.fetching,
      trades: tradesResult.data?.limitUpBoardReplayTrades,
      wsStatus,
    }),
    [
      activeReplay,
      cancel,
      cancelResult.fetching,
      curveResult.data?.limitUpBoardReplayCurve,
      detailResult.error,
      detailResult.fetching,
      history,
      historyResult.fetching,
      preparationResult.data?.limitUpBoardReplayPreparation,
      preparationResult.error,
      preparationResult.fetching,
      refresh,
      selectedJobId,
      selectedReplay,
      setSelectedJobId,
      start,
      startResult.fetching,
      tradesResult.data?.limitUpBoardReplayTrades,
      wsStatus,
    ]
  );
}
