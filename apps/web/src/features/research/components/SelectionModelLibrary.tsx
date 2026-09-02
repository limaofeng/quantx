import {
  CheckCircle2,
  ChevronRight,
  LoaderCircle,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import {
  SetStockSelectionModelStageDocument,
  StockSelectionModelStage,
  StockSelectionModelsDocument,
  type StockSelectionModelsQuery,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

type Model = StockSelectionModelsQuery['stockSelectionModels'][number];
type StageFilter = 'ALL' | StockSelectionModelStage;

const STAGE_LABELS: Record<StockSelectionModelStage, string> = {
  [StockSelectionModelStage.Candidate]: 'CANDIDATE',
  [StockSelectionModelStage.Shadow]: 'SHADOW',
  [StockSelectionModelStage.Active]: 'ACTIVE',
  [StockSelectionModelStage.Suspended]: 'SUSPENDED',
  [StockSelectionModelStage.Retired]: 'RETIRED',
};

const STAGE_FILTERS: Array<{ value: StageFilter; label: string }> = [
  { value: 'ALL', label: '全部' },
  { value: StockSelectionModelStage.Candidate, label: 'CANDIDATE' },
  { value: StockSelectionModelStage.Shadow, label: 'SHADOW' },
  { value: StockSelectionModelStage.Active, label: 'ACTIVE' },
  { value: StockSelectionModelStage.Suspended, label: 'SUSPENDED' },
  { value: StockSelectionModelStage.Retired, label: 'RETIRED' },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function valueAt(root: unknown, ...paths: string[]): unknown {
  let value = root;
  for (const path of paths) {
    if (!isRecord(value)) return null;
    value = value[path];
  }
  return value;
}

function firstPresent(...values: unknown[]): unknown {
  return values.find(value => value !== null && value !== undefined) ?? null;
}

function metricAt(root: unknown, ...paths: string[]): number | null {
  const value = valueAt(root, ...paths);
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function formatMetric(value: number | null, percent = false) {
  if (value === null) return '不可用';
  return percent ? `${(value * 100).toFixed(2)}%` : value.toFixed(4);
}

function pretty(value: unknown): string {
  if (value === null || value === undefined) return '不可用';
  if (typeof value === 'number')
    return Number.isFinite(value) ? String(value) : '不可用';
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

function shortValue(value: string | null | undefined) {
  if (!value) return '不可用';
  return value.length > 28
    ? `${value.slice(0, 14)}…${value.slice(-10)}`
    : value;
}

function stageClass(stage: StockSelectionModelStage) {
  switch (stage) {
    case StockSelectionModelStage.Active:
      return 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200';
    case StockSelectionModelStage.Shadow:
      return 'border-amber-400/30 bg-amber-400/10 text-amber-200';
    case StockSelectionModelStage.Candidate:
      return 'border-blue-400/30 bg-blue-400/10 text-blue-200';
    case StockSelectionModelStage.Suspended:
      return 'border-rose-400/25 bg-rose-400/10 text-rose-200';
    default:
      return 'border-slate-500/30 bg-slate-500/10 text-slate-400';
  }
}

function allowedTargets(stage: StockSelectionModelStage) {
  switch (stage) {
    case StockSelectionModelStage.Candidate:
      return [
        StockSelectionModelStage.Shadow,
        StockSelectionModelStage.Retired,
      ];
    case StockSelectionModelStage.Shadow:
      return [
        StockSelectionModelStage.Active,
        StockSelectionModelStage.Suspended,
        StockSelectionModelStage.Retired,
      ];
    case StockSelectionModelStage.Active:
      return [StockSelectionModelStage.Suspended];
    case StockSelectionModelStage.Suspended:
      return [
        StockSelectionModelStage.Shadow,
        StockSelectionModelStage.Retired,
      ];
    default:
      return [];
  }
}

function modelBrierSkill(model: Model) {
  return firstPresent(
    metricAt(model.metrics, 'frozen_test', 'probability', 'brier_skill'),
    metricAt(model.metrics, 'frozenTest', 'probability', 'brierSkill'),
    metricAt(model.metrics, 'brier_skill'),
    metricAt(model.metrics, 'brierSkill')
  ) as number | null;
}

function modelEce(model: Model) {
  return firstPresent(
    metricAt(model.metrics, 'frozen_test', 'probability', 'ece'),
    metricAt(model.metrics, 'frozenTest', 'probability', 'ece'),
    metricAt(model.metrics, 'ece')
  ) as number | null;
}

function modelTop20Lower(model: Model) {
  return firstPresent(
    metricAt(
      model.metrics,
      'frozen_test',
      'ranking',
      'top_20',
      'up_rate_lift_ci_low'
    ),
    metricAt(
      model.metrics,
      'frozenTest',
      'ranking',
      'top20',
      'upRateLiftCiLow'
    ),
    metricAt(model.metrics, 'top20LowerBound')
  ) as number | null;
}

function GateStatus({ model }: { model: Model }) {
  const passed = model.effectGatePassed && model.historicalUniverseComplete;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 text-ui-micro font-bold',
        passed ? 'text-emerald-300' : 'text-amber-200'
      )}
    >
      {passed ? (
        <CheckCircle2 className="h-3.5 w-3.5" />
      ) : (
        <ShieldAlert className="h-3.5 w-3.5" />
      )}
      {passed ? '效果通过 · 历史完整' : '门禁未完整通过'}
    </span>
  );
}

function ModelDetail({
  model,
  busy,
  onStage,
}: {
  model: Model;
  busy: boolean;
  onStage: (model: Model, stage: StockSelectionModelStage) => void;
}) {
  const eligibleForActive =
    model.effectGatePassed && model.historicalUniverseComplete;
  return (
    <aside
      className="min-w-0 rounded-panel border border-white/[0.08] bg-[#0a1525] p-3"
      aria-labelledby="selection-model-detail-title"
      data-testid="selection-model-detail"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                'rounded-control border px-1.5 py-0.5 font-mono text-ui-micro font-bold',
                stageClass(model.stage)
              )}
            >
              {STAGE_LABELS[model.stage]}
            </span>
            <span className="font-mono text-ui-micro text-slate-600">
              stateVersion {model.stateVersion}
            </span>
          </div>
          <h3
            id="selection-model-detail-title"
            className="mt-2 truncate text-ui-body font-bold text-slate-100"
            title={model.modelVersion}
          >
            {model.modelVersion}
          </h3>
          <p className="mt-1 text-ui-caption text-slate-500">
            {model.selectedFamily}
          </p>
        </div>
        <ChevronRight
          className="h-4 w-4 shrink-0 text-slate-600"
          aria-hidden="true"
        />
      </div>

      <dl className="mt-3 grid gap-2 text-ui-caption md:grid-cols-2">
        <div>
          <dt className="text-slate-600">runKey（只读）</dt>
          <dd
            className="break-all font-mono text-slate-200"
            title={model.runKey}
          >
            {shortValue(model.runKey)}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">manifest</dt>
          <dd
            className="break-all font-mono text-slate-200"
            title={model.artifactManifestSha256}
          >
            {shortValue(model.artifactManifestSha256)}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">indicator / factor</dt>
          <dd className="font-mono text-slate-200">
            {model.indicatorVersion} / {model.factorSetVersion}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">label / calibrator</dt>
          <dd className="font-mono text-slate-200">
            {model.labelVersion} / {model.calibratorVersion}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">训练区间</dt>
          <dd className="font-mono text-slate-200">
            {model.trainingStart} → {model.trainingEnd}
          </dd>
        </div>
        <div>
          <dt className="text-slate-600">冻结区间</dt>
          <dd className="font-mono text-slate-200">
            {model.testStart} → {model.testEnd}
          </dd>
        </div>
      </dl>

      <div className="mt-3 grid gap-2 sm:grid-cols-3">
        <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
          <div className="text-ui-micro text-slate-600">Brier Skill</div>
          <div className="mt-1 font-mono text-ui-caption text-slate-200">
            {formatMetric(modelBrierSkill(model))}
          </div>
        </div>
        <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
          <div className="text-ui-micro text-slate-600">ECE</div>
          <div className="mt-1 font-mono text-ui-caption text-slate-200">
            {formatMetric(modelEce(model), true)}
          </div>
        </div>
        <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
          <div className="text-ui-micro text-slate-600">Top20 CI 下界</div>
          <div className="mt-1 font-mono text-ui-caption text-slate-200">
            {formatMetric(modelTop20Lower(model), true)}
          </div>
        </div>
      </div>

      <div className="mt-3 grid gap-2 text-ui-caption">
        <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
          <div className="font-bold text-slate-300">效果门禁</div>
          <div className="mt-1 text-slate-400">
            {model.effectGatePassed ? '通过' : '未通过'} · {pretty(model.gates)}
          </div>
        </div>
        <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
          <div className="font-bold text-slate-300">历史门禁</div>
          <div className="mt-1 text-slate-400">
            {model.historicalUniverseComplete ? '完整' : '不完整'} · ST / 行业 /
            退市历史证据
          </div>
        </div>
        <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2">
          <div className="font-bold text-slate-300">FINAL 证据</div>
          <div className="mt-1 text-slate-400">{pretty(model.evidence)}</div>
        </div>
      </div>

      <dl className="mt-3 grid gap-2 text-ui-micro text-slate-500 sm:grid-cols-2">
        <div>
          <dt>approvedBy</dt>
          <dd className="font-mono text-slate-300">
            {model.approvedBy || '不可用'}
          </dd>
        </div>
        <div>
          <dt>approvedAt</dt>
          <dd className="font-mono text-slate-300">
            {model.approvedAt || '不可用'}
          </dd>
        </div>
        <div>
          <dt>stateVersion</dt>
          <dd className="font-mono text-slate-300">{model.stateVersion}</dd>
        </div>
        <div>
          <dt>createdAt</dt>
          <dd className="font-mono text-slate-300">
            {model.createdAt || '不可用'}
          </dd>
        </div>
        <div>
          <dt>updatedAt</dt>
          <dd className="font-mono text-slate-300">
            {model.updatedAt || '不可用'}
          </dd>
        </div>
      </dl>

      <div className="mt-3">
        <h4 className="text-ui-caption font-bold text-slate-300">
          允许的阶段动作
        </h4>
        <div className="mt-2 flex flex-wrap gap-2">
          {allowedTargets(model.stage).length === 0 ? (
            <span className="text-ui-micro text-slate-600">
              当前阶段没有可用动作。
            </span>
          ) : (
            allowedTargets(model.stage).map(stage => (
              <Button
                key={stage}
                size="sm"
                variant={
                  stage === StockSelectionModelStage.Active
                    ? 'default'
                    : 'outline'
                }
                disabled={
                  busy ||
                  (stage === StockSelectionModelStage.Active &&
                    !eligibleForActive)
                }
                title={
                  stage === StockSelectionModelStage.Active &&
                  !eligibleForActive
                    ? '需要效果门禁通过且历史股票池完整'
                    : undefined
                }
                onClick={() => onStage(model, stage)}
              >
                转为 {STAGE_LABELS[stage]}
              </Button>
            ))
          )}
        </div>
      </div>
    </aside>
  );
}

export function SelectionModelLibrary() {
  const { toast } = useToast();
  const [stageFilter, setStageFilter] = useState<StageFilter>('ALL');
  const [selectedVersion, setSelectedVersion] = useState<string | null>(null);
  const [busyVersion, setBusyVersion] = useState<string | null>(null);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [modelsResult, refreshModels] = useQuery({
    query: StockSelectionModelsDocument,
    requestPolicy: 'cache-and-network',
  });
  const [, setStage] = useMutation(SetStockSelectionModelStageDocument);
  const models = useMemo(
    () => modelsResult.data?.stockSelectionModels ?? [],
    [modelsResult.data?.stockSelectionModels]
  );
  const filteredModels = useMemo(
    () =>
      stageFilter === 'ALL'
        ? models
        : models.filter(model => model.stage === stageFilter),
    [models, stageFilter]
  );
  const selectedModel = useMemo(
    () =>
      filteredModels.find(model => model.modelVersion === selectedVersion) ??
      null,
    [filteredModels, selectedVersion]
  );

  useEffect(() => {
    setSelectedVersion(current => {
      if (
        current &&
        filteredModels.some(model => model.modelVersion === current)
      )
        return current;
      return filteredModels[0]?.modelVersion ?? null;
    });
  }, [filteredModels]);

  useEffect(() => {
    if (modelsResult.error) {
      toast({
        title: '模型库读取失败',
        description: modelsResult.error.message,
        variant: 'destructive',
      });
    }
  }, [modelsResult.error, toast]);

  const handleStage = async (model: Model, stage: StockSelectionModelStage) => {
    setBusyVersion(model.modelVersion);
    setOperationError(null);
    const result = await setStage({
      modelVersion: model.modelVersion,
      stage,
      expectedVersion: model.stateVersion,
    });
    setBusyVersion(null);
    if (result.error) {
      setOperationError(result.error.message);
      toast({
        title: '模型阶段切换失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    toast({ title: `模型已切换为 ${STAGE_LABELS[stage]}`, variant: 'success' });
    void refreshModels({ requestPolicy: 'network-only' });
  };

  return (
    <section className="space-y-3" aria-label="次日概率模型库">
      <div
        className="flex flex-wrap items-center gap-1"
        role="group"
        aria-label="模型阶段筛选"
      >
        {STAGE_FILTERS.map(filter => (
          <Button
            key={filter.value}
            type="button"
            size="sm"
            variant={stageFilter === filter.value ? 'default' : 'outline'}
            aria-pressed={stageFilter === filter.value}
            onClick={() => setStageFilter(filter.value)}
          >
            {filter.label}
          </Button>
        ))}
        <div className="ml-auto flex items-center gap-2">
          <div className="rounded-control border border-white/[0.06] bg-white/[0.02] px-2 py-1 text-ui-micro text-slate-500">
            最多 1 ACTIVE / 2 SHADOW
          </div>
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={modelsResult.fetching}
            onClick={() =>
              void refreshModels({ requestPolicy: 'network-only' })
            }
          >
            <RefreshCw
              className={cn(
                'mr-1.5 h-3.5 w-3.5',
                modelsResult.fetching &&
                  'animate-spin motion-reduce:animate-none'
              )}
            />
            刷新
          </Button>
        </div>
      </div>

      {modelsResult.error && (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          模型库读取失败：{modelsResult.error.message}
        </div>
      )}
      {operationError && (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          阶段操作失败：{operationError}
        </div>
      )}

      {modelsResult.fetching && models.length === 0 ? (
        <div
          role="status"
          aria-live="polite"
          className="flex min-h-40 items-center justify-center gap-2 rounded-panel border border-white/[0.08] bg-[#081321] text-ui-caption text-slate-500"
        >
          <LoaderCircle className="h-4 w-4 animate-spin text-blue-400 motion-reduce:animate-none" />
          正在读取模型库…
        </div>
      ) : filteredModels.length === 0 ? (
        <div className="flex min-h-40 items-center justify-center rounded-panel border border-dashed border-white/[0.08] bg-[#081321] p-ui-panel text-center text-ui-caption text-slate-500">
          {models.length === 0
            ? '尚未登记具备 FINAL 证据的模型。请从成功的 FINAL 运行详情执行登记。'
            : '当前筛选没有模型。'}
        </div>
      ) : (
        <div className="grid min-w-0 gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(22rem,0.65fr)]">
          <div className="min-w-0 overflow-x-auto rounded-panel border border-white/[0.08] bg-[#081321]">
            <table className="w-full min-w-[760px] text-left text-ui-caption">
              <caption className="sr-only">模型版本与阶段</caption>
              <thead className="border-b border-white/[0.06] bg-[#0b1a2b] text-ui-micro text-slate-500">
                <tr>
                  <th className="px-3 py-2">版本</th>
                  <th className="px-3 py-2">阶段</th>
                  <th className="px-3 py-2">家族</th>
                  <th className="px-3 py-2">冻结区间</th>
                  <th className="px-3 py-2">门禁</th>
                  <th className="px-3 py-2">更新时间</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.05]">
                {filteredModels.map(model => (
                  <tr
                    key={model.modelVersion}
                    tabIndex={0}
                    aria-selected={selectedVersion === model.modelVersion}
                    className={cn(
                      'cursor-pointer transition-colors hover:bg-blue-400/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                      selectedVersion === model.modelVersion &&
                        'bg-blue-400/[0.09]'
                    )}
                    onClick={() => setSelectedVersion(model.modelVersion)}
                    onKeyDown={event => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        setSelectedVersion(model.modelVersion);
                      }
                    }}
                  >
                    <td
                      className="max-w-48 truncate px-3 py-2 font-mono text-slate-200"
                      title={model.modelVersion}
                    >
                      {model.modelVersion}
                    </td>
                    <td className="px-3 py-2">
                      <span
                        className={cn(
                          'rounded-control border px-1.5 py-0.5 font-mono text-ui-micro font-bold',
                          stageClass(model.stage)
                        )}
                      >
                        {STAGE_LABELS[model.stage]}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-slate-300">
                      {model.selectedFamily}
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-slate-400">
                      {model.testStart} → {model.testEnd}
                    </td>
                    <td className="px-3 py-2">
                      <GateStatus model={model} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-slate-500">
                      {model.updatedAt}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {selectedModel ? (
            <ModelDetail
              model={selectedModel}
              busy={busyVersion === selectedModel.modelVersion}
              onStage={(selected, stage) => void handleStage(selected, stage)}
            />
          ) : (
            <div className="flex min-h-40 items-center justify-center rounded-panel border border-dashed border-white/[0.08] text-ui-caption text-slate-600">
              选择模型查看冻结证据与审计信息。
            </div>
          )}
        </div>
      )}
    </section>
  );
}
