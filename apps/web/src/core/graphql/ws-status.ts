import { useSyncExternalStore } from 'react';

export type GraphqlWsStatus =
  'idle' | 'connecting' | 'connected' | 'reconnecting' | 'error' | 'closed';

const listeners = new Set<() => void>();
let currentStatus: GraphqlWsStatus = 'idle';

export function setGraphqlWsStatus(status: GraphqlWsStatus) {
  if (currentStatus === status) return;
  currentStatus = status;
  listeners.forEach(listener => listener());
}

export function getGraphqlWsStatus() {
  return currentStatus;
}

export function subscribeGraphqlWsStatus(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useGraphqlWsStatus() {
  return useSyncExternalStore(
    subscribeGraphqlWsStatus,
    getGraphqlWsStatus,
    getGraphqlWsStatus
  );
}
