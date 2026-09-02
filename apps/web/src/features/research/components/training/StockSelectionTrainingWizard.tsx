import {
  AlertTriangle,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  Cpu,
  Database,
  Info,
  LoaderCircle,
  LockKeyhole,
  Play,
  RefreshCw,
  ServerCog,
  ShieldCheck,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { Textarea } from '@/components/ui/textarea';
import {
  PreviewStockSelectionTrainingDocument,
  StartStockSelectionDevelopmentTrainingDocument,
  StockSelectionTrainingBackend,
  StockSelectionTrainingUniverseKind,
  type StockSelectionDatasetVersion,
  type StockSelectionTrainingInput,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import {
  useStockSelectionDatasetVersions,
  useStockSelectionTrainingCapabilities,
} from '../../hooks';
import { createPendingIdempotencyKeys } from '../../idempotency';

import { TrainingPreviewSummary } from './TrainingPreviewSummary';
import type {
  DatasetStateProps,
  PreviewRequest,
  PreviewState,
  TrainingPreviewEvidence,
  UniverseDraft,
} from './types';
import {
  DEFAULT_UNIVERSE,
  FIXED_TIME_STRUCTURE,
  MODEL_FAMILY,
  WIZARD_STEPS,
  isUniverseKind,
  pretty,
  rangeText,
  toTrainingInput,
  trainingInputSignature,
  universeDraftFromSpec,
  universeLabel,
  validateTrainingInput,
} from './utils';

function DatasetState({
  fetching,
  error,
  datasets,
  onRetry,
}: DatasetStateProps) {
  if (fetching && datasets.length === 0) {
    return (
      <p
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 text-ui-caption text-slate-500"
      >
        <LoaderCircle className="h-3.5 w-3.5 animate-spin text-blue-400 motion-reduce:animate-none" />
        正在读取认证数据集…
      </p>
    );
  }
  if (error) {
    return (
      <div
        role="alert"
        className="flex items-center justify-between gap-2 rounded-control border border-rose-400/20 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
      >
        <span>数据集读取失败：{error.message}</span>
        <Button size="sm" variant="outline" onClick={onRetry}>
          重试
        </Button>
      </div>
    );
  }
  if (datasets.length === 0) {
    return (
      <div className="rounded-control border border-amber-400/20 bg-amber-400/5 p-2 text-amber-100">
        <div className="flex items-center gap-2 text-ui-caption font-bold">
          <AlertTriangle className="h-4 w-4 text-amber-300" />
          暂无认证数据集
        </div>
        <p className="mt-1 text-ui-micro text-amber-200/80">
          向导只接受 status=CERTIFIED
          的数据集。请先在数据管理中完成认证，再返回此处创建训练；当前页面不会上传或修改数据集。
        </p>
      </div>
    );
  }
  return null;
}

function CapabilitySummary({
  data,
  fetching,
  error,
  onRetry,
}: {
  data: ReturnType<typeof useStockSelectionTrainingCapabilities>['data'];
  fetching: boolean;
  error?: { message: string };
  onRetry: () => void;
}) {
  if (fetching && !data) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="text-ui-micro text-slate-500"
      >
        正在读取 CPU/GPU 能力摘要…
      </div>
    );
  }
  if (error) {
    return (
      <div
        role="alert"
        aria-live="polite"
        className="flex flex-wrap items-center justify-between gap-2 rounded-control border border-rose-400/20 bg-rose-400/5 p-2 text-ui-micro text-rose-200"
      >
        <span>能力摘要读取失败：{error.message}</span>
        <Button size="sm" variant="outline" onClick={onRetry}>
          重试能力读取
        </Button>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2 text-ui-micro text-slate-500">
        能力摘要不可用；不会据此伪造 CPU/GPU readiness，最终以后端预检为准。
      </div>
    );
  }
  const gpuReady = data.gpuStatus === 'GPU_AVAILABLE';
  return (
    <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
      <dl className="grid gap-x-3 gap-y-1 text-ui-micro sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <dt className="text-slate-600">CPU</dt>
          <dd
            className={
              data.cpuAvailable ? 'text-emerald-200' : 'text-amber-200'
            }
          >
            {data.cpuAvailable ? '可用' : '不可用'}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">GPU</dt>
          <dd className={gpuReady ? 'text-emerald-200' : 'text-amber-200'}>
            {data.gpuStatus}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">环境 fresh</dt>
          <dd className={data.fresh ? 'text-emerald-200' : 'text-amber-200'}>
            {data.fresh ? '新鲜' : '需重检'}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">updatedAt</dt>
          <dd className="break-all font-mono text-slate-300">
            {data.updatedAt || '不可用'}
          </dd>
        </div>
      </dl>
      <div className="mt-2 text-ui-micro text-slate-500">
        资格证据：{pretty(data.qualification)}
      </div>
      {!gpuReady && (
        <p className="mt-1 text-ui-micro text-amber-200">
          GPU 当前未通过资格状态；不会静默修改你的后端选择。AUTO 的解析与
          GPU_REQUIRED 的阻断由服务端预检决定。
        </p>
      )}
    </div>
  );
}

function FrozenCoordinate({
  input,
  dataset,
  preview,
}: {
  input: StockSelectionTrainingInput;
  dataset: StockSelectionDatasetVersion | null;
  preview: TrainingPreviewEvidence | null;
}) {
  const values: Array<[string, string]> = [
    ['数据集', preview?.datasetVersion || dataset?.datasetVersion || '不可用'],
    ['Manifest SHA-256', dataset?.manifestSha256 || '不可用'],
    ['股票范围', universeLabel(input)],
    ['总区间', rangeText(dataset?.dateStart, dataset?.dateEnd)],
    ['请求后端', input.requestedBackend || '不可用'],
    [
      'Bootstrap / Seed',
      `${pretty(input.bootstrapSamples)} / ${pretty(input.randomSeed)}`,
    ],
    ['Worker batch', pretty(input.workerBatchSize)],
    ['时间结构', FIXED_TIME_STRUCTURE],
  ];
  return (
    <section
      className="min-w-0 rounded-panel border border-white/[0.08] bg-[#0a1525] p-3"
      aria-labelledby="frozen-coordinate-title"
    >
      <div className="flex items-start gap-2">
        <LockKeyhole className="mt-0.5 h-4 w-4 text-slate-400" />
        <div>
          <h3
            id="frozen-coordinate-title"
            className="text-ui-body font-bold text-slate-100"
          >
            冻结训练坐标
          </h3>
          <p className="mt-1 text-ui-caption text-slate-500">
            提交后坐标与实际后端不可变，请确认后再提交。
          </p>
        </div>
      </div>
      <dl className="mt-3 divide-y divide-white/[0.06] rounded-control border border-white/[0.08]">
        {values.map(([label, value]) => (
          <div
            key={label}
            className="grid gap-1 px-3 py-2 sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-center"
          >
            <dt className="text-ui-caption text-slate-500">{label}</dt>
            <dd className="min-w-0 break-words font-mono text-ui-caption text-slate-200">
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <div className="mt-3 flex items-start gap-2 text-ui-micro text-slate-500">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-300" />
        <span>模型家族和 30/6/1/12 时间结构由服务端固定，浏览器不能覆盖。</span>
      </div>
    </section>
  );
}

function PreviewResultPanel({
  preview,
  previewError,
  previewFetching,
  canSubmit,
  dataset,
  input,
  onPreview,
}: {
  preview: TrainingPreviewEvidence | null;
  previewError: string | null;
  previewFetching: boolean;
  canSubmit: boolean;
  dataset: StockSelectionDatasetVersion | null;
  input: StockSelectionTrainingInput;
  onPreview: () => void;
}) {
  return (
    <section
      className="min-w-0 rounded-panel border border-white/[0.08] bg-[#0a1525] p-3"
      aria-labelledby="preview-result-title"
    >
      <div className="flex items-start gap-2">
        <ServerCog className="mt-0.5 h-4 w-4 text-blue-300" />
        <div className="min-w-0 flex-1">
          <h3
            id="preview-result-title"
            className="text-ui-body font-bold text-slate-100"
          >
            预检结果
          </h3>
          <p className="mt-1 text-ui-caption text-slate-500">
            只在此处显式执行预检；坐标变更会立即使旧指纹失效。
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={onPreview}
          disabled={previewFetching}
        >
          <RefreshCw
            className={cn(
              'mr-1.5 h-3.5 w-3.5',
              previewFetching && 'animate-spin motion-reduce:animate-none'
            )}
          />
          {preview ? '重新预检' : '执行预检'}
        </Button>
      </div>
      {previewError && (
        <div
          role="alert"
          aria-live="polite"
          className="mt-3 flex items-center justify-between gap-2 rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          <span>预检失败：{previewError}</span>
          <Button
            size="sm"
            variant="outline"
            onClick={onPreview}
            disabled={previewFetching}
          >
            重新预检
          </Button>
        </div>
      )}
      {previewFetching && (
        <p
          role="status"
          aria-live="polite"
          className="mt-3 flex items-center gap-2 text-ui-caption text-slate-500"
        >
          <LoaderCircle className="h-3.5 w-3.5 animate-spin text-blue-400 motion-reduce:animate-none" />
          正在计算切分、覆盖和资源估算…
        </p>
      )}
      {preview && !previewFetching && (
        <div className="mt-3">
          <TrainingPreviewSummary
            blockers={preview.blockers}
            warnings={preview.warnings}
            resolvedBackend={preview.resolvedBackend}
            canSubmit={canSubmit}
            preview={preview}
            dataset={dataset}
            input={input}
          />
        </div>
      )}
      {!preview && !previewFetching && !previewError && (
        <p className="mt-3 text-ui-caption text-slate-500">
          点击“执行预检”后，服务端才会返回可提交的证据。
        </p>
      )}
    </section>
  );
}

export function StockSelectionTrainingWizard({
  onCreated,
}: {
  onCreated: (runId: string) => void;
}) {
  const { toast } = useToast();
  const datasetsState = useStockSelectionDatasetVersions();
  const capabilities = useStockSelectionTrainingCapabilities();
  const [step, setStep] = useState(0);
  const [datasetVersion, setDatasetVersion] = useState('');
  const [dateStart, setDateStart] = useState('');
  const [dateEnd, setDateEnd] = useState('');
  const [universe, setUniverse] = useState<UniverseDraft>({
    ...DEFAULT_UNIVERSE,
  });
  const [backend, setBackend] = useState<StockSelectionTrainingBackend>(
    StockSelectionTrainingBackend.Cpu
  );
  const [bootstrapSamples, setBootstrapSamples] = useState(2000);
  const [randomSeed, setRandomSeed] = useState(20260901);
  const [workerBatchSize, setWorkerBatchSize] = useState(100);
  const [note, setNote] = useState('');
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [previewState, setPreviewState] = useState<PreviewState | null>(null);
  const [previewRequest, setPreviewRequest] = useState<PreviewRequest | null>(
    null
  );
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const pendingKeys = useRef(createPendingIdempotencyKeys());

  const certifiedDatasets = useMemo(
    () =>
      datasetsState.data.filter(
        dataset => dataset.status.toUpperCase() === 'CERTIFIED'
      ),
    [datasetsState.data]
  );
  const selectedDataset = useMemo(
    () =>
      certifiedDatasets.find(
        dataset => dataset.datasetVersion === datasetVersion
      ) ?? null,
    [certifiedDatasets, datasetVersion]
  );
  const input = useMemo(
    () =>
      toTrainingInput({
        datasetVersion,
        dateStart,
        dateEnd,
        universe,
        backend,
        bootstrapSamples,
        workerBatchSize,
        randomSeed,
        note,
      }),
    [
      backend,
      bootstrapSamples,
      datasetVersion,
      dateEnd,
      dateStart,
      note,
      randomSeed,
      universe,
      workerBatchSize,
    ]
  );
  const signature = useMemo(() => trainingInputSignature(input), [input]);
  const [previewResult, executePreview] = useQuery({
    query: PreviewStockSelectionTrainingDocument,
    variables: { input: previewRequest?.input ?? input },
    pause: previewRequest === null,
    requestPolicy: 'network-only',
  });
  const [startResult, startDevelopment] = useMutation(
    StartStockSelectionDevelopmentTrainingDocument
  );
  const currentPreview =
    previewState?.signature === signature ? previewState.preview : null;
  const previewFetching = Boolean(previewRequest && previewResult.fetching);
  const canSubmit = Boolean(
    currentPreview &&
    currentPreview.canSubmit &&
    currentPreview.blockers.length === 0 &&
    currentPreview.previewFingerprint &&
    !previewFetching
  );

  useEffect(() => {
    if (!previewRequest || previewResult.fetching) return;
    if (previewRequest.signature !== signature) return;
    if (previewResult.error) {
      setPreviewState(null);
      setPreviewError(previewResult.error.message);
      return;
    }
    const data = previewResult.data?.previewStockSelectionTraining;
    if (data) {
      setPreviewState({
        signature: previewRequest.signature,
        preview: data,
      });
    }
  }, [
    previewRequest,
    previewResult.data,
    previewResult.error,
    previewResult.fetching,
    signature,
  ]);

  useEffect(() => {
    if (datasetsState.error) {
      toast({
        title: '数据集读取失败',
        description: datasetsState.error.message,
        variant: 'destructive',
      });
    }
  }, [datasetsState.error, toast]);

  useEffect(() => {
    if (capabilities.error) {
      toast({
        title: '能力摘要读取失败',
        description: capabilities.error.message,
        variant: 'destructive',
      });
    }
  }, [capabilities.error, toast]);

  useEffect(() => {
    if (previewResult.error && previewRequest) {
      toast({
        title: '预检失败',
        description: previewResult.error.message,
        variant: 'destructive',
      });
    }
  }, [previewRequest, previewResult.error, toast]);

  const invalidatePreview = () => {
    setPreviewState(null);
    setPreviewRequest(null);
    setPreviewError(null);
    setSubmitError(null);
    setValidationErrors([]);
  };

  const updateDataset = (value: string) => {
    invalidatePreview();
    setDatasetVersion(value);
    const nextDataset = certifiedDatasets.find(
      dataset => dataset.datasetVersion === value
    );
    setUniverse(
      nextDataset
        ? universeDraftFromSpec(nextDataset.universeSpec)
        : { ...DEFAULT_UNIVERSE }
    );
  };
  const updateUniverse = (next: UniverseDraft) => {
    invalidatePreview();
    setUniverse(next);
  };
  const updateDateStart = (value: string) => {
    invalidatePreview();
    setDateStart(value);
  };
  const updateDateEnd = (value: string) => {
    invalidatePreview();
    setDateEnd(value);
  };
  const updateBackend = (value: StockSelectionTrainingBackend) => {
    invalidatePreview();
    setBackend(value);
  };
  const updateBootstrap = (value: number) => {
    invalidatePreview();
    setBootstrapSamples(value);
  };
  const updateSeed = (value: number) => {
    invalidatePreview();
    setRandomSeed(value);
  };
  const updateBatch = (value: number) => {
    invalidatePreview();
    setWorkerBatchSize(value);
  };
  const updateNote = (value: string) => {
    invalidatePreview();
    setNote(value);
  };

  const requestPreview = () => {
    const errors = validateTrainingInput(input, selectedDataset);
    setValidationErrors(errors);
    setSubmitError(null);
    setStep(3);
    if (errors.length > 0) return;

    setPreviewState(null);
    setPreviewError(null);
    if (previewRequest?.signature === signature) {
      void executePreview({ requestPolicy: 'network-only' });
      return;
    }
    setPreviewRequest({ input, signature });
  };

  const submitDevelopment = async () => {
    const errors = validateTrainingInput(input, selectedDataset);
    setValidationErrors(errors);
    if (errors.length > 0) {
      setStep(3);
      return;
    }
    const preview = currentPreview;
    if (!preview || !canSubmit || !preview.previewFingerprint) {
      setPreviewError('当前坐标尚未完成可提交预检，请重新执行预检。');
      setStep(3);
      return;
    }

    const scope = `development:${preview.previewFingerprint}`;
    const result = await startDevelopment({
      input,
      previewFingerprint: preview.previewFingerprint,
      idempotencyKey: pendingKeys.current.get(scope),
    });
    if (result.error) {
      setSubmitError(result.error.message);
      toast({
        title: 'DEVELOPMENT 启动失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    const run = result.data?.startStockSelectionDevelopmentTraining;
    if (!run) {
      const message = '服务端未返回 DEVELOPMENT 运行。';
      setSubmitError(message);
      toast({
        title: 'DEVELOPMENT 启动失败',
        description: message,
        variant: 'destructive',
      });
      return;
    }
    pendingKeys.current.clear(scope);
    setSubmitError(null);
    toast({ title: 'DEVELOPMENT 已进入队列', variant: 'success' });
    onCreated(run.runId);
  };

  const stepCanNavigate = (target: number) => {
    if (target <= step) return true;
    if (target === 1) return Boolean(datasetVersion);
    return target <= 3 && Boolean(datasetVersion);
  };

  return (
    <section className="space-y-3" aria-label="新建次日概率模型训练">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Cpu className="h-5 w-5 text-blue-300" />
            <h2 className="text-ui-page-title font-black text-slate-100">
              新建模型训练
            </h2>
          </div>
          <p className="mt-1 text-ui-caption text-slate-500">
            创建一次 DEVELOPMENT，完成后由人工审阅证据并决定是否执行 FINAL。
          </p>
        </div>
        <span className="text-ui-micro text-slate-600">
          仅桌面 · 训练坐标服务端最终校验
        </span>
      </div>

      <nav aria-label="训练步骤">
        <ol className="flex flex-wrap items-center gap-1">
          {WIZARD_STEPS.map((label, index) => (
            <li key={label} className="flex items-center gap-1">
              <button
                type="button"
                aria-current={step === index ? 'step' : undefined}
                disabled={!stepCanNavigate(index)}
                onClick={() => setStep(index)}
                className={cn(
                  'inline-flex items-center gap-2 rounded-control px-2.5 py-1.5 text-ui-caption font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
                  step === index
                    ? 'bg-blue-500/15 text-blue-200 ring-1 ring-blue-400/40'
                    : 'text-slate-500 hover:bg-white/[0.04] hover:text-slate-300'
                )}
              >
                <span
                  className={cn(
                    'flex h-5 w-5 items-center justify-center rounded-full border font-mono text-ui-micro',
                    step === index
                      ? 'border-blue-300 bg-blue-500 text-white'
                      : 'border-slate-600 text-slate-500'
                  )}
                >
                  {index + 1}
                </span>
                {label}
              </button>
              {index < WIZARD_STEPS.length - 1 && (
                <ChevronRight
                  className="h-3.5 w-3.5 text-slate-700"
                  aria-hidden="true"
                />
              )}
            </li>
          ))}
        </ol>
      </nav>

      <DatasetState
        fetching={datasetsState.fetching}
        error={datasetsState.error}
        datasets={certifiedDatasets}
        onRetry={() => void datasetsState.refresh()}
      />
      {validationErrors.length > 0 && (
        <div
          role="alert"
          aria-live="polite"
          data-testid="training-validation-errors"
          className="rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          <div className="font-bold">请修正以下字段后继续</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {validationErrors.map(error => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </div>
      )}
      {submitError && (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          DEVELOPMENT 提交失败：{submitError}
        </div>
      )}

      <div className="rounded-panel border border-white/[0.08] bg-[#081321] p-3">
        {step === 0 && (
          <div className="space-y-3" data-testid="training-step-dataset">
            <div className="flex items-center gap-2 text-ui-body font-bold text-slate-100">
              <Database className="h-4 w-4 text-blue-300" />1 · 选择认证数据集
            </div>
            <label
              htmlFor="training-dataset"
              className="block text-ui-caption text-slate-400"
            >
              数据集
              <NativeSelect
                id="training-dataset"
                aria-label="训练数据集"
                value={datasetVersion}
                onChange={event => updateDataset(event.target.value)}
                className="mt-2 font-mono text-ui-caption"
              >
                <option value="">选择 CERTIFIED 数据集</option>
                {certifiedDatasets.map(dataset => (
                  <option
                    key={dataset.datasetVersion}
                    value={dataset.datasetVersion}
                  >
                    {dataset.datasetVersion} · {dataset.dateStart} →{' '}
                    {dataset.dateEnd}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <label
                htmlFor="training-universe-kind"
                className="text-ui-caption text-slate-400"
              >
                股票范围
                <NativeSelect
                  id="training-universe-kind"
                  aria-label="训练股票范围"
                  value={universe.kind}
                  onChange={event => {
                    if (isUniverseKind(event.target.value))
                      updateUniverse({ ...universe, kind: event.target.value });
                  }}
                  className="mt-2 font-mono text-ui-caption"
                >
                  <option
                    value={StockSelectionTrainingUniverseKind.OrdinaryAShare}
                  >
                    普通沪深 A 股
                  </option>
                  <option
                    value={StockSelectionTrainingUniverseKind.CertifiedIndex}
                  >
                    经认证指数成分
                  </option>
                  <option value={StockSelectionTrainingUniverseKind.Explicit}>
                    显式股票列表
                  </option>
                </NativeSelect>
              </label>
              {universe.kind ===
                StockSelectionTrainingUniverseKind.CertifiedIndex && (
                <label
                  htmlFor="training-index-code"
                  className="text-ui-caption text-slate-400"
                >
                  indexCode
                  <Input
                    id="training-index-code"
                    aria-label="认证指数代码"
                    value={universe.indexCode}
                    onChange={event =>
                      updateUniverse({
                        ...universe,
                        indexCode: event.target.value,
                      })
                    }
                    placeholder="000300.SH"
                    className="mt-2 font-mono text-ui-caption"
                  />
                </label>
              )}
              {universe.kind ===
                StockSelectionTrainingUniverseKind.Explicit && (
                <label
                  htmlFor="training-stock-codes"
                  className="text-ui-caption text-slate-400 md:col-span-2"
                >
                  显式股票代码（逗号或换行分隔）
                  <Textarea
                    id="training-stock-codes"
                    aria-label="显式股票代码"
                    value={universe.stockCodes}
                    onChange={event =>
                      updateUniverse({
                        ...universe,
                        stockCodes: event.target.value,
                      })
                    }
                    placeholder="600000.SH\n000001.SZ"
                    className="mt-2 min-h-20 font-mono text-ui-micro"
                  />
                </label>
              )}
              <label
                htmlFor="training-benchmark"
                className="text-ui-caption text-slate-400"
              >
                benchmark
                <Input
                  id="training-benchmark"
                  aria-label="训练基准指数"
                  value={universe.benchmarkCode}
                  onChange={event =>
                    updateUniverse({
                      ...universe,
                      benchmarkCode: event.target.value,
                    })
                  }
                  placeholder="000300.SH"
                  className="mt-2 font-mono text-ui-caption"
                />
              </label>
              <label
                htmlFor="training-minimum-listing-days"
                className="text-ui-caption text-slate-400"
              >
                minimumListingDays
                <Input
                  id="training-minimum-listing-days"
                  aria-label="最少上市交易日"
                  type="number"
                  min={0}
                  value={universe.minimumListingDays}
                  onChange={event =>
                    updateUniverse({
                      ...universe,
                      minimumListingDays: Number(event.target.value),
                    })
                  }
                  className="mt-2 font-mono text-ui-caption"
                />
              </label>
            </div>
            {selectedDataset && (
              <p className="text-ui-micro text-slate-600">
                已从认证数据集 universeSpec
                填入默认股票范围；修改后服务端会重新校验认证边界。
              </p>
            )}
          </div>
        )}

        {step === 1 && (
          <div className="space-y-3" data-testid="training-step-time">
            <div className="flex items-center gap-2 text-ui-body font-bold text-slate-100">
              <CalendarClock className="h-4 w-4 text-blue-300" />2 · 时间切分
            </div>
            <p className="text-ui-caption text-slate-500">
              日期可留空以使用认证数据集总区间；填写时必须落在该数据集边界内。
            </p>
            <div className="grid gap-3 md:grid-cols-2">
              <label
                htmlFor="training-date-start"
                className="text-ui-caption text-slate-400"
              >
                起始日期
                <Input
                  id="training-date-start"
                  aria-label="训练起始日期"
                  type="date"
                  min={selectedDataset?.dateStart}
                  max={selectedDataset?.dateEnd}
                  value={dateStart}
                  onChange={event => updateDateStart(event.target.value)}
                  className="mt-2 font-mono text-ui-caption"
                />
              </label>
              <label
                htmlFor="training-date-end"
                className="text-ui-caption text-slate-400"
              >
                结束日期
                <Input
                  id="training-date-end"
                  aria-label="训练结束日期"
                  type="date"
                  min={selectedDataset?.dateStart}
                  max={selectedDataset?.dateEnd}
                  value={dateEnd}
                  onChange={event => updateDateEnd(event.target.value)}
                  className="mt-2 font-mono text-ui-caption"
                />
              </label>
            </div>
            <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2 text-ui-caption text-slate-500">
              服务端固定时间结构：{FIXED_TIME_STRUCTURE}
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3" data-testid="training-step-resources">
            <div className="flex items-center gap-2 text-ui-body font-bold text-slate-100">
              <ServerCog className="h-4 w-4 text-blue-300" />3 · 模型与资源
            </div>
            <CapabilitySummary
              data={capabilities.data}
              fetching={capabilities.fetching}
              error={capabilities.error}
              onRetry={() => void capabilities.refresh()}
            />
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <fieldset className="md:col-span-2">
                <legend className="text-ui-caption text-slate-400">
                  请求后端
                </legend>
                <div
                  className="mt-2 flex flex-wrap gap-2"
                  role="radiogroup"
                  aria-label="请求后端"
                >
                  {Object.values(StockSelectionTrainingBackend).map(value => (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={backend === value}
                      onClick={() => updateBackend(value)}
                      className={cn(
                        'rounded-control border px-3 py-2 font-mono text-ui-caption transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                        backend === value
                          ? 'border-blue-400/50 bg-blue-400/10 text-blue-200'
                          : 'border-white/10 text-slate-500 hover:border-blue-400/30 hover:text-slate-300'
                      )}
                    >
                      {value}
                    </button>
                  ))}
                </div>
              </fieldset>
              <label
                htmlFor="training-bootstrap"
                className="text-ui-caption text-slate-400"
              >
                bootstrap
                <Input
                  id="training-bootstrap"
                  aria-label="Bootstrap 样本数"
                  type="number"
                  min={100}
                  max={20000}
                  value={bootstrapSamples}
                  onChange={event =>
                    updateBootstrap(Number(event.target.value))
                  }
                  className="mt-2 font-mono text-ui-caption"
                />
              </label>
              <label
                htmlFor="training-seed"
                className="text-ui-caption text-slate-400"
              >
                seed
                <Input
                  id="training-seed"
                  aria-label="随机种子"
                  type="number"
                  value={randomSeed}
                  onChange={event => updateSeed(Number(event.target.value))}
                  className="mt-2 font-mono text-ui-caption"
                />
              </label>
              <label
                htmlFor="training-batch"
                className="text-ui-caption text-slate-400"
              >
                batch
                <Input
                  id="training-batch"
                  aria-label="Worker batch 大小"
                  type="number"
                  min={1}
                  max={1000}
                  value={workerBatchSize}
                  onChange={event => updateBatch(Number(event.target.value))}
                  className="mt-2 font-mono text-ui-caption"
                />
              </label>
              <label
                htmlFor="training-note"
                className="text-ui-caption text-slate-400 md:col-span-2"
              >
                备注（可选）
                <Textarea
                  id="training-note"
                  aria-label="训练备注"
                  maxLength={500}
                  value={note}
                  onChange={event => updateNote(event.target.value)}
                  className="mt-2 min-h-20 text-ui-micro"
                />
              </label>
            </div>
            <div className="flex items-start gap-2 rounded-control border border-white/[0.06] bg-white/[0.02] p-2 text-ui-caption text-slate-500">
              <Cpu className="mt-0.5 h-4 w-4 shrink-0 text-blue-300" />
              模型族（{MODEL_FAMILY}
              ）、超参数和时间切分由系统固定；这里仅请求后端与资源估算参数进入预检坐标。
            </div>
          </div>
        )}

        {step === 3 && (
          <div data-testid="training-step-preflight">
            <div className="mb-3 flex items-center gap-2 text-ui-body font-bold text-slate-100">
              <ShieldCheck className="h-4 w-4 text-blue-300" />4 · 预检确认
            </div>
            <div className="grid gap-3 xl:grid-cols-2">
              <FrozenCoordinate
                input={input}
                dataset={selectedDataset}
                preview={currentPreview}
              />
              <PreviewResultPanel
                preview={currentPreview}
                previewError={previewError}
                previewFetching={previewFetching}
                canSubmit={canSubmit}
                dataset={selectedDataset}
                input={input}
                onPreview={requestPreview}
              />
            </div>
            <div className="mt-3 flex items-start gap-2 rounded-control border border-white/[0.06] bg-white/[0.02] p-2 text-ui-caption text-slate-500">
              <LockKeyhole className="mt-0.5 h-4 w-4 shrink-0" />
              提交后训练坐标和实际后端不可变；DEVELOPMENT 成功不等于 FINAL
              评估或模型登记成功。
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 rounded-panel border border-white/[0.08] bg-[#081321] p-3">
        <Button
          size="sm"
          variant="outline"
          disabled={step === 0}
          onClick={() => setStep(value => Math.max(0, value - 1))}
        >
          <ChevronLeft className="mr-1 h-3.5 w-3.5" />
          上一步
        </Button>
        {step < WIZARD_STEPS.length - 1 ? (
          <Button
            size="sm"
            onClick={() =>
              setStep(value => Math.min(WIZARD_STEPS.length - 1, value + 1))
            }
            disabled={!datasetVersion}
          >
            下一步
            <ChevronRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        ) : (
          <Button
            size="lg"
            disabled={!canSubmit || startResult.fetching}
            onClick={() => void submitDevelopment()}
          >
            <Play className="mr-1.5 h-3.5 w-3.5" />
            提交 DEVELOPMENT
          </Button>
        )}
      </div>
    </section>
  );
}
