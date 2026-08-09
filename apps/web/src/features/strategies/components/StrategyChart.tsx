import { Layers3, Loader2 } from 'lucide-react';
import { useCallback, useMemo, useState } from 'react';

import { cn } from '@/utils/cn';

import { StrategyChartCanvas } from './strategy-chart/StrategyChartCanvas';
import { StrategyChartControls } from './strategy-chart/StrategyChartControls';
import type {
  ChartOverlay,
  StrategyChartHoverData,
  StrategyChartMarker,
  StrategyChartMarkerDetail,
  StrategyChartPeriod,
  StrategyChartProps,
} from './strategy-chart/types';
import { useStrategyChartData } from './strategy-chart/useStrategyChartData';
import { buildTradeMarkers, formatRangeLabel } from './strategy-chart/utils';

export type { ChartOverlay };

const DEFAULT_BACKTEST_PERIOD: StrategyChartPeriod = 'DAY_1';
const MARKER_TOOLTIP_LIMIT = 12;

function markerBadgeClass(marker: StrategyChartMarker) {
  if (marker.eventType === 'rejected') {
    return 'border-amber-300/70 bg-amber-400 text-slate-950';
  }
  if (marker.tradeSide === 'SELL') {
    return marker.eventType === 'signal'
      ? 'border-sky-300/70 bg-sky-400/20 text-sky-100'
      : 'border-sky-300/70 bg-sky-500 text-white';
  }
  if (marker.tradeSide === 'BUY') {
    return marker.eventType === 'signal'
      ? 'border-red-300/70 bg-red-400/20 text-red-100'
      : 'border-red-300/70 bg-red-500 text-white';
  }
  return 'border-slate-300/60 bg-slate-500 text-white';
}

function detailToneClass(tone?: StrategyChartMarkerDetail['tone']) {
  if (tone === 'buy') return 'text-red-300';
  if (tone === 'sell') return 'text-sky-300';
  if (tone === 'success') return 'text-emerald-300';
  if (tone === 'warning') return 'text-amber-300';
  if (tone === 'muted') return 'text-slate-400';
  return 'text-slate-200';
}

function MarkerDetails({ markers }: { markers: StrategyChartMarker[] }) {
  const expandedMarkers = markers.flatMap(marker =>
    marker.childMarkers?.length ? marker.childMarkers : [marker]
  );
  if (expandedMarkers.length === 0) return null;
  const visibleMarkers = expandedMarkers.slice(0, MARKER_TOOLTIP_LIMIT);
  const hiddenCount = expandedMarkers.length - visibleMarkers.length;

  return (
    <div className="mt-2 border-t border-white/5 pt-2">
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <span className="text-[10px] text-slate-500">买卖标记</span>
        <span className="text-[10px] text-slate-400">
          {expandedMarkers.length} 条
        </span>
      </div>
      <div className="space-y-2">
        {visibleMarkers.map(marker => (
          <div key={marker.id || `${marker.time}-${marker.eventTitle}`}>
            <div className="mb-1 flex items-center gap-1.5">
              <span
                className={cn(
                  'inline-flex h-4 min-w-4 items-center justify-center rounded-[3px] border px-1 text-[10px] font-black leading-none shadow-sm',
                  markerBadgeClass(marker)
                )}
              >
                {marker.label}
              </span>
              <span className="text-[10px] font-bold text-slate-100">
                {marker.eventTitle}
              </span>
            </div>
            <div className="grid grid-cols-[42px_minmax(0,1fr)] gap-x-2 gap-y-0.5 pl-5">
              {marker.detailRows.slice(0, 5).map(row => (
                <div
                  key={`${marker.id}-${row.label}-${row.value}`}
                  className="contents"
                >
                  <span className="text-[9px] text-slate-500">{row.label}</span>
                  <span
                    className={cn(
                      'truncate text-[9px] font-medium',
                      detailToneClass(row.tone)
                    )}
                    title={row.value}
                  >
                    {row.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ))}
        {hiddenCount > 0 && (
          <div className="pl-5 text-[10px] text-slate-500">
            另有 {hiddenCount} 条标记
          </div>
        )}
      </div>
    </div>
  );
}

function HoverPanel({ hover }: { hover: StrategyChartHoverData }) {
  return (
    <div className="absolute top-16 left-5 z-10 max-h-[420px] min-w-[178px] max-w-[300px] overflow-hidden rounded-b-lg border border-white/5 bg-[#0B1120]/90 p-2.5 shadow-xl backdrop-blur-md pointer-events-none">
      <div className="space-y-1 text-[10px] font-mono">
        <div className="flex justify-between gap-3">
          <span className="text-slate-500">时间</span>
          <span className="text-slate-200">{hover.time}</span>
        </div>
        {hover.type === 'tick' ? (
          <div className="flex justify-between gap-3">
            <span className="text-slate-500">价格</span>
            <span className="text-sky-300 font-bold">
              {hover.price?.toFixed(2)}
            </span>
          </div>
        ) : (
          <>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">开盘</span>
              <span className="text-slate-200">{hover.open?.toFixed(2)}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">最高</span>
              <span className="text-red-400">{hover.high?.toFixed(2)}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">最低</span>
              <span className="text-green-400">{hover.low?.toFixed(2)}</span>
            </div>
            <div className="flex justify-between gap-3">
              <span className="text-slate-500">收盘</span>
              <span className="text-slate-200 font-bold">
                {hover.close?.toFixed(2)}
              </span>
            </div>
            <div className="border-t border-white/5 pt-1 mt-1">
              <div className="flex justify-between gap-3">
                <span className="text-slate-500">涨跌</span>
                <span
                  className={
                    (hover.change || 0) >= 0 ? 'text-red-400' : 'text-green-400'
                  }
                >
                  {(hover.change || 0) >= 0 ? '+' : ''}
                  {hover.change?.toFixed(2)} ({hover.changePercent?.toFixed(2)}
                  %)
                </span>
              </div>
            </div>
          </>
        )}
        <MarkerDetails markers={hover.markers || []} />
      </div>
    </div>
  );
}

const StrategyChart = ({
  stockCode,
  className,
  overlays = [],
  latestTick,
  liveTicks = [],
  mode = 'live',
  backtestRange,
  decisions = [],
  executions = [],
}: StrategyChartProps) => {
  const [activePeriod, setActivePeriod] = useState<StrategyChartPeriod>(
    DEFAULT_BACKTEST_PERIOD
  );
  const [hoverData, setHoverData] = useState<StrategyChartHoverData | null>(
    null
  );

  const isBacktest = mode === 'backtest';
  const {
    priceData,
    volumeData,
    loading,
    hasMore,
    loadMore,
    hasRange,
    isTickPeriod,
  } = useStrategyChartData({
    stockCode,
    mode,
    period: activePeriod,
    backtestRange,
    liveTicks,
  });

  const markers = useMemo(
    () =>
      buildTradeMarkers({
        period: activePeriod,
        isTickPeriod,
        decisions,
        executions,
      }),
    [activePeriod, decisions, executions, isTickPeriod]
  );

  const handlePeriodChange = useCallback((period: StrategyChartPeriod) => {
    setActivePeriod(period);
    setHoverData(null);
  }, []);

  const rangeLabel = useMemo(
    () => formatRangeLabel(backtestRange),
    [backtestRange]
  );

  if (!stockCode) {
    return (
      <div
        className={cn(
          'w-full h-full relative flex flex-col items-center justify-center bg-slate-900/20 rounded-lg',
          className
        )}
      >
        <p className="text-sm text-slate-500 font-mono">等待行情数据</p>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'w-full h-full relative group flex flex-col overflow-hidden',
        className
      )}
    >
      {loading && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-900/20 backdrop-blur-sm">
          <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
        </div>
      )}

      {(isBacktest || mode === 'live') && (
        <StrategyChartControls
          activePeriod={activePeriod}
          onPeriodChange={handlePeriodChange}
        />
      )}

      {isBacktest && (
        <>
          <div className="absolute bottom-4 left-4 z-20 hidden xl:flex items-center gap-1.5 rounded-lg border border-white/10 bg-slate-950/75 p-1.5 shadow-lg backdrop-blur-md">
            {[
              '成交量',
              overlays.length > 0 ? '策略线' : null,
              markers.length > 0 ? '买卖标记' : null,
            ]
              .filter((label): label is string => Boolean(label))
              .map(label => (
                <span
                  key={label}
                  className="rounded-md bg-white/5 px-2 py-1 text-[10px] font-bold text-slate-300"
                >
                  {label}
                </span>
              ))}
            <span className="inline-flex items-center gap-1 rounded-md border border-dashed border-slate-700 px-2 py-1 text-[10px] font-bold text-slate-500">
              <Layers3 className="h-3 w-3" />
              净值/回撤待序列
            </span>
          </div>
          {rangeLabel && (
            <div className="absolute bottom-4 right-4 z-20 rounded-lg border border-white/10 bg-slate-950/85 px-3 py-2 text-[11px] font-bold text-slate-300 shadow-lg backdrop-blur-md">
              {rangeLabel}
            </div>
          )}
        </>
      )}

      {!loading && isBacktest && hasRange && priceData.length === 0 && (
        <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
          <div className="rounded-xl border border-white/10 bg-slate-950/80 px-4 py-3 text-xs font-bold text-slate-400 shadow-xl backdrop-blur-md">
            当前回测区间暂无行情数据
          </div>
        </div>
      )}

      {hoverData && <HoverPanel hover={hoverData} />}

      <StrategyChartCanvas
        priceData={priceData}
        volumeData={volumeData}
        overlays={overlays}
        markers={markers}
        period={activePeriod}
        isTickPeriod={isTickPeriod}
        isBacktest={isBacktest}
        latestTick={latestTick}
        canLoadMore={hasMore}
        loadMore={loadMore}
        onHoverChange={setHoverData}
      />
    </div>
  );
};

export default StrategyChart;
