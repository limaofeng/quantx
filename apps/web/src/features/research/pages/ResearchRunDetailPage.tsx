import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  Database,
  FileWarning,
  FlaskConical,
  ListFilter,
  RefreshCw,
  Rows3,
} from 'lucide-react';
import { Link, useParams } from 'wouter';

import { safeDecodeURIComponent } from '@/router';

import {
  EventCurveChart,
  InteractionHeatmap,
  RegressionTable,
  ResearchEmptyState,
  ResearchErrorState,
  ResearchLoadingState,
  ResearchPanel,
  ResearchStatusBadge,
  RobustnessSummary,
  SourceProvenancePanel,
  VolumeComparisonPanel,
  WarningStrip,
} from '../components';
import { useResearchRun } from '../hooks';
import { isSmallSample, readResearchRunKey } from '../model';

const STUDY_LABELS: Record<string, string> = {
  'volume-shock': '异常放量 × 价格位置',
};

function formatDuration(value?: number | null) {
  if (value === null || value === undefined) return '—';
  if (value < 60) return `${value.toFixed(1)} 秒`;
  return `${Math.floor(value / 60)} 分 ${(value % 60).toFixed(0)} 秒`;
}

function formatInteger(value?: number | null) {
  return value === null || value === undefined ? '—' : value.toLocaleString();
}

function identityMatches(
  expected: { runId: string; studyId: string; version: string },
  actual: { runId: string; studyId: string; version: string }
) {
  return (
    expected.runId === actual.runId &&
    expected.studyId === actual.studyId &&
    expected.version === actual.version
  );
}

export default function ResearchRunDetailPage() {
  const params = useParams<{
    runId: string;
    studyId: string;
    version: string;
  }>();
  const studyId = safeDecodeURIComponent(params.studyId || '');
  const version = safeDecodeURIComponent(params.version || '');
  const runId = safeDecodeURIComponent(params.runId || '');
  const key = readResearchRunKey(
    typeof window === 'undefined' ? '' : window.location.search
  );
  const { detail, error, fetching, parsed, refresh } = useResearchRun(key);

  if (!key) {
    return (
      <main className="h-full overflow-y-auto bg-[#08101d]">
        <ResearchErrorState
          message="链接缺少服务端签发的研究结果标识，请从研究中心重新打开。"
          onRetry={() => {
            window.location.href = '/research';
          }}
        />
      </main>
    );
  }

  if (fetching && !detail) {
    return (
      <main className="h-full overflow-y-auto bg-[#08101d]">
        <ResearchLoadingState label="正在读取研究结果与统计产物" />
      </main>
    );
  }

  if (error) {
    return (
      <main className="h-full overflow-y-auto bg-[#08101d]">
        <ResearchErrorState message={error.message} onRetry={refresh} />
      </main>
    );
  }

  if (!detail || !parsed) {
    return (
      <main className="h-full overflow-y-auto bg-[#08101d]">
        <ResearchEmptyState
          title="研究运行不存在"
          description="结果可能已被归档、尚未完成，或当前账户没有读取权限。"
        />
      </main>
    );
  }

  const summary = detail.summary;
  if (!identityMatches({ runId, studyId, version }, summary)) {
    return (
      <main className="h-full overflow-y-auto bg-[#08101d]">
        <ResearchErrorState
          message="链接中的研究身份与服务端返回结果不一致，请从研究中心重新打开。"
          onRetry={() => {
            window.location.href = '/research';
          }}
        />
      </main>
    );
  }

  const quality = parsed.dataQuality;
  const qualityWarnings = quality?.warnings || [];
  const artifactErrors = Array.from(
    new Set([
      ...detail.artifactErrors,
      ...summary.artifactErrors,
      ...parsed.validationErrors,
    ])
  );
  const smallSample = isSmallSample(summary.version, summary.eventCount);
  const loadedCodes = quality?.loaded_codes?.length;
  const requestedCodes = quality?.requested_codes?.length;
  const coverage =
    loadedCodes !== undefined && requestedCodes
      ? `${loadedCodes}/${requestedCodes}`
      : formatInteger(loadedCodes);

  return (
    <main className="h-full overflow-y-auto bg-[#08101d] text-slate-200">
      <header className="sticky top-0 z-20 border-b border-white/[0.06] bg-[#0b1423]/95 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-3">
          <Link
            href="/research"
            aria-label="返回研究中心"
            className="flex h-8 w-8 cursor-pointer items-center justify-center rounded-md border border-white/10 text-slate-500 transition-colors hover:border-red-500/35 hover:text-red-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
          </Link>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <FlaskConical className="h-4 w-4 text-red-400" />
              <h1 className="truncate text-sm font-black text-slate-100">
                {STUDY_LABELS[summary.studyId] || summary.studyId}
              </h1>
              <ResearchStatusBadge status={summary.status} />
              <span className="rounded border border-white/10 bg-white/[0.03] px-1.5 py-0.5 font-mono text-[9px] text-slate-400">
                {summary.version}
              </span>
            </div>
            <div className="mt-1 truncate font-mono text-[9px] text-slate-600">
              {summary.studyId} / {summary.version} / {summary.runId}
            </div>
          </div>
          <button
            type="button"
            onClick={refresh}
            disabled={fetching}
            className="flex h-8 cursor-pointer items-center gap-2 rounded-md border border-white/10 px-3 text-[10px] font-bold text-slate-400 transition-colors hover:border-red-500/35 hover:text-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 disabled:cursor-wait disabled:opacity-60"
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${fetching ? 'animate-spin motion-reduce:animate-none' : ''}`}
            />
            刷新
          </button>
        </div>
      </header>

      <div className="mx-auto max-w-[1500px] space-y-3 p-3 md:p-4">
        {smallSample && (
          <WarningStrip>
            <strong>小样本 / Smoke 运行：</strong>
            当前仅有 {formatInteger(summary.eventCount)}{' '}
            个事件，用于验证数据与报告链路，
            不应据此判断因子有效性或形成交易结论。
          </WarningStrip>
        )}
        {artifactErrors.length > 0 && (
          <WarningStrip tone="rose">
            <strong>产物读取不完整：</strong>
            {artifactErrors.join('；')}
          </WarningStrip>
        )}

        <section
          className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6"
          aria-label="研究摘要"
        >
          {[
            {
              icon: CheckCircle2,
              label: '事件样本',
              value: formatInteger(summary.eventCount),
              hint: smallSample ? '低于正式结论门槛' : '已完成事件筛选',
            },
            {
              icon: ListFilter,
              label: '分析样本',
              value: formatInteger(parsed.analysisSampleCount),
              hint: '阈值前合格观测',
            },
            {
              icon: Rows3,
              label: '有效数据行',
              value: formatInteger(quality?.valid_row_count),
              hint: `原始 ${formatInteger(quality?.row_count)} 行`,
            },
            {
              icon: Database,
              label: '标的覆盖',
              value: coverage,
              hint: '已加载 / 请求标的',
            },
            {
              icon: Clock3,
              label: '运行耗时',
              value: formatDuration(summary.elapsedSeconds),
              hint: summary.completedAt ? '离线研究已收敛' : '尚未记录完成时间',
            },
            {
              icon: quality?.is_usable === false ? AlertTriangle : CheckCircle2,
              label: '数据质量',
              value:
                quality?.is_usable === false
                  ? '不可用'
                  : artifactErrors.length
                    ? '需检查'
                    : '可用',
              hint: `${qualityWarnings.length} 条质量警告`,
            },
          ].map(item => {
            const Icon = item.icon;
            return (
              <article
                key={item.label}
                className="rounded-lg border border-white/[0.07] bg-[#0d1728]/80 p-3"
              >
                <div className="flex items-center gap-2 text-[9px] font-black uppercase tracking-wider text-slate-600">
                  <Icon className="h-3 w-3 text-red-400" />
                  {item.label}
                </div>
                <div className="mt-2 font-mono text-lg font-black tabular-nums text-slate-100">
                  {item.value}
                </div>
                <div className="mt-1 text-[9px] text-slate-600">
                  {item.hint}
                </div>
              </article>
            );
          })}
        </section>

        {quality?.source_provenance && (
          <SourceProvenancePanel provenance={quality.source_provenance} />
        )}

        {(qualityWarnings.length > 0 ||
          (quality?.missing_codes?.length || 0) > 0 ||
          (quality?.invalid_adjustment_codes?.length || 0) > 0) && (
          <ResearchPanel
            title="数据质量警告"
            description={`${quality?.requested_start || '—'} 至 ${quality?.requested_end || '—'}`}
          >
            <ul className="grid gap-2 p-3 text-[11px] leading-5 text-amber-100/80 sm:grid-cols-2">
              {qualityWarnings.map(warning => (
                <li key={warning} className="flex gap-2">
                  <AlertTriangle className="mt-1 h-3 w-3 shrink-0 text-amber-300" />
                  {warning}
                </li>
              ))}
              {(quality?.missing_codes?.length || 0) > 0 && (
                <li className="flex gap-2">
                  <FileWarning className="mt-1 h-3 w-3 shrink-0 text-amber-300" />
                  缺失标的 {quality?.missing_codes?.length} 个
                </li>
              )}
              {(quality?.invalid_adjustment_codes?.length || 0) > 0 && (
                <li className="flex gap-2">
                  <FileWarning className="mt-1 h-3 w-3 shrink-0 text-amber-300" />
                  复权异常标的 {quality?.invalid_adjustment_codes?.length} 个
                </li>
              )}
            </ul>
          </ResearchPanel>
        )}

        <VolumeComparisonPanel
          rows={parsed.comparison}
          sensitivity={parsed.comparisonSensitivity}
        />

        <div className="grid min-w-0 gap-3 xl:grid-cols-2">
          <EventCurveChart rows={parsed.eventCurve} />
          <InteractionHeatmap rows={parsed.heatmap} />
        </div>

        <RegressionTable models={parsed.regressions} />
        <RobustnessSummary robustness={parsed.robustness} />

        {detail.warnings.length > 0 && (
          <ResearchPanel
            title="研究解释边界"
            description="以下限制随本次不可变运行结果一并保存。"
          >
            <ul className="grid gap-x-6 gap-y-2 p-3 text-[11px] leading-5 text-slate-400 lg:grid-cols-2">
              {detail.warnings.map((warning: string) => (
                <li key={warning} className="flex gap-2">
                  <span
                    className="mt-2 h-1 w-1 shrink-0 rounded-full bg-slate-600"
                    aria-hidden="true"
                  />
                  {warning}
                </li>
              ))}
            </ul>
          </ResearchPanel>
        )}

        <p className="pb-2 text-center text-[9px] leading-5 text-slate-700">
          页面直接渲染结构化研究产物，不嵌入离线
          HTML。历史相关性不构成因果证据或投资建议。
        </p>
      </div>
    </main>
  );
}
