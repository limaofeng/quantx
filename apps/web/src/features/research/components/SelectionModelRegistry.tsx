import {
  CheckCircle2,
  FlaskConical,
  RefreshCw,
  ShieldAlert,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { useMutation, useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  RegisterStockSelectionModelDocument,
  SetStockSelectionModelStageDocument,
  StockSelectionModelsDocument,
  StockSelectionModelStage,
  type StockSelectionModelsQuery,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

type Model = StockSelectionModelsQuery['stockSelectionModels'][number];

const STAGE_LABELS: Record<StockSelectionModelStage, string> = {
  [StockSelectionModelStage.Candidate]: '候选',
  [StockSelectionModelStage.Shadow]: '影子',
  [StockSelectionModelStage.Active]: '已激活',
  [StockSelectionModelStage.Suspended]: '已暂停',
  [StockSelectionModelStage.Retired]: '已退役',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function numberAt(root: unknown, ...path: string[]): number | null {
  let current: unknown = root;
  for (const segment of path) {
    if (!isRecord(current)) return null;
    current = current[segment];
  }
  return typeof current === 'number' && Number.isFinite(current)
    ? current
    : null;
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

function metric(value: number | null, percent = false) {
  if (value == null) return '--';
  return percent ? `${(value * 100).toFixed(2)}%` : value.toFixed(4);
}

function ModelCard({
  busy,
  model,
  onStage,
}: {
  busy: boolean;
  model: Model;
  onStage: (model: Model, stage: StockSelectionModelStage) => void;
}) {
  const brierSkill = numberAt(
    model.metrics,
    'frozen_test',
    'probability',
    'brier_skill'
  );
  const ece = numberAt(model.metrics, 'frozen_test', 'probability', 'ece');
  const top20Lower = numberAt(
    model.metrics,
    'frozen_test',
    'ranking',
    'top_20',
    'up_rate_lift_ci_low'
  );
  const eligible = model.effectGatePassed && model.historicalUniverseComplete;
  return (
    <article className="w-96 shrink-0 rounded-md border border-white/[0.08] bg-white/[0.02] p-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={cn(
                'rounded border px-1.5 py-0.5 font-mono text-ui-micro font-bold',
                model.stage === StockSelectionModelStage.Active
                  ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200'
                  : model.stage === StockSelectionModelStage.Shadow
                    ? 'border-amber-400/30 bg-amber-400/10 text-amber-200'
                    : 'border-white/10 text-slate-400'
              )}
            >
              {model.stage}
            </span>
            <span className="text-ui-label font-bold text-slate-200">
              {model.selectedFamily}
            </span>
            {eligible ? (
              <CheckCircle2 className="h-3.5 w-3.5 text-emerald-300" />
            ) : (
              <ShieldAlert className="h-3.5 w-3.5 text-amber-300" />
            )}
          </div>
          <p
            className="mt-1 truncate font-mono text-ui-caption text-slate-400"
            title={`${model.modelVersion}\nmanifest ${model.artifactManifestSha256}`}
          >
            {model.modelVersion}
          </p>
        </div>
        <span className="font-mono text-ui-micro text-slate-600">
          状态版本 {model.stateVersion}
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-ui-caption">
        <div>
          <dt className="text-slate-500">Brier Skill</dt>
          <dd className="font-mono text-slate-200">{metric(brierSkill)}</dd>
        </div>
        <div>
          <dt className="text-slate-500">ECE</dt>
          <dd className="font-mono text-slate-200">{metric(ece, true)}</dd>
        </div>
        <div>
          <dt className="text-slate-500">Top20 CI 下界</dt>
          <dd className="font-mono text-slate-200">
            {metric(top20Lower, true)}
          </dd>
        </div>
      </dl>
      <p className="mt-2 text-ui-micro text-slate-500">
        冻结测试 {model.testStart} → {model.testEnd} · 历史股票池{' '}
        {model.historicalUniverseComplete ? '完整' : '不完整'}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {allowedTargets(model.stage).map(stage => (
          <Button
            key={stage}
            size="sm"
            variant={
              stage === StockSelectionModelStage.Active ? 'default' : 'outline'
            }
            disabled={
              busy || (stage === StockSelectionModelStage.Active && !eligible)
            }
            title={
              stage === StockSelectionModelStage.Active && !eligible
                ? '需同时通过效果门禁并具备完整历史 ST/行业/退市股票池'
                : undefined
            }
            onClick={() => onStage(model, stage)}
          >
            转为{STAGE_LABELS[stage]}
          </Button>
        ))}
      </div>
    </article>
  );
}

export function SelectionModelRegistry() {
  const { toast } = useToast();
  const [runKey, setRunKey] = useState('');
  const [busyVersion, setBusyVersion] = useState<string | null>(null);
  const [modelsResult, refreshModels] = useQuery({
    query: StockSelectionModelsDocument,
    requestPolicy: 'cache-and-network',
  });
  const [registerResult, register] = useMutation(
    RegisterStockSelectionModelDocument
  );
  const [, setStage] = useMutation(SetStockSelectionModelStageDocument);
  const models = useMemo(
    () => modelsResult.data?.stockSelectionModels ?? [],
    [modelsResult.data?.stockSelectionModels]
  );

  const handleRegister = async () => {
    const normalized = runKey.trim();
    if (!normalized) return;
    const result = await register({ runKey: normalized });
    if (result.error) {
      toast({
        title: '模型登记失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    setRunKey('');
    refreshModels({ requestPolicy: 'network-only' });
    toast({ title: '模型已登记为 CANDIDATE', variant: 'success' });
  };

  const handleStage = async (model: Model, stage: StockSelectionModelStage) => {
    setBusyVersion(model.modelVersion);
    const result = await setStage({
      modelVersion: model.modelVersion,
      stage,
      expectedVersion: model.stateVersion,
    });
    setBusyVersion(null);
    if (result.error) {
      toast({
        title: '模型阶段切换失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    refreshModels({ requestPolicy: 'network-only' });
    toast({
      title: `模型已切换为 ${stage}`,
      description:
        stage === StockSelectionModelStage.Active
          ? '原 ACTIVE 模型已原子转为 SUSPENDED。'
          : undefined,
      variant: 'success',
    });
  };

  return (
    <details
      open
      className="shrink-0 border-b border-white/[0.06] bg-[#08111f]"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 px-ui-section py-2 text-ui-caption font-bold text-slate-300 focus-visible:outline-blue-400">
        <FlaskConical className="h-3.5 w-3.5 text-cyan-300" />
        次日概率模型证据与人工发布
        <span className="font-normal text-slate-600">
          · 最多 1 ACTIVE / 2 SHADOW
        </span>
      </summary>
      <div className="space-y-3 border-t border-white/[0.04] p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label="研究运行标识"
            className="w-80 flex-1 font-mono"
            placeholder="粘贴成功的 next-day-selection v1 研究运行 key"
            value={runKey}
            onChange={event => setRunKey(event.target.value)}
          />
          <Button
            size="sm"
            disabled={!runKey.trim() || registerResult.fetching}
            onClick={() => void handleRegister()}
          >
            登记模型证据
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={modelsResult.fetching}
            onClick={() => refreshModels({ requestPolicy: 'network-only' })}
          >
            <RefreshCw
              className={cn(
                'mr-2 h-3.5 w-3.5',
                modelsResult.fetching &&
                  'animate-spin motion-reduce:animate-none'
              )}
            />
            刷新
          </Button>
        </div>
        {modelsResult.error && (
          <p role="alert" className="text-ui-caption text-rose-300">
            {modelsResult.error.message}
          </p>
        )}
        {!modelsResult.fetching && models.length === 0 ? (
          <p className="text-ui-caption text-slate-500">
            尚未登记模型。训练仍由本地 CLI
            手动执行；页面不会启动训练或自动切换模型。
          </p>
        ) : (
          <div className="flex max-h-72 gap-3 overflow-auto pb-1 custom-scrollbar">
            {models.map(model => (
              <ModelCard
                key={model.modelVersion}
                model={model}
                busy={busyVersion === model.modelVersion}
                onStage={(item, stage) => void handleStage(item, stage)}
              />
            ))}
          </div>
        )}
      </div>
    </details>
  );
}
