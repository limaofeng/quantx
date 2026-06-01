import type {
  StrategyRunMode,
  StrategyRunStatus,
} from '@/generated/gql/graphql';

export type StrategyJsonValue =
  | string
  | number
  | boolean
  | null
  | StrategyJsonValue[]
  | { [key: string]: StrategyJsonValue };

export interface StrategyDefinition {
  key: string;
  strategyId?: number;
  displayName: string;
  market: string;
  description: string;
  parameterSchema?: unknown;
  supportedInstruments: string[];
  riskLevel?: string | null;
  category?: string | null;
}

export interface StrategyInstance {
  id: string;
  strategyKey: string;
  strategyId?: number;
  instrumentCode: string;
  displayName: string;
  status: StrategyRunStatus | string;
  mode: StrategyRunMode | string;
  parameters: Record<string, StrategyJsonValue>;
  parameterVersion: string;
  createdAt: string;
  updatedAt: string;
  lastDecisionAt?: string | null;
  latestExecutionStatus?: string | null;
}

export interface TradeIntentView {
  id: string;
  side: string;
  instrumentCode: string;
  targetBucket?: string | null;
  priceIntent?: string | number | null;
  quantityIntent?: string | number | null;
  reason?: string | null;
  traceId?: string | null;
  status?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface StrategyDecision {
  id: string;
  instanceId: string;
  decidedAt: string;
  inputSummary: Record<string, StrategyJsonValue>;
  outputSummary: Record<string, StrategyJsonValue>;
  tradeIntents: TradeIntentView[];
  statePatch?: Record<string, StrategyJsonValue>;
  decisionTrace: string[];
}

export interface ExecutionTraceView {
  id: string;
  intentId: string;
  instrumentCode: string;
  side: string;
  orderId?: string | null;
  riskDecision?: string | null;
  sizingResult?: string | null;
  orderStatus?: string | null;
  fillStatus?: string | null;
  executedPrice?: number | null;
  executedVolume?: number | null;
  executedTime?: string | null;
  reason?: string | null;
  traceId?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface BucketLedgerView {
  lockedCore: number;
  core: number;
  swing: number;
  updatedAt?: string | null;
}

export const BUCKET_LABELS: Record<string, string> = {
  locked_core: '封存仓',
  lockedCore: '封存仓',
  core: '核心仓',
  swing: '活跃仓',
};

export function getBucketLabel(bucket?: string | null) {
  if (!bucket) return '未指定';
  return BUCKET_LABELS[bucket] || bucket;
}
