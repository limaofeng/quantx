import {
  Activity,
  ArrowRight,
  CalendarDays,
  ChevronRight,
  Clock3,
  Database,
  Flame,
  Gauge,
  Layers3,
  ListOrdered,
  Percent,
  Radio,
  RefreshCw,
  Search,
  Target,
  TrendingDown,
  TrendingUp,
  Zap,
  type LucideIcon,
} from 'lucide-react';
import {
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import {
  type RadarCandidate,
  type RadarIndustryHeat,
  useLimitUpRadar,
} from '@/features/strategies/hooks/useLimitUpRadar';
import {
  financialDirection,
  financialToneClass,
  type FinancialDirection,
} from '@/shared/utils/financialColors';

import { MarketIndexCustomizer } from '../components/MarketIndexCustomizer';
import { MarketIntradayChart } from '../components/MarketIntradayChart';
import { MarketStockSearch } from '../components/MarketStockSearch';
import { MarketStudioShell } from '../components/MarketStudioShell';
import { useAMarketSession } from '../hooks/useAMarketSession';
import { useMarketIndexPreferences } from '../hooks/useMarketIndexPreferences';
import { useMarketPulse } from '../hooks/useMarketPulse';
import { useMarketWorkbench } from '../hooks/useMarketWorkbench';
import {
  CORE_MARKET_INDICES,
  formatMarketDate,
  formatMarketPercent,
  formatMarketPrice,
  formatMarketTime,
  formatMarketVolume,
  getRangePosition,
  isMarketQuoteFreshForSession,
  type MarketIndexDefinition,
  type MarketQuoteSnapshot,
  type MarketTone,
} from '../marketWorkbench';

const quickTools: ReadonlyArray<{
  description: string;
  href: string;
  icon: LucideIcon;
  label: string;
}> = [
  {
    description: '涨停雷达与候选池',
    href: '/limit-up-board',
    icon: Target,
    label: '打板助手',
  },
  {
    description: '财务与行情条件选股',
    href: '/screening',
    icon: Search,
    label: '股票筛选',
  },
  {
    description: '全市场与指数数据',
    href: '/settings/data/market',
    icon: Database,
    label: '市场数据',
  },
  {
    description: '行业、概念和成分映射',
    href: '/settings/data/sectors',
    icon: Layers3,
    label: '板块数据',
  },
  {
    description: '分红、配股与复权因子',
    href: '/settings/data/market?tab=ex-rights',
    icon: Percent,
    label: '除权数据',
  },
  {
    description: '校验开市与休市安排',
    href: '/settings/data/calendar',
    icon: CalendarDays,
    label: '交易日历',
  },
];

const sectionNavigation: ReadonlyArray<{
  icon: LucideIcon;
  id: string;
  label: string;
}> = [
  { icon: Gauge, id: 'market-overview', label: '大盘全景' },
  { icon: Flame, id: 'market-opportunities', label: '热点机会' },
  { icon: ListOrdered, id: 'market-rankings', label: '个股排行' },
  { icon: Zap, id: 'market-tools', label: '快捷入口' },
];

interface RankedStockRow {
  changePct: number;
  code: string;
  currentPrice: number;
  name: string;
  volumeRatio: number;
}

interface IntradayMover {
  amount: number;
  changePct: number;
  code: string;
  currentPrice: number;
  industry?: string | null;
  intradayTurnoverRatePct?: number | null;
  isStale: boolean;
  last5mVolumeRatio: number;
  name: string;
  volumePaceRatio: number;
}

const quoteTone = (value: number | null | undefined) =>
  financialToneClass(value);

const toneClasses: Record<MarketTone, string> = {
  strong: 'border-market-up/30 bg-market-up/10 text-market-up',
  positive: 'border-market-up/30 bg-market-up/10 text-market-up',
  balanced: 'border-slate-400/20 bg-slate-500/10 text-slate-300',
  negative: 'border-market-down/30 bg-market-down/10 text-market-down',
  weak: 'border-market-down/30 bg-market-down/10 text-market-down',
  waiting: 'border-slate-500/20 bg-slate-500/5 text-slate-500',
};

const averageIndexChange = (
  indices: Array<{
    definition: MarketIndexDefinition;
    quote?: MarketQuoteSnapshot;
  }>,
  codes: readonly string[]
) => {
  const values = indices
    .filter(item => codes.includes(item.definition.code))
    .map(item => item.quote?.changePercent)
    .filter((value): value is number => typeof value === 'number');
  return values.length
    ? values.reduce((total, value) => total + value, 0) / values.length
    : null;
};

const formatRatio = (value: number | null | undefined) =>
  typeof value === 'number' ? `${value.toFixed(2)}x` : '--';

const formatIndustry = (value: string | null | undefined) =>
  value?.replace(/^\d+/, '').trim() || '--';

const formatSnapshotDate = (value: string | null | undefined) => {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    weekday: 'short',
    timeZone: 'Asia/Shanghai',
  }).format(date);
};

const formatTradingDateKey = (value: string | null | undefined) =>
  value ? value.slice(5).replace('-', '/') : '--';

const formatMarketChange = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value)
    ? `${value > 0 ? '+' : ''}${value.toLocaleString('zh-CN', {
        maximumFractionDigits: 2,
        minimumFractionDigits: 2,
      })}`
    : '--';

const marketIndexSurfaceStyles: Record<FinancialDirection, CSSProperties> = {
  up: {
    backgroundImage:
      'linear-gradient(135deg, rgb(var(--market-up) / 0.30) 0%, rgb(var(--market-up) / 0.10) 48%, rgb(2 6 23 / 0.80) 100%)',
    borderColor: 'rgb(var(--market-up) / 0.30)',
  },
  down: {
    backgroundImage:
      'linear-gradient(135deg, rgb(var(--market-down) / 0.30) 0%, rgb(var(--market-down) / 0.10) 48%, rgb(2 6 23 / 0.80) 100%)',
    borderColor: 'rgb(var(--market-down) / 0.30)',
  },
  flat: {
    backgroundImage:
      'linear-gradient(135deg, rgb(var(--market-flat) / 0.12) 0%, rgb(var(--market-flat) / 0.06) 48%, rgb(2 6 23 / 0.80) 100%)',
    borderColor: 'rgb(var(--market-flat) / 0.22)',
  },
};

const marketIndexRangeStyles: Record<FinancialDirection, CSSProperties> = {
  up: { backgroundColor: 'rgb(var(--market-up) / 0.80)' },
  down: { backgroundColor: 'rgb(var(--market-down) / 0.80)' },
  flat: { backgroundColor: 'rgb(var(--market-flat) / 0.80)' },
};

function MarketIndexCard({
  definition,
  isSelected,
  onSelect,
  quote,
}: {
  definition: MarketIndexDefinition;
  isSelected: boolean;
  onSelect: () => void;
  quote?: MarketQuoteSnapshot;
}) {
  const rangePosition = getRangePosition(quote);
  const movement = quote?.changePercent ?? quote?.change;
  const direction = financialDirection(movement);

  return (
    <button
      aria-label={`查看${definition.name}行情`}
      aria-pressed={isSelected}
      className={`group h-28 w-40 shrink-0 cursor-pointer rounded-lg border p-2.5 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 xl:p-2 ${
        isSelected ? 'ring-1 ring-inset ring-blue-400/70' : ''
      }`}
      data-market-direction={direction}
      data-testid={`market-index-${definition.code}`}
      onClick={onSelect}
      style={marketIndexSurfaceStyles[direction]}
      type="button"
    >
      <span className="flex items-start justify-between gap-2">
        <span className="min-w-0">
          <span className="block truncate text-xs font-black text-slate-100">
            {definition.name}
          </span>
          <span className="mt-0.5 block truncate font-mono text-[9px] text-slate-600">
            {definition.code} · {definition.group}
          </span>
        </span>
        {isSelected ? (
          <span className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-400" />
        ) : null}
      </span>
      <span
        className={`mt-2 block font-mono text-2xl font-black leading-none tabular-nums ${quoteTone(movement)}`}
      >
        {formatMarketPrice(quote?.currentPrice)}
      </span>
      <span className="mt-1.5 flex items-center gap-2 text-[11px] font-black tabular-nums">
        <span
          className={`font-mono ${quoteTone(quote?.change)}`}
        >
          {formatMarketChange(quote?.change)}
        </span>
        <span className={`font-mono ${quoteTone(quote?.changePercent)}`}>
          {formatMarketPercent(quote?.changePercent)}
        </span>
      </span>
      <span className="mt-2 block h-0.5 overflow-hidden rounded-full bg-white/[0.06] xl:mt-1.5">
        {rangePosition !== null ? (
          <span
            className="block h-full rounded-full"
            style={{
              ...marketIndexRangeStyles[direction],
              width: `${Math.max(3, rangePosition)}%`,
            }}
          />
        ) : null}
      </span>
    </button>
  );
}

function Metric({
  label,
  tone = 'text-slate-200',
  value,
}: {
  label: string;
  tone?: string;
  value: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[9px] font-bold uppercase tracking-wider text-slate-600">
        {label}
      </div>
      <div
        className={`mt-1 truncate font-mono text-xs font-bold tabular-nums ${tone}`}
      >
        {value}
      </div>
    </div>
  );
}

function ComparisonCard({
  detail,
  label,
  tone,
  value,
}: {
  detail: string;
  label: string;
  tone: string;
  value: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-white/5 bg-black/10 p-3 xl:p-2">
      <div className="text-[10px] font-bold text-slate-500">{label}</div>
      <div
        className={`mt-2 font-mono text-lg font-black xl:mt-1 xl:text-base ${tone}`}
      >
        {value}
      </div>
      <div className="mt-1 truncate text-[9px] text-slate-600 xl:mt-0.5">
        {detail}
      </div>
    </div>
  );
}

function EmptyState({ children }: { children: string }) {
  return (
    <div className="flex min-h-36 items-center justify-center rounded-lg border border-dashed border-white/10 bg-black/10 px-5 text-center text-[11px] leading-5 text-slate-600 xl:min-h-24 xl:px-3">
      {children}
    </div>
  );
}

function HotIndustryGrid({ industries }: { industries: RadarIndustryHeat[] }) {
  if (industries.length === 0) {
    return (
      <EmptyState>涨停雷达未提供行业热度，启动盘中扫描后自动更新。</EmptyState>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-2 xl:grid-cols-3">
      {industries.slice(0, 6).map(industry => (
        <div
          key={industry.industry}
          className="rounded-lg border border-white/5 bg-black/10 p-3 xl:p-2"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[11px] font-black text-slate-200">
              {industry.industry}
            </span>
            <span className="font-mono text-xs font-black text-rose-400">
              {industry.averageScore.toFixed(0)}
            </span>
          </div>
          <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/[0.05]">
            <div
              className="h-full rounded-full bg-gradient-to-r from-orange-400/70 to-rose-400"
              style={{ width: `${Math.min(100, industry.averageScore)}%` }}
            />
          </div>
          <div className="mt-2 flex items-center gap-2 text-[9px] text-slate-600">
            <span>候选 {industry.candidateCount}</span>
            <span>近板 {industry.nearLimitCount}</span>
            <span className="text-rose-400/80">
              封板 {industry.sealedCount}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function RadarCandidates({ candidates }: { candidates: RadarCandidate[] }) {
  if (candidates.length === 0) {
    return (
      <EmptyState>
        暂无涨停候选，雷达运行后这里展示阶段、评分与成交额。
      </EmptyState>
    );
  }

  return (
    <div className="divide-y divide-white/5">
      {candidates.slice(0, 6).map(candidate => (
        <Link
          key={candidate.code}
          className="group flex items-center justify-between gap-3 px-1 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 xl:py-1.5"
          href={`/stock/${encodeURIComponent(candidate.code)}`}
        >
          <span className="flex min-w-0 items-center gap-2.5">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-rose-500/10 font-mono text-[10px] font-black text-rose-300">
              {candidate.radarScore.toFixed(0)}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[11px] font-bold text-slate-200 group-hover:text-white">
                {candidate.name}
              </span>
              <span className="mt-0.5 block truncate font-mono text-[9px] text-slate-600">
                {candidate.code} · {formatIndustry(candidate.industry)}
              </span>
            </span>
          </span>
          <span className="shrink-0 text-right">
            <span className="block text-[10px] font-black text-rose-400">
              {candidate.stageLabel}
            </span>
            <span className="mt-0.5 block font-mono text-[9px] text-slate-600">
              {formatMarketVolume(candidate.amount)}
            </span>
          </span>
        </Link>
      ))}
    </div>
  );
}

function IntradayMovers({
  emptyMessage,
  items,
}: {
  emptyMessage: string;
  items: IntradayMover[];
}) {
  if (items.length === 0) {
    return <EmptyState>{emptyMessage}</EmptyState>;
  }

  return (
    <div className="divide-y divide-white/5">
      {items.slice(0, 6).map(item => (
        <Link
          key={item.code}
          className="group flex items-center gap-3 px-1 py-2.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 xl:py-1.5"
          href={`/stock/${encodeURIComponent(item.code)}`}
        >
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[11px] font-bold text-slate-200 group-hover:text-white">
              {item.name}
            </span>
            <span className="mt-0.5 block truncate font-mono text-[9px] text-slate-600">
              {item.code} · {formatIndustry(item.industry)}
            </span>
          </span>
          <span className="text-right">
            <span className="block font-mono text-[11px] font-black text-cyan-300">
              {formatRatio(item.volumePaceRatio)}
            </span>
            <span className="block text-[8px] text-slate-600">量速</span>
          </span>
          <span className="w-14 text-right">
            <span
              className={`block font-mono text-[11px] font-black ${quoteTone(item.changePct)}`}
            >
              {formatMarketPercent(item.changePct)}
            </span>
            <span className="block text-[8px] text-slate-600">
              {item.isStale ? '已过期' : '盘中'}
            </span>
          </span>
        </Link>
      ))}
    </div>
  );
}

function StockRankingList({
  direction,
  rows,
}: {
  direction: 'gainers' | 'losers';
  rows: RankedStockRow[];
}) {
  const isGainers = direction === 'gainers';
  const DirectionIcon = isGainers ? TrendingUp : TrendingDown;
  const headingId = `stock-ranking-${direction}-heading`;
  const visibleRows = rows.slice(0, 8);

  return (
    <section
      aria-labelledby={headingId}
      className="min-w-0 overflow-hidden bg-slate-900"
      data-testid={`stock-ranking-${direction}`}
    >
      <div className="grid grid-cols-4 items-center gap-1.5 border-b border-white/5 bg-black/10 px-2 py-1.5 text-[8px] font-bold uppercase tracking-wider text-slate-600 sm:grid-cols-5 sm:gap-2 sm:px-3">
        <span />
        <h3
          className="flex min-w-0 items-center gap-1.5 text-[10px] font-black normal-case tracking-normal text-slate-300"
          id={headingId}
        >
          <DirectionIcon
            className={`h-3 w-3 ${isGainers ? 'text-market-up' : 'text-market-down'}`}
          />
          <span>{isGainers ? '涨幅榜' : '跌幅榜'}</span>
          <span className="font-mono text-[8px] font-bold text-slate-600">
            TOP {visibleRows.length}
          </span>
        </h3>
        <span className="text-right">最新</span>
        <span className="text-right">涨跌</span>
        <span className="hidden text-right sm:block">量比</span>
      </div>
      {visibleRows.length === 0 ? (
        <EmptyState>全市场日线快照暂不可用，排行不会使用模拟数据。</EmptyState>
      ) : (
        <ol className="divide-y divide-white/5">
          {visibleRows.map((row, index) => (
            <li key={row.code}>
              <Link
                aria-label={`${row.name} ${formatMarketPercent(row.changePct)}`}
                className="group grid cursor-pointer grid-cols-4 items-center gap-1.5 px-2 py-1.5 transition-colors duration-200 hover:bg-white/[0.03] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-red-500/70 sm:grid-cols-5 sm:gap-2 sm:px-3"
                href={`/stock/${encodeURIComponent(row.code)}`}
              >
                <span
                  className={`font-mono text-[9px] font-black ${
                    index < 3 ? 'text-amber-300' : 'text-slate-700'
                  }`}
                >
                  {(index + 1).toString().padStart(2, '0')}
                </span>
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="truncate text-[10px] font-bold text-slate-200 transition-colors duration-200 group-hover:text-white">
                    {row.name}
                  </span>
                  <span className="hidden shrink-0 font-mono text-[8px] text-slate-600 sm:inline">
                    {row.code}
                  </span>
                </span>
                <span className="text-right font-mono text-[10px] font-bold tabular-nums text-slate-300">
                  {formatMarketPrice(row.currentPrice)}
                </span>
                <span
                  className={`text-right font-mono text-[10px] font-black tabular-nums ${quoteTone(row.changePct)}`}
                >
                  {formatMarketPercent(row.changePct)}
                </span>
                <span className="hidden text-right font-mono text-[9px] tabular-nums text-slate-500 sm:block">
                  {formatRatio(row.volumeRatio)}
                </span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function MarketShortcutsPage() {
  const [selectedCode, setSelectedCode] = useState(CORE_MARKET_INDICES[0].code);
  const preferences = useMarketIndexPreferences();
  const visibleDefinitions = preferences.visibleDefinitions;
  const session = useAMarketSession();
  const market = useMarketWorkbench({
    indexDefinitions: visibleDefinitions,
    now: session.now,
    phase: session.phase,
    targetTradingDate: session.targetTradingDate,
  });
  const pulse = useMarketPulse({
    now: session.now,
    phase: session.phase,
    targetTradingDate: session.targetTradingDate,
  });
  const radar = useLimitUpRadar(true);
  useEffect(() => {
    if (
      visibleDefinitions.length > 0 &&
      !visibleDefinitions.some(definition => definition.code === selectedCode)
    ) {
      setSelectedCode(visibleDefinitions[0].code);
    }
  }, [selectedCode, visibleDefinitions]);
  const selected =
    market.indices.find(item => item.definition.code === selectedCode) ||
    market.indices[0];
  const selectedQuote = selected?.quote;
  const amplitude =
    selectedQuote?.preClose && selectedQuote.preClose > 0
      ? ((selectedQuote.high - selectedQuote.low) / selectedQuote.preClose) *
        100
      : null;
  const rankedIndices = useMemo(
    () =>
      [...market.indices].sort((left, right) => {
        const leftValue = left.quote?.changePercent;
        const rightValue = right.quote?.changePercent;
        if (typeof leftValue !== 'number') return 1;
        if (typeof rightValue !== 'number') return -1;
        return rightValue - leftValue;
      }),
    [market.indices]
  );
  const largeCapChange = averageIndexChange(market.indices, [
    '000001.SH',
    '000300.SH',
  ]);
  const smallCapChange = averageIndexChange(market.indices, [
    '399006.SZ',
    '000905.SH',
    '000852.SH',
  ]);
  const breadthTotal = pulse.breadth.total;
  const advancerWidth = breadthTotal
    ? (pulse.breadth.advancers / breadthTotal) * 100
    : 0;
  const flatWidth = breadthTotal
    ? (pulse.breadth.flats / breadthTotal) * 100
    : 100;
  const declinerWidth = breadthTotal
    ? (pulse.breadth.decliners / breadthTotal) * 100
    : 0;
  const combinedError =
    market.error || pulse.error || radar.error || session.calendarError;
  const marketDateIsMissing = Boolean(
    session.targetTradingDate &&
    market.targetDateCoverage < market.indices.length
  );
  const hasVisibleIndices = market.indices.length > 0;
  const realtimeMarketIsDelayed = Boolean(
    session.isOpen &&
    hasVisibleIndices &&
    !marketDateIsMissing &&
    (market.freshCoverage < market.indices.length ||
      !isMarketQuoteFreshForSession(
        market.latestQuoteAt,
        session.now,
        session.phase
      ))
  );
  const marketDataIsStale = marketDateIsMissing || realtimeMarketIsDelayed;
  const pulseDataIsMissing = pulse.snapshotMode === 'unavailable';
  const marketDataLabel = market.latestQuoteAt
    ? marketDateIsMissing
      ? `${formatTradingDateKey(session.targetTradingDate)} 行情未入库`
      : market.dataMode === 'live' && session.isOpen
        ? `实时 ${formatMarketDate(market.latestQuoteAt)} ${formatMarketTime(market.latestQuoteAt)}`
        : market.dataMode === 'close'
          ? `收盘 ${formatMarketDate(market.latestQuoteAt)}`
          : `实盘截至 ${formatMarketDate(market.latestQuoteAt)} ${formatMarketTime(market.latestQuoteAt)}`
    : session.targetTradingDate
      ? `${formatTradingDateKey(session.targetTradingDate)} 行情未入库`
      : '尚未收到快照';
  const pulseSnapshotLabel = pulse.snapshotAt
    ? pulse.snapshotMode === 'intraday'
      ? `${session.isOpen ? '盘中' : '实盘截至'} ${formatMarketDate(pulse.snapshotAt)} ${formatMarketTime(pulse.snapshotAt)}`
      : `收盘 ${formatMarketDate(pulse.snapshotAt)}`
    : session.targetTradingDate
      ? `${formatTradingDateKey(session.targetTradingDate)} 快照未入库`
      : '尚无个股快照';

  const refreshAll = () => {
    market.refreshLatestQuotes();
    pulse.refresh();
    radar.refresh({ requestPolicy: 'network-only' });
  };

  const scrollTo = (sectionId: string) => {
    document
      .getElementById(sectionId)
      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <MarketStudioShell
      content={
        <div className="studio-workspace-surface flex min-h-0 flex-1 flex-col overflow-hidden">
          <header className="studio-workspace-surface flex shrink-0 flex-col gap-2 border-b border-white/5 px-4 py-2 xl:flex-row xl:items-center xl:justify-between xl:gap-3 xl:py-1">
            <div className="flex min-w-0 items-center">
              <div className="min-w-0">
                <div className="truncate text-xs font-black uppercase tracking-[0.18em] text-slate-100">
                  行情工作台
                </div>
                <div className="truncate text-[10px] font-medium text-slate-600">
                  A股全景 · 指数脉搏 · 机会雷达
                </div>
              </div>
            </div>

            <div className="w-full xl:max-w-md xl:flex-1">
              <MarketStockSearch />
            </div>

            <div className="flex w-full shrink-0 items-center justify-end gap-2 xl:w-auto">
              <div className="min-w-0 flex-1 text-right sm:flex-none">
                <div
                  className={`text-[10px] font-bold ${
                    combinedError || marketDataIsStale || pulseDataIsMissing
                      ? 'text-amber-300'
                      : 'text-emerald-300'
                  }`}
                >
                  {combinedError
                    ? '部分数据不可用'
                    : marketDataIsStale
                      ? '行情滞后'
                      : pulseDataIsMissing
                        ? '个股快照缺失'
                        : session.label}
                </div>
                <div className="mt-0.5 font-mono text-[9px] text-slate-600">
                  {marketDataLabel}
                </div>
              </div>
              <Button
                aria-label="刷新全部行情"
                className="h-8 w-8 rounded-lg border border-white/10 bg-white/[0.03] text-slate-400 hover:border-white/20 hover:text-slate-100"
                onClick={refreshAll}
                size="icon"
                variant="ghost"
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 ${
                    pulse.fetching || radar.fetching ? 'animate-spin' : ''
                  }`}
                />
              </Button>
            </div>
          </header>

          <main className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto scroll-smooth">
            <div className="mx-auto w-full max-w-[1800px] px-3 pb-10 pt-3 sm:px-4 lg:px-5 xl:px-3 xl:pt-2">
              <nav
                aria-label="行情工作台分区"
                className="sticky top-0 z-20 mb-3 flex items-center gap-1 overflow-x-auto rounded-lg border border-white/5 bg-slate-950/95 p-1 backdrop-blur xl:mb-2 xl:p-0.5"
              >
                {sectionNavigation.map(({ icon: Icon, id, label }) => (
                  <button
                    key={id}
                    className="flex min-w-max cursor-pointer items-center gap-1.5 rounded-md px-3 py-2 text-[10px] font-bold text-slate-500 transition-colors hover:bg-white/[0.04] hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 xl:py-1.5"
                    onClick={() => scrollTo(id)}
                    type="button"
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </button>
                ))}
              </nav>

              <section className="scroll-mt-14" id="market-overview">
                <div className="overflow-hidden rounded-xl border border-white/10 bg-slate-900">
                  <div className="flex flex-col justify-between gap-3 border-b border-white/5 px-4 py-3 sm:flex-row sm:items-center xl:px-3 xl:py-2">
                    <div className="flex items-center gap-3">
                      <span
                        className={`flex h-9 w-9 items-center justify-center rounded-lg ${
                          session.isOpen && !marketDataIsStale
                            ? 'bg-rose-500/15 text-rose-300'
                            : session.phase === 'lunch-break' ||
                                marketDataIsStale
                              ? 'bg-amber-500/15 text-amber-300'
                              : 'bg-slate-500/10 text-slate-400'
                        }`}
                      >
                        {session.isOpen && !marketDataIsStale ? (
                          <Radio className="h-4 w-4" />
                        ) : (
                          <Clock3 className="h-4 w-4" />
                        )}
                      </span>
                      <div>
                        <div className="flex items-center gap-2">
                          <h1 className="text-sm font-black text-slate-100">
                            {session.label}
                          </h1>
                          <span
                            className={`rounded border px-1.5 py-0.5 text-[9px] font-black ${toneClasses[market.summary.tone]}`}
                          >
                            {market.summary.toneLabel}
                          </span>
                          {marketDataIsStale ? (
                            <span className="rounded border border-amber-400/25 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-black text-amber-300">
                              数据滞后
                            </span>
                          ) : null}
                          {pulseDataIsMissing ? (
                            <span className="rounded border border-amber-400/25 bg-amber-400/10 px-1.5 py-0.5 text-[9px] font-black text-amber-300">
                              个股快照缺失
                            </span>
                          ) : null}
                        </div>
                        <div className="mt-1 text-[10px] text-slate-600">
                          {session.detail} · 指数{' '}
                          {formatSnapshotDate(market.latestQuoteAt)} ·{' '}
                          {market.targetDateCoverage}/{market.indices.length}{' '}
                          工作台指数
                          {pulse.snapshotAt
                            ? ` · 个股${pulse.snapshotMode === 'intraday' ? '盘中' : '收盘'} ${formatSnapshotDate(pulse.snapshotAt)}`
                            : session.targetTradingDate
                              ? ` · 个股 ${formatTradingDateKey(session.targetTradingDate)} 未入库`
                              : ''}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-5">
                      <div className="text-right">
                        <div
                          className={`font-mono text-xl font-black ${quoteTone(market.summary.averageChange)}`}
                        >
                          {formatMarketPercent(market.summary.averageChange)}
                        </div>
                        <div className="text-[9px] text-slate-600">
                          核心指数等权涨跌
                        </div>
                      </div>
                      <div className="h-8 w-px bg-white/[0.07]" />
                      <div className="text-right">
                        <div className="font-mono text-xl font-black text-slate-100">
                          {breadthTotal
                            ? breadthTotal.toLocaleString('zh-CN')
                            : '--'}
                        </div>
                        <div className="text-[9px] text-slate-600">
                          全市场快照覆盖
                        </div>
                      </div>
                    </div>
                  </div>

                  <div
                    className="no-scrollbar flex gap-2 overflow-x-auto overscroll-x-contain p-3 xl:p-2"
                    data-testid="market-index-strip"
                  >
                    {market.indices.length > 0 ? (
                      market.indices.map(item => (
                        <MarketIndexCard
                          key={item.definition.code}
                          definition={item.definition}
                          isSelected={item.definition.code === selectedCode}
                          onSelect={() => setSelectedCode(item.definition.code)}
                          quote={item.quote}
                        />
                      ))
                    ) : (
                      <div className="flex min-h-28 w-64 shrink-0 items-center rounded-lg border border-dashed border-white/10 px-4 text-xs text-slate-600">
                        当前没有显示中的指数，请打开定制恢复或增补。
                      </div>
                    )}
                    <MarketIndexCustomizer
                      items={preferences.items}
                      onSave={preferences.updateItems}
                      storageStatus={preferences.storageStatus}
                    />
                    <Link
                      aria-label="打开全部指数目录"
                      className="group flex h-28 w-[5.75rem] shrink-0 flex-col items-center justify-center gap-2 rounded-lg border border-white/10 bg-slate-950/70 px-2 py-2 text-center text-slate-300 transition-colors hover:border-blue-400/30 hover:bg-white/[0.06] hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                      href="/market/indices"
                    >
                      <span className="flex flex-col text-[11px] font-black leading-[1.15] text-slate-200">
                        <span>全部</span>
                        <span>指数</span>
                      </span>
                      <ChevronRight className="h-4 w-4 text-slate-600 transition-colors group-hover:text-blue-300" />
                    </Link>
                  </div>

                  <div className="border-t border-white/5 p-4 xl:p-3">
                    <div className="xl:grid xl:grid-cols-4 xl:items-stretch xl:gap-2">
                      <div className="xl:flex xl:flex-col xl:justify-center xl:px-1">
                        <div className="flex items-end justify-between gap-3">
                          <div>
                            <div className="text-xs font-black text-slate-200">
                              全市场涨跌宽度
                            </div>
                            <div className="mt-1 text-[9px] text-slate-600 xl:mt-0.5">
                              非 ST 股票 · {pulseSnapshotLabel}
                            </div>
                          </div>
                          <div className="flex items-center gap-4 font-mono text-[11px] font-black xl:gap-2.5">
                            <span className="text-market-down">
                              跌 {breadthTotal ? pulse.breadth.decliners : '--'}
                            </span>
                            <span className="text-slate-500">
                              平 {breadthTotal ? pulse.breadth.flats : '--'}
                            </span>
                            <span className="text-market-up">
                              涨 {breadthTotal ? pulse.breadth.advancers : '--'}
                            </span>
                          </div>
                        </div>
                        <div
                          aria-label={`上涨 ${pulse.breadth.advancers} 家，平盘 ${pulse.breadth.flats} 家，下跌 ${pulse.breadth.decliners} 家`}
                          className="mt-3 flex h-2 overflow-hidden rounded-full bg-white/[0.05] xl:mt-2"
                        >
                          <span
                            className="h-full bg-market-down"
                            style={{ width: `${declinerWidth}%` }}
                          />
                          <span
                            className="h-full bg-slate-500"
                            style={{ width: `${flatWidth}%` }}
                          />
                          <span
                            className="h-full bg-market-up"
                            style={{ width: `${advancerWidth}%` }}
                          />
                        </div>
                      </div>

                      <div className="mt-3 grid gap-2 sm:grid-cols-3 xl:contents">
                        <ComparisonCard
                          detail="上涨家数 : 下跌家数"
                          label="涨跌家数对比"
                          tone="text-market-flat"
                          value={
                            breadthTotal ? (
                              <>
                                <span className="text-market-up">
                                  {pulse.breadth.advancers}
                                </span>
                                <span className="text-market-flat"> : </span>
                                <span className="text-market-down">
                                  {pulse.breadth.decliners}
                                </span>
                              </>
                            ) : (
                              '-- : --'
                            )
                          }
                        />
                        <ComparisonCard
                          detail={
                            radar.isScannerRunning
                              ? `近涨停 ${radar.summary.nearLimitCount} · 候选 ${radar.summary.candidateCount}`
                              : '涨停雷达当前未运行'
                          }
                          label="封板 / 炸板"
                          tone="text-market-up"
                          value={
                            radar.isScannerRunning
                              ? `${radar.summary.sealedCount} : ${radar.summary.brokenCount}`
                              : '-- : --'
                          }
                        />
                        <ComparisonCard
                          detail={`大盘 ${formatMarketPercent(largeCapChange)} · 小盘 ${formatMarketPercent(smallCapChange)}`}
                          label="大小盘风格"
                          tone={
                            largeCapChange !== null &&
                            smallCapChange !== null &&
                            largeCapChange >= smallCapChange
                              ? 'text-amber-300'
                              : 'text-cyan-300'
                          }
                          value={
                            largeCapChange === null || smallCapChange === null
                              ? '--'
                              : largeCapChange >= smallCapChange
                                ? '大盘占优'
                                : '小盘占优'
                          }
                        />
                      </div>
                    </div>
                  </div>
                </div>

                <div className="mt-3 grid gap-3 xl:mt-2 xl:grid-cols-2 xl:gap-2">
                  <section className="overflow-hidden rounded-xl border border-white/10 bg-slate-900">
                    {selected ? (
                      <>
                        <div className="flex flex-col justify-between gap-4 border-b border-white/5 px-4 py-3 sm:flex-row sm:items-end xl:px-3 xl:py-2">
                          <div>
                            <div className="flex items-center gap-2">
                              <Activity className="h-3.5 w-3.5 text-rose-300" />
                              <h2 className="text-xs font-black text-slate-200">
                                {selected.definition.name}
                              </h2>
                              <span className="font-mono text-[9px] text-slate-600">
                                {selected.definition.code}
                              </span>
                            </div>
                            <div className="mt-2 flex items-baseline gap-3">
                              <span className="font-mono text-2xl font-black text-slate-100">
                                {formatMarketPrice(selectedQuote?.currentPrice)}
                              </span>
                              <span
                                className={`font-mono text-sm font-black ${quoteTone(selectedQuote?.changePercent)}`}
                              >
                                {formatMarketPercent(
                                  selectedQuote?.changePercent
                                )}
                              </span>
                            </div>
                          </div>
                          <div className="grid grid-cols-4 gap-4 xl:gap-3">
                            <Metric
                              label="开盘"
                              value={formatMarketPrice(selectedQuote?.open)}
                            />
                            <Metric
                              label="最高"
                              tone="text-market-up"
                              value={formatMarketPrice(selectedQuote?.high)}
                            />
                            <Metric
                              label="最低"
                              tone="text-market-down"
                              value={formatMarketPrice(selectedQuote?.low)}
                            />
                            <Metric
                              label="振幅"
                              value={formatMarketPercent(amplitude)}
                            />
                          </div>
                        </div>
                        <div className="px-2 pb-2 pt-1">
                          <div className="flex items-center justify-between px-2 py-1 text-[9px] font-bold uppercase tracking-wider text-slate-600">
                            <span>分钟走势</span>
                            <span>1 MIN · SHANGHAI TIME</span>
                          </div>
                          <MarketIntradayChart
                            changePercent={selectedQuote?.changePercent}
                            preClose={selectedQuote?.preClose}
                            stockCode={selected.definition.code}
                            targetTradingDate={session.targetTradingDate}
                          />
                        </div>
                      </>
                    ) : (
                      <EmptyState>
                        当前没有显示中的指数，分钟走势暂不可用。
                      </EmptyState>
                    )}
                  </section>

                  <section className="rounded-xl border border-white/10 bg-slate-900 p-4 xl:p-3">
                    <div className="mb-3 flex items-center justify-between gap-2 xl:mb-1">
                      <div>
                        <h2 className="text-xs font-black text-slate-200">
                          指数强弱排行
                        </h2>
                        <p className="mt-1 text-[9px] text-slate-600">
                          工作台指数横向比较
                        </p>
                      </div>
                      <Gauge className="h-4 w-4 text-slate-600" />
                    </div>
                    <div className="space-y-1">
                      {rankedIndices.map((item, position) => (
                        <button
                          key={item.definition.code}
                          className="flex w-full cursor-pointer items-center justify-between gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 xl:py-1"
                          onClick={() => setSelectedCode(item.definition.code)}
                          type="button"
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            <span className="w-4 shrink-0 font-mono text-[9px] font-bold text-slate-700">
                              {position + 1}
                            </span>
                            {typeof item.quote?.changePercent === 'number' &&
                            item.quote.changePercent >= 0 ? (
                              <TrendingUp className="h-3 w-3 shrink-0 text-market-up" />
                            ) : (
                              <TrendingDown className="h-3 w-3 shrink-0 text-market-down" />
                            )}
                            <span className="truncate text-[11px] font-bold text-slate-300">
                              {item.definition.name}
                            </span>
                          </span>
                          <span
                            className={`shrink-0 font-mono text-[11px] font-black ${quoteTone(item.quote?.changePercent)}`}
                          >
                            {formatMarketPercent(item.quote?.changePercent)}
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                </div>
              </section>

              <section
                className="mt-5 scroll-mt-14 xl:mt-3"
                id="market-opportunities"
              >
                <div className="mb-2 flex items-end justify-between gap-3 px-1">
                  <div>
                    <div className="flex items-center gap-2">
                      <Flame className="h-4 w-4 text-orange-300" />
                      <h2 className="text-sm font-black text-slate-100">
                        热点与异动
                      </h2>
                    </div>
                    <p className="mt-1 text-[10px] text-slate-600">
                      以雷达评分和真实放量快照发现机会，不把行业热度冒充板块涨幅
                    </p>
                  </div>
                  <Link
                    className="hidden items-center gap-1 text-[10px] font-bold text-slate-500 hover:text-slate-200 sm:flex"
                    href="/limit-up-board"
                  >
                    打开完整雷达 <ChevronRight className="h-3 w-3" />
                  </Link>
                </div>

                <div className="grid gap-3 xl:grid-cols-3 xl:gap-2">
                  <section className="rounded-xl border border-white/10 bg-slate-900 p-4 xl:p-3">
                    <div className="mb-3 flex items-start justify-between gap-3 xl:mb-2">
                      <div>
                        <h3 className="text-xs font-black text-slate-200">
                          行业雷达热度
                        </h3>
                        <p className="mt-1 text-[9px] text-slate-600">
                          候选数量、近涨停与封板强度
                        </p>
                      </div>
                      <span
                        className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[9px] font-black ${
                          radar.isScannerRunning
                            ? 'border-emerald-400/20 bg-emerald-400/10 text-emerald-300'
                            : 'border-slate-500/20 bg-slate-500/5 text-slate-500'
                        }`}
                      >
                        <span
                          className={`h-1.5 w-1.5 rounded-full ${
                            radar.isScannerRunning
                              ? 'bg-emerald-400'
                              : 'bg-slate-600'
                          }`}
                        />
                        {radar.isScannerRunning ? '扫描中' : '未运行'}
                      </span>
                    </div>
                    <HotIndustryGrid industries={radar.industries} />
                  </section>

                  <section className="rounded-xl border border-white/10 bg-slate-900 p-4 xl:p-3">
                    <div className="mb-1 flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-xs font-black text-slate-200">
                          涨停候选
                        </h3>
                        <p className="mt-1 text-[9px] text-slate-600">
                          按雷达综合评分排序
                        </p>
                      </div>
                      <span className="font-mono text-[10px] font-black text-rose-300">
                        {radar.summary.candidateCount} CANDIDATES
                      </span>
                    </div>
                    <RadarCandidates candidates={radar.candidates} />
                  </section>

                  <section className="rounded-xl border border-white/10 bg-slate-900 p-4 xl:p-3">
                    <div className="mb-1 flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-xs font-black text-slate-200">
                          盘中放量异动
                        </h3>
                        <p className="mt-1 text-[9px] text-slate-600">
                          量速、5 分钟放量与涨跌幅联合观察
                        </p>
                      </div>
                      <span
                        className={`inline-flex items-center gap-1 text-[9px] font-bold ${
                          pulse.intradayRunning
                            ? 'text-cyan-300'
                            : 'text-slate-600'
                        }`}
                      >
                        <Activity className="h-3 w-3" />
                        {pulse.intradayRunning ? '实时扫描' : '扫描未运行'}
                      </span>
                    </div>
                    <IntradayMovers
                      emptyMessage={
                        pulse.intradayRunning
                          ? '当前没有捕获到满足条件的放量异动。'
                          : '盘中放量扫描未运行，启动扫描后自动显示异常成交。'
                      }
                      items={pulse.intraday as IntradayMover[]}
                    />
                  </section>
                </div>
              </section>

              <section
                className="mt-5 scroll-mt-14 xl:mt-3"
                id="market-rankings"
              >
                <div className="overflow-hidden rounded-xl border border-white/10 bg-slate-900">
                  <div className="flex items-center justify-between gap-3 border-b border-white/5 px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <ListOrdered className="h-3.5 w-3.5 shrink-0 text-red-300" />
                      <h2 className="text-xs font-black text-slate-100">
                        股票排行
                      </h2>
                      <span className="truncate text-[9px] text-slate-600">
                        {pulseSnapshotLabel} · 非 ST
                      </span>
                    </div>
                    <span className="shrink-0 text-[9px] font-bold text-slate-600">
                      涨跌各 8 只
                    </span>
                  </div>
                  <div
                    className="grid grid-cols-1 gap-px bg-white/5 sm:grid-cols-2"
                    data-testid="stock-ranking-grid"
                  >
                    <StockRankingList
                      direction="gainers"
                      rows={pulse.gainers as RankedStockRow[]}
                    />
                    <StockRankingList
                      direction="losers"
                      rows={pulse.losers as RankedStockRow[]}
                    />
                  </div>
                </div>
              </section>

              <section className="mt-5 scroll-mt-14 xl:mt-3" id="market-tools">
                <div className="mb-2 px-1">
                  <div className="flex items-center gap-2">
                    <Zap className="h-4 w-4 text-amber-300" />
                    <h2 className="text-sm font-black text-slate-100">
                      行情工具
                    </h2>
                  </div>
                  <p className="mt-1 text-[10px] text-slate-600">
                    从发现机会继续进入筛选、研究与数据维护
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
                  {quickTools.map(tool => {
                    const Icon = tool.icon;
                    return (
                      <Link
                        key={tool.label}
                        className="group cursor-pointer rounded-xl border border-white/5 bg-slate-900 p-3.5 transition-colors hover:border-white/15 hover:bg-white/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70 xl:p-2.5"
                        href={tool.href}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/[0.04]">
                            <Icon className="h-4 w-4 text-slate-400 transition-colors group-hover:text-red-300" />
                          </span>
                          <ArrowRight className="h-3.5 w-3.5 text-slate-700 transition-colors group-hover:text-slate-300" />
                        </span>
                        <span className="mt-3 block text-[11px] font-black text-slate-200">
                          {tool.label}
                        </span>
                        <span className="mt-1 block truncate text-[9px] text-slate-600">
                          {tool.description}
                        </span>
                      </Link>
                    );
                  })}
                </div>
              </section>
            </div>
          </main>
        </div>
      }
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                marketDataIsStale || pulseDataIsMissing
                  ? 'bg-amber-400'
                  : market.dataMode === 'live'
                    ? 'bg-emerald-400'
                    : market.dataMode === 'intraday'
                      ? 'bg-cyan-400'
                      : 'bg-slate-600'
              }`}
            />
            行情工作台
          </span>
          <span className="text-slate-700">|</span>
          <span>
            {marketDataIsStale
              ? '行情滞后'
              : pulseDataIsMissing
                ? '个股快照缺失'
                : market.dataMode === 'live' && session.isOpen
                  ? '实时 tick'
                  : market.dataMode === 'intraday'
                    ? '实盘快照'
                    : '最近收盘'}{' '}
            {market.targetDateCoverage}/{market.indices.length}
          </span>
        </>
      }
      statusBarRight={
        <>
          <span>{market.summary.toneLabel}</span>
          <span className="text-slate-700">|</span>
          <span>{selected?.definition.name}</span>
        </>
      }
    />
  );
}
