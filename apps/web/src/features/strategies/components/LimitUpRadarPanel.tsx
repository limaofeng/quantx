import {
  AlertTriangle,
  Clock3,
  ExternalLink,
  Radar,
  Search,
  ShieldCheck,
} from 'lucide-react';
import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type UIEvent,
} from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/utils/cn';

import type {
  RadarCandidate,
  RadarIndustryHeat,
  RadarStage,
  RadarSummary,
} from '../hooks/useLimitUpRadar';

import { LimitUpRadarMiniChart } from './LimitUpRadarMiniChart';

const ROW_HEIGHT = 76;
const VIEWPORT_HEIGHT = 624;
const OVERSCAN = 5;

const stageTone: Record<RadarStage, string> = {
  BROKEN: 'border-amber-400/25 bg-amber-400/10 text-amber-200',
  MOMENTUM: 'border-sky-400/25 bg-sky-400/10 text-sky-200',
  NEAR_LIMIT: 'border-red-400/30 bg-red-500/10 text-red-200',
  RESEALED: 'border-fuchsia-400/30 bg-fuchsia-500/10 text-fuchsia-200',
  SEALED: 'border-rose-400/30 bg-rose-500/10 text-rose-200',
  SURGING: 'border-orange-400/25 bg-orange-500/10 text-orange-200',
  TOUCHING: 'border-pink-400/30 bg-pink-500/10 text-pink-200',
};

const stages: Array<{ label: string; value: RadarStage | 'ALL' }> = [
  { label: '全部阶段', value: 'ALL' },
  { label: '异动', value: 'MOMENTUM' },
  { label: '冲板', value: 'SURGING' },
  { label: '临板', value: 'NEAR_LIMIT' },
  { label: '触板', value: 'TOUCHING' },
  { label: '封板', value: 'SEALED' },
  { label: '炸板', value: 'BROKEN' },
  { label: '回封', value: 'RESEALED' },
];

function CandidateRow({
  armed,
  assistantEnabled,
  busy,
  candidate,
  managed,
  onArm,
  onDisarm,
  onOpenStock,
  pending,
  style,
}: {
  armed: boolean;
  assistantEnabled: boolean;
  busy: boolean;
  candidate: RadarCandidate;
  managed: boolean;
  onArm: (code: string) => void;
  onDisarm: (code: string) => void;
  onOpenStock: (code: string) => void;
  pending: boolean;
  style: CSSProperties;
}) {
  const hardBlocked =
    candidate.isStale ||
    candidate.oneWordLimitUp ||
    !candidate.promotionEligible;
  const autoTracked =
    assistantEnabled &&
    candidate.stage === 'NEAR_LIMIT' &&
    candidate.promotionEligible &&
    !hardBlocked;
  const stateLabel = managed
    ? '已托管'
    : pending
      ? '待确认'
      : armed
        ? candidate.stage === 'NEAR_LIMIT'
          ? '优先关注'
          : '等待临板'
        : autoTracked
          ? '晋级候选'
          : '仅观察';

  return (
    <div
      style={style}
      className={cn(
        'absolute left-0 top-0 grid w-full grid-cols-[minmax(170px,1.35fr)_76px_76px_84px_90px_88px_82px_88px_98px] items-center gap-2 rounded-md border px-3 text-left focus-within:ring-2 focus-within:ring-cyan-400/60',
        candidate.isStale
          ? 'border-white/[0.04] bg-slate-950/70 opacity-55'
          : pending
            ? 'border-amber-400/30 bg-amber-400/[0.06]'
            : managed
              ? 'border-emerald-400/25 bg-emerald-400/[0.05]'
              : 'border-white/[0.06] bg-[#0a1322] hover:border-white/15 hover:bg-white/[0.035]'
      )}
      data-testid={`limit-up-candidate-${candidate.code}`}
    >
      <button
        type="button"
        onClick={() => onOpenStock(candidate.code)}
        className="min-w-0 rounded text-left focus-visible:outline-none"
        aria-label={`查看 ${candidate.name} ${candidate.code}`}
      >
        <span className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-xs font-black text-slate-100">
            {candidate.name}
          </span>
          {candidate.isStale ? (
            <Clock3 className="h-3 w-3 shrink-0 text-amber-300" />
          ) : null}
        </span>
        <span className="mt-1 block truncate font-mono text-[10px] text-slate-500">
          {candidate.code} · {candidate.boardSegment === 'GROWTH' ? '双创' : '主板'} ·{' '}
          {candidate.industry || '未分类'}
        </span>
      </button>
      <span className="text-center">
        <span
          className={cn(
            'inline-flex rounded border px-1.5 py-1 text-[10px] font-black',
            stageTone[candidate.stage]
          )}
        >
          {candidate.stageLabel}
        </span>
        <span className="mt-1 block text-[9px] text-slate-600">
          进度 {(candidate.normalizedLimitProgress * 100).toFixed(0)}%
        </span>
      </span>
      <span className="text-center font-mono text-sm font-black text-cyan-200">
        {(candidate.firstBoardCloseProbability * 100).toFixed(0)}%
        <span className="block text-[9px] font-normal text-slate-600">首板封住</span>
      </span>
      <span className="text-center font-mono text-[12px] font-black text-violet-200">
        {(candidate.nextDayLimitTouchProbability * 100).toFixed(0)}%
        <span className="block text-[9px] font-normal text-slate-600">
          二板触及 / {(candidate.nextDayLimitSealProbability * 100).toFixed(0)}% 封住
        </span>
      </span>
      <span className="text-center font-mono text-[11px] font-black text-red-300">
        {candidate.expectedNetReturnPct >= 0 ? '+' : ''}
        {candidate.expectedNetReturnPct.toFixed(2)}%
        <span className="block text-[9px] font-normal text-slate-600">
          净期望 / CVaR {candidate.cvar95LossPct.toFixed(1)}%
        </span>
      </span>
      <span className="text-center text-[10px] font-black text-slate-300">
        {{
          BASE_BREAKOUT: '基底突破',
          HIGH_BREAKOUT: '高位突破',
          OVERHEATED: '过热加速',
          DATA_UNKNOWN: '数据未知',
        }[candidate.highPositionType] ?? candidate.highPositionType}
        <span className="mt-1 block font-mono text-[9px] font-normal text-slate-600">
          晋级分 {candidate.promotionScore.toFixed(1)}
        </span>
      </span>
      <span className="text-center text-[10px] font-black text-slate-300">
        {candidate.researchArtifact ? '研究已生成' : '等待研究'}
        <span className="mt-1 block text-[9px] font-normal text-slate-600">
          {candidate.researchArtifact?.dataGaps.length
            ? `${candidate.researchArtifact.dataGaps.length} 项缺口`
            : candidate.researchArtifact
              ? '公告证据已附'
              : 'AI 不影响资格'}
        </span>
      </span>
      <span className="text-center text-[10px] text-slate-400">
        {candidate.promotionEligible ? '资格通过' : '硬否决'}
        <span className="mt-1 block truncate text-[9px] text-slate-600">
          {candidate.blockedReasons[0] || '确定性规则'}
        </span>
      </span>
      {candidate.isStale ? (
        <span className="rounded-md border border-amber-400/15 bg-amber-400/[0.06] px-2 py-1.5 text-center text-[10px] font-black text-amber-200/80">
          报价过期
        </span>
      ) : managed || pending ? (
        <span
          className={cn(
            'rounded-md border px-2 py-1.5 text-center text-[10px] font-black',
            managed
              ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
              : 'border-amber-400/25 bg-amber-400/10 text-amber-200'
          )}
        >
          {stateLabel}
        </span>
      ) : armed ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={busy}
          onClick={() => onDisarm(candidate.code)}
          className="h-7 border-white/10 bg-white/[0.03] px-2 text-[10px] text-slate-300 hover:bg-white/[0.07]"
        >
          {busy ? '处理中' : '取消关注'}
        </Button>
      ) : (
        <Button
          type="button"
          size="sm"
          disabled={busy || hardBlocked}
          onClick={() => onArm(candidate.code)}
          title={
            candidate.isStale ? '报价超过15秒，仅供观察' : '提高账户候选关注优先级'
          }
          className="h-7 bg-red-500 px-2 text-[10px] font-black text-white hover:bg-red-400"
        >
          {busy ? '处理中' : autoTracked ? '优先关注' : '关注'}
        </Button>
      )}
    </div>
  );
}

export function LimitUpRadarPanel({
  armedCodes,
  assistantEnabled,
  busyCode,
  candidates,
  errorMessage,
  exitPlanCodes,
  fetching,
  industries,
  industry,
  isScannerRunning,
  onArm,
  onDisarm,
  onIndustryChange,
  onOpenStock,
  onSearchChange,
  onStageChange,
  pendingCodes,
  search,
  stage,
  summary,
  systemWarnings,
}: {
  armedCodes: Set<string>;
  assistantEnabled: boolean;
  busyCode?: string | null;
  candidates: RadarCandidate[];
  errorMessage?: string;
  exitPlanCodes: Set<string>;
  fetching: boolean;
  industries: RadarIndustryHeat[];
  industry: string;
  isScannerRunning: boolean;
  onArm: (code: string) => void;
  onDisarm: (code: string) => void;
  onIndustryChange: (value: string) => void;
  onOpenStock: (code: string) => void;
  onSearchChange: (value: string) => void;
  onStageChange: (value: RadarStage | 'ALL') => void;
  pendingCodes: Set<string>;
  search: string;
  stage: RadarStage | 'ALL';
  summary: RadarSummary;
  systemWarnings: string[];
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const selected = useMemo(
    () =>
      candidates.find(item => item.code === selectedCode) ?? candidates[0] ?? null,
    [candidates, selectedCode]
  );
  useEffect(() => {
    if (selectedCode && !candidates.some(item => item.code === selectedCode)) {
      setSelectedCode(null);
    }
  }, [candidates, selectedCode]);
  const start = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visibleCount = Math.ceil(VIEWPORT_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2;
  const visible = useMemo(
    () => candidates.slice(start, start + visibleCount),
    [candidates, start, visibleCount]
  );
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
  };

  return (
    <section
      className="min-w-0 rounded-lg border border-cyan-400/15 bg-[#0d1626]/90"
      data-testid="limit-up-radar-panel"
    >
      <div className="flex flex-col gap-2 border-b border-white/[0.07] p-3 xl:flex-row xl:items-center xl:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-sm font-black text-slate-100">
            <Radar className="h-4 w-4 text-cyan-300" />
            首板晋级候选
          </div>
          <span className="font-mono text-[10px] text-slate-500">
            {summary.discoveredCount} 发现 · {summary.eligibleCount} 合格 ·{' '}
            {summary.scannedCount} 已扫描
          </span>
          <span className="flex items-center gap-1.5 text-[10px] text-slate-500">
            <span
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                isScannerRunning ? 'bg-emerald-400' : 'bg-rose-400'
              )}
            />
            {fetching ? '刷新中' : isScannerRunning ? '实时扫描' : '雷达离线'}
          </span>
        </div>
        <div className="grid grid-cols-[minmax(150px,1fr)_110px_130px] gap-2">
          <label className="relative min-w-0">
            <span className="sr-only">搜索候选</span>
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-600" />
            <Input
              value={search}
              onChange={event => onSearchChange(event.target.value)}
              placeholder="代码 / 名称"
              className="h-8 border-white/10 bg-[#08111f] pl-8 text-[11px]"
            />
          </label>
          <label>
            <span className="sr-only">候选阶段</span>
            <select
              value={stage}
              onChange={event =>
                onStageChange(event.target.value as RadarStage | 'ALL')
              }
              className="h-8 w-full rounded-md border border-white/10 bg-[#08111f] px-2 text-[11px] text-slate-300 outline-none focus:ring-2 focus:ring-cyan-400/50"
            >
              {stages.map(item => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">候选行业</span>
            <select
              value={industry}
              onChange={event => onIndustryChange(event.target.value)}
              className="h-8 w-full rounded-md border border-white/10 bg-[#08111f] px-2 text-[11px] text-slate-300 outline-none focus:ring-2 focus:ring-cyan-400/50"
            >
              <option value="ALL">全部行业</option>
              {industries.map(item => (
                <option key={item.industry} value={item.industry}>
                  {item.industry} · {item.candidateCount}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {errorMessage || systemWarnings.length ? (
        <div
          role="alert"
          className="m-3 flex items-start gap-2 rounded-md border border-rose-400/25 bg-rose-500/[0.08] px-3 py-2 text-[10px] leading-4 text-rose-100"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="font-black">系统保护</span>
          <span className="text-rose-100/75">
            {[errorMessage, ...systemWarnings].filter(Boolean).join(' · ')}
          </span>
        </div>
      ) : null}

      <div className="overflow-x-auto px-3 pb-3 custom-scrollbar">
        <div className="min-w-[1080px]">
          <div className="grid grid-cols-[minmax(170px,1.35fr)_76px_76px_84px_90px_88px_82px_88px_98px] gap-2 border-b border-white/[0.06] px-3 py-2 text-[9px] font-black tracking-wider text-slate-600">
            <span>候选标的</span>
            <span className="text-center">生命周期</span>
            <span className="text-center">首板封住</span>
            <span className="text-center">T+1 晋级</span>
            <span className="text-center">收益 / 尾损</span>
            <span className="text-center">价格位置</span>
            <span className="text-center">AI 研究</span>
            <span className="text-center">资格</span>
            <span className="text-center">操作</span>
          </div>
          {candidates.length ? (
            <div
              className="mt-2 overflow-y-auto overscroll-contain custom-scrollbar"
              style={{ height: VIEWPORT_HEIGHT }}
              onScroll={onScroll}
            >
              <div
                className="relative"
                style={{ height: candidates.length * ROW_HEIGHT }}
              >
                {visible.map((candidate, index) => (
                  <CandidateRow
                    key={candidate.code}
                    candidate={candidate}
                    style={{
                      height: ROW_HEIGHT - 5,
                      transform: `translateY(${(start + index) * ROW_HEIGHT}px)`,
                    }}
                    armed={armedCodes.has(candidate.code)}
                    assistantEnabled={assistantEnabled}
                    busy={busyCode === candidate.code}
                    managed={exitPlanCodes.has(candidate.code)}
                    pending={pendingCodes.has(candidate.code)}
                    onArm={onArm}
                    onDisarm={onDisarm}
                    onOpenStock={setSelectedCode}
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="flex h-[610px] flex-col items-center justify-center rounded-md border border-dashed border-white/10 text-center">
              <ShieldCheck className="h-8 w-8 text-slate-700" />
              <p className="mt-3 text-xs font-black text-slate-300">
                暂无匹配候选
              </p>
              <p className="mt-1 text-[10px] text-slate-600">
                雷达会持续扫描；非交易时段保留最近快照供观察。
              </p>
            </div>
          )}
        </div>
      </div>
      {selected ? (
        <div className="grid gap-px border-t border-white/[0.06] bg-white/[0.06] lg:grid-cols-[220px_1fr_1fr]">
          <section className="bg-[#0a1322] p-3" aria-label="候选走势">
            <div className="mb-2 flex items-center justify-between">
              <div>
                <h3 className="text-[11px] font-black text-slate-200">
                  {selected.name} · 生命周期
                </h3>
                <p className="mt-0.5 font-mono text-[9px] text-slate-600">
                  数据 {new Date(selected.updatedAt).toLocaleTimeString('zh-CN')}
                </p>
              </div>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-[9px]"
                onClick={() => onOpenStock(selected.code)}
              >
                打开 K 线
              </Button>
            </div>
            <LimitUpRadarMiniChart code={selected.code} />
          </section>
          <section className="bg-[#0a1322] p-3" aria-label="晋级因子证据">
            <h3 className="text-[11px] font-black text-slate-200">因子与判断依据</h3>
            <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
              {selected.promotionFactors.map(factor => (
                <div
                  key={factor.code}
                  className="rounded border border-white/[0.06] bg-white/[0.025] px-2 py-1.5"
                >
                  <div className="flex justify-between text-[9px]">
                    <span className="font-black text-slate-300">{factor.label}</span>
                    <span className="font-mono text-cyan-300">
                      {factor.contribution.toFixed(2)}
                    </span>
                  </div>
                  <p className="mt-1 text-[9px] leading-4 text-slate-600">
                    {factor.explanation}
                  </p>
                </div>
              ))}
            </div>
            <div className="mt-2 flex flex-wrap gap-1">
              {selected.events.slice(0, 6).map(event => (
                <span
                  key={event.eventId}
                  className="rounded border border-white/[0.06] px-1.5 py-1 text-[9px] text-slate-500"
                >
                  {event.stageLabel} ·{' '}
                  {new Date(event.occurredAt).toLocaleTimeString('zh-CN')}
                </span>
              ))}
            </div>
          </section>
          <section className="bg-[#0a1322] p-3" aria-label="AI 公告研究">
            <div className="flex items-center justify-between">
              <h3 className="text-[11px] font-black text-slate-200">AI 公告研究</h3>
              <span className="text-[9px] text-slate-600">不参与交易资格</span>
            </div>
            {selected.researchArtifact ? (
              <div className="mt-2 space-y-2 text-[9px] leading-4">
                <p className="text-slate-400">{selected.researchArtifact.summary}</p>
                {selected.researchArtifact.announcementRisks.length ? (
                  <p className="text-amber-200/80">
                    公告风险：{selected.researchArtifact.announcementRisks.join('；')}
                  </p>
                ) : null}
                {selected.researchArtifact.dataGaps.length ? (
                  <p className="text-slate-600">
                    数据缺口：{selected.researchArtifact.dataGaps.join('；')}
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-1">
                  {selected.researchArtifact.citations.slice(0, 5).map(citation =>
                    /^https?:\/\//i.test(citation) ? (
                      <a
                        key={citation}
                        href={citation}
                        target="_blank"
                        rel="noreferrer"
                        className="max-w-[180px] truncate rounded border border-cyan-400/15 px-1.5 py-1 text-cyan-300 hover:bg-cyan-400/10"
                      >
                        公告引用
                      </a>
                    ) : (
                      <span
                        key={citation}
                        className="max-w-[240px] truncate rounded border border-white/10 px-1.5 py-1 text-slate-500"
                        title={citation}
                      >
                        {citation}
                      </span>
                    ),
                  )}
                </div>
              </div>
            ) : (
              <p className="mt-3 rounded border border-dashed border-white/10 p-3 text-[9px] leading-4 text-slate-600">
                候选进入动态 Top 5 后会生成一次市场级共享研究。AI Runtime 离线或公告缺失不会改变资格。
              </p>
            )}
          </section>
        </div>
      ) : null}
      <div className="flex items-center justify-between border-t border-white/[0.06] px-3 py-2 text-[9px] text-slate-600">
        <span>稳定行高 · 超过 100 条自动虚拟滚动</span>
        <button
          type="button"
          className="flex items-center gap-1 rounded px-1.5 py-1 text-slate-500 hover:bg-white/[0.05] hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
          onClick={() => selected && onOpenStock(selected.code)}
          disabled={!selected}
        >
          <ExternalLink className="h-3 w-3" />
          打开首位候选
        </button>
      </div>
    </section>
  );
}
