import {
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Cpu,
  GitCompareArrows,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
} from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import {
  CancelStockSelectionTrainingRunDocument,
  PreviewStockSelectionTrainingDocument,
  RegisterStockSelectionModelDocument,
  StartStockSelectionDevelopmentTrainingDocument,
  StartStockSelectionFinalEvaluationDocument,
  StockSelectionTrainingBackend,
  StockSelectionTrainingUniverseKind,
  type StockSelectionDatasetVersion,
  type StockSelectionTrainingRun,
  type StockSelectionTrainingUniverseInput,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import {
  useStockSelectionDatasetVersions,
  useStockSelectionTrainingCapabilities,
  useStockSelectionTrainingComparison,
  useStockSelectionTrainingRun,
  useStockSelectionTrainingRuns,
} from '../hooks';
import { createPendingIdempotencyKeys } from '../idempotency';

const WIZARD_STEPS = ['数据集', '时间', '模型 / 后端', '预览确认'];
const TERMINAL = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED']);

type UniverseDraft = {
  kind: StockSelectionTrainingUniverseKind;
  indexCode: string;
  stockCodes: string;
  benchmarkCode: string;
  minimumListingDays: number;
};

type TrainingPreviewEvidence = {
  datasetVersion: string;
  requestedBackend: string;
  resolvedBackend: string;
  canSubmit: boolean;
  folds: readonly unknown[];
  coverage: unknown;
  leakage: unknown;
  resourceEstimate: unknown;
  shadowReasons: readonly string[];
  blockers: readonly string[];
  warnings: readonly string[];
  previewFingerprint?: string;
};

const DEFAULT_UNIVERSE: UniverseDraft = {
  kind: StockSelectionTrainingUniverseKind.OrdinaryAShare,
  indexCode: '',
  stockCodes: '',
  benchmarkCode: '000300.SH',
  minimumListingDays: 252,
};

function isUniverseKind(value: unknown): value is StockSelectionTrainingUniverseKind {
  return Object.values(StockSelectionTrainingUniverseKind).some(item => item === value);
}

function universeDraftFromSpec(value: unknown): UniverseDraft {
  if (!isRecord(value)) return DEFAULT_UNIVERSE;
  const kind = isUniverseKind(value.kind) ? value.kind : DEFAULT_UNIVERSE.kind;
  const rawCodes = Array.isArray(value.stock_codes) ? value.stock_codes : [];
  const minimum = typeof value.minimum_listing_days === 'number' && Number.isFinite(value.minimum_listing_days)
    ? Math.max(0, Math.trunc(value.minimum_listing_days))
    : DEFAULT_UNIVERSE.minimumListingDays;
  return {
    kind,
    indexCode: typeof value.index_code === 'string' ? value.index_code : '',
    stockCodes: rawCodes.filter((item): item is string => typeof item === 'string').join('\n'),
    benchmarkCode: typeof value.benchmark_code === 'string' ? value.benchmark_code : DEFAULT_UNIVERSE.benchmarkCode,
    minimumListingDays: minimum,
  };
}

function parseStockCodes(value: string): string[] {
  return Array.from(new Set(value.split(/[\s,;]+/).map(item => item.trim().toUpperCase()).filter(Boolean)));
}

function toUniverseInput(draft: UniverseDraft): StockSelectionTrainingUniverseInput {
  return {
    kind: draft.kind,
    indexCode: draft.kind === StockSelectionTrainingUniverseKind.CertifiedIndex
      ? draft.indexCode.trim().toUpperCase() || null
      : null,
    stockCodes: draft.kind === StockSelectionTrainingUniverseKind.Explicit
      ? parseStockCodes(draft.stockCodes)
      : null,
    benchmarkCode: draft.benchmarkCode.trim().toUpperCase(),
    minimumListingDays: Number.isFinite(draft.minimumListingDays)
      ? Math.trunc(draft.minimumListingDays)
      : 0,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function pretty(value: unknown) {
  if (value == null) return '不可用';
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '不可用';
  }
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return '不可用';
  }
}

function evidenceValue(root: unknown, ...keys: string[]) {
  let value = root;
  for (const key of keys) {
    if (!isRecord(value)) return null;
    value = value[key];
  }
  return value;
}

function firstPresent(...values: unknown[]) {
  return values.find(value => value !== null && value !== undefined) ?? null;
}

function positiveMetric(...values: unknown[]) {
  const value = firstPresent(...values);
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function ratioText(value: unknown) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '不可用';
  return `${(value * 100).toFixed(2)}%`;
}

function rangeText(start: unknown, end: unknown) {
  return typeof start === 'string' && typeof end === 'string' && start && end
    ? `${start} → ${end}`
    : '不可用';
}

function evidenceText(value: unknown) {
  if (value == null) return '不可用';
  if (isRecord(value) && Object.keys(value).length === 0) return '不可用';
  if (Array.isArray(value) && value.length === 0) return '不可用';
  return pretty(value);
}

export function TrainingPreviewSummary({
  blockers,
  warnings,
  resolvedBackend,
  canSubmit,
  preview,
  dataset,
}: {
  blockers: readonly string[];
  warnings: readonly string[];
  resolvedBackend: string;
  canSubmit: boolean;
  preview?: TrainingPreviewEvidence | null;
  dataset?: StockSelectionDatasetVersion | null;
}) {
  const coverage = preview?.coverage;
  const quality = firstPresent(
    evidenceValue(coverage, 'quality'),
    evidenceValue(dataset?.qualitySummary, 'quality'),
    dataset?.qualitySummary,
  );
  const sampleCount = positiveMetric(
    evidenceValue(coverage, 'sample_count'),
    evidenceValue(coverage, 'sampleCount'),
    evidenceValue(quality, 'sample_count'),
    evidenceValue(quality, 'sampleCount'),
    dataset?.sampleCount,
  );
  const stockCount = positiveMetric(
    evidenceValue(coverage, 'stock_count'),
    evidenceValue(coverage, 'stockCount'),
    evidenceValue(quality, 'stock_count'),
    evidenceValue(quality, 'stockCount'),
    dataset?.stockCount,
  );
  const tradingDayCount = positiveMetric(
    evidenceValue(coverage, 'trading_day_count'),
    evidenceValue(coverage, 'tradingDayCount'),
    evidenceValue(quality, 'trading_day_count'),
    evidenceValue(quality, 'tradingDayCount'),
    dataset?.tradingDayCount,
  );
  const positiveRate = firstPresent(
    evidenceValue(coverage, 'positive_rate'),
    evidenceValue(coverage, 'positiveRate'),
    evidenceValue(quality, 'positive_rate'),
    evidenceValue(quality, 'positiveRate'),
  );
  const developmentStart = firstPresent(
    evidenceValue(coverage, 'development_start'),
    evidenceValue(coverage, 'developmentStart'),
    evidenceValue(coverage, 'requested_start'),
    evidenceValue(coverage, 'requestedStart'),
  );
  const developmentEnd = firstPresent(
    evidenceValue(coverage, 'development_end'),
    evidenceValue(coverage, 'developmentEnd'),
    evidenceValue(coverage, 'requested_end'),
    evidenceValue(coverage, 'requestedEnd'),
  );
  const frozenStart = firstPresent(
    evidenceValue(coverage, 'frozen_test_start'),
    evidenceValue(coverage, 'frozenTestStart'),
  );
  const frozenEnd = firstPresent(
    evidenceValue(coverage, 'frozen_test_end'),
    evidenceValue(coverage, 'frozenTestEnd'),
  );
  const resource = preview?.resourceEstimate;
  const evidenceCards: Array<[string, string]> = [
    ['数据集', `${preview?.datasetVersion || dataset?.datasetVersion || '不可用'} · manifest ${dataset?.manifestSha256 || '不可用'}`],
    ['开发区间', rangeText(developmentStart, developmentEnd)],
    ['冻结区间', rangeText(frozenStart, frozenEnd)],
    ['fold 数', preview ? String(preview.folds.length) : '不可用'],
    ['样本 / 股票 / 交易日', `${pretty(sampleCount)} / ${pretty(stockCount)} / ${pretty(tradingDayCount)}`],
    ['正样本比例', ratioText(positiveRate)],
    ['覆盖率', evidenceText(coverage)],
    ['泄漏检查', evidenceText(preview?.leakage)],
    ['内存 / 显存 / 磁盘', `${pretty(positiveMetric(evidenceValue(resource, 'memoryMib'), evidenceValue(resource, 'estimatedMemoryMib')))} / ${pretty(positiveMetric(evidenceValue(resource, 'gpuMemoryMib'), evidenceValue(resource, 'estimatedGpuMemoryMib')))} / ${pretty(positiveMetric(evidenceValue(resource, 'diskMib'), evidenceValue(resource, 'estimatedDiskMib')))}`],
    ['预计耗时', `${pretty(positiveMetric(evidenceValue(resource, 'estimatedMinutes')))} · ${pretty(evidenceValue(resource, 'durationLevel'))}`],
    ['请求 / 解析后端', `${preview?.requestedBackend || '不可用'} / ${resolvedBackend || preview?.resolvedBackend || '不可用'}`],
    ['Shadow 原因', evidenceText(preview?.shadowReasons)],
  ];
  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2 text-ui-caption">
        <span className="text-slate-500">解析后端</span>
        <span className="rounded border border-cyan-400/30 bg-cyan-400/10 px-2 py-1 font-mono font-bold text-cyan-200">
          {resolvedBackend || '不可用'} · 已锁定
        </span>
        {canSubmit && blockers.length === 0 ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-300" aria-label="预检通过" />
        ) : (
          <AlertTriangle className="h-4 w-4 text-amber-300" aria-label="存在预检阻塞" />
        )}
      </div>
      {blockers.length > 0 && (
        <div role="alert" className="rounded border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200">
          <div className="font-bold">提交已禁用</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {blockers.map(item => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="rounded border border-amber-400/20 bg-amber-400/5 p-2 text-ui-caption text-amber-200">
          {warnings.join(' · ')}
        </div>
      )}
      {preview && (
        <div data-testid="training-preview-evidence" className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {evidenceCards.map(([label, value]) => (
            <div key={label} className="min-w-0 rounded border border-white/[0.06] bg-white/[0.02] p-2 text-ui-micro">
              <div className="text-slate-600">{label}</div>
              <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words font-mono text-slate-300">{value}</pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RunStatus({ run }: { run: StockSelectionTrainingRun }) {
  const color = run.status === 'SUCCEEDED'
    ? 'text-emerald-300'
    : run.status === 'FAILED'
      ? 'text-rose-300'
      : run.status === 'CANCELLED'
        ? 'text-slate-500'
        : 'text-cyan-200';
  return (
    <span className={cn('inline-flex items-center gap-1 font-mono text-ui-micro font-bold', color)}>
      {run.status === 'RUNNING' || run.status === 'QUEUED' ? (
        <LoaderCircle className="h-3 w-3 animate-spin motion-reduce:animate-none" />
      ) : run.status === 'SUCCEEDED' ? (
        <CheckCircle2 className="h-3 w-3" />
      ) : (
        <CircleDot className="h-3 w-3" />
      )}
      {run.status}
    </span>
  );
}

function RunDetail({
  run,
  onRefresh,
  error,
  onFinal,
  onCancel,
  onRegister,
  busy,
}: {
  run: StockSelectionTrainingRun;
  onRefresh: () => void;
  error?: Error;
  onFinal: () => void;
  onCancel: () => void;
  onRegister: () => void;
  busy: boolean;
}) {
  const metrics = run.metricsSummary;
  const gates = run.gateSummary;
  const canFinal = run.runKind === 'DEVELOPMENT' && run.status === 'SUCCEEDED';
  const canCancel = !TERMINAL.has(run.status);
  return (
    <aside className="min-w-0 rounded-md border border-white/[0.08] bg-[#0a1525] p-3" aria-label="训练运行详情">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <RunStatus run={run} />
            <span className="font-mono text-ui-micro text-slate-500">{run.runKind}</span>
            <span className="font-mono text-ui-micro text-slate-600">v{run.stateVersion}</span>
          </div>
          <div className="mt-1 truncate font-mono text-ui-caption text-slate-300" title={run.runId}>
            {run.runId}
          </div>
        </div>
        <button type="button" aria-label="刷新运行详情" onClick={onRefresh} className="rounded p-1 text-slate-500 hover:bg-white/[0.06] hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>

      {error && (
        <div className="mt-2 flex items-center justify-between gap-2 rounded border border-rose-400/20 bg-rose-400/5 p-2 text-ui-micro text-rose-300" role="alert">
          <span>详情读取失败：{error.message}</span>
          <button type="button" onClick={onRefresh} className="shrink-0 rounded border border-rose-300/30 px-2 py-1 font-bold hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300" aria-label="重试读取运行详情">重试</button>
        </div>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2 text-ui-caption">
        <div><span className="text-slate-600">阶段</span><div className="font-mono text-slate-200">{run.phase}</div></div>
        <div><span className="text-slate-600">进度</span><div className="font-mono text-slate-200">{run.completedUnits} / {run.totalUnits || '—'}</div></div>
        <div><span className="text-slate-600">后端</span><div className="font-mono text-slate-200">{run.resolvedBackend ?? '不可用'}</div></div>
        <div><span className="text-slate-600">结论</span><div className="font-mono text-slate-200">{run.conclusion ?? '不可用'}</div></div>
      </div>
      {run.queueReason && <p className="mt-2 text-ui-micro text-amber-200">排队原因：{run.queueReason}</p>}
      {run.errorMessage && <p role="alert" className="mt-2 text-ui-micro text-rose-300">{run.errorCode ?? 'ERROR'}：{run.errorMessage}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {canCancel && <Button size="sm" variant="outline" disabled={busy} onClick={onCancel}><Square className="mr-1.5 h-3 w-3" />取消</Button>}
        {canFinal && <Button size="sm" disabled={busy} onClick={onFinal}><Play className="mr-1.5 h-3 w-3" />执行 FINAL_EVALUATION</Button>}
        {run.registerable && run.runKey && <Button size="sm" variant="outline" disabled={busy} onClick={onRegister}><ShieldCheck className="mr-1.5 h-3 w-3" />登记 {run.runKey.slice(0, 10)}…</Button>}
      </div>
      {run.runKind === 'FINAL_EVALUATION' && run.status === 'SUCCEEDED' && (
        <div className="mt-4 border-t border-white/[0.06] pt-3">
          <h3 className="text-ui-caption font-bold text-slate-300">FINAL 评估证据</h3>
          <div className="mt-2 grid grid-cols-2 gap-2 text-ui-micro">
            {[
              ['概率', evidenceValue(metrics, 'frozen_test', 'probability')],
              ['排序', evidenceValue(metrics, 'frozen_test', 'ranking')],
              ['数据', run.environmentEvidence],
              ['稳定性', evidenceValue(metrics, 'frozen_test', 'annual_stability')],
              ['模型分歧', evidenceValue(metrics, 'probability_disagreement')],
              ['门禁', gates],
            ].map(([label, value]) => (
              <div key={label} className="min-w-0 rounded border border-white/[0.06] bg-white/[0.02] p-2">
                <div className="text-slate-600">{label}</div>
                <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-slate-300">{pretty(value)}</pre>
              </div>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}

function Wizard({
  datasetVersion,
  onDatasetChange,
  dateStart,
  setDateStart,
  dateEnd,
  setDateEnd,
  universe,
  setUniverse,
  backend,
  setBackend,
  bootstrapSamples,
  setBootstrapSamples,
  randomSeed,
  setRandomSeed,
  workerBatchSize,
  setWorkerBatchSize,
  note,
  setNote,
  datasets,
  selectedDataset,
  preview,
  previewError,
  previewFetching,
  onPreviewRetry,
  onSubmit,
  submitting,
}: {
  datasetVersion: string;
  onDatasetChange: (value: string) => void;
  dateStart: string;
  setDateStart: (value: string) => void;
  dateEnd: string;
  setDateEnd: (value: string) => void;
  universe: UniverseDraft;
  setUniverse: (value: UniverseDraft) => void;
  backend: StockSelectionTrainingBackend;
  setBackend: (value: StockSelectionTrainingBackend) => void;
  bootstrapSamples: number;
  setBootstrapSamples: (value: number) => void;
  randomSeed: number;
  setRandomSeed: (value: number) => void;
  workerBatchSize: number;
  setWorkerBatchSize: (value: number) => void;
  note: string;
  setNote: (value: string) => void;
  datasets: readonly StockSelectionDatasetVersion[];
  selectedDataset: StockSelectionDatasetVersion | null;
  preview: TrainingPreviewEvidence | null;
  previewError: Error | undefined;
  previewFetching: boolean;
  onPreviewRetry: () => void;
  onSubmit: () => void;
  submitting: boolean;
}) {
  const [step, setStep] = useState(0);
  const blockers = preview?.blockers ?? [];
  const canSubmit = Boolean(preview?.canSubmit && blockers.length === 0 && !previewFetching);
  return (
    <section className="rounded-md border border-white/[0.08] bg-[#081321] p-3" aria-label="次日概率训练工作台">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-ui-body font-black text-slate-100">训练工作台</h2>
          <p className="mt-0.5 text-ui-micro text-slate-500">四步冻结坐标 → 预检 → 提交 DEVELOPMENT</p>
        </div>
        <div className="flex items-center gap-1" aria-label="训练步骤">
          {WIZARD_STEPS.map((label, index) => (
            <button key={label} type="button" aria-current={step === index ? 'step' : undefined} onClick={() => setStep(index)} className={cn('rounded px-2 py-1 text-ui-micro font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400', step === index ? 'bg-cyan-400/15 text-cyan-200' : 'text-slate-600 hover:bg-white/[0.04] hover:text-slate-300')}>
              {index + 1} {label}
            </button>
          ))}
        </div>
      </div>
      <div className="mt-3 min-h-24 rounded border border-white/[0.06] bg-white/[0.015] p-3">
        {step === 0 && (
          <div className="space-y-3">
            <label className="block text-ui-caption text-slate-400">1 · 选择认证数据集
              <NativeSelect aria-label="训练数据集" value={datasetVersion} onChange={event => onDatasetChange(event.target.value)} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60 focus:ring-2 focus:ring-cyan-400/20">
                <option value="">选择 CERTIFIED 数据集</option>
                {datasets.map(dataset => <option key={dataset.datasetVersion} value={dataset.datasetVersion}>{dataset.datasetVersion} · {dataset.dateStart} → {dataset.dateEnd}</option>)}
              </NativeSelect>
            </label>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <label className="text-ui-caption text-slate-400">股票范围
                <NativeSelect aria-label="训练股票范围" value={universe.kind} onChange={event => { if (isUniverseKind(event.target.value)) setUniverse({ ...universe, kind: event.target.value }); }} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60 focus:ring-2 focus:ring-cyan-400/20">
                  <option value={StockSelectionTrainingUniverseKind.OrdinaryAShare}>普通沪深 A 股</option>
                  <option value={StockSelectionTrainingUniverseKind.CertifiedIndex}>经认证指数成分</option>
                  <option value={StockSelectionTrainingUniverseKind.Explicit}>显式股票列表</option>
                </NativeSelect>
              </label>
              {universe.kind === StockSelectionTrainingUniverseKind.CertifiedIndex && (
                <label className="text-ui-caption text-slate-400">指数代码<Input aria-label="认证指数代码" value={universe.indexCode} onChange={event => setUniverse({ ...universe, indexCode: event.target.value })} placeholder="000300.SH" className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
              )}
              {universe.kind === StockSelectionTrainingUniverseKind.Explicit && (
                <label className="text-ui-caption text-slate-400 sm:col-span-2">显式股票代码（逗号或换行分隔）<Textarea aria-label="显式股票代码" value={universe.stockCodes} onChange={event => setUniverse({ ...universe, stockCodes: event.target.value })} placeholder="600000.SH\n000001.SZ" className="mt-2 min-h-14 rounded border border-white/10 bg-[#0c1b2e] px-2 py-1 font-mono text-ui-micro text-slate-200 outline-none focus:border-cyan-400/60" /></label>
              )}
              <label className="text-ui-caption text-slate-400">基准指数<Input aria-label="训练基准指数" value={universe.benchmarkCode} onChange={event => setUniverse({ ...universe, benchmarkCode: event.target.value })} placeholder="000300.SH" className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
              <label className="text-ui-caption text-slate-400">最少上市交易日<Input aria-label="最少上市交易日" type="number" min={0} value={universe.minimumListingDays} onChange={event => setUniverse({ ...universe, minimumListingDays: Number(event.target.value) })} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
            </div>
            {selectedDataset && <p className="text-ui-micro text-slate-600">股票范围默认取该认证数据集 universeSpec；修改后服务端会重新校验认证边界。</p>}
          </div>
        )}
        {step === 1 && (
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-ui-caption text-slate-400">2 · 起始日期<Input aria-label="训练起始日期" type="date" value={dateStart} onChange={event => setDateStart(event.target.value)} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
            <label className="text-ui-caption text-slate-400">结束日期<Input aria-label="训练结束日期" type="date" value={dateEnd} onChange={event => setDateEnd(event.target.value)} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
          </div>
        )}
        {step === 2 && (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="text-ui-caption text-slate-400 sm:col-span-2"><div>3 · 请求后端</div><div className="mt-2 flex flex-wrap gap-2" role="radiogroup" aria-label="训练后端">{Object.values(StockSelectionTrainingBackend).map(value => <button key={value} type="button" role="radio" aria-checked={backend === value} onClick={() => setBackend(value)} className={cn('rounded border px-3 py-2 font-mono text-ui-caption focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400', backend === value ? 'border-cyan-400/50 bg-cyan-400/10 text-cyan-200' : 'border-white/10 text-slate-500 hover:text-slate-300')}>{value}</button>)}</div></div>
            <label className="text-ui-caption text-slate-400">Bootstrap 样本<Input aria-label="Bootstrap 样本数" type="number" min={100} max={20000} value={bootstrapSamples} onChange={event => setBootstrapSamples(Number(event.target.value))} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
            <label className="text-ui-caption text-slate-400">随机种子<Input aria-label="随机种子" type="number" value={randomSeed} onChange={event => setRandomSeed(Number(event.target.value))} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
            <label className="text-ui-caption text-slate-400">Worker batch<Input aria-label="Worker batch 大小" type="number" min={1} max={1000} value={workerBatchSize} onChange={event => setWorkerBatchSize(Number(event.target.value))} className="mt-2 rounded border border-white/10 bg-[#0c1b2e] px-2 font-mono text-ui-caption text-slate-200 outline-none focus:border-cyan-400/60" /></label>
            <label className="text-ui-caption text-slate-400 sm:col-span-2">备注（可选）<Textarea aria-label="训练备注" maxLength={500} value={note} onChange={event => setNote(event.target.value)} className="mt-2 min-h-14 rounded border border-white/10 bg-[#0c1b2e] px-2 py-1 text-ui-micro text-slate-200 outline-none focus:border-cyan-400/60" /></label>
            <div className="rounded border border-white/[0.06] bg-white/[0.02] p-2 text-ui-caption text-slate-500 sm:col-span-2"><Cpu className="mb-1 h-4 w-4 text-cyan-300" />模型族、超参数和 30/6/1/12 时间切分由系统固定；资源参数进入预览坐标。</div>
          </div>
        )}
        {step === 3 && (
          <div>
            <div className="mb-2 text-ui-caption text-slate-400">4 · 预览确认</div>
            {previewError && <div role="alert" className="mb-2 flex items-center justify-between gap-2 text-ui-caption text-rose-300"><span>预检失败：{previewError.message}</span><button type="button" onClick={onPreviewRetry} className="rounded border border-rose-300/30 px-2 py-1 font-bold hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">重试预检</button></div>}
            {previewFetching && <p className="mb-2 flex items-center gap-2 text-ui-caption text-slate-500"><LoaderCircle className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />正在计算切分、覆盖和资源估算…</p>}
            {preview && <TrainingPreviewSummary blockers={blockers} warnings={preview.warnings} resolvedBackend={preview.resolvedBackend} canSubmit={canSubmit} preview={preview} dataset={selectedDataset} />}
            {!preview && !previewFetching && <p className="text-ui-caption text-slate-500">先选择数据集，预检证据会显示在这里。</p>}
          </div>
        )}
      </div>
      <div className="mt-3 flex justify-between gap-2">
        <Button size="sm" variant="outline" disabled={step === 0} onClick={() => setStep(value => Math.max(0, value - 1))}><ChevronLeft className="mr-1 h-3.5 w-3.5" />上一步</Button>
        {step < WIZARD_STEPS.length - 1 ? <Button size="sm" onClick={() => setStep(value => Math.min(WIZARD_STEPS.length - 1, value + 1))} disabled={step === 0 && !datasetVersion}>下一步<ChevronRight className="ml-1 h-3.5 w-3.5" /></Button> : <Button size="sm" disabled={!canSubmit || submitting} onClick={onSubmit}><Play className="mr-1.5 h-3 w-3" />提交 DEVELOPMENT</Button>}
      </div>
    </section>
  );
}

function ComparisonPanel({ runs }: { runs: StockSelectionTrainingRun[] }) {
  const eligible = runs.filter(run => run.runKind === 'FINAL_EVALUATION' && run.status === 'SUCCEEDED');
  const [selected, setSelected] = useState<string[]>([]);
  const selectedCoordinate = selected.length > 0 ? runs.find(run => run.runId === selected[0])?.coordinateHash : null;
  const sameCoordinate = (run: StockSelectionTrainingRun) => !selectedCoordinate || run.coordinateHash === selectedCoordinate;
  const ids = selected.filter(id => eligible.some(run => run.runId === id));
  const { comparison, error, fetching } = useStockSelectionTrainingComparison(ids);
  return (
    <section className="rounded-md border border-white/[0.08] bg-[#081321] p-3" aria-label="训练运行比较">
      <div className="flex items-center gap-2"><GitCompareArrows className="h-4 w-4 text-violet-300" /><h2 className="text-ui-caption font-black text-slate-200">同坐标 FINAL 对比</h2><span className="text-ui-micro text-slate-600">选择 2–5 个</span></div>
      <div className="mt-2 flex flex-wrap gap-2">{eligible.map(run => <label key={run.runId} className={cn('flex max-w-full items-center gap-1.5 rounded border px-2 py-1 text-ui-micro', sameCoordinate(run) ? 'border-white/10 text-slate-300' : 'border-rose-400/20 text-rose-300')}><input type="checkbox" checked={selected.includes(run.runId)} disabled={!sameCoordinate(run) && selected.length > 0} onChange={event => setSelected(value => event.target.checked ? [...value, run.runId].slice(-5) : value.filter(id => id !== run.runId))} /> <span className="max-w-44 truncate font-mono">{run.runId}</span></label>)}</div>
      {selected.length > 0 && (() => {
        const latest = eligible.find(run => run.runId === selected[selected.length - 1]);
        return latest && !sameCoordinate(latest) ? <p role="alert" className="mt-2 text-ui-micro text-rose-300">坐标不一致，不能直接比较。</p> : null;
      })()}
      {error && <p role="alert" className="mt-2 text-ui-micro text-rose-300">比较失败：{error.message}</p>}
      {fetching && <p className="mt-2 text-ui-micro text-slate-500">正在比较…</p>}
      {comparison && !comparison.comparable && <p role="alert" className="mt-2 text-ui-micro text-amber-200">不可比：{comparison.mismatchFields.join('、') || '状态或坐标不一致'}</p>}
      {comparison?.comparable && <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-emerald-400/20 bg-emerald-400/5 p-2 font-mono text-ui-micro text-emerald-200">{pretty(comparison.metrics)}</pre>}
    </section>
  );
}

export default function StockSelectionTrainingWorkbench() {
  const { toast } = useToast();
  const capabilities = useStockSelectionTrainingCapabilities();
  const datasets = useStockSelectionDatasetVersions();
  const runsState = useStockSelectionTrainingRuns();
  const [datasetVersion, setDatasetVersion] = useState('');
  const [dateStart, setDateStart] = useState('');
  const [dateEnd, setDateEnd] = useState('');
  const [universe, setUniverse] = useState<UniverseDraft>(DEFAULT_UNIVERSE);
  const [backend, setBackend] = useState<StockSelectionTrainingBackend>(StockSelectionTrainingBackend.Cpu);
  const [bootstrapSamples, setBootstrapSamples] = useState(2000);
  const [randomSeed, setRandomSeed] = useState(20260901);
  const [workerBatchSize, setWorkerBatchSize] = useState(100);
  const [note, setNote] = useState('');
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const pendingIdempotencyKeys = useRef(createPendingIdempotencyKeys());
  const selectedDataset = useMemo(
    () => datasets.data.find(dataset => dataset.datasetVersion === datasetVersion) ?? null,
    [datasetVersion, datasets.data]
  );
  const onDatasetChange = (value: string) => {
    setDatasetVersion(value);
    const nextDataset = datasets.data.find(dataset => dataset.datasetVersion === value);
    setUniverse(nextDataset ? universeDraftFromSpec(nextDataset.universeSpec) : DEFAULT_UNIVERSE);
  };
  const previewInput = useMemo(() => ({
    datasetVersion,
    dateStart: dateStart || null,
    dateEnd: dateEnd || null,
    universe: toUniverseInput(universe),
    requestedBackend: backend,
    bootstrapSamples,
    workerBatchSize,
    randomSeed,
    note,
  }), [backend, bootstrapSamples, datasetVersion, dateEnd, dateStart, note, randomSeed, universe, workerBatchSize]);
  const [previewResult, preview] = useQuery({
    query: PreviewStockSelectionTrainingDocument,
    variables: { input: previewInput },
    pause: !datasetVersion,
    requestPolicy: 'cache-and-network',
  });
  const [startResult, startDevelopment] = useMutation(StartStockSelectionDevelopmentTrainingDocument);
  const [finalResult, startFinal] = useMutation(StartStockSelectionFinalEvaluationDocument);
  const [cancelResult, cancel] = useMutation(CancelStockSelectionTrainingRunDocument);
  const [registerResult, register] = useMutation(RegisterStockSelectionModelDocument);
  const detail = useStockSelectionTrainingRun(selectedRunId);
  const selectedRun = detail.run ?? runsState.runs.find(run => run.runId === selectedRunId) ?? null;
  const busy = startResult.fetching || finalResult.fetching || cancelResult.fetching || registerResult.fetching;
  const capability = capabilities.data;

  const submitDevelopment = async () => {
    const previewData = previewResult.data?.previewStockSelectionTraining;
    if (!previewData || !previewData.canSubmit || previewData.blockers.length > 0) return;
    const scope = `development:${previewData.previewFingerprint}`;
    const result = await startDevelopment({ input: previewInput, previewFingerprint: previewData.previewFingerprint, idempotencyKey: pendingIdempotencyKeys.current.get(scope) });
    if (result.error) {
      toast({ title: 'DEVELOPMENT 启动失败', description: result.error.message, variant: 'destructive' });
      return;
    }
    const run = result.data?.startStockSelectionDevelopmentTraining;
    if (!run) return;
    pendingIdempotencyKeys.current.clear(scope);
    setSelectedRunId(run.runId);
    toast({ title: 'DEVELOPMENT 已进入队列', variant: 'success' });
    void runsState.refresh();
  };
  const submitFinal = async () => {
    if (!selectedRun || selectedRun.runKind !== 'DEVELOPMENT' || selectedRun.status !== 'SUCCEEDED') return;
    const scope = `final:${selectedRun.runId}`;
    const result = await startFinal({ parentRunId: selectedRun.runId, idempotencyKey: pendingIdempotencyKeys.current.get(scope) });
    if (result.error) toast({ title: 'FINAL_EVALUATION 启动失败', description: result.error.message, variant: 'destructive' });
    else {
      const run = result.data?.startStockSelectionFinalEvaluation;
      if (!run) return;
      pendingIdempotencyKeys.current.clear(scope);
      setSelectedRunId(run.runId);
      toast({ title: 'FINAL_EVALUATION 已进入队列', variant: 'success' });
      void runsState.refresh();
    }
  };
  const submitCancel = async () => {
    if (!selectedRun || TERMINAL.has(selectedRun.status)) return;
    const scope = `cancel:${selectedRun.runId}:${selectedRun.stateVersion}`;
    const result = await cancel({ runId: selectedRun.runId, expectedVersion: selectedRun.stateVersion, idempotencyKey: pendingIdempotencyKeys.current.get(scope) });
    if (result.error) toast({ title: '取消失败', description: result.error.message, variant: 'destructive' });
    else {
      if (!result.data?.cancelStockSelectionTrainingRun) return;
      pendingIdempotencyKeys.current.clear(scope);
      toast({ title: '已请求取消训练', variant: 'success' });
      void runsState.refresh();
    }
  };
  const submitRegister = async () => {
    if (!selectedRun?.runKey || !selectedRun.registerable) return;
    const result = await register({ runKey: selectedRun.runKey });
    if (result.error) toast({ title: '模型登记失败', description: result.error.message, variant: 'destructive' });
    else toast({ title: 'FINAL 模型已登记为 CANDIDATE', variant: 'success' });
  };

  return (
    <section className="shrink-0 border-b border-white/[0.06] bg-[#06101d] p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2"><Cpu className="h-4 w-4 text-cyan-300" /><span className="text-ui-caption font-black text-slate-200">次日上涨概率 · 开发 / 评估</span>{runsState.polling && <span className="rounded border border-cyan-400/20 bg-cyan-400/5 px-1.5 py-0.5 font-mono text-ui-micro text-cyan-200">5s polling</span>}</div>
        <div className="flex items-center gap-2 text-ui-micro text-slate-500"><span>CPU {capability?.cpuAvailable ? '可用' : '不可用'}</span><span>GPU {capability?.gpuStatus ?? '不可用'}</span><span>环境 {capability?.fresh ? '新鲜' : '需重检'}</span></div>
      </div>
      {capabilities.error && <div role="alert" className="mb-2 flex items-center justify-between gap-2 text-ui-caption text-rose-300"><span>能力读取失败：{capabilities.error.message}</span><button type="button" onClick={() => void capabilities.refresh()} className="rounded border border-rose-300/30 px-2 py-1 font-bold hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">重试能力读取</button></div>}
      {datasets.error && <div role="alert" className="mb-2 flex items-center justify-between gap-2 text-ui-caption text-rose-300"><span>数据集读取失败：{datasets.error.message}</span><button type="button" onClick={() => void datasets.refresh()} className="rounded border border-rose-300/30 px-2 py-1 font-bold hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">重试数据集读取</button></div>}
      <Wizard datasetVersion={datasetVersion} onDatasetChange={onDatasetChange} dateStart={dateStart} setDateStart={setDateStart} dateEnd={dateEnd} setDateEnd={setDateEnd} universe={universe} setUniverse={setUniverse} backend={backend} setBackend={setBackend} bootstrapSamples={bootstrapSamples} setBootstrapSamples={setBootstrapSamples} randomSeed={randomSeed} setRandomSeed={setRandomSeed} workerBatchSize={workerBatchSize} setWorkerBatchSize={setWorkerBatchSize} note={note} setNote={setNote} datasets={datasets.data} selectedDataset={selectedDataset} preview={previewResult.data?.previewStockSelectionTraining ?? null} previewError={previewResult.error} previewFetching={previewResult.fetching} onPreviewRetry={() => void preview({ requestPolicy: 'network-only' })} onSubmit={() => void submitDevelopment()} submitting={busy} />
      <div className="mt-3 grid min-h-0 gap-3 xl:grid-cols-[minmax(0,1.25fr)_minmax(20rem,0.75fr)]">
        <section className="min-w-0 rounded-md border border-white/[0.08] bg-[#081321]" aria-label="训练运行列表">
          <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2"><span className="text-ui-caption font-bold text-slate-300">运行队列 <span className="font-mono text-slate-600">{runsState.total}</span></span><button type="button" aria-label="刷新训练运行" onClick={() => void runsState.refresh()} className="rounded p-1 text-slate-500 hover:bg-white/[0.06] hover:text-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400"><RefreshCw className={cn('h-3.5 w-3.5', runsState.fetching && 'animate-spin motion-reduce:animate-none')} /></button></div>
          {runsState.error ? <div role="alert" className="flex items-center justify-between gap-2 p-3 text-ui-caption text-rose-300"><span>读取运行失败：{runsState.error.message}</span><button type="button" onClick={() => void runsState.refresh()} className="rounded border border-rose-300/30 px-2 py-1 font-bold hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300">重试</button></div> : runsState.fetching && runsState.runs.length === 0 ? <div role="status" aria-live="polite" className="p-3 text-ui-caption text-slate-500">正在读取训练运行…</div> : runsState.runs.length === 0 ? <div className="p-3 text-ui-caption text-slate-500">还没有训练运行。完成预检后提交 DEVELOPMENT。</div> : <div className="max-h-64 overflow-auto"><table className="w-full min-w-[620px] text-left text-ui-micro"><caption className="sr-only">次日概率训练运行</caption><thead className="sticky top-0 bg-[#0b1a2b] text-slate-600"><tr><th className="px-3 py-2">状态</th><th className="px-3 py-2">运行</th><th className="px-3 py-2">阶段</th><th className="px-3 py-2">进度</th><th className="px-3 py-2">后端</th></tr></thead><tbody className="divide-y divide-white/[0.05]">{runsState.runs.map(run => <tr key={run.runId} role="button" tabIndex={0} aria-selected={selectedRunId === run.runId} className={cn('cursor-pointer hover:bg-white/[0.04]', selectedRunId === run.runId && 'bg-cyan-400/[0.06]')} onClick={() => setSelectedRunId(run.runId)} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelectedRunId(run.runId); } }}><td className="px-3 py-2"><RunStatus run={run} /></td><td className="max-w-56 truncate px-3 py-2 font-mono text-slate-300">{run.runKind} · {run.runId}</td><td className="px-3 py-2 font-mono text-slate-500">{run.phase}</td><td className="px-3 py-2 font-mono tabular-nums text-slate-400">{run.completedUnits}/{run.totalUnits || '—'}</td><td className="px-3 py-2 font-mono text-slate-500">{run.resolvedBackend ?? '不可用'}</td></tr>)}</tbody></table></div>}
        </section>
        {selectedRun ? <RunDetail run={selectedRun} error={detail.error} onRefresh={() => void detail.refresh()} onFinal={() => void submitFinal()} onCancel={() => void submitCancel()} onRegister={() => void submitRegister()} busy={busy} /> : <div className="flex min-h-40 items-center justify-center rounded-md border border-dashed border-white/[0.08] text-ui-caption text-slate-600">选择一个运行查看阶段、错误和最终证据</div>}
      </div>
      <div className="mt-3"><ComparisonPanel runs={runsState.runs} /></div>
    </section>
  );
}
