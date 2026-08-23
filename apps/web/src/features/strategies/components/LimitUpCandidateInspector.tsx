import { ExternalLink } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { financialToneClass } from '@/shared/utils/financialColors';

import type { RadarCandidate } from '../hooks/useLimitUpRadar';

import { LimitUpRadarMiniChart } from './LimitUpRadarMiniChart';

export interface LimitUpCandidateInspectorProps {
  candidate: RadarCandidate;
  onOpenStock: (code: string) => void;
}

export function LimitUpCandidateInspector({
  candidate,
  onOpenStock,
}: LimitUpCandidateInspectorProps) {
  return (
    <div className="h-full overflow-y-auto p-5 custom-scrollbar">
      <header className="border-b border-white/[0.06] pb-4 pr-8 text-left">
        <h2 className="text-lg font-black text-slate-100">{candidate.name}</h2>
        <p className="mt-2 font-mono text-[10px] text-slate-500">
          {candidate.code} · 首板晋级候选检查器
        </p>
      </header>

      <div className="mt-5 space-y-5">
        <section
          aria-label="晋级概率摘要"
          className="grid grid-cols-2 gap-px border border-white/[0.07] bg-white/[0.07] sm:grid-cols-4"
        >
          <InspectorMetric
            label="首板封住"
            tone="text-cyan-200"
            value={`${(candidate.firstBoardCloseProbability * 100).toFixed(0)}%`}
          />
          <InspectorMetric
            label="T+1 触及"
            tone="text-violet-200"
            value={`${(candidate.nextDayLimitTouchProbability * 100).toFixed(0)}%`}
          />
          <InspectorMetric
            label="T+1 封住"
            tone="text-violet-200"
            value={`${(candidate.nextDayLimitSealProbability * 100).toFixed(0)}%`}
          />
          <InspectorMetric
            detail={`CVaR ${candidate.cvar95LossPct.toFixed(1)}%`}
            label="净期望"
            tone={financialToneClass(candidate.expectedNetReturnPct)}
            value={`${candidate.expectedNetReturnPct >= 0 ? '+' : ''}${candidate.expectedNetReturnPct.toFixed(2)}%`}
          />
        </section>

        <section className="border border-white/[0.07] bg-white/[0.02] p-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-xs font-black text-slate-200">
                生命周期走势
              </h3>
              <p className="mt-1 font-mono text-[10px] text-slate-500">
                数据 {new Date(candidate.updatedAt).toLocaleTimeString('zh-CN')}
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-8 cursor-pointer rounded-sm border-white/10 bg-white/[0.025] px-2.5 text-[10px] text-slate-300 hover:bg-white/[0.06]"
              onClick={() => onOpenStock(candidate.code)}
            >
              <ExternalLink className="h-3.5 w-3.5" />
              打开个股详情
            </Button>
          </div>
          <LimitUpRadarMiniChart code={candidate.code} />
        </section>

        <section>
          <h3 className="text-xs font-black text-slate-200">
            晋级因子与判断依据
          </h3>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {candidate.promotionFactors.map(factor => (
              <div
                key={factor.code}
                className="border border-white/[0.06] bg-white/[0.025] px-3 py-2.5"
              >
                <div className="flex items-center justify-between gap-3 text-[10px]">
                  <span className="font-black text-slate-300">
                    {factor.label}
                  </span>
                  <span className="font-mono font-bold text-cyan-300">
                    {factor.contribution.toFixed(2)}
                  </span>
                </div>
                <p className="mt-1.5 text-[10px] leading-4 text-slate-500">
                  {factor.explanation}
                </p>
              </div>
            ))}
          </div>
          {candidate.events.length ? (
            <div
              className="mt-3 flex flex-wrap gap-1.5"
              aria-label="生命周期事件"
            >
              {candidate.events.slice(0, 6).map(event => (
                <span
                  key={event.eventId}
                  className="border border-white/[0.06] px-2 py-1 text-[10px] text-slate-500"
                >
                  {event.stageLabel} ·{' '}
                  {new Date(event.occurredAt).toLocaleTimeString('zh-CN')}
                </span>
              ))}
            </div>
          ) : null}
        </section>

        <section>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-xs font-black text-slate-200">AI 公告研究</h3>
            <span className="text-[10px] text-slate-500">不参与交易资格</span>
          </div>
          {candidate.researchArtifact ? (
            <div className="mt-3 space-y-3 border border-white/[0.07] bg-white/[0.02] p-3 text-[10px] leading-5">
              <p className="text-slate-400">
                {candidate.researchArtifact.summary}
              </p>
              {candidate.researchArtifact.announcementRisks.length ? (
                <p className="text-amber-200/80">
                  公告风险：
                  {candidate.researchArtifact.announcementRisks.join('；')}
                </p>
              ) : null}
              {candidate.researchArtifact.dataGaps.length ? (
                <p className="text-slate-500">
                  数据缺口：{candidate.researchArtifact.dataGaps.join('；')}
                </p>
              ) : null}
              <div className="flex flex-wrap gap-1.5">
                {candidate.researchArtifact.citations
                  .slice(0, 5)
                  .map(citation =>
                    /^https?:\/\//i.test(citation) ? (
                      <a
                        key={citation}
                        href={citation}
                        target="_blank"
                        rel="noreferrer"
                        className="max-w-[220px] cursor-pointer truncate border border-cyan-400/15 px-2 py-1 text-cyan-300 transition-colors duration-200 hover:bg-cyan-400/10"
                      >
                        公告引用
                      </a>
                    ) : (
                      <span
                        key={citation}
                        className="max-w-[280px] truncate border border-white/10 px-2 py-1 text-slate-500"
                        title={citation}
                      >
                        {citation}
                      </span>
                    )
                  )}
              </div>
            </div>
          ) : (
            <p className="mt-3 border border-dashed border-white/10 p-3 text-[10px] leading-5 text-slate-500">
              候选进入动态 Top 5 后会生成一次市场级共享研究。AI Runtime
              离线或公告缺失不会改变资格。
            </p>
          )}
        </section>
      </div>
    </div>
  );
}

function InspectorMetric({
  detail,
  label,
  tone,
  value,
}: {
  detail?: string;
  label: string;
  tone: string;
  value: string;
}) {
  return (
    <div className="bg-[#0a1524] px-3 py-2.5 text-[9px] text-slate-600">
      {label}
      <strong className={`mt-1 block font-mono text-sm ${tone}`}>
        {value}
      </strong>
      {detail ? (
        <span className="mt-0.5 block font-mono text-[8px] text-slate-600">
          {detail}
        </span>
      ) : null}
    </div>
  );
}
