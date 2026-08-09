import {
  StrategyRunMode,
  StrategyRunStatus,
  type StrategiesQuery,
  type StrategyQuery,
  type StrategyRunsQuery,
  type StrategyRunQuery,
} from '@/generated/gql/graphql';

import { normalizeStrategyRunStatus } from './strategyRunState';
import {
  type BucketLedgerView,
  type ExecutionTraceView,
  type StrategyDecision,
  type StrategyDefinition,
  type StrategyInstance,
  type StrategyJsonValue,
  type TradeIntentView,
} from './types';

type StrategyLike =
  | NonNullable<StrategiesQuery['strategies']>[number]
  | NonNullable<StrategyQuery['strategy']>;

export type StrategyRunLike =
  | StrategyRunsQuery['strategyRuns'][number]
  | NonNullable<StrategyRunQuery['strategyRun']>;

function asRecord(value: unknown): Record<string, StrategyJsonValue> {
  if (!value) return {};

  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return asRecord(parsed);
    } catch {
      return {};
    }
  }

  if (typeof value === 'object' && !Array.isArray(value)) {
    return value as Record<string, StrategyJsonValue>;
  }

  return {};
}

function firstArray(...values: unknown[]) {
  for (const value of values) {
    if (Array.isArray(value) && value.length > 0) return value;
  }
  return [];
}

function readString(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return undefined;
}

function readNumber(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'number') return value;
    if (
      typeof value === 'string' &&
      value.trim() &&
      !Number.isNaN(Number(value))
    ) {
      return Number(value);
    }
  }
  return undefined;
}

function readObject(record: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = record[key];
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>;
    }
  }
  return undefined;
}

export function normalizeInstrumentCode(value: string) {
  return value.trim().toUpperCase();
}

export function parseSingleInstrumentCode(value: string) {
  const instruments = value
    .split(/[,，\s]+/)
    .map(normalizeInstrumentCode)
    .filter(Boolean);

  return {
    instrumentCode: instruments[0] || '',
    instruments,
    hasMultiple: instruments.length > 1,
  };
}

export function mapStrategyToDefinition(
  strategy: StrategyLike
): StrategyDefinition {
  const className = 'className' in strategy ? strategy.className : undefined;
  const filePath = 'filePath' in strategy ? strategy.filePath : undefined;
  const key = className || filePath || String(strategy.id);

  return {
    key,
    displayName: strategy.name,
    market: 'A股',
    description: strategy.description,
    parameterSchema: strategy.parameterSchema,
    supportedInstruments:
      strategy.instrumentScope === 'MULTI' ? ['多标的'] : ['单标的'],
    riskLevel: strategy.riskLevel,
    category: strategy.category,
  };
}

export function mapStrategyRunToInstance(
  run: StrategyRunLike
): StrategyInstance {
  const parameters = asRecord(run.parameters);
  const metrics = asRecord(run.metrics);
  const instrumentCode =
    run.instruments?.[0] ||
    readString(parameters as Record<string, unknown>, [
      'instrument_code',
      'instrumentCode',
      'stockCode',
      'symbol',
    ]) ||
    '未绑定';

  return {
    id: run.id,
    strategyKey: run.strategy?.name || String(run.strategy?.id || ''),
    strategyId: run.strategy?.id,
    instrumentCode,
    displayName: run.name || run.strategy?.name || run.id,
    status: run.status,
    mode: run.mode,
    parameters,
    parameterVersion:
      readString(metrics as Record<string, unknown>, [
        'parameterVersion',
        'parameter_version',
        'configVersion',
      ]) || run.createTime,
    createdAt: run.createTime,
    updatedAt: run.stopTime || run.startTime || run.createTime,
    lastDecisionAt:
      readString(metrics as Record<string, unknown>, [
        'lastDecisionAt',
        'last_decision_at',
        'decidedAt',
      ]) || null,
    latestExecutionStatus:
      readString(metrics as Record<string, unknown>, [
        'latestExecutionStatus',
        'latest_execution_status',
        'orderStatus',
      ]) || null,
  };
}

function mapRawIntent(
  raw: unknown,
  index: number,
  fallbackInstrument: string
): TradeIntentView {
  const record = asRecord(raw);
  const unknownRecord = record as Record<string, unknown>;
  const side =
    readString(unknownRecord, ['side', 'action', 'intentSide']) || 'UNKNOWN';

  return {
    id:
      readString(unknownRecord, ['id', 'intentId', 'intent_id', 'traceId']) ||
      `intent-${index + 1}`,
    side,
    instrumentCode:
      readString(unknownRecord, [
        'instrumentCode',
        'instrument_code',
        'stockCode',
        'symbol',
      ]) || fallbackInstrument,
    targetBucket:
      readString(unknownRecord, ['targetBucket', 'target_bucket', 'bucket']) ||
      null,
    priceIntent:
      readNumber(unknownRecord, ['priceIntent', 'price_intent', 'price']) ??
      readString(unknownRecord, ['priceIntent', 'price_intent', 'price']) ??
      null,
    quantityIntent:
      readNumber(unknownRecord, [
        'quantityIntent',
        'quantity_intent',
        'quantity',
        'shares',
      ]) ??
      readString(unknownRecord, [
        'quantityIntent',
        'quantity_intent',
        'quantity',
        'shares',
      ]) ??
      null,
    reason: readString(unknownRecord, ['reason', 'message', 'note']) || null,
    traceId: readString(unknownRecord, ['traceId', 'trace_id']) || null,
    status: readString(unknownRecord, ['status']) || null,
    createdAt: readString(unknownRecord, ['createdAt', 'created_at']) || null,
    updatedAt: readString(unknownRecord, ['updatedAt', 'updated_at']) || null,
  };
}

function mapDecision(
  raw: unknown,
  index: number,
  run: StrategyRunLike
): StrategyDecision {
  const record = asRecord(raw);
  const unknownRecord = record as Record<string, unknown>;
  const metrics = asRecord(run.metrics);
  const instance = mapStrategyRunToInstance(run);
  const output =
    readObject(unknownRecord, ['outputSummary', 'output_summary', 'output']) ||
    readObject(metrics as Record<string, unknown>, [
      'latestStrategyOutput',
      'latest_strategy_output',
    ]) ||
    {};
  const input =
    readObject(unknownRecord, ['inputSummary', 'input_summary', 'input']) ||
    readObject(metrics as Record<string, unknown>, [
      'latestStrategyInput',
      'latest_strategy_input',
    ]) ||
    {};
  const rawIntents = firstArray(
    unknownRecord.tradeIntents,
    unknownRecord.trade_intents,
    unknownRecord.intents
  );

  const traceValue =
    unknownRecord.decisionTrace ||
    unknownRecord.decision_trace ||
    unknownRecord.trace ||
    (metrics as Record<string, unknown>).decisionTrace;
  const decisionTrace = Array.isArray(traceValue)
    ? traceValue.map(item =>
        typeof item === 'string' ? item : JSON.stringify(item)
      )
    : typeof traceValue === 'string'
      ? [traceValue]
      : [];

  return {
    id:
      readString(unknownRecord, ['id', 'decisionId', 'decision_id']) ||
      `decision-${index + 1}`,
    instanceId: run.id,
    decidedAt:
      readString(unknownRecord, ['decidedAt', 'decided_at', 'timestamp']) ||
      instance.lastDecisionAt ||
      run.startTime ||
      run.createTime,
    inputSummary: input as Record<string, StrategyJsonValue>,
    outputSummary: output as Record<string, StrategyJsonValue>,
    tradeIntents: rawIntents.map((intent, intentIndex) =>
      mapRawIntent(intent, intentIndex, instance.instrumentCode)
    ),
    statePatch:
      (readObject(unknownRecord, ['statePatch', 'state_patch']) as
        Record<string, StrategyJsonValue> | undefined) || undefined,
    decisionTrace,
  };
}

export function mapDecisionHistoryFromRun(
  run?: StrategyRunLike | null
): StrategyDecision[] {
  if (!run?.metrics) return [];

  const metrics = asRecord(run.metrics);
  const rawHistory = firstArray(
    (metrics as Record<string, unknown>).decisionHistory,
    (metrics as Record<string, unknown>).decision_history,
    (metrics as Record<string, unknown>).decisions
  );

  if (rawHistory.length > 0) {
    return rawHistory.map((decision, index) =>
      mapDecision(decision, index, run)
    );
  }

  const latestOutput =
    readObject(metrics as Record<string, unknown>, ['latestStrategyOutput']) ||
    readObject(metrics as Record<string, unknown>, ['latest_strategy_output']);
  const latestInput =
    readObject(metrics as Record<string, unknown>, ['latestStrategyInput']) ||
    readObject(metrics as Record<string, unknown>, ['latest_strategy_input']);
  const latestIntents = firstArray(
    (metrics as Record<string, unknown>).tradeIntents,
    (metrics as Record<string, unknown>).trade_intents,
    (metrics as Record<string, unknown>).intents
  );

  if (!latestOutput && !latestInput && latestIntents.length === 0) return [];

  return [
    mapDecision(
      {
        id: 'latest',
        inputSummary: latestInput || {},
        outputSummary: latestOutput || {},
        tradeIntents: latestIntents,
        decisionTrace: (metrics as Record<string, unknown>).decisionTrace,
        decidedAt:
          (metrics as Record<string, unknown>).lastDecisionAt ||
          (metrics as Record<string, unknown>).last_decision_at,
      },
      0,
      run
    ),
  ];
}

export function mapExecutionTraceFromRun(
  run?: StrategyRunLike | null
): ExecutionTraceView[] {
  if (!run?.metrics) return [];

  const metrics = asRecord(run.metrics);
  const instance = mapStrategyRunToInstance(run);
  const rawTrace = firstArray(
    (metrics as Record<string, unknown>).executionTrace,
    (metrics as Record<string, unknown>).execution_trace
  );

  return rawTrace.map((raw, index) => {
    const record = asRecord(raw) as Record<string, unknown>;
    return {
      id: readString(record, ['id']) || `execution-${index + 1}`,
      intentId:
        readString(record, ['intentId', 'intent_id', 'tradeIntentId']) ||
        `intent-${index + 1}`,
      instrumentCode:
        readString(record, [
          'instrumentCode',
          'instrument_code',
          'stockCode',
          'symbol',
        ]) || instance.instrumentCode,
      side: readString(record, ['side', 'action']) || 'UNKNOWN',
      riskDecision:
        readString(record, ['riskDecision', 'risk_decision', 'riskStatus']) ||
        null,
      sizingResult:
        readString(record, ['sizingResult', 'sizing_result', 'sizingStatus']) ||
        null,
      orderStatus:
        readString(record, ['orderStatus', 'order_status', 'entrustStatus']) ||
        null,
      fillStatus:
        readString(record, ['fillStatus', 'fill_status', 'dealStatus']) || null,
      reason: readString(record, ['reason', 'message', 'note']) || null,
    };
  });
}

export function mapBucketLedgerFromRun(
  run?: StrategyRunLike | null
): BucketLedgerView {
  const emptyLedger = {
    lockedCore: 0,
    core: 0,
    swing: 0,
    updatedAt: null,
  };

  if (!run?.metrics) return emptyLedger;

  const metrics = asRecord(run.metrics);
  const ledger =
    readObject(metrics as Record<string, unknown>, ['bucketLedger']) ||
    readObject(metrics as Record<string, unknown>, ['bucket_ledger']) ||
    readObject(metrics as Record<string, unknown>, ['buckets']);

  if (!ledger) return emptyLedger;

  return {
    lockedCore:
      readNumber(ledger, ['locked_core', 'lockedCore', 'locked']) || 0,
    core: readNumber(ledger, ['core']) || 0,
    swing: readNumber(ledger, ['swing']) || 0,
    updatedAt:
      readString(ledger, ['updatedAt', 'updated_at']) ||
      readString(metrics as Record<string, unknown>, ['lastDecisionAt']) ||
      null,
  };
}

export function mapStrategyDefinitionView(raw: unknown): StrategyDefinition {
  const record = asRecord(raw) as Record<string, unknown>;
  return {
    key:
      readString(record, ['key', 'strategyKey']) ||
      String(record.strategyId || ''),
    strategyId: readNumber(record, ['strategyId', 'strategy_id']),
    displayName:
      readString(record, ['displayName', 'name']) ||
      readString(record, ['key']) ||
      '未命名策略',
    market: readString(record, ['market']) || 'A股',
    description: readString(record, ['description']) || '',
    parameterSchema: record.parameterSchema,
    supportedInstruments:
      firstArray(record.supportedInstruments, record.supported_instruments).map(
        item => String(item)
      ) || [],
    riskLevel: readString(record, ['riskLevel', 'risk_level']) || null,
    category: readString(record, ['category']) || null,
  };
}

export function mapStrategyInstanceView(raw: unknown): StrategyInstance {
  const record = asRecord(raw) as Record<string, unknown>;
  const parameters = asRecord(record.parameters);
  const createdAt =
    readString(record, ['createdAt', 'created_at', 'createTime']) ||
    new Date().toISOString();

  return {
    id: readString(record, ['id']) || '',
    strategyKey: readString(record, ['strategyKey', 'strategy_key']) || '',
    strategyId: readNumber(record, ['strategyId', 'strategy_id']),
    instrumentCode:
      readString(record, ['instrumentCode', 'instrument_code']) ||
      readString(parameters as Record<string, unknown>, ['instrument_code']) ||
      '未绑定',
    displayName:
      readString(record, ['displayName', 'display_name', 'name']) ||
      '未命名实例',
    status: readString(record, ['status']) || StrategyRunStatus.Pending,
    mode: readString(record, ['mode']) || StrategyRunMode.Paper,
    parameters,
    parameterVersion:
      readString(record, ['parameterVersion', 'parameter_version']) ||
      createdAt,
    createdAt,
    updatedAt:
      readString(record, ['updatedAt', 'updated_at']) ||
      readString(record, ['lastDecisionAt', 'last_decision_at']) ||
      createdAt,
    lastDecisionAt:
      readString(record, ['lastDecisionAt', 'last_decision_at']) || null,
    latestExecutionStatus:
      readString(record, [
        'latestExecutionStatus',
        'latest_execution_status',
      ]) || null,
  };
}

export function mapStrategyDecisionView(raw: unknown): StrategyDecision {
  const record = asRecord(raw) as Record<string, unknown>;
  const trace = record.decisionTrace || record.decision_trace;
  const traceRecord = asRecord(trace) as Record<string, unknown>;
  const traceItems =
    Array.isArray(traceRecord.tags) || traceRecord.reason
      ? [
          ...(Array.isArray(traceRecord.tags)
            ? traceRecord.tags.map(item => String(item))
            : []),
          ...(traceRecord.reason ? [String(traceRecord.reason)] : []),
        ]
      : typeof trace === 'string'
        ? [trace]
        : trace
          ? [JSON.stringify(trace)]
          : [];

  return {
    id: readString(record, ['id']) || '',
    instanceId: readString(record, ['instanceId', 'instance_id']) || '',
    decidedAt:
      readString(record, ['decidedAt', 'decided_at']) ||
      new Date().toISOString(),
    inputSummary: (record.inputSummary || record.input_summary || {}) as Record<
      string,
      StrategyJsonValue
    >,
    outputSummary: (record.outputSummary ||
      record.output_summary ||
      {}) as Record<string, StrategyJsonValue>,
    tradeIntents: firstArray(record.tradeIntents, record.trade_intents).map(
      (intent, index) => mapRawIntent(intent, index, '')
    ),
    statePatch: (record.statePatch || record.state_patch || {}) as Record<
      string,
      StrategyJsonValue
    >,
    decisionTrace: traceItems,
  };
}

export function mapExecutionTraceView(raw: unknown): ExecutionTraceView {
  const record = asRecord(raw) as Record<string, unknown>;
  return {
    id: readString(record, ['id']) || '',
    intentId: readString(record, ['intentId', 'intent_id']) || '',
    instrumentCode:
      readString(record, ['instrumentCode', 'instrument_code']) || '',
    side: readString(record, ['side']) || 'UNKNOWN',
    orderId: readString(record, ['orderId', 'order_id']) || null,
    riskDecision: readString(record, ['riskDecision', 'risk_decision']) || null,
    sizingResult: readString(record, ['sizingResult', 'sizing_result']) || null,
    orderStatus: readString(record, ['orderStatus', 'order_status']) || null,
    fillStatus: readString(record, ['fillStatus', 'fill_status']) || null,
    executedPrice:
      readNumber(record, ['executedPrice', 'executed_price']) ?? null,
    executedVolume:
      readNumber(record, ['executedVolume', 'executed_volume']) ?? null,
    executedTime: readString(record, ['executedTime', 'executed_time']) || null,
    reason: readString(record, ['reason']) || null,
    traceId: readString(record, ['traceId', 'trace_id']) || null,
    createdAt: readString(record, ['createdAt', 'created_at']) || null,
    updatedAt: readString(record, ['updatedAt', 'updated_at']) || null,
  };
}

export function mapBucketLedgerView(raw: unknown): BucketLedgerView {
  const record = asRecord(raw) as Record<string, unknown>;
  return {
    lockedCore: readNumber(record, ['lockedCore', 'locked_core']) || 0,
    core: readNumber(record, ['core']) || 0,
    swing: readNumber(record, ['swing']) || 0,
    updatedAt: readString(record, ['updatedAt', 'updated_at']) || null,
  };
}

export function isEditableInstance(instance?: StrategyInstance | null) {
  return (
    !!instance && normalizeStrategyRunStatus(instance.status) !== 'RUNNING'
  );
}
