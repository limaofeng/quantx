import type { EntryPlanStatus } from './types';

const terminalEntryPlanStatuses = new Set<EntryPlanStatus>([
  'CANCELLED',
  'COMPLETED',
  'EXPIRED',
]);

export function shouldSubscribeToEntryPlan(status: EntryPlanStatus): boolean {
  return !terminalEntryPlanStatuses.has(status);
}
