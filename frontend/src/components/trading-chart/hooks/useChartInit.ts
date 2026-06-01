import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  CandlestickSeries,
  AreaSeries,
} from 'lightweight-charts';
import React, { useEffect, useMemo, useRef, useState } from 'react';

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
  const areaSeriesRef = useRef<ISeriesApi<'Area'> | null>(null);

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
      upColor: '#ef4444',
      downColor: '#22c55e',
      borderVisible: false,
      wickUpColor: '#ef4444',
      wickDownColor: '#22c55e',
      visible: !isTimeMode,
      lastValueVisible: false,
      priceLineVisible: true,
    });

    const areaSeries = mainChart.addSeries(AreaSeries, {
      topColor: 'rgba(37, 99, 235, 0.4)',
      bottomColor: 'rgba(37, 99, 235, 0.01)',
      lineColor: '#2563eb',
      lineWidth: 2,
      visible: isTimeMode,
      lastValueVisible: false,
      priceLineVisible: true,
    });

    mainChartRef.current = mainChart;
    candlestickSeriesRef.current = candlestickSeries;
    areaSeriesRef.current = areaSeries;

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
      subChartsRef.current.set(key, subChart);
    });

    setIsReady(true);
    setChartVersion(v => v + 1);

    return () => {
      setIsReady(false);
      mainChart.remove();
      mainChartRef.current = null;
      candlestickSeriesRef.current = null;
      areaSeriesRef.current = null;

      subChartsRef.current.forEach(c => c.remove());
      subChartsRef.current.clear();
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
        area: areaSeriesRef,
      },
      isReady,
      chartVersion,
    }),
    [isReady, chartVersion]
  );
}
