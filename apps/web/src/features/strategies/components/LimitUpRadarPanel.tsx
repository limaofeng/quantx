import {
  AlertTriangle,
  Clock3,
  ExternalLink,
  Radar,
  Search,
  ShieldCheck,
} from 'lucide-react';
import {
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

const ROW_HEIGHT = 64;
const VIEWPORT_HEIGHT = 610;
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
  autoScore,
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
  autoScore: number;
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
    candidate.blockedReasons.some(reason =>
      ['ONE_WORD_LIMIT_UP', 'LIMIT_UP_ALREADY_REACHED'].includes(reason)
    );
  const autoTracked =
    assistantEnabled &&
    candidate.stage === 'NEAR_LIMIT' &&
    candidate.radarScore >= autoScore &&
    !hardBlocked;
  const stateLabel = managed
    ? '已托管'
    : pending
      ? '待确认'
      : armed
        ? candidate.stage === 'NEAR_LIMIT'
          ? '人工布防'
          : '等待临板'
        : autoTracked
          ? '自动布防'
          : '仅观察';

  return (
    <div
      style={style}
      className={cn(
        'absolute left-0 top-0 grid w-full grid-cols-[minmax(150px,1.35fr)_74px_70px_72px_70px_62px_96px] items-center gap-2 rounded-md border px-3 text-left focus-within:ring-2 focus-within:ring-cyan-400/60',
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
          {candidate.code} · {candidate.industry || '未分类'}
        </span>
      </button>
      <span className="text-right font-mono text-xs font-black text-red-300">
        {candidate.currentPrice.toFixed(2)}
        <span className="block text-[9px] text-red-400/75">
          +{candidate.changePct.toFixed(2)}%
        </span>
      </span>
      <span className="text-right font-mono text-[11px] text-slate-300">
        {candidate.distanceToLimitPct.toFixed(2)}%
        <span className="block text-[9px] text-slate-600">距涨停</span>
      </span>
      <span className="text-right font-mono text-[11px] text-slate-300">
        {candidate.last5mVolumeRatio.toFixed(1)}x
        <span className="block text-[9px] text-slate-600">5m 量比</span>
      </span>
      <span
        className={cn(
          'rounded border px-1.5 py-1 text-center text-[10px] font-black',
          stageTone[candidate.stage]
        )}
      >
        {candidate.stageLabel}
      </span>
      <span className="text-center font-mono text-sm font-black text-cyan-200">
        {candidate.radarScore.toFixed(1)}
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
          {busy ? '处理中' : '取消布防'}
        </Button>
      ) : (
        <Button
          type="button"
          size="sm"
          disabled={busy || hardBlocked}
          onClick={() => onArm(candidate.code)}
          title={
            candidate.isStale ? '报价超过15秒，仅供观察' : '加入当日人工布防'
          }
          className="h-7 bg-red-500 px-2 text-[10px] font-black text-white hover:bg-red-400"
        >
          {busy ? '处理中' : autoTracked ? '保留至收盘' : '当日布防'}
        </Button>
      )}
    </div>
  );
}

export function LimitUpRadarPanel({
  armedCodes,
  assistantEnabled,
  autoScore,
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
  autoScore: number;
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
            市场候选
          </div>
          <span className="font-mono text-[10px] text-slate-500">
            {summary.candidateCount} 候选 · {summary.scannedCount} 已扫描
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
        <div className="min-w-[760px]">
          <div className="grid grid-cols-[minmax(150px,1.35fr)_74px_70px_72px_70px_62px_96px] gap-2 border-b border-white/[0.06] px-3 py-2 text-[9px] font-black uppercase tracking-wider text-slate-600">
            <span>候选标的</span>
            <span className="text-right">价格</span>
            <span className="text-right">距离</span>
            <span className="text-right">量能</span>
            <span className="text-center">阶段</span>
            <span className="text-center">评分</span>
            <span className="text-center">状态 / 操作</span>
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
                    autoScore={autoScore}
                    busy={busyCode === candidate.code}
                    managed={exitPlanCodes.has(candidate.code)}
                    pending={pendingCodes.has(candidate.code)}
                    onArm={onArm}
                    onDisarm={onDisarm}
                    onOpenStock={onOpenStock}
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
      <div className="flex items-center justify-between border-t border-white/[0.06] px-3 py-2 text-[9px] text-slate-600">
        <span>稳定行高 · 超过 100 条自动虚拟滚动</span>
        <button
          type="button"
          className="flex items-center gap-1 rounded px-1.5 py-1 text-slate-500 hover:bg-white/[0.05] hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
          onClick={() => candidates[0] && onOpenStock(candidates[0].code)}
          disabled={!candidates.length}
        >
          <ExternalLink className="h-3 w-3" />
          打开首位候选
        </button>
      </div>
    </section>
  );
}
