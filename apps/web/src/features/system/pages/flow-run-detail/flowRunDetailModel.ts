export interface FlowRunLogRecord {
  time?: string | null;
  level?: number | null;
  message?: string | null;
}

export type FlowRunLogLevel = 'INFO' | 'WARN' | 'ERROR';
export type FlowRunLogFilter = 'ALL' | FlowRunLogLevel;
export type FlowRunStatusTone =
  'info' | 'success' | 'warning' | 'danger' | 'neutral';

export interface FlowRunStatusVisual {
  label: string;
  tone: FlowRunStatusTone;
}

const LIVE_FLOW_RUN_STATES = new Set([
  'PENDING',
  'SCHEDULED',
  'RUNNING',
  'LATE',
  'PAUSED',
  'CANCELLING',
]);

export function normalizeFlowRunState(state: string | null | undefined) {
  return state?.trim().toUpperCase() || 'UNKNOWN';
}

export function isLiveFlowRunState(state: string | null | undefined) {
  return LIVE_FLOW_RUN_STATES.has(normalizeFlowRunState(state));
}

export function getFlowRunStatusVisual(
  state: string | null | undefined
): FlowRunStatusVisual {
  switch (normalizeFlowRunState(state)) {
    case 'COMPLETED':
      return { label: '已完成', tone: 'success' };
    case 'FAILED':
      return { label: '失败', tone: 'danger' };
    case 'CRASHED':
      return { label: '已崩溃', tone: 'danger' };
    case 'RUNNING':
      return { label: '运行中', tone: 'info' };
    case 'PENDING':
      return { label: '等待中', tone: 'warning' };
    case 'SCHEDULED':
      return { label: '已计划', tone: 'warning' };
    case 'LATE':
      return { label: '已延迟', tone: 'warning' };
    case 'PAUSED':
      return { label: '已暂停', tone: 'warning' };
    case 'CANCELLING':
      return { label: '正在取消', tone: 'warning' };
    case 'CANCELLED':
      return { label: '已取消', tone: 'neutral' };
    default:
      return { label: '未知状态', tone: 'neutral' };
  }
}

export function getFlowRunLogLevel(level: number | null | undefined) {
  const numericLevel = Number(level ?? 0);
  if (numericLevel >= 40) return 'ERROR' as const;
  if (numericLevel >= 30) return 'WARN' as const;
  return 'INFO' as const;
}

function getLogTimestamp(log: FlowRunLogRecord) {
  if (!log.time) return 0;
  const timestamp = new Date(log.time).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function getLogKey(log: FlowRunLogRecord) {
  return `${log.time ?? ''}::${log.level ?? ''}::${log.message ?? ''}`;
}

export function mergeFlowRunLogs(
  ...logGroups: ReadonlyArray<ReadonlyArray<FlowRunLogRecord>>
) {
  const seen = new Set<string>();
  const merged: FlowRunLogRecord[] = [];

  logGroups.flat().forEach(log => {
    const key = getLogKey(log);
    if (seen.has(key)) return;
    seen.add(key);
    merged.push(log);
  });

  return merged
    .sort((left, right) => getLogTimestamp(left) - getLogTimestamp(right))
    .slice(-5000);
}

export function isFlowRunLogFilter(value: string): value is FlowRunLogFilter {
  return ['ALL', 'INFO', 'WARN', 'ERROR'].includes(value);
}

export function filterFlowRunLogs(
  logs: ReadonlyArray<FlowRunLogRecord>,
  filter: FlowRunLogFilter,
  searchQuery: string
) {
  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();

  return logs.filter(log => {
    const level = getFlowRunLogLevel(log.level);
    if (filter !== 'ALL' && level !== filter) return false;
    if (!normalizedSearch) return true;

    return [log.time ?? '', level, log.message ?? '']
      .join(' ')
      .toLocaleLowerCase()
      .includes(normalizedSearch);
  });
}

export function safeParseFlowRunParameters(parameters: unknown) {
  if (!parameters) return {} as Record<string, unknown>;

  if (typeof parameters === 'string') {
    try {
      const parsed = JSON.parse(parameters);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : {};
    } catch {
      return {} as Record<string, unknown>;
    }
  }

  if (typeof parameters === 'object' && !Array.isArray(parameters)) {
    return parameters as Record<string, unknown>;
  }

  return {} as Record<string, unknown>;
}

export function formatFlowRunParameterValue(value: unknown) {
  if (typeof value === 'string') return value;
  if (value === undefined) return 'undefined';
  return JSON.stringify(value, null, 2);
}

function parseDateTime(value: string | null | undefined) {
  if (!value) return null;
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? null : timestamp;
}

export function resolveFlowRunElapsedSeconds({
  startedAt,
  finishedAt,
  totalRunTime,
  live,
  nowMs,
}: {
  startedAt?: string | null;
  finishedAt?: string | null;
  totalRunTime?: number | null;
  live: boolean;
  nowMs: number;
}) {
  const startedTimestamp = parseDateTime(startedAt);

  if (live && startedTimestamp !== null) {
    return Math.max(0, (nowMs - startedTimestamp) / 1000);
  }

  if (typeof totalRunTime === 'number' && totalRunTime >= 0) {
    return totalRunTime;
  }

  const finishedTimestamp = parseDateTime(finishedAt);
  if (startedTimestamp !== null && finishedTimestamp !== null) {
    return Math.max(0, (finishedTimestamp - startedTimestamp) / 1000);
  }

  return null;
}

export function formatFlowRunDuration(seconds: number | null | undefined) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return '—';
  }

  const totalSeconds = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const remainingSeconds = totalSeconds % 60;

  return [hours, minutes, remainingSeconds]
    .map(value => String(value).padStart(2, '0'))
    .join(':');
}
