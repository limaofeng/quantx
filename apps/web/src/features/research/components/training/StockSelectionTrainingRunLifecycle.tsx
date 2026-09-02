import {
  CheckCircle2,
  CircleDot,
  GitCompareArrows,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation } from 'urql';

import { Button } from '@/components/ui/button';
import {
  CancelStockSelectionTrainingRunDocument,
  RegisterStockSelectionModelDocument,
  StartStockSelectionFinalEvaluationDocument,
  StockSelectionTrainingRunKind,
  StockSelectionTrainingRunStatus,
  type StockSelectionTrainingRun,
} from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import {
  useStockSelectionTrainingComparison,
  useStockSelectionTrainingRun,
  useStockSelectionTrainingRuns,
} from '../../hooks';
import { createPendingIdempotencyKeys } from '../../idempotency';

import {
  PHASES,
  TERMINAL_STATUSES,
  evidenceValue,
  firstPresent,
  isSha256,
  phaseLabel,
  pretty,
  runStatusClass,
  shortHash,
} from './utils';

function RunStatus({ run }: { run: StockSelectionTrainingRun }) {
  const active =
    run.status === StockSelectionTrainingRunStatus.Running ||
    run.status === StockSelectionTrainingRunStatus.Queued;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 font-mono text-ui-micro font-bold',
        runStatusClass(run.status)
      )}
    >
      {active ? (
        <LoaderCircle className="h-3 w-3 animate-spin motion-reduce:animate-none" />
      ) : run.status === StockSelectionTrainingRunStatus.Succeeded ? (
        <CheckCircle2 className="h-3 w-3" />
      ) : (
        <CircleDot className="h-3 w-3" />
      )}
      {run.status}
    </span>
  );
}

function EvidenceCard({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0 rounded-control border border-white/[0.06] bg-white/[0.02] p-2 text-ui-micro">
      <div className="text-slate-600">{label}</div>
      <pre className="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words font-mono text-slate-300">
        {pretty(value)}
      </pre>
    </div>
  );
}

function RunComparison({
  currentRun,
  candidates,
  selectedIds,
  onSelected,
  comparison,
  error,
  fetching,
}: {
  currentRun: StockSelectionTrainingRun;
  candidates: StockSelectionTrainingRun[];
  selectedIds: string[];
  onSelected: (runId: string, checked: boolean) => void;
  comparison: ReturnType<
    typeof useStockSelectionTrainingComparison
  >['comparison'];
  error?: { message: string };
  fetching: boolean;
}) {
  return (
    <section
      className="rounded-panel border border-white/[0.08] bg-[#081321] p-3"
      aria-labelledby="training-comparison-title"
    >
      <div className="flex items-center gap-2">
        <GitCompareArrows className="h-4 w-4 text-blue-300" />
        <h3
          id="training-comparison-title"
          className="text-ui-body font-bold text-slate-100"
        >
          同坐标 FINAL 对比
        </h3>
        <span className="text-ui-micro text-slate-600">
          当前运行 + 最多 4 个
        </span>
      </div>
      {candidates.length === 0 ? (
        <p className="mt-2 text-ui-caption text-slate-500">
          当前运行没有可比较的同坐标 FINAL 运行，或 coordinateHash 不可用。
        </p>
      ) : (
        <div
          className="mt-3 flex flex-wrap gap-2"
          role="group"
          aria-label="同坐标 FINAL 运行"
        >
          {candidates.map(candidate => (
            <label
              key={candidate.runId}
              className={cn(
                'flex max-w-full items-center gap-1.5 rounded-control border px-2 py-1.5 text-ui-micro transition-colors',
                selectedIds.includes(candidate.runId)
                  ? 'border-blue-400/40 bg-blue-400/10 text-blue-100'
                  : 'border-white/10 text-slate-300'
              )}
            >
              <input
                type="checkbox"
                checked={selectedIds.includes(candidate.runId)}
                disabled={
                  !selectedIds.includes(candidate.runId) &&
                  selectedIds.length >= 5
                }
                onChange={event =>
                  onSelected(candidate.runId, event.target.checked)
                }
              />
              <span
                className="max-w-56 truncate font-mono"
                title={candidate.runId}
              >
                {candidate.runId}
              </span>
              {candidate.runId === currentRun.runId && (
                <span className="text-blue-300">（当前）</span>
              )}
            </label>
          ))}
        </div>
      )}
      {selectedIds.length < 2 && candidates.length > 0 && (
        <p className="mt-2 text-ui-micro text-slate-600">
          至少再选择一个运行后请求比较。
        </p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-ui-caption text-rose-200">
          比较失败：{error.message}
        </p>
      )}
      {fetching && (
        <p
          role="status"
          aria-live="polite"
          className="mt-2 text-ui-caption text-slate-500"
        >
          正在比较同坐标证据…
        </p>
      )}
      {comparison && !comparison.comparable && (
        <div
          role="alert"
          className="mt-3 rounded-control border border-amber-400/25 bg-amber-400/5 p-2 text-ui-caption text-amber-100"
        >
          <div className="font-bold">不可比</div>
          <p className="mt-1">
            {comparison.mismatchFields.join('、') ||
              pretty(comparison.mismatchedFields) ||
              '坐标、数据集或实验身份不一致。'}
          </p>
        </div>
      )}
      {comparison?.comparable && (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          <EvidenceCard label="比较指标" value={comparison.metrics} />
          <EvidenceCard label="比较门禁" value={comparison.gates} />
        </div>
      )}
    </section>
  );
}

function RunDetailContent({
  run,
  onRefresh,
  onCancel,
  onFinal,
  onRegister,
  busy,
}: {
  run: StockSelectionTrainingRun;
  onRefresh: () => void;
  onCancel: () => void;
  onFinal: () => void;
  onRegister: () => void;
  busy: boolean;
}) {
  const canCancel = !TERMINAL_STATUSES.has(run.status);
  const canFinal =
    run.runKind === StockSelectionTrainingRunKind.Development &&
    run.status === StockSelectionTrainingRunStatus.Succeeded &&
    Boolean(run.runKey?.trim()) &&
    isSha256(run.artifactManifestSha256);
  const canRegister =
    run.runKind === StockSelectionTrainingRunKind.FinalEvaluation &&
    run.status === StockSelectionTrainingRunStatus.Succeeded &&
    run.registerable &&
    Boolean(run.runKey?.trim());
  const phaseIndex = PHASES.indexOf(run.phase as (typeof PHASES)[number]);
  const progress =
    run.totalUnits > 0
      ? Math.min(100, Math.max(0, (run.completedUnits / run.totalUnits) * 100))
      : null;
  const metrics = run.metricsSummary;

  return (
    <section className="space-y-3" aria-labelledby="training-run-detail-title">
      <section className="rounded-panel border border-white/[0.08] bg-[#081321] p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <RunStatus run={run} />
              <span className="rounded-control border border-white/10 px-1.5 py-0.5 font-mono text-ui-micro text-slate-400">
                {run.runKind}
              </span>
              <span className="font-mono text-ui-micro text-slate-600">
                stateVersion {run.stateVersion}
              </span>
            </div>
            <h2
              id="training-run-detail-title"
              className="mt-1 truncate font-mono text-ui-body font-bold text-slate-100"
              title={run.runId}
            >
              {run.runId}
            </h2>
            <p className="mt-1 break-all font-mono text-ui-micro text-slate-500">
              runKey：{shortHash(run.runKey)}
            </p>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={onRefresh}
            aria-label="刷新运行详情"
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            刷新
          </Button>
        </div>
        <dl className="mt-3 grid gap-2 text-ui-caption md:grid-cols-2 xl:grid-cols-4">
          <div>
            <dt className="text-slate-600">数据集</dt>
            <dd className="font-mono text-slate-200">
              {run.datasetVersion || '不可用'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">请求 / 实际后端</dt>
            <dd className="font-mono text-slate-200">
              {run.requestedBackend || '不可用'} /{' '}
              {run.resolvedBackend || '不可用'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">requestedAt</dt>
            <dd className="font-mono text-slate-200">
              {run.requestedAt || '不可用'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">startedAt / completedAt</dt>
            <dd className="font-mono text-slate-200">
              {run.startedAt || '不可用'} / {run.completedAt || '不可用'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">coordinateHash</dt>
            <dd className="break-all font-mono text-slate-200">
              {shortHash(run.coordinateHash)}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">artifactManifestSha256</dt>
            <dd className="break-all font-mono text-slate-200">
              {shortHash(run.artifactManifestSha256)}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">结论</dt>
            <dd className="font-mono text-slate-200">
              {run.conclusion || '不可用'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">进度</dt>
            <dd className="font-mono text-slate-200">
              {run.completedUnits} / {run.totalUnits || '不可用'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">queueReason</dt>
            <dd className="font-mono text-slate-200">
              {run.queueReason || '不可用'}
            </dd>
          </div>
          <div>
            <dt className="text-slate-600">errorCode / errorMessage</dt>
            <dd className="break-words font-mono text-slate-200">
              {run.errorCode || '不可用'} / {run.errorMessage || '不可用'}
            </dd>
          </div>
        </dl>
        {progress !== null && (
          <div
            className="mt-3"
            role="progressbar"
            aria-label="训练完成进度"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(progress)}
          >
            <div className="h-2 overflow-hidden rounded-full bg-slate-800">
              <div
                className="h-full rounded-full bg-blue-500 transition-[width] duration-200 motion-reduce:transition-none"
                style={{ width: `${progress}%` }}
              />
            </div>
          </div>
        )}
        {run.errorMessage && (
          <p
            role="alert"
            className="mt-2 rounded-control border border-rose-400/20 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
          >
            {run.errorCode || 'ERROR'}：{run.errorMessage}
          </p>
        )}
        <div className="mt-3 grid gap-2 xl:grid-cols-[minmax(0,1fr)_minmax(16rem,0.6fr)]">
          <div>
            <h3 className="text-ui-caption font-bold text-slate-300">
              阶段序列
            </h3>
            <ol
              className="mt-2 grid gap-1 sm:grid-cols-2 xl:grid-cols-4"
              aria-label="训练阶段序列"
            >
              {PHASES.map((phase, index) => {
                const current = phase === run.phase;
                const passed = phaseIndex >= 0 && index < phaseIndex;
                return (
                  <li
                    key={phase}
                    className={cn(
                      'rounded-control border px-2 py-1.5 text-ui-micro',
                      current
                        ? 'border-blue-400/40 bg-blue-400/10 text-blue-100'
                        : passed
                          ? 'border-emerald-400/20 text-emerald-200'
                          : 'border-white/[0.06] text-slate-600'
                    )}
                    aria-current={current ? 'step' : undefined}
                  >
                    {index + 1}. {phaseLabel(phase)} · {phase}
                  </li>
                );
              })}
            </ol>
          </div>
          <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2 text-ui-caption">
            <div className="text-slate-600">状态门禁摘要</div>
            <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-slate-300">
              {pretty(run.gateSummary)}
            </pre>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {canCancel && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={onCancel}
            >
              <Square className="mr-1.5 h-3 w-3" />
              取消训练
            </Button>
          )}
          {canFinal && (
            <Button size="sm" disabled={busy} onClick={onFinal}>
              <Play className="mr-1.5 h-3 w-3" />
              执行 FINAL_EVALUATION
            </Button>
          )}
          {canRegister && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={onRegister}
            >
              <ShieldCheck className="mr-1.5 h-3 w-3" />
              登记到模型库
            </Button>
          )}
        </div>
        {run.runKind === StockSelectionTrainingRunKind.FinalEvaluation && (
          <div className="mt-4 border-t border-white/[0.06] pt-3">
            <h3 className="text-ui-body font-bold text-slate-100">
              FINAL 评估证据
            </h3>
            <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              <EvidenceCard
                label="probability"
                value={firstPresent(
                  evidenceValue(metrics, 'frozen_test', 'probability'),
                  evidenceValue(metrics, 'probability')
                )}
              />
              <EvidenceCard
                label="ranking"
                value={firstPresent(
                  evidenceValue(metrics, 'frozen_test', 'ranking'),
                  evidenceValue(metrics, 'ranking')
                )}
              />
              <EvidenceCard
                label="data"
                value={firstPresent(
                  evidenceValue(metrics, 'frozen_test', 'data'),
                  run.environmentEvidence
                )}
              />
              <EvidenceCard
                label="stability"
                value={firstPresent(
                  evidenceValue(metrics, 'frozen_test', 'annual_stability'),
                  evidenceValue(metrics, 'frozen_test', 'stability'),
                  evidenceValue(metrics, 'stability')
                )}
              />
              <EvidenceCard
                label="disagreement"
                value={firstPresent(
                  evidenceValue(metrics, 'probability_disagreement'),
                  evidenceValue(metrics, 'disagreement')
                )}
              />
              <EvidenceCard label="gates" value={run.gateSummary} />
            </div>
          </div>
        )}
      </section>
    </section>
  );
}

export function StockSelectionTrainingRunLifecycle({
  runId,
  onFinalCreated,
  onRegistered,
}: {
  runId: string;
  onFinalCreated: (runId: string) => void;
  onRegistered: () => void;
}) {
  const { toast } = useToast();
  const detail = useStockSelectionTrainingRun(runId);
  const runsState = useStockSelectionTrainingRuns();
  const [cancelResult, cancel] = useMutation(
    CancelStockSelectionTrainingRunDocument
  );
  const [finalResult, startFinal] = useMutation(
    StartStockSelectionFinalEvaluationDocument
  );
  const [registerResult, register] = useMutation(
    RegisterStockSelectionModelDocument
  );
  const pendingKeys = useRef(createPendingIdempotencyKeys());
  const [operationError, setOperationError] = useState<string | null>(null);
  const [selectedComparisonIds, setSelectedComparisonIds] = useState<string[]>(
    []
  );
  const run = detail.run;

  const comparisonCandidates = useMemo(() => {
    if (!run?.coordinateHash) return [];
    const all = [run, ...runsState.runs];
    const unique = new Map(all.map(item => [item.runId, item]));
    return Array.from(unique.values())
      .filter(
        item =>
          item.runKind === StockSelectionTrainingRunKind.FinalEvaluation &&
          item.status === StockSelectionTrainingRunStatus.Succeeded &&
          item.coordinateHash === run.coordinateHash
      )
      .slice(0, 100);
  }, [run, runsState.runs]);

  useEffect(() => {
    setSelectedComparisonIds(current => {
      const valid = current.filter(id =>
        comparisonCandidates.some(candidate => candidate.runId === id)
      );
      const currentIsCandidate =
        run &&
        comparisonCandidates.some(candidate => candidate.runId === run.runId);
      if (valid.length === 0 && currentIsCandidate && run) return [run.runId];
      if (
        valid.length === current.length &&
        valid.every((id, index) => id === current[index])
      )
        return current;
      return valid.slice(0, 5);
    });
  }, [comparisonCandidates, run]);

  const comparison = useStockSelectionTrainingComparison(selectedComparisonIds);
  const busy =
    cancelResult.fetching || finalResult.fetching || registerResult.fetching;

  useEffect(() => {
    if (detail.error) {
      toast({
        title: '运行详情读取失败',
        description: detail.error.message,
        variant: 'destructive',
      });
    }
  }, [detail.error, toast]);
  useEffect(() => {
    if (runsState.error) {
      toast({
        title: '训练运行列表读取失败',
        description: runsState.error.message,
        variant: 'destructive',
      });
    }
  }, [runsState.error, toast]);
  useEffect(() => {
    if (comparison.error) {
      toast({
        title: '同坐标比较失败',
        description: comparison.error.message,
        variant: 'destructive',
      });
    }
  }, [comparison.error, toast]);

  const handleCancel = async () => {
    if (!run || TERMINAL_STATUSES.has(run.status)) return;
    setOperationError(null);
    const scope = `cancel:${run.runId}`;
    const result = await cancel({
      runId: run.runId,
      expectedVersion: run.stateVersion,
      idempotencyKey: pendingKeys.current.get(scope),
    });
    if (result.error) {
      setOperationError(result.error.message);
      toast({
        title: '取消训练失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    if (!result.data?.cancelStockSelectionTrainingRun) return;
    pendingKeys.current.clear(scope);
    toast({ title: '已请求取消训练', variant: 'success' });
    void detail.refresh();
    void runsState.refresh();
  };

  const handleFinal = async () => {
    if (
      !run ||
      run.runKind !== StockSelectionTrainingRunKind.Development ||
      run.status !== StockSelectionTrainingRunStatus.Succeeded ||
      !run.runKey?.trim() ||
      !isSha256(run.artifactManifestSha256)
    )
      return;
    setOperationError(null);
    const scope = `final:${run.runId}`;
    const result = await startFinal({
      parentRunId: run.runId,
      idempotencyKey: pendingKeys.current.get(scope),
    });
    if (result.error) {
      setOperationError(result.error.message);
      toast({
        title: 'FINAL_EVALUATION 启动失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    const finalRun = result.data?.startStockSelectionFinalEvaluation;
    if (!finalRun) return;
    pendingKeys.current.clear(scope);
    toast({ title: 'FINAL_EVALUATION 已进入队列', variant: 'success' });
    onFinalCreated(finalRun.runId);
    void runsState.refresh();
  };

  const handleRegister = async () => {
    if (
      !run ||
      run.runKind !== StockSelectionTrainingRunKind.FinalEvaluation ||
      run.status !== StockSelectionTrainingRunStatus.Succeeded ||
      !run.registerable ||
      !run.runKey?.trim()
    )
      return;
    setOperationError(null);
    const result = await register({ runKey: run.runKey });
    if (result.error) {
      setOperationError(result.error.message);
      toast({
        title: '模型登记失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }
    toast({ title: 'FINAL 模型已登记为 CANDIDATE', variant: 'success' });
    onRegistered();
    void detail.refresh();
    void runsState.refresh();
  };

  if (detail.error) {
    return (
      <section
        className="rounded-panel border border-rose-400/25 bg-rose-400/5 p-ui-panel text-ui-caption text-rose-200"
        role="alert"
      >
        <div>运行详情读取失败：{detail.error.message}</div>
        <Button
          className="mt-3"
          size="sm"
          variant="outline"
          onClick={() => void detail.refresh()}
        >
          重试读取
        </Button>
      </section>
    );
  }
  if (detail.fetching && !run) {
    return (
      <section
        className="flex min-h-40 items-center justify-center gap-2 rounded-panel border border-white/[0.08] bg-[#081321] p-ui-panel text-ui-caption text-slate-500"
        role="status"
        aria-live="polite"
      >
        <LoaderCircle className="h-4 w-4 animate-spin text-blue-400 motion-reduce:animate-none" />
        正在读取运行详情…
      </section>
    );
  }
  if (!run) {
    return (
      <section
        className="rounded-panel border border-white/[0.08] bg-[#081321] p-ui-panel text-ui-caption text-slate-500"
        role="status"
      >
        未找到运行 {runId || '不可用'}。它可能已被删除，或当前账号无权访问。
      </section>
    );
  }

  return (
    <div className="space-y-3" data-testid="training-run-lifecycle">
      {operationError && (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          操作失败：{operationError}
        </div>
      )}
      {runsState.error && (
        <div
          role="alert"
          aria-live="polite"
          className="rounded-control border border-rose-400/25 bg-rose-400/5 p-2 text-ui-caption text-rose-200"
        >
          训练运行列表读取失败：{runsState.error.message}
          <Button
            className="ml-2"
            size="sm"
            variant="outline"
            onClick={() => void runsState.refresh()}
          >
            重试
          </Button>
        </div>
      )}
      <RunDetailContent
        run={run}
        onRefresh={() => void detail.refresh()}
        onCancel={() => void handleCancel()}
        onFinal={() => void handleFinal()}
        onRegister={() => void handleRegister()}
        busy={busy}
      />
      <RunComparison
        currentRun={run}
        candidates={comparisonCandidates}
        selectedIds={selectedComparisonIds}
        onSelected={(id, checked) =>
          setSelectedComparisonIds(current =>
            checked
              ? current.includes(id) || current.length >= 5
                ? current
                : [...current, id]
              : current.filter(item => item !== id)
          )
        }
        comparison={comparison.comparison}
        error={comparison.error}
        fetching={comparison.fetching}
      />
    </div>
  );
}
