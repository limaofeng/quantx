import * as React from 'react';
import { useClient, useMutation, useQuery } from 'urql';

import {
  CancelEntryPlanDocument,
  ConfirmEntryIntentDocument,
  ConfirmEntryPlanAuthorizationDocument,
  CreateEntryPlanDocument,
  EntryPlanEventsDocument,
  EntryPlanSecuritySearchDocument,
  EntryPlanWorkspaceDocument,
  EvaluateEntryPlanNowDocument,
  PreviewEntryIntentDocument,
  PreviewEntryPlanAuthorizationDocument,
  RejectEntryIntentDocument,
  SetEntryAutomationPausedDocument,
  SetEntryPlanEnabledDocument,
  TriggerEntryPlanManualRuleDocument,
  UpdateEntryPlanDocument,
} from '@/generated/gql/graphql';

import {
  buildEntryPlanConfiguration,
  mapEntryPlanWorkspace,
  type EntryEventProjection,
  type EntryPlanProjection,
  type EntryPlanWorkspaceProjection,
} from '../model/adapters';
import type {
  EntryPlanController,
  EntryPlanDraft,
  EntryPlanSaveAction,
  EntrySecurityOption,
} from '../model/types';

export interface EntryAuthorizationChallenge {
  planId: string;
  configVersion: number;
  challengeId: string;
  confirmationToken: string;
  authorizationFingerprint: string;
  challengeExpiresAt: string;
  authorizationExpiresAt: string;
  summary: string;
  riskEnvelope: Record<string, unknown>;
}

export interface EntryIntentConfirmationPreview {
  intentId: string;
  planId: string;
  instrumentCode: string;
  valid: boolean;
  code: string;
  message: string;
  signalPrice: number;
  latestPrice: number;
  priceDeviationBps: number;
  requestedAmountCny: number;
  sizedVolume: number;
  finalVolume: number;
  riskAction: string;
  expiresAtMs: number;
  challengeId: string;
  confirmationToken: string;
  challengeExpiresAt: string;
  warnings: string[];
}

function createIdempotencyKey(prefix: string): string {
  const suffix =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}:${suffix}`;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function useEntryPlansWorkspace() {
  const client = useClient();
  const [workspaceResult, reexecuteWorkspace] = useQuery({
    query: EntryPlanWorkspaceDocument,
    requestPolicy: 'cache-and-network',
  });
  const [, createEntryPlan] = useMutation(CreateEntryPlanDocument);
  const [, updateEntryPlan] = useMutation(UpdateEntryPlanDocument);
  const [, setEntryPlanEnabled] = useMutation(SetEntryPlanEnabledDocument);
  const [, cancelEntryPlan] = useMutation(CancelEntryPlanDocument);
  const [, evaluateEntryPlan] = useMutation(EvaluateEntryPlanNowDocument);
  const [, triggerManualRule] = useMutation(TriggerEntryPlanManualRuleDocument);
  const [, setAutomationPaused] = useMutation(SetEntryAutomationPausedDocument);
  const [, previewAuthorization] = useMutation(
    PreviewEntryPlanAuthorizationDocument
  );
  const [, confirmAuthorization] = useMutation(
    ConfirmEntryPlanAuthorizationDocument
  );
  const [, previewIntent] = useMutation(PreviewEntryIntentDocument);
  const [, confirmIntent] = useMutation(ConfirmEntryIntentDocument);
  const [, rejectIntent] = useMutation(RejectEntryIntentDocument);
  const [events, setEvents] = React.useState<EntryEventProjection[]>([]);
  const [authorizationChallenge, setAuthorizationChallenge] =
    React.useState<EntryAuthorizationChallenge | null>(null);
  const [intentConfirmation, setIntentConfirmation] =
    React.useState<EntryIntentConfirmationPreview | null>(null);
  const [confirmationBusy, setConfirmationBusy] = React.useState(false);
  const [confirmationError, setConfirmationError] = React.useState<
    string | null
  >(null);

  const workspace = workspaceResult.data;
  const planIds = React.useMemo(
    () => (workspace?.entryPlans ?? []).map(plan => plan.planId),
    [workspace?.entryPlans]
  );
  const planIdKey = planIds.join('|');

  const loadEvents = React.useCallback(
    async (ids: string[], requestPolicy: 'cache-first' | 'network-only') => {
      if (ids.length === 0) {
        setEvents([]);
        return;
      }
      const results = await Promise.all(
        ids.map(planId =>
          client
            .query(
              EntryPlanEventsDocument,
              { limit: 100, planId },
              { requestPolicy }
            )
            .toPromise()
        )
      );
      const firstError = results.find(result => result.error)?.error;
      if (firstError) throw new Error(firstError.message);
      setEvents(
        results.flatMap(result =>
          (result.data?.entryPlanEvents ?? []).map(event => ({
            ...event,
            details: event.details,
          }))
        ) as EntryEventProjection[]
      );
    },
    [client]
  );

  React.useEffect(() => {
    void loadEvents(planIds, 'cache-first').catch(() => undefined);
    // planIdKey is a stable scalar dependency for the plan identity set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadEvents, planIdKey]);

  const refresh = React.useCallback(async () => {
    reexecuteWorkspace({ requestPolicy: 'network-only' });
    await loadEvents(planIds, 'network-only');
  }, [loadEvents, planIds, reexecuteWorkspace]);

  const planById = React.useCallback(
    (planId: string) => {
      const plan = workspace?.entryPlans.find(item => item.planId === planId);
      if (!plan) throw new Error('计划已变化，请刷新后重试');
      return plan;
    },
    [workspace?.entryPlans]
  );

  const requestAuthorizationPreview = React.useCallback(
    async (planId: string, configVersion: number) => {
      const result = await previewAuthorization({
        input: {
          planId,
          configVersion,
          idempotencyKey: createIdempotencyKey('entry-authorization-preview'),
        },
      });
      if (result.error) throw new Error(result.error.message);
      const preview = result.data?.previewEntryPlanAuthorization;
      if (!preview) throw new Error('未收到实盘自动建仓授权预览');
      setConfirmationError(null);
      setAuthorizationChallenge({
        planId,
        configVersion,
        challengeId: String(preview.challengeId),
        confirmationToken: preview.confirmationToken,
        authorizationFingerprint: preview.authorizationFingerprint,
        challengeExpiresAt: preview.challengeExpiresAt,
        authorizationExpiresAt: preview.authorizationExpiresAt,
        summary: preview.summary,
        riskEnvelope: recordValue(preview.riskEnvelope),
      });
    },
    [previewAuthorization]
  );

  const searchSecurities = React.useCallback(
    async (query: string): Promise<EntrySecurityOption[]> => {
      const result = await client
        .query(
          EntryPlanSecuritySearchDocument,
          { limit: 12, query: query.trim() },
          { requestPolicy: 'network-only' }
        )
        .toPromise();
      if (result.error) throw new Error(result.error.message);
      const positions = new Map(
        (workspace?.positions ?? []).map(position => [
          position.stockCode,
          position,
        ])
      );
      const matches = [
        ...(result.data?.codeMatches ?? []),
        ...(result.data?.nameMatches ?? []),
      ];
      const unique = new Map<string, EntrySecurityOption>();
      matches.forEach(instrument => {
        if (unique.has(instrument.id)) return;
        const position = positions.get(instrument.id);
        unique.set(instrument.id, {
          instrumentCode: instrument.id,
          instrumentName: instrument.name || instrument.id,
          latestPrice:
            instrument.quote?.lastPrice ?? position?.lastPrice ?? null,
          heldVolume: Number(position?.volume ?? 0),
        });
      });
      return Array.from(unique.values()).slice(0, 12);
    },
    [client, workspace?.positions]
  );

  const saveDraft = React.useCallback(
    async (draft: EntryPlanDraft, action: EntryPlanSaveAction) => {
      const account = workspace?.currentAccount;
      if (!account) throw new Error('账户快照尚未就绪，暂不能创建买入计划');
      const sourcePlan = draft.planId
        ? (workspace.entryPlans.find(item => item.planId === draft.planId) as
            EntryPlanProjection | undefined)
        : undefined;
      const configuration = buildEntryPlanConfiguration(draft, sourcePlan);
      let planId = draft.planId ?? '';
      let configVersion = draft.configVersion ?? 1;

      if (planId) {
        const mustRemainPaused =
          action === 'SAVE_PAUSED' || action === 'PREVIEW_LIVE_AUTHORIZATION';
        if (mustRemainPaused) {
          const pausedResult = await setEntryPlanEnabled({
            planId,
            configVersion,
            enabled: false,
          });
          if (pausedResult.error) throw new Error(pausedResult.error.message);
          const pausedPayload = pausedResult.data?.setEntryPlanEnabled;
          if (!pausedPayload?.success) {
            throw new Error(
              pausedPayload?.message || '更新配置前暂停买入计划失败'
            );
          }
        }
        const result = await updateEntryPlan({
          input: {
            planId,
            configVersion,
            ...configuration,
            note: sourcePlan?.note ?? '',
            idempotencyKey: createIdempotencyKey('entry-plan-update'),
          },
        });
        if (result.error) {
          throw new Error(
            mustRemainPaused
              ? `${result.error.message}；计划已安全保持暂停`
              : result.error.message
          );
        }
        const payload = result.data?.updateEntryPlan;
        if (!payload?.success) {
          throw new Error(
            mustRemainPaused
              ? `${payload?.message || '更新买入计划失败'}；计划已安全保持暂停`
              : payload?.message || '更新买入计划失败'
          );
        }
        planId = String(payload.plan?.planId ?? planId);
        configVersion = Number(
          payload.plan?.configVersion ?? configVersion + 1
        );
      } else {
        const result = await createEntryPlan({
          input: {
            instrumentCode: draft.instrumentCode,
            bucket: draft.bucket,
            ...configuration,
            note: '',
            startImmediately:
              action === 'START_PAPER' || action === 'START_LIVE_MANUAL',
            idempotencyKey: createIdempotencyKey('entry-plan-create'),
          },
        });
        if (result.error) throw new Error(result.error.message);
        const payload = result.data?.createEntryPlan;
        if (!payload?.success || !payload.plan) {
          throw new Error(payload?.message || '创建买入计划失败');
        }
        planId = String(payload.plan.planId);
        configVersion = Number(payload.plan.configVersion);
      }

      if (
        draft.planId &&
        (action === 'START_PAPER' || action === 'START_LIVE_MANUAL')
      ) {
        const enabledResult = await setEntryPlanEnabled({
          planId,
          configVersion,
          enabled: true,
        });
        if (enabledResult.error) throw new Error(enabledResult.error.message);
        const enabledPayload = enabledResult.data?.setEntryPlanEnabled;
        if (!enabledPayload?.success) {
          throw new Error(enabledPayload?.message || '更新计划运行状态失败');
        }
      }

      if (action === 'PREVIEW_LIVE_AUTHORIZATION') {
        await requestAuthorizationPreview(planId, configVersion);
      }
      await refresh();
    },
    [
      refresh,
      requestAuthorizationPreview,
      setEntryPlanEnabled,
      updateEntryPlan,
      createEntryPlan,
      workspace?.currentAccount,
      workspace?.entryPlans,
    ]
  );

  const controller = React.useMemo<EntryPlanController>(
    () => ({
      searchSecurities,
      saveDraft,
      refresh,
      async setGlobalAutoEntryPaused(paused) {
        const result = await setAutomationPaused({
          paused,
          reason: paused ? 'USER_GLOBAL_PAUSE' : 'USER_GLOBAL_RESUME',
        });
        if (result.error) throw new Error(result.error.message);
        await refresh();
      },
      async pausePlan(planId) {
        const plan = planById(planId);
        const result = await setEntryPlanEnabled({
          planId,
          enabled: false,
          configVersion: plan.configVersion,
        });
        if (result.error) throw new Error(result.error.message);
        const payload = result.data?.setEntryPlanEnabled;
        if (!payload?.success) {
          throw new Error(payload?.message || '暂停计划失败');
        }
        await refresh();
      },
      async resumePlan(planId) {
        const plan = planById(planId);
        if (
          plan.environment === 'LIVE' &&
          plan.authorizationMode === 'AUTO' &&
          plan.authorizationState !== 'AUTHORIZED'
        ) {
          await requestAuthorizationPreview(planId, plan.configVersion);
          return;
        }
        const result = await setEntryPlanEnabled({
          planId,
          enabled: true,
          configVersion: plan.configVersion,
        });
        if (result.error) throw new Error(result.error.message);
        const payload = result.data?.setEntryPlanEnabled;
        if (!payload?.success) {
          throw new Error(payload?.message || '恢复计划失败');
        }
        await refresh();
      },
      async evaluatePlan(planId) {
        const result = await evaluateEntryPlan({ planId });
        if (result.error) throw new Error(result.error.message);
        const payload = result.data?.evaluateEntryPlanNow;
        if (!payload?.success) {
          throw new Error(payload?.message || '立即检查失败');
        }
        await refresh();
      },
      async triggerManualRule(planId, ruleId) {
        const plan = planById(planId);
        if (
          !plan.triggerRules?.some(
            rule => rule.ruleId === ruleId && rule.ruleType === 'MANUAL_TRIGGER'
          )
        ) {
          throw new Error('当前计划不是人工触发规则');
        }
        const result = await triggerManualRule({ planId, ruleId });
        if (result.error) throw new Error(result.error.message);
        const payload = result.data?.triggerEntryPlanManualRule;
        if (!payload?.success) {
          throw new Error(payload?.message || '触发本批检查失败');
        }
        await refresh();
      },
      async cancelPlan(planId, cancelWorkingOrder = false) {
        const plan = planById(planId);
        const result = await cancelEntryPlan({
          planId,
          configVersion: plan.configVersion,
          cancelWorkingOrder,
        });
        if (result.error) throw new Error(result.error.message);
        const payload = result.data?.cancelEntryPlan;
        if (!payload?.success) {
          throw new Error(payload?.message || '取消计划失败');
        }
        await refresh();
      },
      async previewPendingIntent(intentId) {
        const intent = workspace?.pendingEntryIntents.find(
          item => item.intentId === intentId
        );
        if (!intent) throw new Error('待确认意图已过期，请刷新');
        const result = await previewIntent({
          planId: intent.planId,
          intentId,
        });
        if (result.error) throw new Error(result.error.message);
        const preview = result.data?.previewEntryIntent;
        if (!preview) throw new Error('未收到最新买入风控预览');
        setConfirmationError(null);
        setIntentConfirmation({
          ...preview,
          intentId: String(preview.intentId),
          planId: String(preview.planId),
          challengeId: String(preview.challengeId),
        });
      },
      async rejectPendingIntent(intentId) {
        const intent = workspace?.pendingEntryIntents.find(
          item => item.intentId === intentId
        );
        if (!intent) throw new Error('待确认意图已过期，请刷新');
        const result = await rejectIntent({
          planId: intent.planId,
          intentId,
        });
        if (result.error) throw new Error(result.error.message);
        const payload = result.data?.rejectEntryIntent;
        if (!payload?.success) {
          throw new Error(payload?.message || '拒绝买入意图失败');
        }
        await refresh();
      },
    }),
    [
      cancelEntryPlan,
      evaluateEntryPlan,
      planById,
      previewIntent,
      refresh,
      rejectIntent,
      requestAuthorizationPreview,
      saveDraft,
      searchSecurities,
      setAutomationPaused,
      setEntryPlanEnabled,
      triggerManualRule,
      workspace?.pendingEntryIntents,
    ]
  );

  const confirmAuthorizationChallenge = React.useCallback(async () => {
    if (!authorizationChallenge) return;
    setConfirmationBusy(true);
    setConfirmationError(null);
    try {
      const result = await confirmAuthorization({
        input: {
          planId: authorizationChallenge.planId,
          configVersion: authorizationChallenge.configVersion,
          challengeId: authorizationChallenge.challengeId,
          confirmationToken: authorizationChallenge.confirmationToken,
        },
      });
      if (result.error) throw new Error(result.error.message);
      const payload = result.data?.confirmEntryPlanAuthorization;
      if (!payload?.success) {
        throw new Error(payload?.message || '自动建仓授权失败');
      }
      setAuthorizationChallenge(null);
      await refresh();
    } catch (error) {
      setConfirmationError(
        error instanceof Error ? error.message : '自动建仓授权失败'
      );
    } finally {
      setConfirmationBusy(false);
    }
  }, [authorizationChallenge, confirmAuthorization, refresh]);

  const confirmPendingIntent = React.useCallback(async () => {
    if (!intentConfirmation || !intentConfirmation.valid) return;
    setConfirmationBusy(true);
    setConfirmationError(null);
    try {
      const result = await confirmIntent({
        planId: intentConfirmation.planId,
        intentId: intentConfirmation.intentId,
        confirmationToken: intentConfirmation.confirmationToken,
      });
      if (result.error) throw new Error(result.error.message);
      const payload = result.data?.confirmEntryIntent;
      if (!payload?.success) {
        throw new Error(payload?.message || '确认买入意图失败');
      }
      setIntentConfirmation(null);
      await refresh();
    } catch (error) {
      setConfirmationError(
        error instanceof Error ? error.message : '确认买入意图失败'
      );
    } finally {
      setConfirmationBusy(false);
    }
  }, [confirmIntent, intentConfirmation, refresh]);

  const view = React.useMemo(
    () =>
      mapEntryPlanWorkspace(
        workspace as EntryPlanWorkspaceProjection | undefined,
        events,
        workspaceResult.error?.message
      ),
    [events, workspace, workspaceResult.error?.message]
  );

  return {
    authorizationChallenge,
    clearAuthorizationChallenge: () => {
      if (!confirmationBusy) setAuthorizationChallenge(null);
    },
    clearIntentConfirmation: () => {
      if (!confirmationBusy) setIntentConfirmation(null);
    },
    confirmationBusy,
    confirmationError,
    confirmAuthorizationChallenge,
    confirmPendingIntent,
    controller,
    fetching: workspaceResult.fetching && !workspaceResult.data,
    intentConfirmation,
    view,
  };
}
