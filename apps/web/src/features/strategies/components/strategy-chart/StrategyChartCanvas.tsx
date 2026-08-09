import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  TickMarkType,
  createChart,
  createSeriesMarkers,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type LineData,
  type Logical,
  type LogicalRange,
  type MouseEventParams,
  type Time,
} from 'lightweight-charts';
import { useEffect, useRef, useState } from 'react';

import type { StrategyTickData } from '../../hooks/useStrategyTicks';

import type {
  ChartOverlay,
  StrategyChartHoverData,
  StrategyChartMarker,
  StrategyChartPeriod,
  StrategyChartDataState,
} from './types';
import { chartTimeKey, formatHoverTime } from './utils';

const EDGE_PADDING_BARS = 2;
const LOAD_MORE_EDGE_THRESHOLD = 20;

function latestTickBarTime(tickTime: Date, period: StrategyChartPeriod): Time {
  if (period === 'DAY_1' || period === 'WEEK_1' || period === 'MONTH_1') {
    return tickTime.toISOString().split('T')[0] as Time;
  }
  const match = period.match(/^MIN_(\d+)$/);
  const intervalMinutes = match ? Number(match[1]) : 1;
  const intervalMs = Math.max(1, intervalMinutes) * 60 * 1000;
  return Math.floor(
    (Math.floor(tickTime.getTime() / intervalMs) * intervalMs) / 1000
  ) as Time;
}

interface StrategyChartCanvasProps {
  priceData: StrategyChartDataState['priceData'];
  volumeData: StrategyChartDataState['volumeData'];
  overlays: ChartOverlay[];
  markers: StrategyChartMarker[];
  period: StrategyChartPeriod;
  isTickPeriod: boolean;
  isBacktest: boolean;
  latestTick?: StrategyTickData | null;
  canLoadMore: boolean;
  loadMore: () => void;
  onHoverChange: (hover: StrategyChartHoverData | null) => void;
}

type PriceSeries = ISeriesApi<'Candlestick'> | ISeriesApi<'Line'>;
type PriceDatum = StrategyChartDataState['priceData'][number];

export function StrategyChartCanvas({
  priceData,
  volumeData,
  overlays,
  markers,
  period,
  isTickPeriod,
  isBacktest,
  latestTick,
  canLoadMore,
  loadMore,
  onHoverChange,
}: StrategyChartCanvasProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const [chartInstance, setChartInstance] = useState<IChartApi | null>(null);
  const priceSeriesRef = useRef<PriceSeries | null>(null);
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const priceDataWriterRef = useRef<((data: PriceDatum[]) => void) | null>(
    null
  );
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null);
  const markerApiRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const hasFittedContentRef = useRef(false);
  const clampingRangeRef = useRef(false);

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
        scaleMargins: { top: 0.08, bottom: 0.24 },
      },
      timeScale: {
        borderVisible: false,
        timeVisible: true,
        secondsVisible: false,
        rightOffset: EDGE_PADDING_BARS,
        fixLeftEdge: true,
        fixRightEdge: true,
        lockVisibleTimeRangeOnResize: true,
        rightBarStaysOnScroll: true,
        tickMarkFormatter: (time: Time, tickMarkType: TickMarkType) => {
          if (typeof time === 'number') {
            const date = new Date(time * 1000);
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            const hour = String(date.getHours()).padStart(2, '0');
            const minute = String(date.getMinutes()).padStart(2, '0');
            if (
              tickMarkType === TickMarkType.Year ||
              tickMarkType === TickMarkType.Month ||
              tickMarkType === TickMarkType.DayOfMonth
            ) {
              return `${month}-${day}`;
            }
            return `${hour}:${minute}`;
          }
          const parts = String(time).split('-');
          if (parts.length === 3) {
            if (tickMarkType === TickMarkType.Year) return parts[0];
            return `${parts[1]}-${parts[2]}`;
          }
          return String(time);
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
      priceSeriesRef.current = null;
      candlestickSeriesRef.current = null;
      priceDataWriterRef.current = null;
      volumeSeriesRef.current = null;
      markerApiRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartInstance) return;

    hasFittedContentRef.current = false;

    if (markerApiRef.current) {
      try {
        markerApiRef.current.detach();
      } catch (_e) {
        // Ignore stale marker plugin cleanup.
      }
      markerApiRef.current = null;
    }
    if (priceSeriesRef.current) {
      try {
        chartInstance.removeSeries(priceSeriesRef.current);
      } catch (_e) {
        // Ignore stale series cleanup.
      }
      priceSeriesRef.current = null;
      candlestickSeriesRef.current = null;
      priceDataWriterRef.current = null;
    }
    if (volumeSeriesRef.current) {
      try {
        chartInstance.removeSeries(volumeSeriesRef.current);
      } catch (_e) {
        // Ignore stale series cleanup.
      }
      volumeSeriesRef.current = null;
    }

    let priceSeries: PriceSeries;
    if (isTickPeriod) {
      const lineSeries = chartInstance.addSeries(LineSeries, {
        color: '#38bdf8',
        lineWidth: 2,
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      });
      priceSeries = lineSeries;
      priceDataWriterRef.current = data =>
        lineSeries.setData(
          data.filter((item): item is LineData<Time> => 'value' in item)
        );
    } else {
      const candlestickSeries = chartInstance.addSeries(CandlestickSeries, {
        upColor: '#ef4444',
        downColor: '#22c55e',
        borderVisible: false,
        wickUpColor: '#ef4444',
        wickDownColor: '#22c55e',
        priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      });
      priceSeries = candlestickSeries;
      candlestickSeriesRef.current = candlestickSeries;
      priceDataWriterRef.current = data =>
        candlestickSeries.setData(
          data.filter((item): item is CandlestickData<Time> => 'open' in item)
        );
    }

    const volumeSeries = chartInstance.addSeries(HistogramSeries, {
      priceScaleId: '',
      priceFormat: { type: 'volume' },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chartInstance.priceScale('').applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 },
    });

    overlays.forEach(overlay => {
      priceSeries.createPriceLine({
        price: overlay.price,
        color: overlay.color,
        lineWidth: (overlay.lineWidth || 1) as 1 | 2 | 3 | 4,
        lineStyle:
          overlay.lineStyle === 'dashed'
            ? LineStyle.Dashed
            : overlay.lineStyle === 'dotted'
              ? LineStyle.Dotted
              : LineStyle.Solid,
        axisLabelVisible: true,
        title: overlay.title || '',
      });
    });

    priceSeriesRef.current = priceSeries;
    volumeSeriesRef.current = volumeSeries;
    markerApiRef.current = createSeriesMarkers(priceSeries, [], {
      zOrder: 'top',
    });
  }, [chartInstance, isTickPeriod, overlays]);

  useEffect(() => {
    if (!priceDataWriterRef.current) return;
    priceDataWriterRef.current(priceData);
    if (priceData.length === 0) {
      hasFittedContentRef.current = false;
      return;
    }
    if (!hasFittedContentRef.current) {
      hasFittedContentRef.current = true;
      setTimeout(() => chartInstance?.timeScale().fitContent(), 0);
    }
  }, [chartInstance, priceData]);

  useEffect(() => {
    if (!volumeSeriesRef.current) return;
    volumeSeriesRef.current.setData(volumeData);
  }, [volumeData]);

  useEffect(() => {
    if (!markerApiRef.current) return;
    markerApiRef.current.setMarkers(markers);
  }, [markers]);

  useEffect(() => {
    if (
      isBacktest ||
      !latestTick ||
      !candlestickSeriesRef.current ||
      !chartInstance ||
      isTickPeriod
    ) {
      return;
    }

    const tickTime = new Date(latestTick.time);
    const time = latestTickBarTime(tickTime, period);
    const existingData = priceData.find(
      (datum): datum is CandlestickData<Time> =>
        'open' in datum && datum.time === time
    );

    if (existingData) {
      candlestickSeriesRef.current.update({
        ...existingData,
        close: latestTick.lastPrice,
        high: Math.max(
          existingData.high,
          latestTick.highPrice || latestTick.lastPrice
        ),
        low: Math.min(
          existingData.low,
          latestTick.lowPrice || latestTick.lastPrice
        ),
      });
    } else {
      candlestickSeriesRef.current.update({
        time,
        open: latestTick.openPrice || latestTick.lastPrice,
        high: latestTick.highPrice || latestTick.lastPrice,
        low: latestTick.lowPrice || latestTick.lastPrice,
        close: latestTick.lastPrice,
      });
    }
  }, [chartInstance, isBacktest, isTickPeriod, latestTick, period, priceData]);

  useEffect(() => {
    if (!chartInstance) return;

    const handleVisibleLogicalRangeChange = (range: LogicalRange | null) => {
      if (
        !range ||
        clampingRangeRef.current ||
        priceData.length === 0 ||
        !Number.isFinite(range.from) ||
        !Number.isFinite(range.to)
      ) {
        return;
      }

      if (isBacktest && canLoadMore && range.from < LOAD_MORE_EDGE_THRESHOLD) {
        loadMore();
      }

      const firstIndex = 0;
      const lastIndex = priceData.length - 1;
      const leftLimit = firstIndex;
      const rightLimit = lastIndex + EDGE_PADDING_BARS;
      const currentWidth = range.to - range.from;
      const maxWidth = rightLimit - leftLimit;
      if (currentWidth <= 0 || maxWidth <= 0) return;

      let nextFrom: number = range.from;
      let nextTo: number = range.to;

      if (currentWidth >= maxWidth) {
        nextFrom = leftLimit;
        nextTo = rightLimit;
      } else if (range.from < leftLimit) {
        nextFrom = leftLimit;
        nextTo = leftLimit + currentWidth;
      } else if (range.to > rightLimit) {
        nextTo = rightLimit;
        nextFrom = rightLimit - currentWidth;
      }

      if (
        Math.abs(nextFrom - range.from) > 0.01 ||
        Math.abs(nextTo - range.to) > 0.01
      ) {
        clampingRangeRef.current = true;
        requestAnimationFrame(() => {
          chartInstance.timeScale().setVisibleLogicalRange({
            from: nextFrom as Logical,
            to: nextTo as Logical,
          });
          requestAnimationFrame(() => {
            clampingRangeRef.current = false;
          });
        });
      }
    };
    const timeScale = chartInstance.timeScale();
    timeScale.subscribeVisibleLogicalRangeChange(
      handleVisibleLogicalRangeChange
    );

    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(
        handleVisibleLogicalRangeChange
      );
    };
  }, [canLoadMore, chartInstance, isBacktest, loadMore, priceData.length]);

  useEffect(() => {
    if (!chartInstance) return;

    const handleCrosshairMove = (param: MouseEventParams<Time>) => {
      if (
        !param ||
        !param.time ||
        !param.seriesData ||
        !priceSeriesRef.current
      ) {
        onHoverChange(null);
        return;
      }

      const item = param.seriesData.get(priceSeriesRef.current);
      const hoverMarkers = markers.filter(
        marker => chartTimeKey(marker.time) === chartTimeKey(param.time)
      );
      if (item && 'open' in item) {
        const change = item.close - item.open;
        onHoverChange({
          type: 'candle',
          time: formatHoverTime(param.time),
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
          change,
          changePercent: item.open ? (change / item.open) * 100 : 0,
          markers: hoverMarkers,
        });
      } else if (item && 'value' in item) {
        onHoverChange({
          type: 'tick',
          time: formatHoverTime(param.time),
          price: item.value,
          markers: hoverMarkers,
        });
      } else if (hoverMarkers.length > 0) {
        onHoverChange({
          type: 'tick',
          time: formatHoverTime(param.time),
          markers: hoverMarkers,
        });
      } else {
        onHoverChange(null);
      }
    };

    chartInstance.subscribeCrosshairMove(handleCrosshairMove);
    return () => {
      chartInstance.unsubscribeCrosshairMove(handleCrosshairMove);
    };
  }, [chartInstance, markers, onHoverChange]);

  return <div className="flex-1 w-full min-h-0" ref={chartContainerRef} />;
}
