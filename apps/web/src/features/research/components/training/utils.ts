import {
  StockSelectionTrainingRunStatus,
  StockSelectionTrainingUniverseKind,
  type StockSelectionDatasetVersion,
  type StockSelectionTrainingInput,
} from '@/generated/gql/graphql';

import type { UniverseDraft, WizardInputDraft } from './types';

export const WIZARD_STEPS = [
  '数据集',
  '时间切分',
  '模型与资源',
  '预检确认',
] as const;
export const FIXED_TIME_STRUCTURE =
  '30 月训练 / 6 月校准 / 1 月验证 / 12 月冻结测试';
export const MODEL_FAMILY = 'Logistic + LightGBM';
export const PHASES = [
  'PREFLIGHT',
  'DATASET_BUILD',
  'WALK_FORWARD',
  'CALIBRATION',
  'FINAL_FIT',
  'FROZEN_TEST',
  'ARTIFACT_PUBLISH',
] as const;

export const TERMINAL_STATUSES = new Set<StockSelectionTrainingRunStatus>([
  StockSelectionTrainingRunStatus.Succeeded,
  StockSelectionTrainingRunStatus.Failed,
  StockSelectionTrainingRunStatus.Cancelled,
]);

export const DEFAULT_UNIVERSE: UniverseDraft = {
  kind: StockSelectionTrainingUniverseKind.OrdinaryAShare,
  indexCode: '',
  stockCodes: '',
  benchmarkCode: '000300.SH',
  minimumListingDays: 252,
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function isUniverseKind(
  value: unknown
): value is StockSelectionTrainingUniverseKind {
  return Object.values(StockSelectionTrainingUniverseKind).some(
    item => item === value
  );
}

function valueAt(value: Record<string, unknown>, key: string) {
  const camel = key.replace(/_([a-z])/g, (_, letter: string) =>
    letter.toUpperCase()
  );
  return value[key] ?? value[camel];
}

export function universeDraftFromSpec(value: unknown): UniverseDraft {
  if (!isRecord(value)) return { ...DEFAULT_UNIVERSE };

  const rawKind = valueAt(value, 'kind');
  const kind = isUniverseKind(rawKind) ? rawKind : DEFAULT_UNIVERSE.kind;
  const rawCodes = valueAt(value, 'stock_codes');
  const stockCodes = Array.isArray(rawCodes)
    ? rawCodes
        .filter((item): item is string => typeof item === 'string')
        .join('\n')
    : '';
  const rawMinimum = valueAt(value, 'minimum_listing_days');
  const minimumListingDays =
    typeof rawMinimum === 'number' && Number.isFinite(rawMinimum)
      ? Math.max(0, Math.trunc(rawMinimum))
      : DEFAULT_UNIVERSE.minimumListingDays;
  const rawIndex = valueAt(value, 'index_code');
  const rawBenchmark = valueAt(value, 'benchmark_code');

  return {
    kind,
    indexCode: typeof rawIndex === 'string' ? rawIndex : '',
    stockCodes,
    benchmarkCode:
      typeof rawBenchmark === 'string'
        ? rawBenchmark
        : DEFAULT_UNIVERSE.benchmarkCode,
    minimumListingDays,
  };
}

export function parseStockCodes(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(/[\s,;]+/)
        .map(item => item.trim().toUpperCase())
        .filter(Boolean)
    )
  );
}

export function toUniverseInput(draft: UniverseDraft) {
  return {
    kind: draft.kind,
    indexCode:
      draft.kind === StockSelectionTrainingUniverseKind.CertifiedIndex
        ? draft.indexCode.trim().toUpperCase() || null
        : null,
    stockCodes:
      draft.kind === StockSelectionTrainingUniverseKind.Explicit
        ? parseStockCodes(draft.stockCodes)
        : null,
    benchmarkCode: draft.benchmarkCode.trim().toUpperCase(),
    minimumListingDays: Number.isFinite(draft.minimumListingDays)
      ? Math.trunc(draft.minimumListingDays)
      : 0,
  } satisfies NonNullable<StockSelectionTrainingInput['universe']>;
}

export function toTrainingInput(
  draft: WizardInputDraft
): StockSelectionTrainingInput {
  return {
    datasetVersion: draft.datasetVersion,
    dateStart: draft.dateStart || null,
    dateEnd: draft.dateEnd || null,
    universe: toUniverseInput(draft.universe),
    requestedBackend: draft.backend,
    bootstrapSamples: draft.bootstrapSamples,
    workerBatchSize: draft.workerBatchSize,
    randomSeed: draft.randomSeed,
    note: draft.note,
  };
}

/** Stable, key-order-independent serialization for the complete typed input. */
export function stableSerialize(value: unknown): string {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'number')
    return Number.isFinite(value) ? String(value) : 'null';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableSerialize).join(',')}]`;
  if (!isRecord(value)) return JSON.stringify(String(value));

  return `{${Object.keys(value)
    .sort()
    .map(key => `${JSON.stringify(key)}:${stableSerialize(value[key])}`)
    .join(',')}}`;
}

export function trainingInputSignature(input: StockSelectionTrainingInput) {
  return stableSerialize(input);
}

export function pretty(value: unknown): string {
  if (value === null || value === undefined) return '不可用';
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '不可用';
  }
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value || '不可用';
  try {
    const serialized = JSON.stringify(value, null, 2);
    return serialized && serialized !== '{}' && serialized !== '[]'
      ? serialized
      : '不可用';
  } catch {
    return '不可用';
  }
}

export function evidenceValue(root: unknown, ...keys: string[]): unknown {
  let value = root;
  for (const key of keys) {
    if (!isRecord(value)) return null;
    value = value[key];
  }
  return value;
}

export function firstPresent(...values: unknown[]): unknown {
  return values.find(value => value !== null && value !== undefined) ?? null;
}

export function numericValue(...values: unknown[]): number | null {
  const value = firstPresent(...values);
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

export function ratioText(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '不可用';
  return `${(value * 100).toFixed(2)}%`;
}

export function rangeText(start: unknown, end: unknown): string {
  return typeof start === 'string' && typeof end === 'string' && start && end
    ? `${start} → ${end}`
    : '不可用';
}

export function shortHash(value: string | null | undefined): string {
  if (!value) return '不可用';
  return value.length > 24 ? `${value.slice(0, 12)}…${value.slice(-8)}` : value;
}

function isValidDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
}

export function validateTrainingInput(
  input: StockSelectionTrainingInput,
  dataset: StockSelectionDatasetVersion | null
): string[] {
  const errors: string[] = [];
  if (!dataset || dataset.status.toUpperCase() !== 'CERTIFIED') {
    errors.push('请选择已认证（CERTIFIED）的数据集。');
  }

  const dateStart = input.dateStart ?? null;
  const dateEnd = input.dateEnd ?? null;
  if ((dateStart && !dateEnd) || (!dateStart && dateEnd)) {
    errors.push('起始日期和结束日期需要同时填写。');
  } else if (dateStart && dateEnd) {
    if (!isValidDate(dateStart) || !isValidDate(dateEnd)) {
      errors.push('日期格式无效。');
    } else if (dateStart > dateEnd) {
      errors.push('起始日期不能晚于结束日期。');
    }
    if (
      dataset &&
      (dateStart < dataset.dateStart || dateEnd > dataset.dateEnd)
    ) {
      errors.push(
        `日期必须位于数据集边界 ${dataset.dateStart} → ${dataset.dateEnd} 内。`
      );
    }
  }

  const universe = input.universe;
  if (universe?.kind === StockSelectionTrainingUniverseKind.CertifiedIndex) {
    if (!universe.indexCode?.trim())
      errors.push('认证指数范围必须填写 indexCode。');
  }
  if (universe?.kind === StockSelectionTrainingUniverseKind.Explicit) {
    if (!universe.stockCodes || universe.stockCodes.length === 0) {
      errors.push('显式股票范围至少需要一个股票代码。');
    }
  }
  if (!universe?.benchmarkCode?.trim()) errors.push('benchmark 不能为空。');
  if (
    universe?.minimumListingDays === undefined ||
    !Number.isFinite(universe.minimumListingDays) ||
    !Number.isInteger(universe.minimumListingDays) ||
    universe.minimumListingDays < 0
  ) {
    errors.push('minimumListingDays 必须是大于等于 0 的整数。');
  }
  if (
    input.bootstrapSamples === undefined ||
    !Number.isFinite(input.bootstrapSamples) ||
    !Number.isInteger(input.bootstrapSamples) ||
    input.bootstrapSamples < 100 ||
    input.bootstrapSamples > 20000
  ) {
    errors.push('bootstrap 必须是 100–20,000 之间的整数。');
  }
  if (
    input.workerBatchSize === undefined ||
    !Number.isFinite(input.workerBatchSize) ||
    !Number.isInteger(input.workerBatchSize) ||
    input.workerBatchSize < 1 ||
    input.workerBatchSize > 1000
  ) {
    errors.push('batch 必须是 1–1,000 之间的整数。');
  }
  if (
    input.randomSeed === undefined ||
    !Number.isFinite(input.randomSeed) ||
    !Number.isInteger(input.randomSeed)
  ) {
    errors.push('seed 必须是有限整数。');
  }
  if ((input.note ?? '').length > 500) errors.push('备注不能超过 500 个字符。');
  return errors;
}

export function universeLabel(input: StockSelectionTrainingInput | null) {
  const universe = input?.universe;
  if (!universe) return '不可用';
  if (universe.kind === StockSelectionTrainingUniverseKind.CertifiedIndex) {
    return `经认证指数成分 · ${universe.indexCode || '不可用'}`;
  }
  if (universe.kind === StockSelectionTrainingUniverseKind.Explicit) {
    return `显式股票列表 · ${universe.stockCodes?.length ?? 0} 个代码`;
  }
  return '普通沪深 A 股';
}

export function runStatusClass(status: StockSelectionTrainingRunStatus) {
  if (status === StockSelectionTrainingRunStatus.Succeeded)
    return 'text-emerald-300';
  if (status === StockSelectionTrainingRunStatus.Failed) return 'text-rose-300';
  if (status === StockSelectionTrainingRunStatus.Cancelled)
    return 'text-slate-500';
  return 'text-blue-200';
}

export function phaseLabel(phase: string) {
  const labels: Record<string, string> = {
    PREFLIGHT: '预检',
    DATASET_BUILD: '数据集构建',
    WALK_FORWARD: 'Walk-forward',
    CALIBRATION: '校准',
    FINAL_FIT: '最终拟合',
    FROZEN_TEST: '冻结测试',
    ARTIFACT_PUBLISH: '产物发布',
  };
  return labels[phase] ?? phase;
}

export function isSha256(value: string | null | undefined) {
  return Boolean(value && /^[0-9a-f]{64}$/i.test(value));
}
