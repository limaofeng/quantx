import {
  createChart,
  ColorType,
  type UTCTimestamp,
  AreaSeries,
} from 'lightweight-charts';
import { useEffect, useMemo, useRef } from 'react';

interface SparklineChartProps {
  data: { time: UTCTimestamp; value: number }[];
  color?: string;
  className?: string;
  visibleRange?: { from: UTCTimestamp; to: UTCTimestamp };
}

export function SparklineChart({
  data = [],
  color = '#3b82f6',
  className = '',
  visibleRange,
}: SparklineChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartData = useMemo(() => (Array.isArray(data) ? data : []), [data]);

  useEffect(() => {
    if (!chartContainerRef.current || chartData.length < 2) return;

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: chartContainerRef.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: 'transparent',
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      handleScroll: false,
      handleScale: false,
      rightPriceScale: {
        visible: false,
      },
      timeScale: {
        visible: false,
      },
      crosshair: {
        vertLine: { visible: false },
        horzLine: { visible: false },
      },
    });

    const areaSeries = chart.addSeries(AreaSeries, {
      lineColor: color,
      topColor: color + '33', // 20% opacity
      bottomColor: color + '00', // 0% opacity
      lineWidth: 2,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    });

    areaSeries.setData(chartData);

    if (visibleRange) {
      chart.timeScale().setVisibleRange(visibleRange);
    } else {
      chart.timeScale().fitContent();
    }

    const handleResize = () => {
      if (chartContainerRef.current && chart) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });

        if (visibleRange) {
          chart.timeScale().setVisibleRange(visibleRange);
        } else {
          chart.timeScale().fitContent();
        }
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [chartData, color, visibleRange]);

  return (
    <div
      ref={chartContainerRef}
      className={`w-full h-full pointer-events-none ${className}`}
    />
  );
}
