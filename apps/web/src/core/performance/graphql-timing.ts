import {
  getOperationName,
  mapExchange,
  type Operation,
  type OperationResult,
} from 'urql';

const MAX_GRAPHQL_TIMINGS = 200;
const MAX_PENDING_GRAPHQL_OPERATIONS = 200;
const MAX_SERVER_FIELDS = 100;
const MAX_SERVER_SQL_STATEMENTS = 20;

export interface GraphqlFieldTiming {
  parentType: string;
  field: string;
  paths: string[];
  count: number;
  totalMs: number;
  maxMs: number;
  errors: number;
}

export interface GraphqlSqlStatementTiming {
  kind: string;
  fingerprint: string;
  count: number;
  totalMs: number;
  maxMs: number;
  errors: number;
}

export interface QuantxGraphqlTiming {
  requestId: string;
  operationName: string;
  operationType: string;
  totalMs: number;
  phases: Record<string, number>;
  fieldInvocations: number;
  fieldResolverMs: number;
  fieldCount: number;
  fieldsTruncated: boolean;
  fields: GraphqlFieldTiming[];
  sql: {
    count: number;
    totalMs: number;
    maxMs: number;
    errors: number;
    statementCount: number;
    statementsTruncated: boolean;
    statements: GraphqlSqlStatementTiming[];
  };
}

export interface GraphqlRequestTiming {
  requestId: string;
  operationName: string;
  operationType: string;
  route: string;
  startedAt: number;
  receivedAt: number;
  clientMs: number;
  graphqlMs: number | null;
  outsideGraphqlMs: number | null;
  hasError: boolean;
  server: QuantxGraphqlTiming | null;
}

interface PendingOperation {
  operationName: string;
  operationType: string;
  route: string;
  startedAt: number;
  startedClock: number;
}

const pendingOperations = new Map<number, PendingOperation>();
const requestTimings: GraphqlRequestTiming[] = [];

export const graphqlTimingExchange = mapExchange({
  onOperation(operation) {
    startGraphqlOperation(operation);
  },
  onResult(result) {
    finishGraphqlOperation(result);
  },
});

export function getGraphqlRequestTimings(): readonly GraphqlRequestTiming[] {
  return requestTimings.slice();
}

export function clearGraphqlRequestTimings(): void {
  requestTimings.length = 0;
  pendingOperations.clear();
}

export function parseQuantxGraphqlTiming(
  value: unknown
): QuantxGraphqlTiming | null {
  if (!isRecord(value)) return null;
  const fields = Array.isArray(value.fields)
    ? value.fields
        .slice(0, MAX_SERVER_FIELDS)
        .map(parseFieldTiming)
        .filter(isPresent)
    : [];
  const sqlValue = isRecord(value.sql) ? value.sql : {};
  const statements = Array.isArray(sqlValue.statements)
    ? sqlValue.statements
        .slice(0, MAX_SERVER_SQL_STATEMENTS)
        .map(parseSqlTiming)
        .filter(isPresent)
    : [];
  const phasesValue = isRecord(value.phases) ? value.phases : {};
  const phases: Record<string, number> = {};
  Object.entries(phasesValue).forEach(([name, duration]) => {
    if (isFiniteNumber(duration)) phases[name] = Math.max(0, duration);
  });

  return {
    requestId: readString(value.requestId, 'unknown'),
    operationName: readString(value.operationName, 'Anonymous'),
    operationType: readString(value.operationType, 'Unknown'),
    totalMs: readNumber(value.totalMs),
    phases,
    fieldInvocations: readNumber(value.fieldInvocations),
    fieldResolverMs: readNumber(value.fieldResolverMs),
    fieldCount: readNumber(value.fieldCount),
    fieldsTruncated: value.fieldsTruncated === true,
    fields,
    sql: {
      count: readNumber(sqlValue.count),
      totalMs: readNumber(sqlValue.totalMs),
      maxMs: readNumber(sqlValue.maxMs),
      errors: readNumber(sqlValue.errors),
      statementCount: readNumber(sqlValue.statementCount),
      statementsTruncated: sqlValue.statementsTruncated === true,
      statements,
    },
  };
}

export function recordGraphqlRequestTiming(timing: GraphqlRequestTiming): void {
  requestTimings.push(timing);
  if (requestTimings.length > MAX_GRAPHQL_TIMINGS) {
    requestTimings.splice(0, requestTimings.length - MAX_GRAPHQL_TIMINGS);
  }
}

function startGraphqlOperation(operation: Operation): void {
  if (operation.kind !== 'query' && operation.kind !== 'mutation') return;
  // A teardown does not pass through mapExchange callbacks. Replacing an old
  // key and bounding the map prevents cancelled requests from leaking state.
  pendingOperations.delete(operation.key);
  pendingOperations.set(operation.key, {
    operationName: getOperationName(operation.query) || 'Anonymous',
    operationType: operation.kind,
    route: typeof window === 'undefined' ? 'server' : window.location.pathname,
    startedAt: Date.now(),
    startedClock: currentClock(),
  });
  while (pendingOperations.size > MAX_PENDING_GRAPHQL_OPERATIONS) {
    const oldestKey = pendingOperations.keys().next().value;
    if (typeof oldestKey !== 'number') break;
    pendingOperations.delete(oldestKey);
  }
}

function finishGraphqlOperation(result: OperationResult): void {
  if (
    result.operation.kind !== 'query' &&
    result.operation.kind !== 'mutation'
  ) {
    return;
  }
  if (result.stale) return;
  const pending = pendingOperations.get(result.operation.key);
  if (!pending) return;
  pendingOperations.delete(result.operation.key);

  const server = parseQuantxGraphqlTiming(
    result.extensions?.quantxTiming as unknown
  );
  const clientMs = Math.max(0, currentClock() - pending.startedClock);
  const graphqlMs = server?.totalMs ?? null;
  recordGraphqlRequestTiming({
    requestId: server?.requestId ?? `client-${pending.startedAt.toString(36)}`,
    operationName: server?.operationName ?? pending.operationName,
    operationType: server?.operationType ?? pending.operationType,
    route: pending.route,
    startedAt: pending.startedAt,
    receivedAt: Date.now(),
    clientMs,
    graphqlMs,
    outsideGraphqlMs:
      graphqlMs === null ? null : Math.max(0, clientMs - graphqlMs),
    hasError: Boolean(result.error),
    server,
  });
}

function parseFieldTiming(value: unknown): GraphqlFieldTiming | null {
  if (!isRecord(value)) return null;
  return {
    parentType: readString(value.parentType, 'Unknown'),
    field: readString(value.field, 'Unknown'),
    paths: Array.isArray(value.paths)
      ? value.paths
          .slice(0, 3)
          .filter((path): path is string => typeof path === 'string')
          .map(path => path.slice(0, 256))
      : [],
    count: readNumber(value.count),
    totalMs: readNumber(value.totalMs),
    maxMs: readNumber(value.maxMs),
    errors: readNumber(value.errors),
  };
}

function parseSqlTiming(value: unknown): GraphqlSqlStatementTiming | null {
  if (!isRecord(value)) return null;
  return {
    kind: readString(value.kind, 'OTHER'),
    fingerprint: readString(value.fingerprint, 'unknown'),
    count: readNumber(value.count),
    totalMs: readNumber(value.totalMs),
    maxMs: readNumber(value.maxMs),
    errors: readNumber(value.errors),
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function readNumber(value: unknown): number {
  return isFiniteNumber(value) ? Math.max(0, value) : 0;
}

function readString(value: unknown, fallback: string): string {
  return typeof value === 'string' && value ? value : fallback;
}

function isPresent<T>(value: T | null): value is T {
  return value !== null;
}

function currentClock(): number {
  return typeof performance === 'undefined' ? Date.now() : performance.now();
}
