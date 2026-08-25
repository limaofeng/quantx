import { CalendarDays, CheckCircle2, CircleDashed, Scale } from 'lucide-react';
import { useMemo, useState } from 'react';

import { NativeSelect } from '@/components/ui/native-select';

import { isConfirmedComparison, type ComparisonStatistic } from '../model';

import { ResearchEmptyState, ResearchPanel } from './ResearchSurface';

type Benchmark = ComparisonStatistic['benchmark'];
type ReturnKind = ComparisonStatistic['return_kind'];
type EffectDirection = 'flat' | 'negative' | 'positive';

const BENCHMARK_ORDER: Benchmark[] = [
  'csi300',
  'market_equal_weight',
  'absolute',
];
const BENCHMARK_LABELS: Record<Benchmark, string> = {
  absolute: '绝对收益',
  csi300: '相对沪深300',
  market_equal_weight: '相对等权市场',
};
const RETURN_KIND_LABELS: Record<ReturnKind, string> = {
  close_response: '收盘响应',
  next_open: '次日开盘执行',
};
const POSITION_ORDER = ['low', 'mid', 'high', 'high_minus_low'] as const;
const POSITION_LABELS: Record<(typeof POSITION_ORDER)[number], string> = {
  high: '高位',
  high_minus_low: '高位 − 低位',
  low: '低位',
  mid: '中位',
};
const DIRECTION_LABELS: Record<EffectDirection, string> = {
  flat: '接近零',
  negative: '负向',
  positive: '正向',
};

function percent(value: number | null, signed = false) {
  if (value === null) return '—';
  const amount = value * 100;
  const prefix = signed && amount > 0 ? '+' : '';
  return `${prefix}${amount.toFixed(2)}%`;
}

function probability(value: number | null) {
  if (value === null) return '未估计';
  if (value < 0.001) return '<0.001';
  return value.toFixed(3);
}

function unique<T>(values: T[]) {
  return Array.from(new Set(values));
}

function effectDirection(row: ComparisonStatistic | undefined) {
  if (!row || row.spread_mean === null) return null;
  if (row.spread_mean > 0) return 'positive' as const;
  if (row.spread_mean < 0) return 'negative' as const;
  return 'flat' as const;
}

function sensitivityLabel(key: string) {
  const cooldownDays = /^cooldown_(\d+)d$/.exec(key)?.[1];
  return cooldownDays ? `冷却期 ${cooldownDays} 日` : key;
}

function sensitivityOrder(left: string, right: string) {
  const leftDays = Number(/^cooldown_(\d+)d$/.exec(left)?.[1] ?? Infinity);
  const rightDays = Number(/^cooldown_(\d+)d$/.exec(right)?.[1] ?? Infinity);
  return leftDays - rightDays || left.localeCompare(right);
}

function findInteraction(
  rows: ComparisonStatistic[],
  benchmark: Benchmark | undefined,
  returnKind: ReturnKind | undefined,
  horizon: number | undefined
) {
  return rows.find(
    row =>
      row.benchmark === benchmark &&
      row.return_kind === returnKind &&
      row.horizon === horizon &&
      (row.dimensions.comparison === 'high_minus_low' ||
        row.dimensions.price_position_bin === 'high_minus_low')
  );
}

function selectPreferredComparisonContext(rows: ComparisonStatistic[]) {
  const benchmark = BENCHMARK_ORDER.find(candidate =>
    rows.some(row => row.benchmark === candidate)
  );
  if (!benchmark) return null;
  const benchmarkRows = rows.filter(row => row.benchmark === benchmark);
  const returnKind: ReturnKind = benchmarkRows.some(
    row => row.return_kind === 'close_response'
  )
    ? 'close_response'
    : 'next_open';
  const contextRows = benchmarkRows.filter(
    row => row.return_kind === returnKind
  );
  const horizon = contextRows.some(row => row.horizon === 5)
    ? 5
    : Math.min(...contextRows.map(row => row.horizon));
  return { benchmark, horizon, returnKind };
}

function SensitivitySummary({
  baseline,
  benchmark,
  horizon,
  returnKind,
  sensitivity,
}: {
  baseline: ComparisonStatistic | undefined;
  benchmark: Benchmark | undefined;
  horizon: number | undefined;
  returnKind: ReturnKind | undefined;
  sensitivity: Record<string, ComparisonStatistic[]>;
}) {
  const baselineDirection = effectDirection(baseline);
  const baselineConfirmed = baseline
    ? isConfirmedComparison(baseline)
    : undefined;
  const scenarios = Object.entries(sensitivity)
    .sort(([left], [right]) => sensitivityOrder(left, right))
    .map(([key, rows]) => {
      const row = findInteraction(rows, benchmark, returnKind, horizon);
      const direction = effectDirection(row);
      const confirmed = row ? isConfirmedComparison(row) : undefined;
      return {
        confirmed,
        direction,
        directionConsistent:
          baselineDirection !== null &&
          direction !== null &&
          baselineDirection === direction,
        key,
        label: sensitivityLabel(key),
        row,
        significanceConsistent:
          baselineConfirmed !== undefined &&
          confirmed !== undefined &&
          baselineConfirmed === confirmed,
      };
    });

  if (scenarios.length === 0) return null;

  return (
    <section
      className="mx-3 mb-3 rounded-md border border-white/[0.06] bg-black/10 p-3"
      aria-labelledby="comparison-sensitivity-title"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h3
            id="comparison-sensitivity-title"
            className="text-ui-caption font-black text-slate-300"
          >
            冷却期敏感性摘要
          </h3>
          <p className="mt-1 text-ui-micro text-slate-600">
            只核对方向与 FDR 确认状态，不展开敏感性原始长表。
          </p>
        </div>
        <div className="flex items-center gap-1.5 text-ui-micro text-slate-500">
          <span>主比较</span>
          <span className="rounded border border-white/10 px-1.5 py-0.5 font-bold text-slate-300">
            {baselineDirection
              ? DIRECTION_LABELS[baselineDirection]
              : '方向不可用'}
          </span>
          <span className="rounded border border-white/10 px-1.5 py-0.5 font-bold text-slate-300">
            {baselineConfirmed === undefined
              ? '结论不可用'
              : baselineConfirmed
                ? '已确认'
                : '未确认'}
          </span>
        </div>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        {scenarios.map(scenario => (
          <article
            key={scenario.key}
            className="flex flex-wrap items-center justify-between gap-2 rounded border border-white/[0.05] bg-white/[0.02] px-2.5 py-2"
          >
            <div>
              <div className="text-ui-caption font-bold text-slate-300">
                {scenario.label}
              </div>
              {scenario.row && scenario.direction ? (
                <div className="mt-0.5 text-ui-micro text-slate-600">
                  敏感性结果：{DIRECTION_LABELS[scenario.direction]} ·{' '}
                  {scenario.confirmed ? '已确认' : '未确认'}
                </div>
              ) : (
                <div className="mt-0.5 text-ui-micro text-slate-600">
                  当前口径无对应结果
                </div>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5 text-ui-micro font-bold">
              <span
                className={`rounded border px-1.5 py-0.5 ${
                  scenario.row &&
                  scenario.direction !== null &&
                  baselineDirection !== null
                    ? scenario.directionConsistent
                      ? 'border-emerald-500/20 text-emerald-300'
                      : 'border-rose-500/20 text-rose-300'
                    : 'border-slate-500/20 text-slate-500'
                }`}
              >
                {scenario.row &&
                scenario.direction !== null &&
                baselineDirection !== null
                  ? scenario.directionConsistent
                    ? '方向一致'
                    : '方向不一致'
                  : '方向无法比较'}
              </span>
              <span
                className={`rounded border px-1.5 py-0.5 ${
                  scenario.row && baselineConfirmed !== undefined
                    ? scenario.significanceConsistent
                      ? 'border-emerald-500/20 text-emerald-300'
                      : 'border-rose-500/20 text-rose-300'
                    : 'border-slate-500/20 text-slate-500'
                }`}
              >
                {scenario.row && baselineConfirmed !== undefined
                  ? scenario.significanceConsistent
                    ? '显著性一致'
                    : '显著性不一致'
                  : '显著性无法比较'}
              </span>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function ComparisonCard({ row }: { row: ComparisonStatistic }) {
  const position = row.dimensions.price_position_bin;
  const isInteraction =
    row.dimensions.comparison === 'high_minus_low' ||
    position === 'high_minus_low';
  const label =
    POSITION_LABELS[position as keyof typeof POSITION_LABELS] || position;
  const confirmed = isConfirmedComparison(row);
  const positive = (row.spread_mean || 0) > 0;
  const Icon = confirmed ? CheckCircle2 : CircleDashed;
  const evidenceLabel = confirmed
    ? '已确认'
    : row.q_value === null
      ? '证据不足'
      : row.significant === false
        ? '未通过 FDR'
        : '未完整确认';

  return (
    <article
      className={`rounded-md border p-3 ${
        confirmed
          ? positive
            ? 'border-emerald-500/25 bg-emerald-500/[0.06]'
            : 'border-rose-500/25 bg-rose-500/[0.06]'
          : 'border-white/[0.07] bg-white/[0.02]'
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <h3 className="text-ui-caption font-black text-slate-200">
            {label || '未分组'}
          </h3>
          <p className="mt-0.5 text-ui-micro text-slate-600">
            {isInteraction ? '位置交互（差上差）' : '异常量减正常量'}
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-ui-micro font-bold ${
            confirmed
              ? positive
                ? 'border-emerald-500/30 text-emerald-300'
                : 'border-rose-500/30 text-rose-300'
              : 'border-slate-500/20 text-slate-500'
          }`}
        >
          <Icon className="h-3 w-3" />
          {evidenceLabel}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-1">
        <div className="rounded bg-black/10 p-2">
          <div className="text-ui-micro font-bold text-slate-600">
            {isInteraction ? '放量组位置差' : '异常放量'}
          </div>
          <div className="mt-1 font-mono text-ui-label font-bold tabular-nums text-slate-300">
            {percent(row.shock_mean)}
          </div>
        </div>
        <div className="rounded bg-black/10 p-2">
          <div className="text-ui-micro font-bold text-slate-600">
            {isInteraction ? '正常组位置差' : '正常成交量'}
          </div>
          <div className="mt-1 font-mono text-ui-label font-bold tabular-nums text-slate-300">
            {percent(row.normal_mean)}
          </div>
        </div>
        <div className="rounded bg-black/10 p-2">
          <div className="text-ui-micro font-bold text-slate-600">
            {isInteraction ? '差上差' : '均值差'}
          </div>
          <div
            className={`mt-1 font-mono text-ui-label font-black tabular-nums ${
              confirmed
                ? positive
                  ? 'text-emerald-300'
                  : 'text-rose-300'
                : 'text-slate-400'
            }`}
          >
            {percent(row.spread_mean, true)}
          </div>
        </div>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-1.5 text-ui-micro">
        <div className="flex justify-between gap-2">
          <dt className="text-slate-600">FDR q</dt>
          <dd className="font-mono text-slate-400">
            {probability(row.q_value)}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-slate-600">有效交易日</dt>
          <dd className="font-mono text-slate-400">{row.unique_dates}</dd>
        </div>
        <div className="col-span-2 flex justify-between gap-2">
          <dt className="text-slate-600">置信区间</dt>
          <dd className="font-mono text-slate-400">
            [{percent(row.ci_low)}, {percent(row.ci_high)}]
          </dd>
        </div>
        <div className="col-span-2 flex justify-between gap-2">
          <dt className="text-slate-600">异常量 / 正常量样本</dt>
          <dd className="font-mono text-slate-400">
            {row.shock_sample_size.toLocaleString()} /{' '}
            {row.normal_sample_size.toLocaleString()}
          </dd>
        </div>
      </dl>
    </article>
  );
}

export function VolumeComparisonPanel({
  rows,
  sensitivity = {},
}: {
  rows: ComparisonStatistic[];
  sensitivity?: Record<string, ComparisonStatistic[]>;
}) {
  const preferred = selectPreferredComparisonContext(rows);
  const [requestedBenchmark, setRequestedBenchmark] =
    useState<Benchmark | null>(null);
  const [requestedReturnKind, setRequestedReturnKind] =
    useState<ReturnKind | null>(null);
  const [requestedHorizon, setRequestedHorizon] = useState<number | null>(null);

  const benchmarks = BENCHMARK_ORDER.filter(candidate =>
    rows.some(row => row.benchmark === candidate)
  );
  const benchmark =
    requestedBenchmark && benchmarks.includes(requestedBenchmark)
      ? requestedBenchmark
      : preferred?.benchmark;
  const returnKinds = benchmark
    ? unique(
        rows
          .filter(row => row.benchmark === benchmark)
          .map(row => row.return_kind)
      )
    : [];
  const returnKind =
    requestedReturnKind && returnKinds.includes(requestedReturnKind)
      ? requestedReturnKind
      : preferred?.returnKind;
  const horizons =
    benchmark && returnKind
      ? unique(
          rows
            .filter(
              row =>
                row.benchmark === benchmark && row.return_kind === returnKind
            )
            .map(row => row.horizon)
        ).sort((left, right) => left - right)
      : [];
  const horizon =
    requestedHorizon !== null && horizons.includes(requestedHorizon)
      ? requestedHorizon
      : horizons.includes(5)
        ? 5
        : horizons[0];
  const selectedRows = useMemo(
    () =>
      rows.filter(
        row =>
          row.benchmark === benchmark &&
          row.return_kind === returnKind &&
          row.horizon === horizon
      ),
    [benchmark, horizon, returnKind, rows]
  );
  const cards = POSITION_ORDER.map(position =>
    selectedRows.find(row => {
      const comparison = row.dimensions.comparison;
      const rowPosition = row.dimensions.price_position_bin;
      return position === 'high_minus_low'
        ? comparison === 'high_minus_low' || rowPosition === 'high_minus_low'
        : comparison === 'shock_minus_normal' && rowPosition === position;
    })
  ).filter((row): row is ComparisonStatistic => Boolean(row));
  const interaction = findInteraction(
    selectedRows,
    benchmark,
    returnKind,
    horizon
  );
  const confirmedInteraction =
    interaction && isConfirmedComparison(interaction) ? interaction : null;

  return (
    <ResearchPanel
      title="异常放量 vs 正常成交量"
      description="先在事件日期内做截面等权，再比较异常量与正常量；显著性以 FDR q 值和置信区间共同确认。"
    >
      {rows.length === 0 || !preferred ? (
        <ResearchEmptyState
          title="暂无正常成交量对照"
          description="该运行未生成 comparison 数据，不能仅依据异常放量组自身收益形成结论。"
        />
      ) : (
        <>
          <div className="flex flex-wrap gap-2 border-b border-white/[0.05] px-3 py-2">
            <label className="flex items-center gap-2 text-ui-micro font-bold text-slate-600">
              基准
              <NativeSelect
                value={benchmark}
                onChange={event =>
                  setRequestedBenchmark(event.target.value as Benchmark)
                }
                className="h-7 cursor-pointer rounded border border-white/10 bg-[#0b1120] px-2 text-ui-caption text-slate-300 outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                {benchmarks.map(value => (
                  <option key={value} value={value}>
                    {BENCHMARK_LABELS[value]}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <label className="flex items-center gap-2 text-ui-micro font-bold text-slate-600">
              收益口径
              <NativeSelect
                value={returnKind}
                onChange={event =>
                  setRequestedReturnKind(event.target.value as ReturnKind)
                }
                className="h-7 cursor-pointer rounded border border-white/10 bg-[#0b1120] px-2 text-ui-caption text-slate-300 outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                {returnKinds.map(value => (
                  <option key={value} value={value}>
                    {RETURN_KIND_LABELS[value]}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <label className="flex items-center gap-2 text-ui-micro font-bold text-slate-600">
              持有期
              <NativeSelect
                value={horizon}
                onChange={event =>
                  setRequestedHorizon(Number(event.target.value))
                }
                className="h-7 cursor-pointer rounded border border-white/10 bg-[#0b1120] px-2 font-mono text-ui-caption text-slate-300 outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              >
                {horizons.map(value => (
                  <option key={value} value={value}>
                    T+{value}
                  </option>
                ))}
              </NativeSelect>
            </label>
          </div>

          <div
            className={`m-3 flex items-start gap-3 rounded-md border p-3 ${
              confirmedInteraction
                ? (confirmedInteraction.spread_mean || 0) > 0
                  ? 'border-emerald-500/25 bg-emerald-500/[0.07]'
                  : 'border-rose-500/25 bg-rose-500/[0.07]'
                : 'border-amber-500/20 bg-amber-500/[0.06]'
            }`}
            role="status"
          >
            {confirmedInteraction ? (
              <CheckCircle2
                className={`mt-0.5 h-4 w-4 shrink-0 ${
                  (confirmedInteraction.spread_mean || 0) > 0
                    ? 'text-emerald-300'
                    : 'text-rose-300'
                }`}
              />
            ) : (
              <Scale className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
            )}
            <div>
              <div className="text-ui-caption font-black text-slate-100">
                {confirmedInteraction
                  ? '价格位置交互已确认'
                  : '价格位置交互未形成有效结论'}
              </div>
              <p className="mt-1 text-ui-caption leading-5 text-slate-400">
                {confirmedInteraction ? (
                  <>
                    高位相对低位的放量效应差上差为{' '}
                    {percent(confirmedInteraction.spread_mean, true)}，FDR q=
                    {probability(confirmedInteraction.q_value)}，置信区间 [
                    {percent(confirmedInteraction.ci_low)},{' '}
                    {percent(confirmedInteraction.ci_high)}]，覆盖{' '}
                    {confirmedInteraction.unique_dates} 个有效交易日。
                  </>
                ) : interaction ? (
                  <>
                    当前差上差为 {percent(interaction.spread_mean, true)}，FDR
                    q={probability(interaction.q_value)}，置信区间 [
                    {percent(interaction.ci_low)},{' '}
                    {percent(interaction.ci_high)}
                    ]，覆盖 {interaction.unique_dates}{' '}
                    个有效交易日；未通过完整确认条件，不作为有效因子结论。
                  </>
                ) : (
                  '当前口径缺少 high-minus-low 差上差，无法判断价格位置是否改变放量效应。'
                )}
              </p>
            </div>
          </div>

          <SensitivitySummary
            baseline={interaction}
            benchmark={benchmark}
            horizon={horizon}
            returnKind={returnKind}
            sensitivity={sensitivity}
          />

          {cards.length === 0 ? (
            <ResearchEmptyState
              title="当前口径没有可比较分组"
              description="请选择其他基准、收益口径或持有期。"
            />
          ) : (
            <div className="grid gap-2 p-3 pt-0 sm:grid-cols-2 xl:grid-cols-4">
              {cards.map(row => (
                <ComparisonCard
                  key={`${row.dimensions.comparison}:${row.dimensions.price_position_bin}`}
                  row={row}
                />
              ))}
            </div>
          )}

          <div className="flex items-center gap-2 border-t border-white/[0.05] px-3 py-2 text-ui-micro leading-4 text-slate-600">
            <CalendarDays className="h-3 w-3 shrink-0" />
            “已确认”要求 significant=true、FDR q 可用且置信区间不跨
            0；其他结果仅作描述展示。
          </div>
        </>
      )}
    </ResearchPanel>
  );
}
