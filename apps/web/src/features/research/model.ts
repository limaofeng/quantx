import { z } from 'zod';

const nullableFiniteNumber = z.number().finite().nullable();

const eventCurvePointSchema = z.object({
  benchmark: z.enum(['absolute', 'csi300', 'market_equal_weight']),
  ci_high: nullableFiniteNumber,
  ci_low: nullableFiniteNumber,
  horizon: z.number().int().positive(),
  mean: nullableFiniteNumber,
  median: nullableFiniteNumber,
  positive_rate: nullableFiniteNumber,
  return_kind: z.enum(['close_response', 'next_open']),
  sample_size: z.number().int().nonnegative(),
  unique_dates: z.number().int().nonnegative().optional(),
});

const groupStatisticSchema = z.object({
  benchmark: z.enum(['absolute', 'csi300', 'market_equal_weight']),
  ci_high: nullableFiniteNumber.optional(),
  ci_low: nullableFiniteNumber.optional(),
  dimensions: z.record(z.string()),
  horizon: z.number().int().positive(),
  mean: nullableFiniteNumber,
  p_value: nullableFiniteNumber.optional(),
  positive_rate: nullableFiniteNumber.optional(),
  q_value: nullableFiniteNumber.optional(),
  return_kind: z.enum(['close_response', 'next_open']),
  sample_size: z.number().int().nonnegative(),
  significant: z.boolean().nullable().optional(),
  unique_dates: z.number().int().nonnegative().optional(),
});

const comparisonStatisticSchema = z.object({
  benchmark: z.enum(['absolute', 'csi300', 'market_equal_weight']),
  ci_high: nullableFiniteNumber,
  ci_low: nullableFiniteNumber,
  dimensions: z.record(z.string()),
  horizon: z.number().int().positive(),
  normal_mean: nullableFiniteNumber,
  normal_median: nullableFiniteNumber,
  normal_sample_size: z.number().int().nonnegative(),
  p_value: nullableFiniteNumber,
  q_value: nullableFiniteNumber,
  return_kind: z.enum(['close_response', 'next_open']),
  shock_mean: nullableFiniteNumber,
  shock_median: nullableFiniteNumber,
  shock_sample_size: z.number().int().nonnegative(),
  significant: z.boolean().nullable(),
  spread_mean: nullableFiniteNumber,
  spread_median: nullableFiniteNumber,
  unique_dates: z.number().int().nonnegative(),
});

const regressionCoefficientSchema = z.object({
  ci_high: nullableFiniteNumber,
  ci_low: nullableFiniteNumber,
  estimate: nullableFiniteNumber,
  p_value: nullableFiniteNumber,
  q_value: nullableFiniteNumber.optional(),
  significant: z.boolean().nullable().optional(),
  std_error: nullableFiniteNumber,
  t_stat: nullableFiniteNumber,
  term: z.string(),
});

const regressionSchema = z.object({
  coefficients: z.array(regressionCoefficientSchema),
  covariance: z.string(),
  dependent_variable: z.string(),
  horizon: z.number().int().positive(),
  nobs: z.number().int().nonnegative(),
  r_squared: nullableFiniteNumber,
  return_kind: z.enum(['close_response', 'next_open']),
  warnings: z.array(z.string()),
});

const coverageRowSchema = z.object({
  adjustment_valid: z.boolean().optional(),
  duplicate_rows: z.number().int().nonnegative().optional(),
  first_time: z.string().nullable().optional(),
  first_valid_time: z.string().nullable().optional(),
  has_end_coverage: z.boolean().optional(),
  has_instrument_metadata: z.boolean().optional(),
  has_minimum_observations: z.boolean().optional(),
  has_start_coverage: z.boolean().optional(),
  invalid_ohlc_rows: z.number().int().nonnegative().optional(),
  last_time: z.string().nullable().optional(),
  last_valid_time: z.string().nullable().optional(),
  missing_price_rows: z.number().int().nonnegative().optional(),
  negative_volume_rows: z.number().int().nonnegative().optional(),
  rows: z.number().int().nonnegative().optional(),
  stock_code: z.string().optional(),
  suspended_rows: z.number().int().nonnegative().optional(),
  valid_rows: z.number().int().nonnegative().optional(),
  zero_volume_rows: z.number().int().nonnegative().optional(),
});

const sourceCampaignSchema = z.object({
  end_date: z.string().optional(),
  job_plan_sha256: z.string().optional(),
  run_key: z.string().optional(),
  start_date: z.string().optional(),
  universe_sha256: z.string().optional(),
});

const sourcePreprocessingSchema = z.object({
  compatibility: z.string().optional(),
  price_decimals: z.number().int().nonnegative().optional(),
  timezone: z.string().optional(),
  volume_amount_decimals: z.number().int().nonnegative().optional(),
});

const sourceQuerySchema = z.object({
  available_end: z.string().optional(),
  available_start: z.string().optional(),
  boundary_truncated: z.boolean().optional(),
  emitted_rows: z.number().int().nonnegative().optional(),
  requested_code_count: z.number().int().nonnegative().optional(),
  requested_codes_sha256: z.string().optional(),
  requested_end: z.string().optional(),
  requested_start: z.string().optional(),
  selected_request_count: z.number().int().nonnegative().optional(),
});

const sourceProvenanceSchema = z.object({
  archive_format: z.string().optional(),
  boundary_tolerance_days: z.number().int().nonnegative().optional(),
  campaign: sourceCampaignSchema.optional(),
  emitted_rows: z.number().int().nonnegative().optional(),
  kind: z.string().optional(),
  ledger_sha256: z.string().optional(),
  metadata_universe_validated: z.boolean().optional(),
  preprocessing: sourcePreprocessingSchema.optional(),
  queries: z.array(sourceQuerySchema).optional(),
  required_request_count: z.number().int().nonnegative().optional(),
  schema_version: z.number().int().nonnegative().optional(),
  selected_chunk_count: z.number().int().nonnegative().optional(),
  selected_chunk_manifest_sha256: z.string().optional(),
  selected_request_count: z.number().int().nonnegative().optional(),
  selected_source_record_count: z.number().int().nonnegative().optional(),
});

const dataQualitySchema = z
  .object({
    coverage: z.array(coverageRowSchema).optional(),
    duplicate_rows: z.number().int().nonnegative().optional(),
    insufficient_history_codes: z.array(z.string()).optional(),
    invalid_adjustment_codes: z.array(z.string()).optional(),
    invalid_ohlc_rows: z.number().int().nonnegative().optional(),
    is_usable: z.boolean().optional(),
    loaded_codes: z.array(z.string()).optional(),
    missing_codes: z.array(z.string()).optional(),
    missing_metadata_codes: z.array(z.string()).optional(),
    missing_price_rows: z.number().int().nonnegative().optional(),
    negative_volume_rows: z.number().int().nonnegative().optional(),
    requested_codes: z.array(z.string()).optional(),
    requested_end: z.string().optional(),
    requested_start: z.string().optional(),
    row_count: z.number().int().nonnegative().optional(),
    source_provenance: sourceProvenanceSchema.optional(),
    suspended_rows: z.number().int().nonnegative().optional(),
    valid_row_count: z.number().int().nonnegative().optional(),
    warnings: z.array(z.string()).optional(),
    zero_volume_rows: z.number().int().nonnegative().optional(),
  })
  .passthrough();

export type EventCurvePoint = z.infer<typeof eventCurvePointSchema>;
export type GroupStatistic = z.infer<typeof groupStatisticSchema>;
export type ComparisonStatistic = z.infer<typeof comparisonStatisticSchema>;
export type RegressionCoefficient = z.infer<typeof regressionCoefficientSchema>;
export type RegressionResult = z.infer<typeof regressionSchema>;
export type ResearchDataQuality = z.infer<typeof dataQualitySchema>;
export type ResearchSourceProvenance = z.infer<typeof sourceProvenanceSchema>;

export interface ResearchResultData {
  analysisSampleCount: number | null;
  comparison: ComparisonStatistic[];
  comparisonSensitivity: Record<string, ComparisonStatistic[]>;
  dataQuality: ResearchDataQuality | null;
  eventCurve: EventCurvePoint[];
  heatmap: GroupStatistic[];
  regressions: RegressionResult[];
  robustness: Record<string, GroupStatistic[]>;
  validationErrors: string[];
}

function parseSection<T>(
  name: string,
  schema: z.ZodType<T>,
  value: unknown,
  fallback: T,
  errors: string[]
) {
  if (value === null || value === undefined) return fallback;
  const parsed = schema.safeParse(value);
  if (parsed.success) return parsed.data;
  errors.push(`${name} 结构不符合当前展示契约`);
  return fallback;
}

export function parseResearchResult(input: {
  analysisSampleCount: unknown;
  comparison: unknown;
  comparisonSensitivity: unknown;
  dataQuality: unknown;
  eventCurve: unknown;
  interactionHeatmap: unknown;
  regressions: unknown;
  robustness: unknown;
}): ResearchResultData {
  const validationErrors: string[] = [];
  return {
    analysisSampleCount: parseSection(
      '分析样本数',
      z.number().int().nonnegative().nullable(),
      input.analysisSampleCount,
      null,
      validationErrors
    ),
    comparison: parseSection(
      '正常成交量对照',
      z.array(comparisonStatisticSchema),
      input.comparison,
      [],
      validationErrors
    ),
    comparisonSensitivity: parseSection(
      '正常成交量对照敏感性',
      z.record(z.array(comparisonStatisticSchema)),
      input.comparisonSensitivity,
      {},
      validationErrors
    ),
    dataQuality: parseSection(
      '数据质量',
      dataQualitySchema.nullable(),
      input.dataQuality,
      null,
      validationErrors
    ),
    eventCurve: parseSection(
      '事件曲线',
      z.array(eventCurvePointSchema),
      input.eventCurve,
      [],
      validationErrors
    ),
    heatmap: parseSection(
      '交互热力图',
      z.array(groupStatisticSchema),
      input.interactionHeatmap,
      [],
      validationErrors
    ),
    regressions: parseSection(
      '回归结果',
      z.array(regressionSchema),
      input.regressions,
      [],
      validationErrors
    ),
    robustness: parseSection(
      '稳健性检验',
      z.record(z.array(groupStatisticSchema)),
      input.robustness,
      {},
      validationErrors
    ),
    validationErrors,
  };
}

export function buildResearchRunPath(
  studyId: string,
  version: string,
  runId: string,
  key: string
) {
  const path = `/research/${encodeURIComponent(studyId)}/${encodeURIComponent(version)}/runs/${encodeURIComponent(runId)}`;
  return `${path}?key=${encodeURIComponent(key)}`;
}

export function readResearchRunKey(search: string) {
  return new URLSearchParams(search).get('key')?.trim() || '';
}

export function isSmallSample(version: string, eventCount?: number | null) {
  return version.toLowerCase().includes('smoke') || (eventCount ?? 0) < 100;
}

export function selectPreferredBenchmark(
  rows: Array<{ benchmark: EventCurvePoint['benchmark']; sample_size: number }>
) {
  const candidates: EventCurvePoint['benchmark'][] = [
    'csi300',
    'market_equal_weight',
    'absolute',
  ];
  return (
    candidates.find(candidate =>
      rows.some(row => row.benchmark === candidate && row.sample_size > 0)
    ) || 'absolute'
  );
}

export function isConfirmedComparison(row: ComparisonStatistic) {
  return (
    row.significant === true &&
    row.q_value !== null &&
    row.ci_low !== null &&
    row.ci_high !== null &&
    (row.ci_low > 0 || row.ci_high < 0)
  );
}

export function isConfirmedRegressionCoefficient(row: RegressionCoefficient) {
  return (
    row.significant === true &&
    row.q_value !== null &&
    row.q_value !== undefined &&
    row.ci_low !== null &&
    row.ci_high !== null &&
    (row.ci_low > 0 || row.ci_high < 0)
  );
}
