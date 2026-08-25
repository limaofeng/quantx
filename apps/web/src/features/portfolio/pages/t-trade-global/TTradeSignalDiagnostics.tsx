import {
  AlertTriangle,
  BarChart3,
  Clock3,
  Database,
  GitBranch,
  Loader2,
  ShieldAlert,
} from 'lucide-react';
import * as React from 'react';

import { NativeSelect } from '@/components/ui/native-select';
import { cn } from '@/utils/cn';

import { matchesDiagnosticVersion } from './clientTrust';
import type { SignalEvaluationLike } from './TTradeLiveMonitor';
import { formatNumber, formatTime } from './utils';

export type SignalDiagnosticPartitionLike = {
  policyVersion: string;
  featureSchemaVersion: string;
  profileVersion?: string | null;
  denominator: {
    code: string;
    label: string;
    readyInstrumentSeconds: number;
  };
  funnel: readonly {
    code: string;
    label: string;
    unitCode: string;
    denominatorCode?: string | null;
    count: number;
    conversionRate?: number | null;
  }[];
  blockers: readonly {
    blocker: { code: string; label: string; detail: string };
    count: number;
    rate?: number | null;
    denominatorCode: string;
    denominatorValue: number;
  }[];
  scoreDistribution: readonly {
    policyVersion: string;
    featureSchemaVersion: string;
    profileVersion?: string | null;
    path?: string | null;
    lowerBound: number;
    upperBound: number;
    count: number;
  }[];
  fsmDwell: readonly {
    branch: string;
    phase: string;
    durationSeconds: number;
    transitionCount: number;
  }[];
  fsmTransitions: readonly {
    branch: string;
    fromPhase: string;
    toPhase: string;
    count: number;
  }[];
  candidateOutcomes: readonly {
    code: string;
    label: string;
    count: number;
  }[];
  postCandidatePerformance: {
    available: boolean;
    reasonCode?: string | null;
    reason?: string | null;
    sampleCount: number;
    netMfePct?: number | null;
    netMaePct?: number | null;
    fixedWindowReturns: readonly {
      windowSeconds: number;
      sampleCount: number;
      averageNetReturnPct?: number | null;
    }[];
    requiredDataCodes: readonly string[];
  };
};

export type SignalDiagnosticsLike = {
  available: boolean;
  reasonCode?: string | null;
  reason?: string | null;
  accountId: string;
  stockCode?: string | null;
  startTime: string;
  endTime: string;
  mergedVersions: boolean;
  warnings: readonly string[];
  partitions: readonly SignalDiagnosticPartitionLike[];
  versionGroups: readonly {
    policyVersion: string;
    featureSchemaVersion: string;
    profileVersion?: string | null;
    count: number;
  }[];
};

type SignalDiagnosticVersionGroupLike =
  SignalDiagnosticsLike['versionGroups'][number];

function sameVersionCoordinate(
  left: {
    policyVersion: string;
    featureSchemaVersion: string;
    profileVersion?: string | null;
  },
  right: {
    policyVersion: string;
    featureSchemaVersion: string;
    profileVersion?: string | null;
  }
) {
  return (
    left.policyVersion === right.policyVersion &&
    left.featureSchemaVersion === right.featureSchemaVersion &&
    (left.profileVersion || null) === (right.profileVersion || null)
  );
}

function versionGroupForPartition(
  partition: SignalDiagnosticPartitionLike,
  versionGroups: readonly SignalDiagnosticVersionGroupLike[]
) {
  return versionGroups.find(group => sameVersionCoordinate(group, partition));
}

function percentage(value?: number | null) {
  return value == null || !Number.isFinite(value)
    ? '不可计算'
    : `${formatNumber(value * 100, 1)}%`;
}

function duration(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return '不可计算';
  if (seconds < 60) return `${formatNumber(seconds, 0)} 秒`;
  if (seconds < 3600) return `${formatNumber(seconds / 60, 1)} 分钟`;
  return `${formatNumber(seconds / 3600, 2)} 小时`;
}

function EmptyDiagnostic({ reason }: { reason?: string | null }) {
  return (
    <div className="flex h-full min-h-72 flex-col items-center justify-center p-ui-section text-center">
      <Database className="h-10 w-10 text-slate-800" />
      <div className="mt-3 text-ui-body font-bold text-slate-500">
        诊断样本尚不可用
      </div>
      <div className="mt-1 max-w-lg text-ui-caption leading-5 text-slate-700">
        {reason ||
          '等待服务端积累 MATERIAL 与合并诊断评估。不会用原始 Tick 数伪造分母。'}
      </div>
    </div>
  );
}

export function TTradeSignalDiagnosticsPanel({
  diagnostics,
  evaluations,
  error,
  loading,
}: {
  diagnostics?: SignalDiagnosticsLike | null;
  evaluations: readonly SignalEvaluationLike[];
  error?: string | null;
  loading: boolean;
}) {
  const [selectedPartitionKey, setSelectedPartitionKey] = React.useState('');
  if (loading && !diagnostics) {
    return (
      <div
        role="status"
        aria-busy="true"
        className="flex h-full min-h-72 flex-col items-center justify-center p-ui-section text-center text-ui-caption text-cyan-100"
      >
        <Loader2
          className="h-7 w-7 animate-spin text-cyan-300 motion-reduce:animate-none"
          aria-hidden="true"
        />
        <span className="mt-3">正在读取近 20 日服务端诊断…</span>
      </div>
    );
  }
  if (!diagnostics?.available) {
    return (
      <div className="h-full min-h-0">
        {error && (
          <div
            role="alert"
            className="flex items-start gap-2 border-b border-rose-400/20 bg-rose-400/[0.06] px-ui-section py-2.5 text-ui-caption leading-4 text-rose-100"
          >
            <ShieldAlert
              className="mt-0.5 h-3.5 w-3.5 shrink-0"
              aria-hidden="true"
            />
            诊断读取失败：{error}
          </div>
        )}
        <EmptyDiagnostic reason={diagnostics?.reason} />
      </div>
    );
  }
  const partitionKey = (item: SignalDiagnosticPartitionLike) =>
    `${item.policyVersion}:${item.featureSchemaVersion}:${item.profileVersion || ''}`;
  const partition =
    diagnostics.partitions.find(
      item => partitionKey(item) === selectedPartitionKey
    ) ?? diagnostics.partitions[0];
  if (!partition) {
    return <EmptyDiagnostic reason="该时间范围没有可诊断的版本分区" />;
  }
  const partitionEvaluations = evaluations.filter(evaluation =>
    matchesDiagnosticVersion(evaluation, partition)
  );
  const maxBlockerCount = Math.max(
    1,
    ...partition.blockers.map(item => item.count)
  );
  const maxBucketCount = Math.max(
    1,
    ...partition.scoreDistribution.map(item => item.count)
  );
  const versionGroup = versionGroupForPartition(
    partition,
    diagnostics.versionGroups
  );
  // The paged MATERIAL evaluation list is not a denominator. Only the exact
  // server-provided version group can be shown as the partition's count.
  const evaluationCountLabel = versionGroup
    ? `${versionGroup.count} 条`
    : '样本不可用';

  return (
    <div
      className="h-full min-h-0 overflow-y-auto p-ui-section custom-scrollbar"
      aria-busy={loading}
    >
      {error && (
        <div
          role="alert"
          className="mb-3 flex items-start gap-2 border border-rose-400/20 bg-rose-400/[0.06] px-3 py-2.5 text-ui-caption leading-4 text-rose-100"
        >
          <ShieldAlert
            className="mt-0.5 h-3.5 w-3.5 shrink-0"
            aria-hidden="true"
          />
          诊断刷新失败；以下仍显示上次成功读取的近 20 日分区。
        </div>
      )}
      {loading && (
        <div
          role="status"
          aria-busy="true"
          className="mb-3 flex items-center gap-2 border border-cyan-400/15 bg-cyan-400/[0.04] px-3 py-2 text-ui-micro text-cyan-100"
        >
          <Loader2
            className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          正在刷新诊断，暂保留上次结果…
        </div>
      )}
      <header className="mb-4 flex flex-wrap items-start justify-between gap-3 border border-white/[0.07] bg-[#0b1628] p-ui-section">
        <div>
          <div className="text-ui-micro font-black uppercase tracking-[0.16em] text-cyan-300">
            Server-side diagnostics
          </div>
          <h2 className="mt-1 text-ui-body font-black text-slate-100">
            机会引擎诊断
          </h2>
          <p className="mt-1 text-ui-caption text-slate-600">
            {formatTime(diagnostics.startTime)} —{' '}
            {formatTime(diagnostics.endTime)}
          </p>
        </div>
        <div className="grid grid-cols-2 gap-2 text-ui-caption sm:grid-cols-3">
          {diagnostics.partitions.length > 1 && (
            <label className="col-span-2 border border-white/[0.06] px-3 py-2 sm:col-span-3">
              <span className="text-slate-600">版本分区</span>
              <NativeSelect
                aria-label="诊断版本分区"
                value={partitionKey(partition)}
                onChange={event => setSelectedPartitionKey(event.target.value)}
                className="mt-1 w-full bg-[#07111f] px-2 py-1 font-mono text-slate-200"
              >
                {diagnostics.partitions.map(item => (
                  <option key={partitionKey(item)} value={partitionKey(item)}>
                    {item.policyVersion} · {item.featureSchemaVersion} ·{' '}
                    {item.profileVersion || '无画像'} ·{' '}
                    {versionGroupForPartition(item, diagnostics.versionGroups)
                      ? `${versionGroupForPartition(item, diagnostics.versionGroups)?.count} 条评估`
                      : '样本不可用'}
                  </option>
                ))}
              </NativeSelect>
            </label>
          )}
          <div className="border border-white/[0.06] px-3 py-2">
            <div className="text-slate-600">统计分母</div>
            <div className="mt-1 text-slate-200">
              {partition.denominator.label}
            </div>
          </div>
          <div className="border border-white/[0.06] px-3 py-2">
            <div className="text-slate-600">READY 标的时长</div>
            <div className="mt-1 font-mono text-slate-200">
              {duration(partition.denominator.readyInstrumentSeconds)}
            </div>
          </div>
          <div className="border border-white/[0.06] px-3 py-2">
            <div className="text-slate-600">评估证据</div>
            <div className="mt-1 font-mono text-slate-200">
              {evaluationCountLabel}
            </div>
          </div>
        </div>
      </header>

      <section
        className="mb-4 border border-white/[0.07] bg-[#0b1628] p-ui-section"
        aria-labelledby="t-trade-funnel-title"
      >
        <div className="mb-3 flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-cyan-300" />
          <h3
            id="t-trade-funnel-title"
            className="text-ui-label font-black text-slate-200"
          >
            机会漏斗
          </h3>
        </div>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5 2xl:grid-cols-9">
          {partition.funnel.map((stage, index) => (
            <div
              key={stage.code}
              className="relative border border-white/[0.06] p-3"
            >
              <div className="text-ui-micro text-slate-600">
                {index + 1}. {stage.label}
              </div>
              <div className="mt-1 font-mono text-ui-heading font-black text-slate-100">
                {stage.count}
              </div>
              <div className="mt-1 text-ui-micro text-slate-500">
                相对 {stage.denominatorCode || '起点'} ·{' '}
                {percentage(stage.conversionRate)}
              </div>
              <div className="mt-1 font-mono text-ui-micro text-slate-700">
                单位 {stage.unitCode}
              </div>
            </div>
          ))}
        </div>
        <p className="mt-3 text-ui-micro text-slate-600">
          eligible → data ready → pattern → preview → candidate → TradeIntent →
          confirmed → ordered → filled；漏斗只计 MATERIAL 证据，不展开合并 Tick
          数。
        </p>
      </section>

      <div className="grid gap-ui-section 2xl:grid-cols-2">
        <section className="border border-white/[0.07] bg-[#0b1628] p-ui-section">
          <div className="mb-3 flex items-center gap-2">
            <ShieldAlert className="h-4 w-4 text-amber-300" />
            <h3 className="text-ui-label font-black text-slate-200">
              主要 blocker
            </h3>
          </div>
          <div className="space-y-2">
            {partition.blockers.map(item => (
              <div
                key={item.blocker.code}
                className="border border-white/[0.06] p-2.5 text-ui-caption"
              >
                <div className="flex items-center justify-between gap-3">
                  <span className="font-bold text-slate-200">
                    {item.blocker.label}
                  </span>
                  <span className="font-mono text-amber-200">
                    {item.count} · {percentage(item.rate)}
                  </span>
                </div>
                <div className="mt-2 h-1 bg-white/[0.06]">
                  <div
                    className="h-full bg-amber-400"
                    style={{
                      width: `${(item.count / maxBlockerCount) * 100}%`,
                    }}
                  />
                </div>
                <div className="mt-1 text-ui-micro text-slate-600">
                  {item.blocker.detail}
                </div>
                <div className="mt-1 font-mono text-ui-micro text-slate-700">
                  分母 {item.denominatorCode} ={' '}
                  {formatNumber(item.denominatorValue, 0)}
                </div>
              </div>
            ))}
            {partition.blockers.length === 0 && (
              <div className="py-ui-panel text-center text-ui-caption text-slate-600">
                该时间范围没有 blocker
              </div>
            )}
          </div>
        </section>

        <section className="border border-white/[0.07] bg-[#0b1628] p-ui-section">
          <div className="mb-3 flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-violet-300" />
            <h3 className="text-ui-label font-black text-slate-200">
              机会分分布
            </h3>
          </div>
          <div className="space-y-2">
            {partition.scoreDistribution.map((bucket, index) => (
              <div
                key={`${bucket.path || 'NONE'}:${bucket.lowerBound}:${index}`}
                className="grid grid-cols-[100px_1fr_42px] items-center gap-2 text-ui-micro"
              >
                <span className="font-mono text-slate-500">
                  {bucket.path || '未选路径'}
                  <br />
                  {bucket.lowerBound}–{bucket.upperBound}
                  <br />
                  {bucket.policyVersion}
                </span>
                <div className="h-3 bg-white/[0.06]">
                  <div
                    className="h-full bg-violet-400/70"
                    style={{
                      width: `${(bucket.count / maxBucketCount) * 100}%`,
                    }}
                  />
                </div>
                <span className="text-right font-mono text-slate-300">
                  {bucket.count}
                </span>
              </div>
            ))}
            {partition.scoreDistribution.length === 0 && (
              <div className="py-ui-panel text-center text-ui-caption text-slate-600">
                暂无可计算分数样本
              </div>
            )}
          </div>
        </section>

        <section className="border border-white/[0.07] bg-[#0b1628] p-ui-section">
          <div className="mb-3 flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-emerald-300" />
            <h3 className="text-ui-label font-black text-slate-200">
              双 FSM 停留
            </h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] text-left text-ui-caption">
              <thead className="text-slate-600">
                <tr>
                  <th className="pb-2">分支</th>
                  <th>阶段</th>
                  <th>停留</th>
                  <th>跃迁</th>
                </tr>
              </thead>
              <tbody>
                {partition.fsmDwell.map(item => (
                  <tr
                    key={`${item.branch}:${item.phase}`}
                    className="border-t border-white/[0.05]"
                  >
                    <td className="py-2">{item.branch}</td>
                    <td>{item.phase}</td>
                    <td className="font-mono">
                      {duration(item.durationSeconds)}
                    </td>
                    <td className="font-mono">{item.transitionCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3 border-t border-white/[0.05] pt-3">
            <div className="mb-2 text-ui-micro font-bold text-slate-500">
              from → to 转移边
            </div>
            <div className="space-y-1 text-ui-micro">
              {partition.fsmTransitions.map(item => (
                <div
                  key={`${item.branch}:${item.fromPhase}:${item.toPhase}`}
                  className="flex justify-between gap-3 font-mono text-slate-400"
                >
                  <span>
                    {item.branch} · {item.fromPhase} → {item.toPhase}
                  </span>
                  <span>×{item.count}</span>
                </div>
              ))}
              {partition.fsmTransitions.length === 0 && (
                <div className="text-slate-700">该范围没有阶段跃迁</div>
              )}
            </div>
          </div>
        </section>

        <section className="border border-white/[0.07] bg-[#0b1628] p-ui-section">
          <div className="mb-3 flex items-center gap-2">
            <Clock3 className="h-4 w-4 text-rose-300" />
            <h3 className="text-ui-label font-black text-slate-200">
              候选结果
            </h3>
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {partition.candidateOutcomes.map(item => (
              <div key={item.code} className="border border-white/[0.06] p-3">
                <div className="text-ui-micro text-slate-600">{item.label}</div>
                <div className="mt-1 font-mono text-ui-heading font-black text-slate-100">
                  {item.count}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="mt-4 border border-white/[0.07] bg-[#0b1628] p-ui-section">
        <div className="mb-3 flex items-center gap-2">
          <Clock3 className="h-4 w-4 text-cyan-300" />
          <h3 className="text-ui-label font-black text-slate-200">
            成交后费用化表现
          </h3>
        </div>
        {!partition.postCandidatePerformance.available ? (
          <div className="border border-amber-400/15 bg-amber-400/[0.03] p-3 text-ui-caption">
            <div className="font-bold text-amber-200">
              MFE / MAE / 固定窗口收益未计算
            </div>
            <div className="mt-1 leading-5 text-slate-500">
              {partition.postCandidatePerformance.reason}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {partition.postCandidatePerformance.requiredDataCodes.map(
                code => (
                  <code
                    key={code}
                    className="border border-white/[0.06] px-1.5 py-1 text-ui-micro text-slate-600"
                  >
                    {code}
                  </code>
                )
              )}
            </div>
          </div>
        ) : (
          <div className="grid gap-2 text-ui-caption sm:grid-cols-3">
            <div className="border border-white/[0.06] p-3">
              净 MFE{' '}
              <span className="font-mono text-slate-200">
                {partition.postCandidatePerformance.netMfePct == null
                  ? '不可计算'
                  : `${formatNumber(partition.postCandidatePerformance.netMfePct, 2)}%`}
              </span>
            </div>
            <div className="border border-white/[0.06] p-3">
              净 MAE{' '}
              <span className="font-mono text-slate-200">
                {partition.postCandidatePerformance.netMaePct == null
                  ? '不可计算'
                  : `${formatNumber(partition.postCandidatePerformance.netMaePct, 2)}%`}
              </span>
            </div>
            <div className="border border-white/[0.06] p-3">
              权威样本{' '}
              <span className="font-mono text-slate-200">
                {partition.postCandidatePerformance.sampleCount}
              </span>
            </div>
            <div className="border border-white/[0.06] p-3 sm:col-span-3">
              <div className="mb-2 text-slate-500">固定窗口净收益</div>
              {partition.postCandidatePerformance.fixedWindowReturns.length ===
              0 ? (
                <span className="text-slate-600">暂无成熟窗口样本</span>
              ) : (
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {partition.postCandidatePerformance.fixedWindowReturns.map(
                    item => (
                      <div
                        key={item.windowSeconds}
                        className="border border-white/[0.05] px-2.5 py-2"
                      >
                        <div className="flex items-center justify-between gap-2 text-ui-micro text-slate-500">
                          <span>{duration(item.windowSeconds)}</span>
                          <span>{item.sampleCount} 样本</span>
                        </div>
                        <div className="mt-1 font-mono text-slate-200">
                          {item.averageNetReturnPct == null
                            ? '不可计算'
                            : `${formatNumber(item.averageNetReturnPct, 2)}%`}
                        </div>
                      </div>
                    )
                  )}
                </div>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="mt-4 border border-white/[0.07] bg-[#0b1628] p-ui-section">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-ui-label font-black text-slate-200">版本分组</h3>
          <span className="inline-flex items-center gap-1 text-ui-micro text-amber-200">
            <AlertTriangle className="h-3 w-3" />
            {diagnostics.mergedVersions
              ? '已显式合并不同规则版本'
              : '不同规则版本默认不合并'}
          </span>
        </div>
        {diagnostics.warnings.length > 0 && (
          <div className="mb-3 border border-amber-400/15 px-3 py-2 font-mono text-ui-micro text-amber-200">
            {diagnostics.warnings.join(' · ')}
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[620px] text-left text-ui-caption">
            <thead className="text-slate-600">
              <tr>
                <th className="pb-2">Policy</th>
                <th>Feature schema</th>
                <th>Profile</th>
                <th>样本</th>
              </tr>
            </thead>
            <tbody>
              {diagnostics.versionGroups.map((item, index) => (
                <tr
                  key={`${item.policyVersion}:${item.featureSchemaVersion}:${item.profileVersion || ''}:${index}`}
                  className={cn(
                    'border-t border-white/[0.05]',
                    diagnostics.versionGroups.length > 1 && 'text-amber-100'
                  )}
                >
                  <td className="py-2 font-mono">{item.policyVersion}</td>
                  <td className="font-mono">{item.featureSchemaVersion}</td>
                  <td className="font-mono">
                    {item.profileVersion || '无画像'}
                  </td>
                  <td className="font-mono">{item.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mt-4 border border-white/[0.07] bg-[#0b1628] p-ui-section">
        <h3 className="mb-3 text-ui-label font-black text-slate-200">
          单标的评估时间线
        </h3>
        <div className="space-y-1.5">
          {partitionEvaluations.slice(0, 50).map(item => (
            <article
              key={item.id}
              className="grid gap-1 border border-white/[0.05] px-3 py-2 text-ui-micro sm:grid-cols-[145px_100px_130px_1fr]"
            >
              <time className="font-mono text-slate-500">
                {formatTime(item.evaluatedAt)}
              </time>
              <span className="text-slate-300">{item.stockCode}</span>
              <span className="text-slate-500">
                {item.eventKind} · ×{item.coalescedCount}
              </span>
              <span className="text-slate-400">
                {item.eventType} ·{' '}
                {item.signalSnapshot?.topBlockers[0]?.label || '无首要 blocker'}{' '}
                · policy {item.policyVersion} · feature{' '}
                {item.signalSnapshot?.featureSchemaVersion || '不可用'} ·
                profile {item.signalSnapshot?.profileVersion || '无画像'}
              </span>
            </article>
          ))}
          {!loading && partitionEvaluations.length === 0 && (
            <div className="py-ui-panel text-center text-ui-caption text-slate-600">
              该范围暂无持久化评估事件
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
