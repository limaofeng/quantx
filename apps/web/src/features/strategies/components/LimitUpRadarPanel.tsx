import {
  AlertTriangle,
  Clock3,
  PanelRightOpen,
  Radar,
  Search,
  ShieldCheck,
} from 'lucide-react';
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type UIEvent,
} from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/native-select';
import { cn } from '@/utils/cn';

import type {
  RadarCandidate,
  RadarIndustryHeat,
  RadarStage,
  RadarSummary,
} from '../hooks/useLimitUpRadar';

type CandidateLayout = 'wide' | 'compact' | 'narrow';

const DEFAULT_CONTAINER_WIDTH = 1080;
const FALLBACK_VIEWPORT_HEIGHT = 624;
const WIDE_LAYOUT_MIN_WIDTH = 1080;
const COMPACT_LAYOUT_MIN_WIDTH = 720;
const OVERSCAN = 5;
const ROW_HEIGHTS: Record<CandidateLayout, number> = {
  wide: 76,
  compact: 96,
  narrow: 170,
};

const wideGridColumns =
  'grid-cols-[minmax(148px,1.35fr)_68px_70px_78px_84px_80px_74px_80px_92px]';
const compactGridColumns =
  'grid-cols-[minmax(130px,1.2fr)_64px_70px_76px_minmax(110px,1fr)_82px]';

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

function getCandidateLayout(width: number): CandidateLayout {
  if (width >= WIDE_LAYOUT_MIN_WIDTH) return 'wide';
  if (width >= COMPACT_LAYOUT_MIN_WIDTH) return 'compact';
  return 'narrow';
}

function getHighPositionLabel(value: string) {
  return (
    {
      BASE_BREAKOUT: '基底突破',
      HIGH_BREAKOUT: '高位突破',
      OVERHEATED: '过热加速',
      DATA_UNKNOWN: '数据未知',
    }[value] ?? value
  );
}

function getResearchLabel(candidate: RadarCandidate) {
  return candidate.researchArtifact ? '研究已生成' : '等待研究';
}

function getResearchDetail(candidate: RadarCandidate) {
  return candidate.researchArtifact?.dataGaps.length
    ? `${candidate.researchArtifact.dataGaps.length} 项缺口`
    : candidate.researchArtifact
      ? '公告证据已附'
      : 'AI 不影响资格';
}

function CandidateStage({ candidate }: { candidate: RadarCandidate }) {
  return (
    <span className="text-center">
      <span
        className={cn(
          'inline-flex rounded border px-1.5 py-1 text-ui-caption font-black',
          stageTone[candidate.stage]
        )}
      >
        {candidate.stageLabel}
      </span>
      <span className="mt-1 block text-ui-micro text-slate-500">
        进度 {(candidate.normalizedLimitProgress * 100).toFixed(0)}%
      </span>
    </span>
  );
}

function CandidateIdentity({ candidate }: { candidate: RadarCandidate }) {
  return (
    <span className="min-w-0 text-left">
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="truncate text-ui-label font-black text-slate-100">
          {candidate.name}
        </span>
        {candidate.isStale ? (
          <Clock3 className="h-3 w-3 shrink-0 text-amber-300" />
        ) : null}
      </span>
      <span className="mt-0.5 block truncate font-mono text-ui-micro text-slate-500">
        {candidate.code} ·{' '}
        {candidate.boardSegment === 'GROWTH' ? '双创' : '主板'} ·{' '}
        {candidate.industry || '未分类'}
      </span>
    </span>
  );
}

function CandidateAction({
  armed,
  autoTracked,
  busy,
  candidate,
  hardBlocked,
  managed,
  onArm,
  onDisarm,
  pending,
  stateLabel,
}: {
  armed: boolean;
  autoTracked: boolean;
  busy: boolean;
  candidate: RadarCandidate;
  hardBlocked: boolean;
  managed: boolean;
  onArm: (code: string) => void;
  onDisarm: (code: string) => void;
  pending: boolean;
  stateLabel: string;
}) {
  if (candidate.isStale) {
    return (
      <span className="rounded-md border border-amber-400/15 bg-amber-400/[0.06] px-2 py-1.5 text-center text-ui-caption font-black text-amber-200/80">
        报价过期
      </span>
    );
  }

  if (managed || pending) {
    return (
      <span
        className={cn(
          'rounded-md border px-2 py-1.5 text-center text-ui-caption font-black',
          managed
            ? 'border-emerald-400/25 bg-emerald-400/10 text-emerald-200'
            : 'border-amber-400/25 bg-amber-400/10 text-amber-200'
        )}
      >
        {stateLabel}
      </span>
    );
  }

  if (armed) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={event => {
          event.stopPropagation();
          onDisarm(candidate.code);
        }}
        className="h-7 cursor-pointer border-white/10 bg-white/[0.03] px-2 text-ui-caption text-slate-300 hover:bg-white/[0.07]"
      >
        {busy ? '处理中' : '取消关注'}
      </Button>
    );
  }

  return (
    <Button
      type="button"
      size="sm"
      disabled={busy || hardBlocked}
      onClick={event => {
        event.stopPropagation();
        onArm(candidate.code);
      }}
      title={
        candidate.isStale ? '报价超过15秒，仅供观察' : '提高账户候选关注优先级'
      }
      className="h-7 cursor-pointer bg-red-500 px-2 text-ui-caption font-black text-white hover:bg-red-400"
    >
      {busy ? '处理中' : autoTracked ? '优先关注' : '关注'}
    </Button>
  );
}

function CandidateMetric({
  detail,
  tone,
  value,
}: {
  detail: string;
  tone: string;
  value: string;
}) {
  return (
    <span className={cn('text-center font-mono font-black', tone)}>
      {value}
      <span className="block text-ui-micro font-normal text-slate-500">
        {detail}
      </span>
    </span>
  );
}

function CandidateRow({
  armed,
  assistantEnabled,
  busy,
  candidate,
  layout,
  managed,
  onArm,
  onDisarm,
  onSelect,
  pending,
  rowIndex,
  selected,
  style,
}: {
  armed: boolean;
  assistantEnabled: boolean;
  busy: boolean;
  candidate: RadarCandidate;
  layout: CandidateLayout;
  managed: boolean;
  onArm: (code: string) => void;
  onDisarm: (code: string) => void;
  onSelect: (code: string) => void;
  pending: boolean;
  rowIndex: number;
  selected: boolean;
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
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar')
      return;
    event.preventDefault();
    onSelect(candidate.code);
  };
  const action = (
    <CandidateAction
      armed={armed}
      autoTracked={autoTracked}
      busy={busy}
      candidate={candidate}
      hardBlocked={hardBlocked}
      managed={managed}
      onArm={onArm}
      onDisarm={onDisarm}
      pending={pending}
      stateLabel={stateLabel}
    />
  );

  return (
    <div
      style={style}
      className={cn(
        'absolute left-0 top-0 w-full cursor-pointer border-b border-white/[0.045] text-left transition-colors duration-150 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-400/70 motion-reduce:transition-none',
        layout === 'wide' &&
          `grid ${wideGridColumns} items-center gap-2 px-ui-section`,
        layout === 'compact' &&
          `grid ${compactGridColumns} items-center gap-2 px-ui-section`,
        layout === 'narrow' &&
          'flex flex-col justify-between px-ui-section py-2.5',
        candidate.isStale
          ? 'bg-slate-950/45'
          : pending
            ? 'bg-amber-400/[0.045]'
            : managed
              ? 'bg-emerald-400/[0.04]'
              : 'bg-transparent',
        selected &&
          'bg-cyan-400/[0.07] shadow-[inset_3px_0_0_rgba(103,232,249,0.8)]'
      )}
      data-testid={`limit-up-candidate-${candidate.code}`}
      aria-label={`查看 ${candidate.name} ${candidate.code}`}
      aria-rowindex={rowIndex}
      aria-selected={selected}
      onClick={() => onSelect(candidate.code)}
      onKeyDown={handleKeyDown}
      role="row"
      tabIndex={0}
    >
      {layout === 'wide' ? (
        <>
          <span className="min-w-0" role="gridcell">
            <CandidateIdentity candidate={candidate} />
          </span>
          <span role="gridcell">
            <CandidateStage candidate={candidate} />
          </span>
          <span role="gridcell">
            <CandidateMetric
              detail="首板封住"
              tone="text-ui-body text-cyan-200"
              value={`${(candidate.firstBoardCloseProbability * 100).toFixed(0)}%`}
            />
          </span>
          <span role="gridcell">
            <CandidateMetric
              detail={`二板触及 / ${(candidate.nextDayLimitSealProbability * 100).toFixed(0)}% 封住`}
              tone="text-ui-label text-violet-200"
              value={`${(candidate.nextDayLimitTouchProbability * 100).toFixed(0)}%`}
            />
          </span>
          <span role="gridcell">
            <CandidateMetric
              detail={`净期望 / CVaR ${candidate.cvar95LossPct.toFixed(1)}%`}
              tone="text-ui-caption text-red-300"
              value={`${candidate.expectedNetReturnPct >= 0 ? '+' : ''}${candidate.expectedNetReturnPct.toFixed(2)}%`}
            />
          </span>
          <span
            className="text-center text-ui-caption font-black text-slate-300"
            role="gridcell"
          >
            {getHighPositionLabel(candidate.highPositionType)}
            <span className="mt-1 block font-mono text-ui-micro font-normal text-slate-500">
              晋级分 {candidate.promotionScore.toFixed(1)}
            </span>
          </span>
          <span
            className="text-center text-ui-caption font-black text-slate-300"
            role="gridcell"
          >
            {getResearchLabel(candidate)}
            <span className="mt-1 block text-ui-micro font-normal text-slate-500">
              {getResearchDetail(candidate)}
            </span>
          </span>
          <span
            className="text-center text-ui-caption text-slate-300"
            role="gridcell"
          >
            {candidate.promotionEligible ? '资格通过' : '硬否决'}
            <span className="mt-1 block truncate text-ui-micro text-slate-500">
              {candidate.blockedReasons[0] || '确定性规则'}
            </span>
          </span>
          <span className="flex justify-center" role="gridcell">
            {action}
          </span>
        </>
      ) : layout === 'compact' ? (
        <>
          <span className="min-w-0" role="gridcell">
            <CandidateIdentity candidate={candidate} />
            <span className="mt-1.5 flex items-center gap-2">
              <span
                className={cn(
                  'inline-flex rounded border px-1.5 py-0.5 text-ui-micro font-black',
                  stageTone[candidate.stage]
                )}
              >
                {candidate.stageLabel}
              </span>
              <span className="text-ui-micro text-slate-500">
                进度 {(candidate.normalizedLimitProgress * 100).toFixed(0)}%
              </span>
            </span>
          </span>
          <span role="gridcell">
            <CandidateMetric
              detail="首板封住"
              tone="text-ui-label text-cyan-200"
              value={`${(candidate.firstBoardCloseProbability * 100).toFixed(0)}%`}
            />
          </span>
          <span role="gridcell">
            <CandidateMetric
              detail={`${(candidate.nextDayLimitSealProbability * 100).toFixed(0)}% 封住`}
              tone="text-ui-label text-violet-200"
              value={`${(candidate.nextDayLimitTouchProbability * 100).toFixed(0)}%`}
            />
          </span>
          <span role="gridcell">
            <CandidateMetric
              detail={`CVaR ${candidate.cvar95LossPct.toFixed(1)}%`}
              tone="text-ui-caption text-red-300"
              value={`${candidate.expectedNetReturnPct >= 0 ? '+' : ''}${candidate.expectedNetReturnPct.toFixed(2)}%`}
            />
          </span>
          <span className="min-w-0 text-ui-micro leading-4" role="gridcell">
            <span className="block truncate font-black text-slate-300">
              {getHighPositionLabel(candidate.highPositionType)} · 晋级分{' '}
              {candidate.promotionScore.toFixed(1)}
            </span>
            <span className="block truncate text-slate-400">
              {getResearchLabel(candidate)} · {getResearchDetail(candidate)}
            </span>
            <span className="block truncate text-slate-500">
              {candidate.promotionEligible ? '资格通过' : '硬否决'} ·{' '}
              {candidate.blockedReasons[0] || '确定性规则'}
            </span>
          </span>
          <span className="flex justify-center" role="gridcell">
            {action}
          </span>
        </>
      ) : (
        <>
          <span
            className="flex w-full min-w-0 items-start justify-between gap-3"
            role="gridcell"
          >
            <CandidateIdentity candidate={candidate} />
            <CandidateStage candidate={candidate} />
          </span>
          <span className="grid w-full grid-cols-3 gap-2" role="gridcell">
            <CandidateMetric
              detail="首板封住"
              tone="text-ui-label text-cyan-200"
              value={`${(candidate.firstBoardCloseProbability * 100).toFixed(0)}%`}
            />
            <CandidateMetric
              detail={`T+1 / ${(candidate.nextDayLimitSealProbability * 100).toFixed(0)}% 封`}
              tone="text-ui-label text-violet-200"
              value={`${(candidate.nextDayLimitTouchProbability * 100).toFixed(0)}%`}
            />
            <CandidateMetric
              detail={`CVaR ${candidate.cvar95LossPct.toFixed(1)}%`}
              tone="text-ui-caption text-red-300"
              value={`${candidate.expectedNetReturnPct >= 0 ? '+' : ''}${candidate.expectedNetReturnPct.toFixed(2)}%`}
            />
          </span>
          <span
            className="grid w-full grid-cols-3 gap-2 text-ui-micro leading-4"
            role="gridcell"
          >
            <span className="min-w-0 truncate text-slate-400">
              <strong className="text-slate-300">价格</strong> ·{' '}
              {getHighPositionLabel(candidate.highPositionType)} /{' '}
              {candidate.promotionScore.toFixed(1)}
            </span>
            <span className="min-w-0 truncate text-slate-400">
              <strong className="text-slate-300">AI</strong> ·{' '}
              {getResearchLabel(candidate)} / {getResearchDetail(candidate)}
            </span>
            <span className="min-w-0 truncate text-slate-400">
              <strong className="text-slate-300">资格</strong> ·{' '}
              {candidate.promotionEligible ? '通过' : '否决'} /{' '}
              {candidate.blockedReasons[0] || '确定性规则'}
            </span>
          </span>
          <span className="flex w-full justify-end" role="gridcell">
            {action}
          </span>
        </>
      )}
    </div>
  );
}

function CandidateGridHeader({ layout }: { layout: CandidateLayout }) {
  if (layout === 'narrow') {
    return (
      <div className="sr-only" role="row">
        <span role="columnheader">候选卡片</span>
      </div>
    );
  }

  const labels =
    layout === 'wide'
      ? [
          '候选标的',
          '生命周期',
          '首板封住',
          'T+1 晋级',
          '收益 / 尾损',
          '价格位置',
          'AI 研究',
          '资格',
          '操作',
        ]
      : ['候选 / 阶段', '首板', 'T+1', '收益 / 尾损', '判断依据', '操作'];

  return (
    <div
      className={cn(
        'grid shrink-0 gap-2 bg-[#0b1628] px-ui-section py-2.5 text-ui-micro font-black uppercase tracking-[0.1em] text-slate-600 shadow-[0_1px_0_rgba(255,255,255,0.05)]',
        layout === 'wide' ? wideGridColumns : compactGridColumns
      )}
      role="row"
    >
      {labels.map((label, index) => (
        <span
          key={label}
          className={cn(index > 0 && 'text-center')}
          role="columnheader"
        >
          {label}
        </span>
      ))}
    </div>
  );
}

export interface LimitUpRadarPanelProps {
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
  onSearchChange: (value: string) => void;
  onSelectCandidate: (code: string) => void;
  onStageChange: (value: RadarStage | 'ALL') => void;
  pendingCodes: Set<string>;
  search: string;
  selectedCode: string | null;
  stage: RadarStage | 'ALL';
  summary: RadarSummary;
  systemWarnings: string[];
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
  onSearchChange,
  onSelectCandidate,
  onStageChange,
  pendingCodes,
  search,
  selectedCode,
  stage,
  summary,
  systemWarnings,
}: LimitUpRadarPanelProps) {
  const panelRef = useRef<HTMLElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [containerWidth, setContainerWidth] = useState(DEFAULT_CONTAINER_WIDTH);
  const [viewportHeight, setViewportHeight] = useState(
    FALLBACK_VIEWPORT_HEIGHT
  );
  const [scrollTop, setScrollTop] = useState(0);
  const layout = getCandidateLayout(containerWidth);
  const rowHeight = ROW_HEIGHTS[layout];
  const previousRowHeightRef = useRef(rowHeight);

  useEffect(() => {
    const panel = panelRef.current;
    const viewport = viewportRef.current;
    if (!panel || !viewport) return;

    const applySize = (target: Element, width: number, height: number) => {
      if (target === panel && width > 0) {
        const nextWidth = Math.round(width);
        setContainerWidth(current =>
          current === nextWidth ? current : nextWidth
        );
      }
      if (target === viewport && height > 0) {
        const nextHeight = Math.round(height);
        setViewportHeight(current =>
          current === nextHeight ? current : nextHeight
        );
      }
    };
    const panelRect = panel.getBoundingClientRect();
    const viewportRect = viewport.getBoundingClientRect();
    applySize(panel, panelRect.width, panelRect.height);
    applySize(viewport, viewportRect.width, viewportRect.height);

    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(entries => {
      entries.forEach(entry => {
        applySize(
          entry.target,
          entry.contentRect.width,
          entry.contentRect.height
        );
      });
    });
    observer.observe(panel);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const previousRowHeight = previousRowHeightRef.current;
    if (previousRowHeight === rowHeight) return;
    const firstVisibleIndex = Math.floor(scrollTop / previousRowHeight);
    const nextScrollTop = firstVisibleIndex * rowHeight;
    if (viewportRef.current) viewportRef.current.scrollTop = nextScrollTop;
    setScrollTop(nextScrollTop);
    previousRowHeightRef.current = rowHeight;
  }, [rowHeight, scrollTop]);

  const maxScrollTop = Math.max(
    0,
    candidates.length * rowHeight - viewportHeight
  );
  useEffect(() => {
    if (scrollTop <= maxScrollTop) return;
    if (viewportRef.current) viewportRef.current.scrollTop = maxScrollTop;
    setScrollTop(maxScrollTop);
  }, [maxScrollTop, scrollTop]);

  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN);
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + OVERSCAN * 2;
  const visible = useMemo(
    () => candidates.slice(start, start + visibleCount),
    [candidates, start, visibleCount]
  );
  const selected = useMemo(
    () => candidates.find(candidate => candidate.code === selectedCode) ?? null,
    [candidates, selectedCode]
  );
  const inspectionCode = selected?.code ?? candidates[0]?.code ?? null;
  const onScroll = (event: UIEvent<HTMLDivElement>) => {
    setScrollTop(event.currentTarget.scrollTop);
  };

  return (
    <section
      ref={panelRef}
      className="studio-workspace-surface flex h-full min-h-0 min-w-0 flex-col overflow-hidden"
      data-layout={layout}
      data-testid="limit-up-radar-panel"
    >
      <div
        className={cn(
          'flex shrink-0 gap-3 border-b border-white/[0.05] px-ui-section py-3',
          layout === 'wide'
            ? 'flex-row items-center justify-between'
            : 'flex-col items-stretch'
        )}
      >
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-ui-body font-black text-slate-100">
            <Radar className="h-4 w-4 text-cyan-300" />
            首板晋级候选
          </h2>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-ui-caption text-slate-600">
            <span className="font-mono">
              {summary.discoveredCount} 发现 · {summary.eligibleCount} 合格 ·{' '}
              {summary.scannedCount} 已扫描
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span
                className={cn(
                  'h-1.5 w-1.5 rounded-full',
                  isScannerRunning ? 'bg-emerald-400' : 'bg-rose-400'
                )}
              />
              {fetching ? '刷新中' : isScannerRunning ? '实时扫描' : '雷达离线'}
            </span>
          </p>
        </div>
        <div
          className={cn(
            'grid gap-2',
            layout === 'narrow'
              ? 'grid-cols-2'
              : 'grid-cols-[minmax(150px,1fr)_110px_130px]',
            layout === 'narrow' && '[&>label:first-child]:col-span-2'
          )}
        >
          <label className="relative min-w-0">
            <span className="sr-only">搜索候选</span>
            <Search className="pointer-events-none absolute left-2.5 top-2 h-3.5 w-3.5 text-slate-500" />
            <Input
              value={search}
              onChange={event => onSearchChange(event.target.value)}
              placeholder="代码 / 名称"
              className="h-8 rounded-sm border border-white/10 bg-[#08111f] pl-8 text-ui-caption"
            />
          </label>
          <label>
            <span className="sr-only">候选阶段</span>
            <NativeSelect
              value={stage}
              onChange={event =>
                onStageChange(event.target.value as RadarStage | 'ALL')
              }
              className="h-8 w-full rounded-sm border border-white/10 bg-[#08111f] px-2 text-ui-caption text-slate-300 outline-none focus:ring-2 focus:ring-cyan-400/50"
            >
              {stages.map(item => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </NativeSelect>
          </label>
          <label>
            <span className="sr-only">候选行业</span>
            <NativeSelect
              value={industry}
              onChange={event => onIndustryChange(event.target.value)}
              className="h-8 w-full rounded-sm border border-white/10 bg-[#08111f] px-2 text-ui-caption text-slate-300 outline-none focus:ring-2 focus:ring-cyan-400/50"
            >
              <option value="ALL">全部行业</option>
              {industries.map(item => (
                <option key={item.industry} value={item.industry}>
                  {item.industry} · {item.candidateCount}
                </option>
              ))}
            </NativeSelect>
          </label>
        </div>
      </div>

      {errorMessage || systemWarnings.length ? (
        <div
          role="alert"
          className="flex shrink-0 items-start gap-2 border-b border-rose-400/20 bg-rose-500/[0.07] px-ui-section py-2.5 text-ui-caption leading-4 text-rose-100"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="font-black">系统保护</span>
          <span className="text-rose-100/75">
            {[errorMessage, ...systemWarnings].filter(Boolean).join(' · ')}
          </span>
        </div>
      ) : null}

      <div
        className="flex min-h-0 flex-1 flex-col"
        role="grid"
        aria-label="首板晋级候选列表"
        aria-rowcount={candidates.length + 1}
      >
        <CandidateGridHeader layout={layout} />
        <div
          ref={viewportRef}
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain custom-scrollbar"
          data-testid="limit-up-radar-viewport"
          onScroll={onScroll}
          role="rowgroup"
        >
          {candidates.length ? (
            <div
              className="relative"
              style={{ height: candidates.length * rowHeight }}
            >
              {visible.map((candidate, index) => (
                <CandidateRow
                  key={candidate.code}
                  candidate={candidate}
                  style={{
                    height: rowHeight,
                    transform: `translateY(${(start + index) * rowHeight}px)`,
                  }}
                  armed={armedCodes.has(candidate.code)}
                  assistantEnabled={assistantEnabled}
                  busy={busyCode === candidate.code}
                  layout={layout}
                  managed={exitPlanCodes.has(candidate.code)}
                  pending={pendingCodes.has(candidate.code)}
                  rowIndex={start + index + 2}
                  selected={selectedCode === candidate.code}
                  onArm={onArm}
                  onDisarm={onDisarm}
                  onSelect={onSelectCandidate}
                />
              ))}
            </div>
          ) : (
            <div
              className="flex h-full items-stretch px-ui-section py-3"
              role="row"
            >
              <div
                className="flex flex-1 flex-col items-center justify-center text-center"
                role="gridcell"
              >
                <ShieldCheck className="h-8 w-8 text-slate-700" />
                <p className="mt-3 text-ui-label font-black text-slate-300">
                  暂无匹配候选
                </p>
                <p className="mt-1 text-ui-caption text-slate-500">
                  雷达会持续扫描；非交易时段保留最近快照供观察。
                </p>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-5 gap-y-1 border-t border-white/[0.05] bg-[#07111f] px-ui-section py-2 text-ui-micro text-slate-600">
        <span>报价过期仅供观察 · 点击候选查看评分、盘口与研究</span>
        <button
          type="button"
          className="flex cursor-pointer items-center gap-1 rounded px-1.5 py-1 text-slate-400 transition-colors duration-200 hover:bg-white/[0.05] hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/60"
          onClick={() => inspectionCode && onSelectCandidate(inspectionCode)}
          disabled={!inspectionCode}
        >
          <PanelRightOpen className="h-3 w-3" />
          {selected ? '查看已选候选' : '查看首位候选'}
        </button>
      </div>
    </section>
  );
}
