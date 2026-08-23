import {
  createChart,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type MouseEventParams,
  type Time,
  LineStyle,
  CandlestickSeries,
  TickMarkType,
} from 'lightweight-charts';
import { Loader2 } from 'lucide-react';
import React, { useEffect, useRef, useState, useMemo } from 'react';

import { useInfiniteKLines } from '@/hooks';
import { FINANCIAL_CHART_COLORS } from '@/shared/utils/financialColors';

import { type GridResult } from '../types';

interface Props {
  result: GridResult;
  stockCode?: string;
}

interface HoverData {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  change: number;
  changePercent: number;
}

const GridChart: React.FC<Props> = ({ result, stockCode }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const [chartInstance, setChartInstance] = useState<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const [hoverData, setHoverData] = useState<HoverData | null>(null);

  // Fetch real K-line data using the hook
  const { data: rawKlines, loading } = useInfiniteKLines(
    stockCode || '',
    'DAY_1',
    !!stockCode
  );

  // Transform API data to chart format (no mock data fallback)
  const chartData = useMemo(() => {
    if (!stockCode || !rawKlines || rawKlines.length === 0) {
      return [];
    }
    return rawKlines.map((k): CandlestickData<Time> & { value: number } => ({
      time: k.time.split('T')[0] as Time,
      open: k.open,
      high: k.high,
      low: k.low,
      close: k.close,
      value: k.close,
    }));
  }, [stockCode, rawKlines]);

  // Chart Initialization
  useEffect(() => {
    if (!chartContainerRef.current) return;

    const isDark = document.documentElement.classList.contains('dark');
    const newChart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: isDark ? '#94a3b8' : '#64748b',
        fontSize: 10,
        fontFamily: 'JetBrains Mono, monospace',
      },
      grid: {
        vertLines: {
          color: isDark
            ? 'rgba(148, 163, 184, 0.05)'
            : 'rgba(148, 163, 184, 0.1)',
        },
        horzLines: {
          color: isDark
            ? 'rgba(148, 163, 184, 0.05)'
            : 'rgba(148, 163, 184, 0.1)',
        },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.05, bottom: 0.05 },
      },
      timeScale: {
        borderVisible: false,
        timeVisible: false,
        secondsVisible: false,
        rightOffset: 0, // Prevent scrolling beyond last data point
        lockVisibleTimeRangeOnResize: true,
        tickMarkFormatter: (time: string, tickMarkType: TickMarkType) => {
          // time is in 'YYYY-MM-DD' format
          const parts = time.split('-');
          if (parts.length === 3) {
            if (tickMarkType === TickMarkType.Year) {
              return parts[0];
            }
            return `${parts[1]}-${parts[2]}`;
          }
          return time;
        },
      },
      localization: {
        dateFormat: 'yyyy-MM-dd',
      },
      crosshair: {
        mode: 0,
        vertLine: { labelBackgroundColor: '#3b82f6' },
        horzLine: { labelBackgroundColor: '#3b82f6' },
      },
      handleScroll: true,
      handleScale: true,
    });

    setChartInstance(newChart);

    // Use ResizeObserver for container-based resize detection (handles ResizablePanel changes)
    const resizeObserver = new ResizeObserver(() => {
      requestAnimationFrame(() => {
        if (chartContainerRef.current) {
          newChart.applyOptions({
            width: chartContainerRef.current.clientWidth,
            height: chartContainerRef.current.clientHeight,
          });
        }
      });
    });
    resizeObserver.observe(chartContainerRef.current);

    return () => {
      resizeObserver.disconnect();
      newChart.remove();
      setChartInstance(null);
    };
  }, [stockCode]); // Re-run when stockCode changes (mounting/unmounting chart view)

  // Series Branding & Data Update
  useEffect(() => {
    if (!chartInstance) {
      return;
    }

    // Cleanup previous series
    if (seriesRef.current) {
      try {
        chartInstance.removeSeries(seriesRef.current);
      } catch (_e) {
        // Series might have been removed already on unmount
      }
    }

    // Add New Series based on type

    // Calculate price range that includes all grid levels and force center base price
    const gridPrices = result.levels.map(l => l.price);
    const dataPrices = chartData.map(d => d.value);
    const allPrices = [...gridPrices, ...dataPrices];

    // Calculate max deviation from base price to create symmetric range
    const minPrice = Math.min(...allPrices);
    const maxPrice = Math.max(...allPrices);
    const maxDiff = Math.max(
      Math.abs(maxPrice - result.basePrice),
      Math.abs(minPrice - result.basePrice)
    );

    // Add 10% padding
    const range = maxDiff * 1.1;
    const centerMin = result.basePrice - range;
    const centerMax = result.basePrice + range;

    // Use CandlestickSeries for daily K-line chart
    // Determine if last price overlaps with base price (within a small margin)
    // In this calculator context, they often start identical.
    // If they are very close, hide the series label to avoid duplication with the Base Price line label.
    let lastValueVisible = true;
    if (chartData.length > 0) {
      const lastClose = chartData[chartData.length - 1].close;
      if (Math.abs(lastClose - result.basePrice) < 0.01) {
        lastValueVisible = false;
      }
    }

    const newSeries = chartInstance.addSeries(CandlestickSeries, {
      upColor: FINANCIAL_CHART_COLORS.up,
      downColor: FINANCIAL_CHART_COLORS.down,
      borderVisible: false,
      wickUpColor: FINANCIAL_CHART_COLORS.up,
      wickDownColor: FINANCIAL_CHART_COLORS.down,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      lastValueVisible,
      autoscaleInfoProvider: () => ({
        priceRange: {
          minValue: centerMin,
          maxValue: centerMax,
        },
      }),
    });
    newSeries.setData(chartData);
    seriesRef.current = newSeries;

    // Add Grid Lines
    result.levels.forEach(level => {
      newSeries.createPriceLine({
        price: level.price,
        color:
          level.side === 'BUY'
            ? `${FINANCIAL_CHART_COLORS.up}99`
            : `${FINANCIAL_CHART_COLORS.down}99`,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: level.side === 'BUY' ? '买' : '卖',
      });
    });

    // Add Base Price Line
    // Add Base Price Line
    newSeries.createPriceLine({
      price: result.basePrice,
      color: '#3b82f6',
      lineWidth: 1,
      lineStyle: LineStyle.Solid,
      axisLabelVisible: true,
      title: '基',
    });

    chartInstance.timeScale().fitContent();

    // Prevent scrolling beyond the last data point and limit max visible bars to 51
    const dataLength = chartData.length;
    const maxVisibleBars = 51;
    const timeScale = chartInstance.timeScale();

    // Set initial visible range to show last 51 bars plus padding
    if (dataLength > 0) {
      const rightPadding = 3;
      const from = Math.max(0, dataLength - maxVisibleBars);
      timeScale.setVisibleLogicalRange({
        from,
        to: dataLength - 1 + rightPadding,
      });
    }

    const handleVisibleRangeChange = () => {
      const logicalRange = timeScale.getVisibleLogicalRange();
      if (!logicalRange) return;

      let from = logicalRange.from as number;
      let to = logicalRange.to as number;
      let needsUpdate = false;

      // Limit right boundary with padding
      const rightPadding = 3;
      if (to > dataLength - 1 + rightPadding) {
        const rangeSize = to - from;
        to = dataLength - 1 + rightPadding;
        from = to - rangeSize;
        needsUpdate = true;
      }

      // Limit max visible bars to 51
      const visibleBars = to - from;
      if (visibleBars > maxVisibleBars) {
        from = to - maxVisibleBars;
        needsUpdate = true;
      }

      if (needsUpdate) {
        timeScale.setVisibleLogicalRange({ from, to });
      }
    };
    timeScale.subscribeVisibleLogicalRangeChange(handleVisibleRangeChange);

    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(handleVisibleRangeChange);
    };
  }, [chartInstance, chartData, result.levels, result.basePrice]);

  // Subscribe to crosshair move for hover data - Separate effect to ensure seriesRef is populated
  useEffect(() => {
    if (!chartInstance) return;

    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (!param || !param.time || !param.seriesData || !seriesRef.current) {
        setHoverData(null);
        return;
      }

      // Explicitly get data for our series
      const candleData = param.seriesData.get(seriesRef.current);

      if (candleData && 'open' in candleData) {
        const prevClose = candleData.open; // Simplified: use open as previous close
        const change = candleData.close - prevClose;
        const changePercent = (change / prevClose) * 100;

        setHoverData({
          time: param.time as string,
          open: candleData.open,
          high: candleData.high,
          low: candleData.low,
          close: candleData.close,
          change,
          changePercent,
        });
      } else {
        setHoverData(null);
      }
    };
    chartInstance.subscribeCrosshairMove(handleCrosshairMove);

    return () => {
      chartInstance.unsubscribeCrosshairMove(handleCrosshairMove);
    };
  }, [chartInstance, chartData]); // Re-subscribe if chart instance or data changes (data change might replace series)

  // Show empty state if no stock selected
  if (!stockCode) {
    return (
      <div className="w-full h-full min-h-0 relative flex flex-col items-center justify-center bg-slate-50/50 dark:bg-slate-900/50 rounded-lg">
        <div className="text-center space-y-2">
          <div className="w-12 h-12 mx-auto rounded-full bg-slate-200/50 dark:bg-slate-800/50 flex items-center justify-center">
            <svg
              className="w-6 h-6 text-muted-foreground"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z"
              />
            </svg>
          </div>
          <p className="text-sm font-medium text-muted-foreground">
            请选择股票以查看K线图
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full min-h-0 relative group flex flex-col overflow-hidden">
      {/* Loading Overlay */}
      {loading && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-background/50 backdrop-blur-sm">
          <Loader2 className="w-6 h-6 animate-spin text-blue-500" />
        </div>
      )}

      {/* K-Line Info Card - Top Left */}
      {hoverData && (
        <div className="absolute top-12 left-4 z-10 bg-slate-900/80 dark:bg-slate-950/90 backdrop-blur-sm rounded-lg border border-slate-700/50 p-3 min-w-[140px] shadow-lg">
          <div className="space-y-1 text-xs font-mono">
            <div className="flex justify-between gap-4">
              <span className="text-slate-400">日期</span>
              <span className="text-slate-200 font-medium">
                {hoverData.time}
              </span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-slate-400">开盘</span>
              <span className="text-slate-200">
                {hoverData.open.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-slate-400">最高</span>
              <span className="text-market-up">
                {hoverData.high.toFixed(2)}
              </span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-slate-400">最低</span>
              <span className="text-market-down">{hoverData.low.toFixed(2)}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span className="text-slate-400">收盘</span>
              <span className="text-slate-200 font-medium">
                {hoverData.close.toFixed(2)}
              </span>
            </div>
            <div className="border-t border-slate-700/50 pt-1 mt-1">
              <div className="flex justify-between gap-4">
                <span className="text-slate-400">涨跌</span>
                <span
                  className={
                    hoverData.change >= 0
                      ? 'text-market-up'
                      : 'text-market-down'
                  }
                >
                  {hoverData.change >= 0 ? '+' : ''}
                  {hoverData.change.toFixed(2)}
                </span>
              </div>
              <div className="flex justify-between gap-4">
                <span className="text-slate-400">涨跌幅</span>
                <span
                  className={
                    hoverData.changePercent >= 0
                      ? 'text-market-up'
                      : 'text-market-down'
                  }
                >
                  {hoverData.changePercent >= 0 ? '+' : ''}
                  {hoverData.changePercent.toFixed(2)}%
                </span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Toolbar - Legend Only */}
      <div className="shrink-0 px-4 py-2 z-10 flex items-center justify-end">
        <div className="flex gap-3 pr-2">
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-market-up/50" />
            <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
              盈利区
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-1.5 h-1.5 rounded-full bg-market-down/50" />
            <span className="text-[10px] font-bold text-muted-foreground uppercase tracking-widest">
              防御区
            </span>
          </div>
        </div>
      </div>
      {/* Chart Container */}
      <div
        className="flex-1 w-full min-h-0 overflow-hidden"
        ref={chartContainerRef}
      />
      {/* Watermark */}
      <div className="absolute bottom-4 left-4 pointer-events-none opacity-10">
        <h4 className="text-xl font-black italic text-foreground tracking-tighter">
          QUANTX PRO
        </h4>
      </div>
    </div>
  );
};

export default GridChart;
