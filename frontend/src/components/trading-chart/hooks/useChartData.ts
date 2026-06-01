import type {
  Time,
  ISeriesApi,
  CandlestickData,
  AreaData,
  HistogramData,
  IChartApi,
  IPriceLine,
} from 'lightweight-charts';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';

import { getTradingRange } from '../utils/time-utils';

export function useChartData(
  isTimeMode: boolean,
  rawTicks: any[],
  rawKlines: any[],
  series: {
    candlestick: React.RefObject<ISeriesApi<'Candlestick'> | null>;
    area: React.RefObject<ISeriesApi<'Area'> | null>;
  },
  charts: {
    main: React.RefObject<IChartApi | null>;
    subs: React.RefObject<Map<string, IChartApi>>;
  },
  isReady: boolean,
  chartVersion: number
) {
  // Keep track of the price line to remove it before creating a new one
  const priceLineRef = useRef<IPriceLine | null>(null);

  const [datasets, setDatasets] = useState<{
    candlestickData: CandlestickData[];
    volumeData: HistogramData[];
  }>({ candlestickData: [], volumeData: [] });

  useEffect(() => {
    if (!isReady) return;

    const { candlestick, area } = series;
    const { main, subs } = charts;

    if (!main.current) return;

    const candlestickData: CandlestickData[] = [];
    const areaData: AreaData[] = [];
    const volumeData: HistogramData[] = [];

    const safeTicks = Array.isArray(rawTicks) ? rawTicks : [];
    const latestTick =
      safeTicks.length > 0 ? safeTicks[safeTicks.length - 1] : null;
    const refDate = latestTick?.time || new Date().toISOString();
    const dayRange = getTradingRange(refDate);

    // Clean up existing price line if any
    if (priceLineRef.current && area.current) {
      try {
        area.current.removePriceLine(priceLineRef.current);
      } catch (e) {
        // Ignore error if line cannot be removed (e.g. belongs to destroyed series)
      }
      priceLineRef.current = null;
    }

    if (isTimeMode && safeTicks.length > 0 && area.current) {
      const currentDayTicks = safeTicks
        .map((item: any) => ({
          time: (new Date(item.time).getTime() / 1000) as Time,
          price: item.lastPrice ?? item.high,
          volume: item.volume || 0,
          preClose: item.preClose,
        }))
        .filter(
          (t: any) =>
            (t.time as number) >= (dayRange.from as number) &&
            (t.time as number) <= (dayRange.to as number)
        )
        .sort((a: any, b: any) => (a.time as number) - (b.time as number));

      let lastTotalVol = 0;
      const preCloseValue =
        currentDayTicks.length > 0 ? currentDayTicks[0].preClose : null;

      currentDayTicks.forEach((item: any, idx: number) => {
        let intervalVol = 0;
        if (idx > 0) intervalVol = Math.max(0, item.volume - lastTotalVol);
        lastTotalVol = item.volume;

        areaData.push({ time: item.time, value: item.price });
        volumeData.push({
          time: item.time,
          value: intervalVol,
          color:
            item.price >=
            (idx > 0
              ? currentDayTicks[idx - 1].price
              : preCloseValue || item.price)
              ? 'rgba(239, 68, 68, 0.5)'
              : 'rgba(34, 197, 94, 0.5)',
        });
      });

      // Supplement boundaries
      const startPrice =
        preCloseValue || (areaData.length > 0 ? areaData[0].value : 0);
      const endPrice =
        areaData.length > 0 ? areaData[areaData.length - 1].value : startPrice;

      const areaMap = new Map<number, any>();
      const volMap = new Map<number, any>();

      areaMap.set(dayRange.from as number, {
        time: dayRange.from,
        value: startPrice,
      });
      volMap.set(dayRange.from as number, { time: dayRange.from, value: 0 });
      areaMap.set(dayRange.to as number, {
        time: dayRange.to,
        value: endPrice,
      });
      volMap.set(dayRange.to as number, { time: dayRange.to, value: 0 });

      areaData.forEach(d => areaMap.set(d.time as number, d));
      volumeData.forEach(d => volMap.set(d.time as number, d));

      const sortedArea = Array.from(areaMap.values()).sort(
        (a, b) => (a.time as number) - (b.time as number)
      );
      const sortedVol = Array.from(volMap.values()).sort(
        (a, b) => (a.time as number) - (b.time as number)
      );

      area.current.setData(sortedArea);
      // volume data is not set here anymore

      // Symmetry axis
      if (preCloseValue !== null && preCloseValue > 0) {
        area.current.applyOptions({
          autoscaleInfoProvider: (original: any) => {
            const res = original();
            if (res !== null) {
              const priceRange = res.priceRange;
              const maxDiff = Math.max(
                Math.abs(priceRange.maxValue - preCloseValue),
                Math.abs(priceRange.minValue - preCloseValue)
              );
              const padding =
                maxDiff > 0 ? maxDiff * 0.05 : preCloseValue * 0.001;
              const finalDiff = Math.max(maxDiff, padding);
              return {
                priceRange: {
                  minValue: preCloseValue - finalDiff,
                  maxValue: preCloseValue + finalDiff,
                },
              };
            }
            return res;
          },
        });

        const priceLine = area.current.createPriceLine({
          price: preCloseValue,
          color: 'rgba(120, 120, 120, 0.4)',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: '0.00%',
        });

        priceLineRef.current = priceLine;
      }

      // Use setTimeout to ensure chart has processed the new data
      setTimeout(() => {
        try {
          if (main.current) {
            main.current.timeScale().setVisibleRange(dayRange);
          }
          if (subs.current) {
            subs.current.forEach(subChart => {
              subChart.timeScale().setVisibleRange(dayRange);
            });
          }
        } catch (e) {
          // Chart might not be ready or range is invalid relative to data
        }
      }, 0);

      // Generate pseudo-candlestick data for indicators in Time Mode
      const timeModeCandles: CandlestickData[] = sortedArea.map((d: any) => ({
        time: d.time,
        open: d.value,
        high: d.value,
        low: d.value,
        close: d.value,
      }));

      setDatasets({
        candlestickData: timeModeCandles,
        volumeData: sortedVol as HistogramData[],
      });
    } else if (!isTimeMode && rawKlines && candlestick.current) {
      rawKlines.forEach((item: any) => {
        const time = (new Date(item.time).getTime() / 1000) as Time;
        candlestickData.push({
          time,
          open: item.open,
          high: item.high,
          low: item.low,
          close: item.close,
        });
        volumeData.push({
          time,
          value: item.volume,
          color:
            item.close >= item.open
              ? 'rgba(239, 68, 68, 0.5)'
              : 'rgba(34, 197, 94, 0.5)',
        });
      });
      candlestickData.sort((a, b) => (a.time as number) - (b.time as number));
      volumeData.sort((a, b) => (a.time as number) - (b.time as number));

      candlestick.current.setData(candlestickData);
      setDatasets({ candlestickData, volumeData });

      // Auto fit content for K-line mode to ensure data is visible
      // Use setTimeout to ensure options and data are fully settled
      setTimeout(() => {
        if (main.current) {
          main.current.timeScale().fitContent();
        }
        if (subs.current) {
          subs.current.forEach(subChart => {
            subChart.timeScale().fitContent();
          });
        }
      }, 50);
    }
  }, [isTimeMode, rawTicks, rawKlines, series, charts, isReady, chartVersion]);

  return datasets;
}
