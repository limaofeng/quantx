import {
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  HistogramSeries,
  type CandlestickData,
  type HistogramData,
  type LineData,
} from 'lightweight-charts';
import type React from 'react';
import { useEffect, useRef } from 'react';

import { FINANCIAL_CHART_COLORS } from '@/shared/utils/financialColors';

import {
  type MainIndicatorType,
  type SubIndicatorType,
  calculateSMA,
  calculateEMA,
  calculateBOLL,
  calculateSAR,
  calculateMACD,
  calculateKDJ,
  calculateRSI,
} from '../utils/indicators';

type IndicatorSeries = ISeriesApi<'Line'> | ISeriesApi<'Histogram'>;

interface StoredIndicatorSeries {
  api: IndicatorSeries;
  setData: (data: LineData[]) => void;
}

export function useChartIndicators(
  charts: {
    main: React.RefObject<IChartApi | null>;
    subs: React.RefObject<Map<string, IChartApi>>;
  },
  data: CandlestickData[],
  volumeData: HistogramData[],
  activeMain: MainIndicatorType[],

  activeSubs: SubIndicatorType[],
  subSeriesMapRef: React.MutableRefObject<Map<string, IndicatorSeries>>,
  isReady: boolean,
  chartVersion: number
) {
  // Keep track of active series to remove them later
  const mainSeriesMap = useRef<Map<string, ISeriesApi<'Line'>[]>>(new Map());

  // Track sub series to cleanup
  // Map<subIndicatorType, { chart: IChartApi, series: StoredIndicatorSeries[] }>
  const subSeriesStore = useRef<
    Map<string, { chart: IChartApi; series: StoredIndicatorSeries[] }>
  >(new Map());

  // Reset stores when charts are recreated
  useEffect(() => {
    if (!isReady) {
      mainSeriesMap.current.clear();
      subSeriesStore.current.clear();
      subSeriesMapRef.current.clear();
    }
  }, [isReady, subSeriesMapRef]);

  // --- Main Indicators ---
  useEffect(() => {
    const chart = charts.main.current;
    if (!chart || data.length === 0) return;

    // cleanup removed indicators
    mainSeriesMap.current.forEach((seriesList, name) => {
      if (!activeMain.includes(name as MainIndicatorType)) {
        seriesList.forEach(s => chart.removeSeries(s));
        mainSeriesMap.current.delete(name);
      }
    });

    // add new indicators
    activeMain.forEach(name => {
      if (mainSeriesMap.current.has(name)) return; // already exists

      const seriesList: ISeriesApi<'Line'>[] = [];

      if (name === 'MA') {
        const ma5 = calculateSMA(data, 5);
        const ma10 = calculateSMA(data, 10);
        const ma20 = calculateSMA(data, 20);

        const s1 = chart.addSeries(LineSeries, {
          color: '#f59e0b',
          lineWidth: 1,
          title: 'MA5',
        });
        s1.setData(ma5);
        const s2 = chart.addSeries(LineSeries, {
          color: '#3b82f6',
          lineWidth: 1,
          title: 'MA10',
        });
        s2.setData(ma10);
        const s3 = chart.addSeries(LineSeries, {
          color: '#ec4899',
          lineWidth: 1,
          title: 'MA20',
        });
        s3.setData(ma20);

        seriesList.push(s1, s2, s3);
      } else if (name === 'EMA') {
        const ema12 = calculateEMA(data, 12);
        const ema26 = calculateEMA(data, 26);

        const s1 = chart.addSeries(LineSeries, {
          color: '#f59e0b',
          lineWidth: 1,
          title: 'EMA12',
        });
        s1.setData(ema12);
        const s2 = chart.addSeries(LineSeries, {
          color: '#3b82f6',
          lineWidth: 1,
          title: 'EMA26',
        });
        s2.setData(ema26);

        seriesList.push(s1, s2);
      } else if (name === 'BOLL') {
        const { upper, middle, lower } = calculateBOLL(data, 20, 2);
        const s1 = chart.addSeries(LineSeries, {
          color: '#8b5cf6',
          lineWidth: 1,
          title: 'UP',
        });
        s1.setData(upper);
        const s2 = chart.addSeries(LineSeries, {
          color: '#f59e0b',
          lineWidth: 1,
          title: 'MB',
        });
        s2.setData(middle);
        const s3 = chart.addSeries(LineSeries, {
          color: '#8b5cf6',
          lineWidth: 1,
          title: 'DN',
        });
        s3.setData(lower);

        seriesList.push(s1, s2, s3);
      } else if (name === 'SAR') {
        const sar = calculateSAR(data);
        const s1 = chart.addSeries(LineSeries, {
          color: '#ffffff',
          lineWidth: 1,
          title: 'SAR',
          lineStyle: 2,
        });
        s1.setData(sar);
        seriesList.push(s1);
      }

      mainSeriesMap.current.set(name, seriesList);
    });
  }, [activeMain, data, charts.main, isReady, chartVersion]);

  // --- Sub Indicators Lifecycle (Create/Remove Series) ---
  useEffect(() => {
    const subCharts = charts.subs.current;
    if (!subCharts) return;

    // 1. Cleanup removed
    subSeriesStore.current.forEach((val, key) => {
      // If indicator not active OR chart instance mismatch (stale), clean it up
      const currentChart = subCharts.get(key);
      if (
        !activeSubs.includes(key as SubIndicatorType) ||
        !currentChart ||
        currentChart !== val.chart
      ) {
        // Try to remove from the stored chart (if it still exists/valid)
        val.series.forEach(s => {
          try {
            val.chart.removeSeries(s.api);
          } catch (_error) {
            // ignore
          }
        });
        subSeriesStore.current.delete(key);
        subSeriesMapRef.current.delete(key);
      }
    });

    // 2. Create new
    activeSubs.forEach(type => {
      const chart = subCharts.get(type);
      if (!chart) return;

      // Check if we have series for this type AND if they belong to the CURRENT chart
      const stored = subSeriesStore.current.get(type);
      if (stored && stored.chart === chart) return; // Matches current chart, skip

      // If we are here, it means either no series, or series belong to OLD chart.
      // (Cleanup for OLD chart happened above or implicitly we just overwrite now)

      const seriesList: StoredIndicatorSeries[] = [];
      let primarySeries: IndicatorSeries | null = null;

      if (type === 'VOL') {
        chart.applyOptions({
          rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0 } },
        });
        const s = chart.addSeries(HistogramSeries, {
          color: '#26a69a',
          priceFormat: { type: 'volume' },
        });
        seriesList.push({ api: s, setData: next => s.setData(next) });
        primarySeries = s;
      } else if (type === 'MACD') {
        chart.applyOptions({
          rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } },
        });
        const sMacd = chart.addSeries(HistogramSeries, {
          title: 'MACD',
          color: FINANCIAL_CHART_COLORS.up,
        });
        const sDiff = chart.addSeries(LineSeries, {
          color: '#ffffff',
          lineWidth: 1,
          title: 'DIF',
        });
        const sDea = chart.addSeries(LineSeries, {
          color: '#f59e0b',
          lineWidth: 1,
          title: 'DEA',
        });

        seriesList.push(
          { api: sMacd, setData: next => sMacd.setData(next) },
          { api: sDiff, setData: next => sDiff.setData(next) },
          { api: sDea, setData: next => sDea.setData(next) }
        );
        primarySeries = sMacd;
      } else if (type === 'KDJ') {
        chart.applyOptions({
          rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } },
        });
        const sK = chart.addSeries(LineSeries, {
          color: '#ffffff',
          lineWidth: 1,
          title: 'K',
        });
        const sD = chart.addSeries(LineSeries, {
          color: '#3b82f6',
          lineWidth: 1,
          title: 'D',
        });
        const sJ = chart.addSeries(LineSeries, {
          color: '#ec4899',
          lineWidth: 1,
          title: 'J',
        });

        seriesList.push(
          { api: sK, setData: next => sK.setData(next) },
          { api: sD, setData: next => sD.setData(next) },
          { api: sJ, setData: next => sJ.setData(next) }
        );
        primarySeries = sK;
      } else if (type === 'RSI') {
        chart.applyOptions({
          rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } },
        });
        const s = chart.addSeries(LineSeries, {
          color: '#9c27b0',
          lineWidth: 1,
          title: 'RSI',
        });
        seriesList.push({ api: s, setData: next => s.setData(next) });
        primarySeries = s;
      }

      if (seriesList.length > 0) {
        subSeriesStore.current.set(type, { chart, series: seriesList });
        if (primarySeries) {
          subSeriesMapRef.current.set(type, primarySeries);
        }
      }
    });
  }, [activeSubs, charts.subs, isReady, chartVersion, subSeriesMapRef]);

  // --- Sub Indicators Data Update ---
  useEffect(() => {
    // Trigger whenever data or activeSubs changes
    if (data.length === 0) return;

    activeSubs.forEach(type => {
      const stored = subSeriesStore.current.get(type);
      if (!stored || stored.series.length === 0) return;
      const seriesList = stored.series;

      if (type === 'VOL') {
        seriesList[0].setData(volumeData);
      } else if (type === 'MACD') {
        const { diff, dea, macd } = calculateMACD(data);
        // Order: MACD (hist), Diff, Dea
        seriesList[0].setData(macd);
        seriesList[1].setData(diff);
        seriesList[2].setData(dea);
      } else if (type === 'KDJ') {
        const { k, d, j } = calculateKDJ(data);
        // Order: K, D, J
        seriesList[0].setData(k);
        seriesList[1].setData(d);
        seriesList[2].setData(j);
      } else if (type === 'RSI') {
        const rsi = calculateRSI(data);
        seriesList[0].setData(rsi);
      }
    });
  }, [activeSubs, data, volumeData, isReady, chartVersion]);
}
