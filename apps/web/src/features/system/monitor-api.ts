export type MonitorStatus =
  'healthy' | 'degraded' | 'unavailable' | 'unknown' | 'disabled';

export type MonitorWindow = '24h' | '7d' | '30d';
export type MonitorRange = MonitorWindow | '90d' | '1y';

export interface MonitorTargetSummary {
  id: string;
  name: string;
  group: 'external_dependency' | 'quantx_runtime';
  optional: boolean;
  derived: boolean;
  status: MonitorStatus;
  checkedAt: string | null;
  lastSuccessAt: string | null;
  latencyMs: number | null;
  reasonCode: string | null;
  availabilityPct: number | null;
  healthyPct: number | null;
  coveragePct: number | null;
  latencyP50Ms: number | null;
  latencyP95Ms: number | null;
  sampleCount: number;
  activeIncident: boolean;
}

export interface MonitorSummary {
  generatedAt: string;
  lastCycleAt: string | null;
  window: MonitorWindow;
  checkIntervalSeconds: number;
  overallStatus: MonitorStatus;
  groups: Array<{
    id: MonitorTargetSummary['group'];
    name: string;
    status: MonitorStatus;
    targetIds: string[];
  }>;
  targets: MonitorTargetSummary[];
}

export interface MonitorHistoryPoint {
  start: string;
  status: MonitorStatus;
  sampleCount: number;
  healthyCount: number;
  degradedCount: number;
  unavailableCount: number;
  unknownCount: number;
  disabledCount: number;
  latencyCount: number;
  latencyMaxMs: number | null;
  latencyP50Ms: number | null;
  latencyP95Ms: number | null;
}

export interface MonitorHistory {
  target: { id: string; name: string };
  range: MonitorRange;
  bucketSeconds: number;
  points: MonitorHistoryPoint[];
}

export interface MonitorIncident {
  id: number;
  targetId: string;
  targetName: string;
  openedAt: string;
  resolvedAt: string | null;
  active: boolean;
  reasonCode: string | null;
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { cache: 'no-store', signal });
  if (!response.ok) {
    throw new Error(`Monitor request failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export function getMonitorSummary(
  window: MonitorWindow = '24h',
  signal?: AbortSignal
) {
  return getJson<MonitorSummary>(
    `/monitor/api/v1/summary?window=${window}`,
    signal
  );
}

export function getMonitorHistory(
  targetId: string,
  range: MonitorRange,
  signal?: AbortSignal
) {
  return getJson<MonitorHistory>(
    `/monitor/api/v1/targets/${encodeURIComponent(targetId)}/history?range=${range}`,
    signal
  );
}

export async function getMonitorIncidents(
  range: MonitorRange,
  targetId?: string,
  signal?: AbortSignal
): Promise<MonitorIncident[]> {
  const query = new URLSearchParams({ range });
  if (targetId) query.set('targetId', targetId);
  const response = await getJson<{ incidents: MonitorIncident[] }>(
    `/monitor/api/v1/incidents?${query.toString()}`,
    signal
  );
  return response.incidents;
}
