import type {
  Time,
  ISeriesApi,
  CandlestickData,
  LineData,
  HistogramData,
  IChartApi,
  IPriceLine,
  WhitespaceData,
  AutoscaleInfoProvider,
} from 'lightweight-charts';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';

import type { IntradayTrendBar } from '@/hooks/useIntradayTrendData';
import { FINANCIAL_CHART_COLORS } from '@/shared/utils/financialColors';

import {
  getTradingRange,
  getTradingSessionMinutes,
  isCallAuctionTimestamp,
  toChartTimestamp,
} from '../utils/time-utils';

const MAX_INTRADAY_BAR_GAP_SECONDS = 2 * 60;

interface ChartKLine {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

const toFiniteNumber = (value: unknown): number | null => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const toValidPrice = (...values: unknown[]): number | null => {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null && parsed > 0) return parsed;
  }
  return null;
};

const getShanghaiMinutes = (time: number) => {
  const date = new Date(time * 1000);
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(date);
  const hour = Number(
    parts.find(part => part.type === 'hour')?.value.replace('24', '0') || 0
  );
  const minute = Number(parts.find(part => part.type === 'minute')?.value || 0);
  return hour * 60 + minute;
};

const isLunchBreakGap = (previousTime: number, currentTime: number) => {
  const previousMinutes = getShanghaiMinutes(previousTime);
  const currentMinutes = getShanghaiMinutes(currentTime);
  return previousMinutes <= 11 * 60 + 30 && currentMinutes >= 13 * 60;
};

const isCallAuctionChartTime = (time: number) =>
  isCallAuctionTimestamp(new Date(time * 1000));

const isCallAuctionInternalGap = (previousTime: number, currentTime: number) =>
  isCallAuctionChartTime(previousTime) && isCallAuctionChartTime(currentTime);

export function useChartData(
  isTimeMode: boolean,
  rawIntradayBars: IntradayTrendBar[],
  rawKlines: ChartKLine[],
  series: {
    candlestick: React.RefObject<ISeriesApi<'Candlestick'> | null>;
    timeLine: React.RefObject<ISeriesApi<'Line'> | null>;
    timeAverage: React.RefObject<ISeriesApi<'Line'> | null>;
    timeAnchor: React.RefObject<ISeriesApi<'Line'> | null>;
  },
  charts: {
    main: React.RefObject<IChartApi | null>;
    subs: React.RefObject<Map<string, IChartApi>>;
  },
  isReady: boolean,
  chartVersion: number
) {
  const priceLineRef = useRef<IPriceLine | null>(null);

  const [datasets, setDatasets] = useState<{
    candlestickData: CandlestickData[];
    volumeData: HistogramData[];
  }>({ candlestickData: [], volumeData: [] });

  useEffect(() => {
    if (!isReady) return;

    const { candlestick, timeLine, timeAverage, timeAnchor } = series;
    const { main, subs } = charts;

    if (!main.current) return;

    const candlestickData: CandlestickData[] = [];
    const lineData: Array<LineData | WhitespaceData> = [];
    const averageData: Array<LineData | WhitespaceData> = [];
    const volumeData: HistogramData[] = [];

    const safeIntradayBars = Array.isArray(rawIntradayBars)
      ? rawIntradayBars
      : [];
    const latestBar =
      safeIntradayBars.length > 0
        ? safeIntradayBars[safeIntradayBars.length - 1]
        : null;
    const rawRefDate = latestBar?.time || new Date();
    const refDate =
      rawRefDate instanceof Date
        ? rawRefDate.toISOString()
        : String(rawRefDate);
    const hasCallAuctionBars = safeIntradayBars.some(item =>
      isCallAuctionTimestamp(item?.sourceTime ?? item?.time)
    );
    const tradingSessionOptions = hasCallAuctionBars
      ? { includeCallAuction: true }
      : {};
    const dayRange = getTradingRange(refDate, tradingSessionOptions);

    if (priceLineRef.current && timeLine.current) {
      try {
        timeLine.current.removePriceLine(priceLineRef.current);
      } catch (_e) {
        // Series may already have been destroyed during chart re-init.
      }
      priceLineRef.current = null;
    }

    if (isTimeMode && timeLine.current) {
      const sessionMinutes = getTradingSessionMinutes(
        refDate,
        tradingSessionOptions
      );
      const intradayBars = safeIntradayBars
        .flatMap(item => {
          const rawTimeValue = toChartTimestamp(item.time);
          const timeValue =
            typeof rawTimeValue === 'number' ? rawTimeValue : null;
          const close = toValidPrice(
            item.close,
            item.lastPrice,
            item.currentPrice,
            item.open,
            item.high,
            item.low
          );
          if (
            close === null ||
            timeValue === null ||
            !Number.isFinite(timeValue) ||
            timeValue < (dayRange.from as number) ||
            timeValue > (dayRange.to as number)
          ) {
            return [];
          }
          const open = toValidPrice(item.open, close);
          return [
            {
              time: timeValue,
              open: open ?? close,
              high: toValidPrice(item.high, close) ?? close,
              low: toValidPrice(item.low, close) ?? close,
              close,
              volume: Math.max(0, toFiniteNumber(item.volume) || 0),
              amount: Math.max(0, toFiniteNumber(item.amount) || 0),
              preClose: toValidPrice(item.preClose),
            },
          ];
        })
        .sort((a, b) => a.time - b.time);

      let runningPriceSum = 0;
      let runningVolume = 0;
      let runningAmount = 0;
      const preCloseValue =
        intradayBars.find(item => item.preClose !== null)?.preClose || null;
      const anchorValue =
        preCloseValue ||
        (intradayBars.length > 0 ? intradayBars[0].close : null);

      intradayBars.forEach((item, idx) => {
        const previousBar = idx > 0 ? intradayBars[idx - 1] : null;
        const previousTime = previousBar?.time as number | undefined;
        const currentTime = item.time as number;
        const hasMissingMinutes =
          previousTime !== undefined &&
          currentTime - previousTime > MAX_INTRADAY_BAR_GAP_SECONDS &&
          !isCallAuctionInternalGap(previousTime, currentTime) &&
          !isLunchBreakGap(previousTime, currentTime);

        if (hasMissingMinutes) {
          const breakTime = Math.min(
            previousTime + 60,
            currentTime - 60
          ) as Time;
          lineData.push({ time: breakTime });
          averageData.push({ time: breakTime });
        }

        runningPriceSum += item.close;
        runningVolume += item.volume;
        runningAmount += item.amount;

        const rawWeightedAverage =
          runningAmount > 0 && runningVolume > 0
            ? runningAmount / runningVolume
            : null;
        const weightedAverage =
          rawWeightedAverage !== null && rawWeightedAverage > item.close * 20
            ? rawWeightedAverage / 100
            : rawWeightedAverage;
        const averagePrice =
          weightedAverage !== null
            ? weightedAverage
            : runningPriceSum / (idx + 1);

        lineData.push({ time: item.time as Time, value: item.close });
        averageData.push({
          time: item.time as Time,
          value: averagePrice,
        });

        const previousPrice = previousBar?.close || preCloseValue || item.open;
        volumeData.push({
          time: item.time as Time,
          value: item.volume,
          color:
            item.close >= previousPrice
              ? `${FINANCIAL_CHART_COLORS.up}94`
              : `${FINANCIAL_CHART_COLORS.down}94`,
        });
      });

      const lineMap = new Map<number, LineData | WhitespaceData>();
      const averageMap = new Map<number, LineData | WhitespaceData>();
      const volMap = new Map<number, HistogramData>();

      sessionMinutes.forEach(time => {
        volMap.set(time as number, { time, value: 0 });
      });

      if (lineData.length === 1) {
        const firstPoint = lineData[0] as LineData;
        const firstAverage = averageData[0] as LineData;
        const previousTime = Math.max(
          dayRange.from as number,
          (firstPoint.time as number) - 60
        ) as Time;
        lineMap.set(previousTime as number, {
          time: previousTime,
          value: firstPoint.value,
        });
        averageMap.set(previousTime as number, {
          time: previousTime,
          value: firstAverage.value,
        });
      }

      lineData.forEach(d => lineMap.set(d.time as number, d));
      averageData.forEach(d => averageMap.set(d.time as number, d));
      volumeData.forEach(d => volMap.set(d.time as number, d));

      const sortedLine = Array.from(lineMap.values()).sort(
        (a, b) => (a.time as number) - (b.time as number)
      );
      const sortedAverage = Array.from(averageMap.values()).sort(
        (a, b) => (a.time as number) - (b.time as number)
      );
      const sortedVol = Array.from(volMap.values()).sort(
        (a, b) => (a.time as number) - (b.time as number)
      );

      if (anchorValue !== null) {
        timeAnchor.current?.setData(
          sessionMinutes.map(time => ({ time, value: anchorValue }))
        );
      } else {
        timeAnchor.current?.setData([]);
      }
      timeLine.current.setData(sortedLine);
      timeAverage.current?.setData(sortedAverage);

      if (preCloseValue !== null && preCloseValue > 0) {
        const centeredAutoscale: AutoscaleInfoProvider = original => {
          const res = original();
          if (
            res !== null &&
            res.priceRange !== null &&
            Number.isFinite(res.priceRange.maxValue) &&
            Number.isFinite(res.priceRange.minValue)
          ) {
            const priceRange = res.priceRange;
            const maxDiff = Math.max(
              Math.abs(priceRange.maxValue - preCloseValue),
              Math.abs(priceRange.minValue - preCloseValue)
            );
            const padding =
              maxDiff > 0 ? maxDiff * 0.05 : preCloseValue * 0.001;
            const finalDiff = maxDiff + padding;
            return {
              priceRange: {
                minValue: preCloseValue - finalDiff,
                maxValue: preCloseValue + finalDiff,
              },
            };
          }
          return res;
        };
        timeLine.current.applyOptions({
          autoscaleInfoProvider: centeredAutoscale,
        });

        const priceLine = timeLine.current.createPriceLine({
          price: preCloseValue,
          color: 'rgba(120, 120, 120, 0.4)',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: '0.00%',
        });

        priceLineRef.current = priceLine;
      } else {
        timeLine.current.applyOptions({
          autoscaleInfoProvider: undefined,
        });
      }

      const applyFullDayRange = () => {
        try {
          main.current?.timeScale().setVisibleRange(dayRange);
          subs.current?.forEach(subChart => {
            subChart.timeScale().setVisibleRange(dayRange);
          });
        } catch (_e) {
          // Chart might not be ready or range is invalid relative to data.
        }
      };

      [0, 80, 240].forEach(delay => setTimeout(applyFullDayRange, delay));

      const timeModeCandles: CandlestickData[] = intradayBars.map(item => ({
        time: item.time,
        open: item.open || item.close,
        high: item.high || item.close,
        low: item.low || item.close,
        close: item.close,
      }));

      setDatasets({
        candlestickData: timeModeCandles,
        volumeData: sortedVol,
      });
    } else if (!isTimeMode && rawKlines && candlestick.current) {
      rawKlines.forEach(item => {
        const time = toChartTimestamp(item.time);
        if (time === null) return;
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
              ? `${FINANCIAL_CHART_COLORS.up}80`
              : `${FINANCIAL_CHART_COLORS.down}80`,
        });
      });
      candlestickData.sort((a, b) => (a.time as number) - (b.time as number));
      volumeData.sort((a, b) => (a.time as number) - (b.time as number));

      candlestick.current.setData(candlestickData);
      setDatasets({ candlestickData, volumeData });

      setTimeout(() => {
        main.current?.timeScale().fitContent();
        subs.current?.forEach(subChart => {
          subChart.timeScale().fitContent();
        });
      }, 50);
    }
  }, [
    isTimeMode,
    rawIntradayBars,
    rawKlines,
    series,
    charts,
    isReady,
    chartVersion,
  ]);

  return datasets;
}
