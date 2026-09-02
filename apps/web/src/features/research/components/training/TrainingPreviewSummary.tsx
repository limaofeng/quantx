import { AlertTriangle, CheckCircle2 } from 'lucide-react';

import type {
  StockSelectionDatasetVersion,
  StockSelectionTrainingInput,
} from '@/generated/gql/graphql';

import type { TrainingPreviewEvidence } from './types';
import {
  FIXED_TIME_STRUCTURE,
  MODEL_FAMILY,
  evidenceValue,
  firstPresent,
  numericValue,
  pretty,
  rangeText,
  ratioText,
  universeLabel,
} from './utils';

export function TrainingPreviewSummary({
  blockers,
  warnings,
  resolvedBackend,
  canSubmit,
  preview,
  dataset,
  input,
}: {
  blockers: readonly string[];
  warnings: readonly string[];
  resolvedBackend: string;
  canSubmit: boolean;
  preview?: TrainingPreviewEvidence | null;
  dataset?: StockSelectionDatasetVersion | null;
  input?: StockSelectionTrainingInput | null;
}) {
  const coverage = preview?.coverage;
  const quality = firstPresent(
    evidenceValue(coverage, 'quality'),
    evidenceValue(dataset?.qualitySummary, 'quality'),
    dataset?.qualitySummary
  );
  const resource = preview?.resourceEstimate;
  const sampleCount = numericValue(
    evidenceValue(resource, 'sampleCount'),
    evidenceValue(coverage, 'sample_count'),
    evidenceValue(coverage, 'sampleCount'),
    evidenceValue(quality, 'sample_count'),
    evidenceValue(quality, 'sampleCount'),
    dataset?.sampleCount
  );
  const stockCount = numericValue(
    evidenceValue(resource, 'stockCount'),
    evidenceValue(coverage, 'stock_count'),
    evidenceValue(coverage, 'stockCount'),
    evidenceValue(quality, 'stock_count'),
    evidenceValue(quality, 'stockCount'),
    dataset?.stockCount
  );
  const tradingDayCount = numericValue(
    evidenceValue(resource, 'tradingDayCount'),
    evidenceValue(coverage, 'trading_day_count'),
    evidenceValue(coverage, 'tradingDayCount'),
    evidenceValue(quality, 'trading_day_count'),
    evidenceValue(quality, 'tradingDayCount'),
    dataset?.tradingDayCount
  );
  const positiveRate = firstPresent(
    evidenceValue(coverage, 'positive_rate'),
    evidenceValue(coverage, 'positiveRate'),
    evidenceValue(quality, 'positive_rate'),
    evidenceValue(quality, 'positiveRate')
  );
  const developmentStart = firstPresent(
    evidenceValue(coverage, 'development_start'),
    evidenceValue(coverage, 'developmentStart'),
    evidenceValue(coverage, 'requested_start'),
    evidenceValue(coverage, 'requestedStart'),
    input?.dateStart
  );
  const developmentEnd = firstPresent(
    evidenceValue(coverage, 'development_end'),
    evidenceValue(coverage, 'developmentEnd'),
    evidenceValue(coverage, 'requested_end'),
    evidenceValue(coverage, 'requestedEnd'),
    input?.dateEnd
  );
  const frozenStart = firstPresent(
    evidenceValue(coverage, 'frozen_test_start'),
    evidenceValue(coverage, 'frozenTestStart')
  );
  const frozenEnd = firstPresent(
    evidenceValue(coverage, 'frozen_test_end'),
    evidenceValue(coverage, 'frozenTestEnd')
  );
  const effectiveWarnings = [
    ...warnings,
    ...(preview?.requestedBackend === 'AUTO' &&
    preview.resolvedBackend === 'CPU'
      ? ['GPU 未通过资格验证，AUTO 已解析为 CPU。']
      : []),
  ];
  const cards: Array<[string, string]> = [
    [
      '数据集 / Manifest SHA-256',
      `${preview?.datasetVersion || dataset?.datasetVersion || '不可用'} · ${dataset?.manifestSha256 || '不可用'}`,
    ],
    ['股票范围', universeLabel(input ?? null)],
    ['总区间', rangeText(dataset?.dateStart, dataset?.dateEnd)],
    ['时间结构', FIXED_TIME_STRUCTURE],
    [
      '开发 / 冻结区间',
      `${rangeText(developmentStart, developmentEnd)} · ${rangeText(frozenStart, frozenEnd)}`,
    ],
    ['模型家族', MODEL_FAMILY],
    [
      '样本 / 股票 / 交易日 / fold',
      `${pretty(sampleCount)} / ${pretty(stockCount)} / ${pretty(tradingDayCount)} / ${preview ? preview.folds.length : '不可用'}`,
    ],
    [
      '请求 / 实际后端',
      `${preview?.requestedBackend || input?.requestedBackend || '不可用'} / ${resolvedBackend || preview?.resolvedBackend || '不可用'}`,
    ],
    [
      'Bootstrap / Seed',
      `${pretty(input?.bootstrapSamples)} / ${pretty(input?.randomSeed)}`,
    ],
    [
      '内存 / 显存 / 磁盘',
      `${pretty(numericValue(evidenceValue(resource, 'memoryMib'), evidenceValue(resource, 'estimatedMemoryMib')))} / ${pretty(numericValue(evidenceValue(resource, 'gpuMemoryMib'), evidenceValue(resource, 'estimatedGpuMemoryMib')))} / ${pretty(numericValue(evidenceValue(resource, 'diskMib'), evidenceValue(resource, 'estimatedDiskMib')))}`,
    ],
    [
      '预计耗时',
      `${pretty(evidenceValue(resource, 'estimatedMinutes'))} · ${pretty(evidenceValue(resource, 'durationLevel'))}`,
    ],
    ['覆盖率 / 正样本比例', `${pretty(coverage)} · ${ratioText(positiveRate)}`],
    ['泄漏检查', pretty(preview?.leakage)],
    ['Shadow 原因', pretty(preview?.shadowReasons)],
  ];

  return (
    <div className="space-y-2" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2 text-ui-caption">
        <span className="text-slate-500">解析后端</span>
        <span className="rounded-control border border-blue-400/30 bg-blue-400/10 px-2 py-1 font-mono font-bold text-blue-200">
          {resolvedBackend || '不可用'} · 已锁定
        </span>
        {canSubmit && blockers.length === 0 ? (
          <CheckCircle2
            className="h-4 w-4 text-emerald-300"
            aria-label="预检通过"
          />
        ) : (
          <AlertTriangle
            className="h-4 w-4 text-amber-300"
            aria-label="存在预检阻塞"
          />
        )}
      </div>
      {blockers.length > 0 && (
        <div
          role="alert"
          className="rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          <div className="font-bold">提交已禁用</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {blockers.map(item => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}
      {effectiveWarnings.length > 0 && (
        <div className="rounded-control border border-amber-400/20 bg-amber-400/5 p-2 text-ui-caption text-amber-200">
          {effectiveWarnings.join(' · ')}
        </div>
      )}
      {preview && (
        <div
          data-testid="training-preview-evidence"
          className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3"
        >
          {cards.map(([label, value]) => (
            <div
              key={label}
              className="min-w-0 rounded-control border border-white/[0.06] bg-white/[0.02] p-2 text-ui-micro"
            >
              <div className="text-slate-600">{label}</div>
              <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap break-words font-mono text-slate-300">
                {value}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
