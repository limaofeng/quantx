import { LoaderCircle } from 'lucide-react';
import * as React from 'react';
import { useSubscription } from 'urql';

import {
  EntryIntentUpdatedDocument,
  EntryPlanUpdatedDocument,
} from '@/generated/gql/graphql';

import {
  EntryAuthorizationConfirmationDialog,
  EntryIntentConfirmationDialog,
} from '../components/EntryPlanConfirmations';
import { useEntryPlansWorkspace } from '../hooks/useEntryPlansWorkspace';
import { shouldSubscribeToEntryPlan } from '../model/realtime';

import { EntryPlansPage } from './EntryPlansPage';

function EntryPlanRealtimeBridge({
  onUpdate,
  planId,
}: {
  onUpdate: () => void;
  planId: string;
}) {
  const [planUpdate] = useSubscription({
    query: EntryPlanUpdatedDocument,
    variables: { planId },
  });
  const [intentUpdate] = useSubscription({
    query: EntryIntentUpdatedDocument,
    variables: { planId },
  });

  React.useEffect(() => {
    if (planUpdate.data?.entryPlanUpdated) onUpdate();
  }, [onUpdate, planUpdate.data?.entryPlanUpdated]);

  React.useEffect(() => {
    if (intentUpdate.data) onUpdate();
  }, [intentUpdate.data, onUpdate]);

  return null;
}

export function ConnectedEntryPlansPage() {
  const workspace = useEntryPlansWorkspace();
  const refreshRef = React.useRef(workspace.controller.refresh);
  const refreshTimerRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    refreshRef.current = workspace.controller.refresh;
  }, [workspace.controller.refresh]);

  React.useEffect(
    () => () => {
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
      }
    },
    []
  );

  const scheduleAuthoritativeRefresh = React.useCallback(() => {
    if (refreshTimerRef.current !== null) {
      window.clearTimeout(refreshTimerRef.current);
    }
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      void refreshRef.current().catch(() => undefined);
    }, 80);
  }, []);

  return (
    <>
      {workspace.view.plans
        .filter(plan => shouldSubscribeToEntryPlan(plan.status))
        .map(plan => (
          <EntryPlanRealtimeBridge
            key={plan.id}
            onUpdate={scheduleAuthoritativeRefresh}
            planId={plan.id}
          />
        ))}
      <EntryPlansPage controller={workspace.controller} view={workspace.view} />
      {workspace.fetching ? (
        <div
          aria-live="polite"
          className="pointer-events-none fixed bottom-5 right-5 z-40 inline-flex items-center gap-2 rounded-md border border-white/10 bg-[#0b1120]/95 px-3 py-2 text-ui-label text-slate-300 shadow-none"
        >
          <LoaderCircle
            aria-hidden="true"
            className="h-4 w-4 animate-spin motion-reduce:animate-none"
          />
          正在载入账户、计划与安全门…
        </div>
      ) : null}
      <EntryAuthorizationConfirmationDialog
        busy={workspace.confirmationBusy}
        challenge={workspace.authorizationChallenge}
        error={workspace.confirmationError}
        onCancel={workspace.clearAuthorizationChallenge}
        onConfirm={workspace.confirmAuthorizationChallenge}
      />
      <EntryIntentConfirmationDialog
        busy={workspace.confirmationBusy}
        error={workspace.confirmationError}
        onCancel={workspace.clearIntentConfirmation}
        onConfirm={workspace.confirmPendingIntent}
        preview={workspace.intentConfirmation}
      />
    </>
  );
}
