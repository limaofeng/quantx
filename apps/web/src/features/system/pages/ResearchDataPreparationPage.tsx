import { useEffect, useRef, useState } from 'react';
import { useClient, useMutation, useQuery } from 'urql';
import { Link } from 'wouter';
import { z } from 'zod';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import {
  StudioPanel,
  StudioPanelContent,
  StudioPanelHeader,
  StudioPanelTitle,
} from '@/components/ui/studio-layout';
import { Textarea } from '@/components/ui/textarea';
import {
  useStockSelectionDatasetVersions,
  useStockSelectionTrainingCapabilities,
} from '@/features/research/hooks';
import {
  ResearchPreparationKind,
  ResearchPreparationPageDocument as PREPARATION_STATE,
  SaveResearchPreparationDocument as SAVE,
  PreviewResearchDownloadDocument as PREVIEW,
  StartResearchPreparationDocument as START,
  RetryResearchPreparationDocument as RETRY,
} from '@/generated/research-preparation/graphql';
import { createClientId } from '@/utils/clientId';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';

const configSchema = z.object({
  date_start: z.string(),
  date_end: z.string(),
  stock_codes: z.array(z.string()),
  benchmark_code: z.string(),
  minimum_listing_days: z.number(),
  st_file: z.string().nullable(),
  industry_file: z.string().nullable(),
  delisting_file: z.string().nullable(),
});
type Config = z.infer<typeof configSchema>;
const reportSchema = z.object({
  checks: z
    .array(
      z.object({ name: z.string(), status: z.string(), detail: z.string() })
    )
    .optional(),
  sample_count: z.number().optional(),
  manifest_sha256: z.string().optional(),
});
const previewSchema = z.object({
  start: z.string(),
  end: z.string(),
  warmup_days: z.number(),
  label_available: z.boolean(),
});
const historyFields = [
  ['st_file', '历史 ST', 'is_st'],
  ['industry_file', '历史行业', 'industry'],
  ['delisting_file', '历史退市', 'delisting_risk'],
] as const;
const kindLabels = {
  COVERAGE: '数据覆盖',
  DOWNLOAD: '行情下载',
  CERTIFY: '数据集认证',
  GPU: 'GPU 资格',
};
const stateLabels: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '运行中',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  READY: '满足',
  MISSING: '缺失',
  BLOCKED: '需处理',
};

export function ResearchDataPreparationPage() {
  const client = useClient();
  const [state, refresh] = useQuery({
    query: PREPARATION_STATE,
    requestPolicy: 'network-only',
  });
  const [, save] = useMutation(SAVE);
  const [, start] = useMutation(START);
  const [, retry] = useMutation(RETRY);
  const datasets = useStockSelectionDatasetVersions();
  const capabilities = useStockSelectionTrainingCapabilities();
  const [draft, setDraft] = useState<Config>();
  const [version, setVersion] = useState('');
  const [gpuDataset, setGpuDataset] = useState('');
  const [busy, setBusy] = useState(false);
  const pendingKeys = useRef(new Map<string, string>());
  const [message, setMessage] = useState('');
  const [preview, setPreview] = useState<z.infer<typeof previewSchema>>();
  const [previewSignature, setPreviewSignature] = useState('');
  const parsed = configSchema.safeParse(state.data?.researchPreparation.config);
  const config = draft ?? (parsed.success ? parsed.data : undefined);
  const jobs = state.data?.researchPreparation.jobs ?? [];
  const dirty =
    !!config &&
    (!parsed.success || JSON.stringify(config) !== JSON.stringify(parsed.data));
  const refreshCapabilities = capabilities.refresh;
  const refreshDatasets = datasets.refresh;
  useEffect(() => {
    const timer = window.setInterval(() => {
      refresh({ requestPolicy: 'network-only' });
      refreshCapabilities();
      refreshDatasets();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refresh, refreshCapabilities, refreshDatasets]);
  const edit = (patch: Partial<Config>) => {
    if (config) setDraft({ ...config, ...patch });
    setPreview(undefined);
    setMessage('');
  };
  const action = async (run: () => Promise<void>) => {
    setBusy(true);
    setMessage('');
    try {
      await run();
      refresh({ requestPolicy: 'network-only' });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '操作失败，请重试');
    } finally {
      setBusy(false);
    }
  };
  const submit = (kind: ResearchPreparationKind) =>
    action(async () => {
      const signature = JSON.stringify([kind, config, version, gpuDataset]);
      const requestKey =
        pendingKeys.current.get(signature) ?? createClientId('research-prep');
      pendingKeys.current.set(signature, requestKey);
      const result = await start({
        kind,
        config,
        requestKey,
        datasetVersion:
          kind === ResearchPreparationKind.Gpu
            ? gpuDataset
            : kind === ResearchPreparationKind.Certify
              ? version
              : null,
      });
      if (result.error) throw result.error;
      pendingKeys.current.delete(signature);
      setMessage('任务已排队；可离开页面，稍后查看结果。');
    });
  const capability = capabilities.data;
  const disabled = busy || !config || dirty;
  return (
    <DataStudioPageFrame
      activeMode="RESEARCH"
      title="研究训练数据"
      description="配置、下载、覆盖检查与认证"
    >
      <div className="h-full overflow-y-auto space-y-ui-section p-ui-section text-ui-body">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <h1 className="text-ui-page-title font-semibold">研究训练数据</h1>
          <Link href="/research/training" className="text-blue-400">
            返回模型训练
          </Link>
        </header>
        <p className="text-slate-400">
          保存设置后检查覆盖，再按需下载和认证。每项操作均需主动发起；运行中任务保留提交时的配置。
        </p>
        {(state.error || message) && (
          <p role="status" className="text-amber-300">
            {state.error?.message || message}
          </p>
        )}
        {state.error && (
          <Button onClick={() => refresh({ requestPolicy: 'network-only' })}>
            重试读取
          </Button>
        )}
        {!config ? (
          <p>正在读取准备配置…</p>
        ) : (
          <StudioPanel>
            <StudioPanelHeader>
              <StudioPanelTitle>准备配置</StudioPanelTitle>
            </StudioPanelHeader>
            <StudioPanelContent className="space-y-3">
              <div className="grid gap-ui-group sm:grid-cols-2 xl:grid-cols-4">
                <label>
                  开始日期
                  <Input
                    type="date"
                    value={config.date_start}
                    onChange={e => edit({ date_start: e.target.value })}
                  />
                </label>
                <label>
                  结束日期
                  <Input
                    type="date"
                    value={config.date_end}
                    onChange={e => edit({ date_end: e.target.value })}
                  />
                </label>
                <label>
                  基准代码
                  <Input
                    value={config.benchmark_code}
                    onChange={e =>
                      edit({ benchmark_code: e.target.value.toUpperCase() })
                    }
                  />
                </label>
                <label>
                  最低上市天数
                  <Input
                    type="number"
                    min={252}
                    value={config.minimum_listing_days}
                    onChange={e =>
                      edit({ minimum_listing_days: Number(e.target.value) })
                    }
                  />
                </label>
              </div>
              <label className="block">
                股票代码（逗号或换行分隔；留空按历史证据范围检查）
                <Textarea
                  value={config.stock_codes.join(', ')}
                  onChange={e =>
                    edit({
                      stock_codes: e.target.value
                        .toUpperCase()
                        .split(/[\s,，]+/)
                        .filter(Boolean),
                    })
                  }
                />
              </label>
              <div className="grid gap-ui-group lg:grid-cols-3">
                {historyFields.map(([key, label, column]) => (
                  <label key={key}>
                    {label}
                    <NativeSelect
                      aria-label={label}
                      className="block w-full h-control-default rounded-control border border-slate-700 bg-slate-900 px-2"
                      value={config[key] ?? ''}
                      onChange={e => edit({ [key]: e.target.value || null })}
                    >
                      <option value="">未配置</option>
                      {state.data?.researchPreparation.evidenceFiles.map(
                        file => (
                          <option key={file}>{file}</option>
                        )
                      )}
                    </NativeSelect>
                    <span className="text-ui-caption text-slate-400">
                      所需字段：event_date、stock_code、{column}
                    </span>
                  </label>
                ))}
              </div>
              <p className="text-ui-caption text-slate-400">
                由运行端配置历史 CSV/Parquet
                文件后在此选择。三类历史证据不提供自动下载；当前证券属性不能替代历史证据。
              </p>
              <Button
                disabled={busy || !dirty}
                onClick={() =>
                  action(async () => {
                    const result = await save({ config });
                    if (result.error) throw result.error;
                    setDraft(config);
                    setMessage('配置已保存，尚未启动任务。');
                  })
                }
              >
                保存配置
              </Button>
              {dirty && (
                <span className="ml-2 text-amber-300">请先保存修改</span>
              )}
            </StudioPanelContent>
          </StudioPanel>
        )}
        <div className="grid gap-ui-section xl:grid-cols-2">
          <StudioPanel>
            <StudioPanelHeader>
              <StudioPanelTitle>数据覆盖与下载</StudioPanelTitle>
            </StudioPanelHeader>
            <StudioPanelContent className="space-y-3">
              <p>
                检查日线、基准、交易日历、指标预热与历史证据。停牌、退市和未核验区间会保留为缺口。
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  disabled={disabled}
                  onClick={() => submit(ResearchPreparationKind.Coverage)}
                >
                  检查数据覆盖
                </Button>
                <Button
                  disabled={disabled}
                  onClick={() =>
                    action(async () => {
                      const result = await client
                        .query(
                          PREVIEW,
                          { config },
                          { requestPolicy: 'network-only' }
                        )
                        .toPromise();
                      if (result.error) throw result.error;
                      setPreviewSignature(JSON.stringify(config));
                      setPreview(
                        previewSchema.parse(
                          result.data?.previewResearchDownload
                        )
                      );
                    })
                  }
                >
                  预览下载范围
                </Button>
              </div>
              {preview && previewSignature === JSON.stringify(config) && (
                <div className="space-y-2">
                  <p>
                    日线及复权依赖：{preview.start} → {preview.end}；包含{' '}
                    {preview.warmup_days} 天预热缓冲及次日标签区间。不下载分钟或
                    Tick，不触发实时快照计算。
                  </p>
                  {!preview.label_available && (
                    <p className="text-amber-300">
                      次日标签尚未产生，本次最多下载至今天；认证仍需等待标签。
                    </p>
                  )}
                  <Button
                    disabled={disabled}
                    onClick={() => submit(ResearchPreparationKind.Download)}
                  >
                    提交行情下载
                  </Button>
                </div>
              )}
            </StudioPanelContent>
          </StudioPanel>
          <StudioPanel>
            <StudioPanelHeader>
              <StudioPanelTitle>数据集认证</StudioPanelTitle>
            </StudioPanelHeader>
            <StudioPanelContent className="space-y-3">
              <p>
                完整认证需三类历史证据及数据检查通过。认证任务会再次校验；不会自动开始模型训练。
              </p>
              <label className="block">
                新数据集版本
                <Input
                  placeholder="按实际截止日命名，版本不可覆盖"
                  value={version}
                  onChange={e => setVersion(e.target.value)}
                />
              </label>
              <Button
                disabled={
                  disabled ||
                  !version ||
                  !config?.st_file ||
                  !config.industry_file ||
                  !config.delisting_file
                }
                onClick={() => submit(ResearchPreparationKind.Certify)}
              >
                生成并认证数据集
              </Button>
              {datasets.error && <p role="alert">认证数据集读取失败</p>}
              {datasets.data.map(dataset => (
                <div
                  key={dataset.datasetVersion}
                  className="border-t border-slate-800 pt-2"
                >
                  <p>
                    {dataset.datasetVersion} ·{' '}
                    {dataset.sampleCount.toLocaleString()} 样本
                  </p>
                  <details>
                    <summary>查看质量证据</summary>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap text-ui-caption">
                      {JSON.stringify(dataset.qualitySummary, null, 2)}
                    </pre>
                  </details>
                  <Link
                    className="text-blue-400"
                    href={`/research/training/new?dataset=${encodeURIComponent(dataset.datasetVersion)}`}
                  >
                    使用此数据集预检
                  </Link>
                </div>
              ))}
            </StudioPanelContent>
          </StudioPanel>
        </div>
        <StudioPanel>
          <StudioPanelHeader>
            <StudioPanelTitle>训练环境与 GPU 资格</StudioPanelTitle>
          </StudioPanelHeader>
          <StudioPanelContent className="space-y-3">
            <p>
              CPU：{capability?.cpuAvailable ? '可用' : '未确认可用'}；心跳：
              {capability?.fresh ? '新鲜' : '缺失或过期'}；最后成功：
              {capability?.updatedAt ?? '无'}；GPU：
              {capability?.gpuStatus ?? '未知'}
            </p>
            {capabilities.error && <p role="alert">能力读取失败，请重试</p>}
            <p>
              能力过期时检查 Worker 与能力探测计划。GPU 构建或驱动缺失需在
              Windows 运行端处理；未通过 GPU 资格不单独阻止 CPU 训练。
            </p>
            <a
              className="text-blue-400"
              href="/docs/guide/research-preparation"
              target="_blank"
              rel="noreferrer"
            >
              运行端准备说明
            </a>
            <label className="block">
              GPU 资格数据集
              <NativeSelect
                aria-label="GPU 资格数据集"
                className="ml-2 h-control-default rounded-control bg-slate-900 border border-slate-700 px-2"
                value={gpuDataset}
                onChange={e => setGpuDataset(e.target.value)}
              >
                <option value="">
                  {datasets.data.length ? '请选择认证数据集' : '等待认证数据集'}
                </option>
                {datasets.data.map(dataset => (
                  <option
                    key={dataset.datasetVersion}
                    value={dataset.datasetVersion}
                  >
                    {dataset.datasetVersion}
                  </option>
                ))}
              </NativeSelect>
            </label>
            <Button
              disabled={disabled || !gpuDataset}
              onClick={() => submit(ResearchPreparationKind.Gpu)}
            >
              运行 GPU 资格验证
            </Button>
          </StudioPanelContent>
        </StudioPanel>
        <StudioPanel>
          <StudioPanelHeader>
            <StudioPanelTitle>准备任务</StudioPanelTitle>
          </StudioPanelHeader>
          <StudioPanelContent className="space-y-3">
            <p className="text-slate-400">
              运行中的任务自动刷新。交易关键时段可能继续排队；失败任务重试沿用原配置和下载分块。
            </p>
            {!jobs.length && <p>暂无准备任务</p>}
            {jobs.map(job => {
              const report = reportSchema.safeParse(job.result);
              return (
                <article
                  key={job.jobId}
                  className="border-t border-slate-800 pt-3 space-y-2"
                >
                  <p className="font-medium">
                    {kindLabels[job.kind]} ·{' '}
                    {stateLabels[job.status] ?? job.status} · {job.phase}
                  </p>
                  <p className="text-ui-caption text-slate-400">
                    {job.createdAt} · {job.datasetVersion ?? '数据准备'} ·{' '}
                    {job.jobId}
                  </p>
                  {job.error && (
                    <p role="alert" className="text-amber-300">
                      {job.error}
                    </p>
                  )}
                  {job.status === 'FAILED' && (
                    <Button
                      disabled={busy}
                      onClick={() =>
                        action(async () => {
                          const result = await retry({ jobId: job.jobId });
                          if (result.error) throw result.error;
                        })
                      }
                    >
                      重试任务
                    </Button>
                  )}
                  <details>
                    <summary>提交时配置</summary>
                    <pre className="whitespace-pre-wrap text-ui-caption">
                      {JSON.stringify(job.config, null, 2)}
                    </pre>
                  </details>
                  {report.success &&
                    report.data.checks?.map(check => (
                      <p key={check.name}>
                        <span
                          className={
                            check.status === 'READY'
                              ? 'text-emerald-300'
                              : 'text-amber-300'
                          }
                        >
                          {check.name}：
                          {stateLabels[check.status] ?? check.status}
                        </span>{' '}
                        · {check.detail}
                      </p>
                    ))}
                  {job.kind === ResearchPreparationKind.Certify &&
                    job.status === 'SUCCEEDED' &&
                    job.datasetVersion && (
                      <Link
                        className="text-blue-400"
                        href={`/research/training/new?dataset=${encodeURIComponent(job.datasetVersion)}`}
                      >
                        返回模型训练并预选数据集
                      </Link>
                    )}
                </article>
              );
            })}
          </StudioPanelContent>
        </StudioPanel>
      </div>
    </DataStudioPageFrame>
  );
}
