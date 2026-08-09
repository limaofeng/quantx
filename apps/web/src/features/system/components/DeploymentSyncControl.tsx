import React, { useState } from 'react';

import {
  useDeploymentSync,
  type UseDeploymentSyncResult,
} from '@/hooks/useDeploymentSync';

import { SyncControlPanel } from './SyncControlPanel';
import { TaskHistory } from './TaskHistory';

type SyncParameters =
  | Record<string, unknown>
  | (() => Record<string, unknown> | undefined)
  | undefined;

interface DeploymentSyncControlBaseProps {
  defaultFlowName: string;
  historyFallbackName?: string;
  syncParameters?: SyncParameters;
  syncDisabled?: boolean;
  syncDisabledReason?: string;
}

interface DeploymentSyncControlByNameProps extends DeploymentSyncControlBaseProps {
  deploymentName: string;
  errorMessage?: string;
  successMessage?: string;
  sync?: never;
}

interface DeploymentSyncControlByStateProps extends DeploymentSyncControlBaseProps {
  sync: UseDeploymentSyncResult;
  deploymentName?: never;
  errorMessage?: never;
  successMessage?: never;
}

type DeploymentSyncControlProps =
  DeploymentSyncControlByNameProps | DeploymentSyncControlByStateProps;

export function DeploymentSyncControl(props: DeploymentSyncControlProps) {
  if (props.sync) {
    return (
      <DeploymentSyncControlContent
        defaultFlowName={props.defaultFlowName}
        historyFallbackName={props.historyFallbackName}
        sync={props.sync}
        syncParameters={props.syncParameters}
        syncDisabled={props.syncDisabled}
        syncDisabledReason={props.syncDisabledReason}
      />
    );
  }

  return (
    <DeploymentSyncControlWithHook
      defaultFlowName={props.defaultFlowName}
      deploymentName={props.deploymentName}
      errorMessage={props.errorMessage}
      historyFallbackName={props.historyFallbackName}
      successMessage={props.successMessage}
      syncParameters={props.syncParameters}
      syncDisabled={props.syncDisabled}
      syncDisabledReason={props.syncDisabledReason}
    />
  );
}

function DeploymentSyncControlWithHook({
  defaultFlowName,
  deploymentName,
  errorMessage,
  historyFallbackName,
  successMessage,
  syncParameters,
  syncDisabled,
  syncDisabledReason,
}: DeploymentSyncControlByNameProps) {
  const sync = useDeploymentSync(deploymentName, {
    errorMessage,
    successMessage,
  });

  return (
    <DeploymentSyncControlContent
      defaultFlowName={defaultFlowName}
      historyFallbackName={historyFallbackName}
      sync={sync}
      syncParameters={syncParameters}
      syncDisabled={syncDisabled}
      syncDisabledReason={syncDisabledReason}
    />
  );
}

function DeploymentSyncControlContent({
  defaultFlowName,
  historyFallbackName,
  sync,
  syncParameters,
  syncDisabled,
  syncDisabledReason,
}: DeploymentSyncControlBaseProps & {
  sync: UseDeploymentSyncResult;
}) {
  const [showHistory, setShowHistory] = useState(false);
  const {
    deployment,
    cancelActiveRun,
    isScheduleUpdating,
    isRunCancelling,
    isSyncing,
    setScheduleActive,
    triggerSync,
  } = sync;

  const handleSync = () => {
    if (syncDisabled) return;
    const parameters =
      typeof syncParameters === 'function' ? syncParameters() : syncParameters;

    void triggerSync(parameters);
  };

  const handleToggleSchedule = () => {
    if (!deployment) return;
    void setScheduleActive(!deployment.isScheduleActive);
  };

  const handleCancelRun = () => {
    void cancelActiveRun();
  };

  return (
    <>
      <SyncControlPanel
        deployment={deployment}
        isRunCancelling={isRunCancelling}
        isScheduleUpdating={isScheduleUpdating}
        isSyncing={isSyncing}
        defaultFlowName={defaultFlowName}
        onCancelRun={handleCancelRun}
        onShowHistory={() => setShowHistory(true)}
        onSync={handleSync}
        onToggleSchedule={handleToggleSchedule}
        syncDisabled={syncDisabled}
        syncDisabledReason={syncDisabledReason}
      />

      <TaskHistory
        open={showHistory}
        onOpenChange={setShowHistory}
        deploymentId={deployment?.id}
        deploymentName={
          deployment?.flowName || historyFallbackName || defaultFlowName
        }
        workPoolName={deployment?.workPoolName}
      />
    </>
  );
}
