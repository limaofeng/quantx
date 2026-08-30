import type { SignalSnapshot } from './monitoring';
import {
  candidateStatusLabels,
  nullableScore,
  signalPathLabels,
  signalPhaseLabels,
} from './signalPresentation';
import { formatNumber, formatTime } from './utils';

const healthLabels: Readonly<Record<string, string>> = {
  READY: '数据就绪',
  WARMING: '窗口积累中',
  DEGRADED: '数据降级',
  STALE: '数据陈旧',
  CONTINUITY_LOST: '连续性中断',
  INSUFFICIENT: '样本不足',
};

/** Pure evidence presentation shared by live and replay; no approval capability. */
export function TTradeSignalEvidence({
  snapshot,
}: {
  snapshot: SignalSnapshot;
}) {
  const path = snapshot.selectedPath
    ? signalPathLabels[snapshot.selectedPath] || snapshot.selectedPath
    : signalPhaseLabels[snapshot.dominantPhase] || '未选择路径';
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 text-ui-caption lg:grid-cols-4">
        {[
          [
            '候选状态',
            candidateStatusLabels[snapshot.candidateStatus] ||
              snapshot.candidateStatus,
          ],
          ['形态 / 路径', path],
          [
            '机会分 / 候选阈值',
            `${nullableScore(snapshot.opportunityScore)} / ${nullableScore(snapshot.candidateThreshold)}`,
          ],
          [
            '数据健康',
            healthLabels[snapshot.dataHealth] || snapshot.dataHealth,
          ],
          [
            '样本 / 覆盖',
            `${snapshot.sampleCount} 条 / ${nullableScore(snapshot.windowCoverageSeconds)} 秒`,
          ],
          [
            '重验 / 再武装阈值',
            `${nullableScore(snapshot.revalidateThreshold)} / ${nullableScore(snapshot.rearmThreshold)}`,
          ],
          ['行情源时间', formatTime(snapshot.sourceAt)],
          [
            '行情年龄',
            snapshot.dataAgeMs == null
              ? '未记录'
              : `${formatNumber(snapshot.dataAgeMs, 0)} ms`,
          ],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-control border border-white/[0.08] p-3"
          >
            <div className="text-slate-400">{label}</div>
            <div className="mt-1 font-mono font-semibold text-slate-200">
              {value}
            </div>
          </div>
        ))}
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        <section className="rounded-panel border border-white/[0.08] p-3">
          <h4 className="text-ui-label font-semibold text-slate-200">
            硬门槛与阻断原因
          </h4>
          <div className="mt-2 flex flex-wrap gap-2">
            {snapshot.hardGates.map(gate => (
              <span
                key={gate.code}
                title={gate.detail}
                className={`rounded border px-2 py-1 text-ui-caption ${gate.passed ? 'border-emerald-400/20 text-emerald-200' : 'border-amber-400/25 text-amber-100'}`}
              >
                {gate.label} · {gate.passed ? '通过' : '未通过'}
              </span>
            ))}
          </div>
          {snapshot.topBlockers.length === 0 ? (
            <p className="mt-2 text-ui-caption text-slate-400">
              当前信号没有首要阻断。
            </p>
          ) : (
            <ul className="mt-3 space-y-2">
              {snapshot.topBlockers.map(blocker => (
                <li
                  key={blocker.code}
                  className="border-l-2 border-amber-400/50 pl-2 text-ui-caption"
                >
                  <div className="font-semibold text-amber-100">
                    {blocker.label}
                  </div>
                  <div className="mt-1 break-words text-slate-400">
                    {blocker.detail || blocker.code}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {snapshot.dataHealthReasons.map(reason => (
            <p
              key={reason.code}
              className="mt-2 text-ui-caption text-slate-400"
            >
              {reason.label}：{reason.detail}
            </p>
          ))}
        </section>
        <section className="rounded-panel border border-white/[0.08] p-3">
          <h4 className="text-ui-label font-semibold text-slate-200">
            评分贡献
          </h4>
          {snapshot.scoreContributions.length === 0 ? (
            <p className="mt-2 text-ui-caption text-slate-400">
              当前快照没有评分贡献明细。
            </p>
          ) : (
            <ul className="mt-2 space-y-2 text-ui-caption">
              {snapshot.scoreContributions.map(contribution => (
                <li
                  key={contribution.code}
                  title={contribution.detail}
                  className="flex justify-between gap-3"
                >
                  <span className="text-slate-300">{contribution.label}</span>
                  <span className="shrink-0 font-mono text-slate-200">
                    {formatNumber(contribution.points, 1)} /{' '}
                    {formatNumber(contribution.maxPoints, 1)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <div className="break-all border-t border-white/[0.06] pt-3 text-ui-caption text-slate-400">
        <span className="font-semibold">因果身份</span> ·{' '}
        <span className="font-mono">
          {snapshot.candidateId || '无候选'} · source {snapshot.sourceTimeMs} ·
          tick {snapshot.tickOrdinal} · generation{' '}
          {snapshot.continuityGeneration}
        </span>
        <div className="mt-1 font-mono">
          policy {snapshot.policyVersion} · features{' '}
          {snapshot.featureSchemaVersion} · profile{' '}
          {snapshot.profileVersion || '未记录'} · state{' '}
          {snapshot.candidateStateVersion}
        </div>
      </div>
    </div>
  );
}
