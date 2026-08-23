import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  CandlestickSeries,
  LineSeries,
} from 'lightweight-charts';
import { useEffect, useMemo, useRef, useState } from 'react';

import { FINANCIAL_CHART_COLORS } from '@/shared/utils/financialColors';

import { getCommonOptions } from '../utils/options';

export function useChartInit(
  mainContainer: HTMLDivElement | null,
  subContainers: Map<string, HTMLElement>,
  isTimeMode: boolean
) {
  const mainChartRef = useRef<IChartApi | null>(null);
  // Store sub-charts in a map
  const subChartsRef = useRef<Map<string, IChartApi>>(new Map());

  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const timeLineSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const timeAverageSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);
  const timeAnchorSeriesRef = useRef<ISeriesApi<'Line'> | null>(null);

  const [isReady, setIsReady] = useState(false);
  const [chartVersion, setChartVersion] = useState(0);

  // Resize Observer
  useEffect(() => {
    if (!mainContainer) return;

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        // Check main container
        if (entry.target === mainContainer && mainChartRef.current) {
          mainChartRef.current.applyOptions({ width, height });
        }
        // Check sub containers
        subContainers.forEach((el, key) => {
          if (entry.target === el) {
            const chart = subChartsRef.current.get(key);
            if (chart) {
              chart.applyOptions({ width, height });
            }
          }
        });
      }
    });

    resizeObserver.observe(mainContainer);
    subContainers.forEach(el => resizeObserver.observe(el));

    return () => {
      resizeObserver.disconnect();
    };
  }, [mainContainer, subContainers, subContainers.size]);

  useEffect(() => {
    if (!mainContainer) return;

    const commonOptions = getCommonOptions(isTimeMode);
    const subCharts = subChartsRef.current;

    // --- Main Chart ---
    const mainChart = createChart(mainContainer, {
      ...commonOptions,
      width: mainContainer.clientWidth,
      height: mainContainer.clientHeight,
      rightPriceScale: {
        borderColor: 'rgba(39, 39, 42, 0.2)',
        scaleMargins: { top: 0.15, bottom: 0.1 },
        minimumWidth: 70,
      },
      timeScale: {
        ...commonOptions.timeScale,
        visible: true,
      },
    });

    const candlestickSeries = mainChart.addSeries(CandlestickSeries, {
      upColor: FINANCIAL_CHART_COLORS.up,
      downColor: FINANCIAL_CHART_COLORS.down,
      borderVisible: false,
      wickUpColor: FINANCIAL_CHART_COLORS.up,
      wickDownColor: FINANCIAL_CHART_COLORS.down,
      visible: !isTimeMode,
      lastValueVisible: false,
      priceLineVisible: true,
    });

    const timeLineSeries = mainChart.addSeries(LineSeries, {
      color: '#e5e7eb',
      lineWidth: 2,
      visible: isTimeMode,
      lastValueVisible: true,
      priceLineVisible: true,
      priceLineColor: 'rgba(234, 179, 8, 0.65)',
      priceLineStyle: 2,
      priceLineWidth: 1,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 4,
    });

    const timeAverageSeries = mainChart.addSeries(LineSeries, {
      color: '#f59e0b',
      lineWidth: 2,
      visible: isTimeMode,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    const timeAnchorSeries = mainChart.addSeries(LineSeries, {
      color: 'rgba(0, 0, 0, 0)',
      lineWidth: 1,
      visible: isTimeMode,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    mainChartRef.current = mainChart;
    candlestickSeriesRef.current = candlestickSeries;
    timeLineSeriesRef.current = timeLineSeries;
    timeAverageSeriesRef.current = timeAverageSeries;
    timeAnchorSeriesRef.current = timeAnchorSeries;

    // --- Sub Charts ---
    // Initialize sub-charts
    subContainers.forEach((container, key) => {
      const subChart = createChart(container, {
        ...commonOptions,
        width: container.clientWidth,
        height: container.clientHeight,
        rightPriceScale: {
          borderColor: 'rgba(39, 39, 42, 0.2)',
          scaleMargins: { top: 0.1, bottom: 0.1 },
          minimumWidth: 70,
        },
        timeScale: {
          ...commonOptions.timeScale,
          visible: false,
        },
        crosshair: {
          ...commonOptions.crosshair,
          vertLine: {
            ...commonOptions.crosshair?.vertLine,
            labelVisible: true,
          },
        },
      });
      subCharts.set(key, subChart);
    });

    setIsReady(true);
    setChartVersion(v => v + 1);

    return () => {
      setIsReady(false);
      mainChart.remove();
      mainChartRef.current = null;
      candlestickSeriesRef.current = null;
      timeLineSeriesRef.current = null;
      timeAverageSeriesRef.current = null;
      timeAnchorSeriesRef.current = null;

      subCharts.forEach(c => c.remove());
      subCharts.clear();
    };
  }, [isTimeMode, mainContainer, subContainers, subContainers.size]);

  return useMemo(
    () => ({
      charts: {
        main: mainChartRef,
        subs: subChartsRef,
      },
      series: {
        candlestick: candlestickSeriesRef,
        timeLine: timeLineSeriesRef,
        timeAverage: timeAverageSeriesRef,
        timeAnchor: timeAnchorSeriesRef,
      },
      isReady,
      chartVersion,
    }),
    [isReady, chartVersion]
  );
}
