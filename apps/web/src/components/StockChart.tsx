import {
  createChart,
  ColorType,
  type IChartApi,
  type UTCTimestamp,
  AreaSeries,
  LineSeries,
  CandlestickSeries,
} from 'lightweight-charts';
import { ChevronDown } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { FINANCIAL_CHART_COLORS } from '@/shared/utils/financialColors';
import {
  generateIntradayData,
  generateHistoricalData,
  calculateMA,
} from '@/utils/transform/chart';

interface StockChartProps {
  stockCode: string;
  stockName: string;
  currentPrice: number;
  className?: string;
}

type TimePeriod =
  | 'intraday'
  | '5D'
  | 'daily'
  | 'weekly'
  | 'monthly'
  | '1min'
  | '5min'
  | '10min'
  | '30min'
  | '1hour';

export default function StockChart({
  stockCode,
  stockName,
  currentPrice,
  className = '',
}: StockChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [timePeriod, setTimePeriod] = useState<TimePeriod>('intraday');
  const [isLoading, setIsLoading] = useState(false);
  const [showExtended, setShowExtended] = useState(false);
  const [chartKey, setChartKey] = useState(0);

  const basicPeriods = [
    { key: 'intraday' as TimePeriod, label: '分时' },
    { key: 'daily' as TimePeriod, label: '日K' },
    { key: 'weekly' as TimePeriod, label: '周K' },
    { key: 'monthly' as TimePeriod, label: '月K' },
    { key: '5D' as TimePeriod, label: '五日' },
  ];

  const extendedPeriods = [
    { key: '1min' as TimePeriod, label: '1分钟' },
    { key: '5min' as TimePeriod, label: '5分钟' },
    { key: '10min' as TimePeriod, label: '10分钟' },
    { key: '30min' as TimePeriod, label: '30分钟' },
    { key: '1hour' as TimePeriod, label: '1小时' },
  ];

  const useLineChart = timePeriod === 'intraday' || timePeriod === '5D';

  // 图表初始化和数据更新
  useEffect(() => {
    if (!chartContainerRef.current) return;

    // 清理旧图表
    if (chartRef.current) {
      try {
        chartRef.current.remove();
      } catch {
        // 忽略错误
      }
    }

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: 400,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#64748b',
      },
      grid: {
        vertLines: { color: '#e2e8f0' },
        horzLines: { color: '#e2e8f0' },
      },
      crosshair: {
        mode: 1,
      },
      rightPriceScale: {
        borderColor: '#e2e8f0',
      },
      timeScale: {
        borderColor: '#e2e8f0',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    chartRef.current = chart;

    // 生成并设置数据
    try {
      if (useLineChart) {
        // 分时图和五日线图
        const lineData =
          timePeriod === 'intraday'
            ? generateIntradayData(currentPrice)
            : generateHistoricalData(currentPrice, timePeriod).map(d => ({
                time: d.time as UTCTimestamp,
                value: d.close,
              }));

        const lineSeries = chart.addSeries(AreaSeries, {
          lineColor: '#3b82f6',
          topColor: '#3b82f6',
          bottomColor: 'rgba(59, 130, 246, 0.1)',
          lineWidth: 2,
          crosshairMarkerVisible: true,
          crosshairMarkerRadius: 4,
          priceLineVisible: false,
        });

        lineSeries.setData(
          lineData.map(d => ({
            time: d.time as UTCTimestamp,
            value: d.value,
          }))
        );

        // 如果是分时数据，添加均价线
        if (timePeriod === 'intraday' && lineData.length > 10) {
          const avgPrice =
            lineData.reduce((sum, d) => sum + d.value, 0) / lineData.length;
          const avgLineSeries = chart.addSeries(LineSeries, {
            color: '#fbbf24',
            lineWidth: 1,
            lineStyle: 2,
            crosshairMarkerVisible: false,
            priceLineVisible: false,
          });

          avgLineSeries.setData(
            lineData.map(d => ({
              time: d.time as UTCTimestamp,
              value: Number(avgPrice.toFixed(2)),
            }))
          );
        }
      } else {
        // K线图（阴阳线）
        const candleData = generateHistoricalData(currentPrice, timePeriod);

        const candlestickSeries = chart.addSeries(CandlestickSeries, {
          upColor: FINANCIAL_CHART_COLORS.up,
          downColor: FINANCIAL_CHART_COLORS.down,
          borderVisible: false,
          wickUpColor: FINANCIAL_CHART_COLORS.up,
          wickDownColor: FINANCIAL_CHART_COLORS.down,
        });

        candlestickSeries.setData(
          candleData.map(d => ({
            time: d.time as UTCTimestamp,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
          }))
        );

        // 添加移动平均线
        if (candleData.length > 5) {
          const ma5 = calculateMA(
            candleData,
            Math.min(5, candleData.length - 1)
          );
          const ma20 = calculateMA(
            candleData,
            Math.min(20, candleData.length - 1)
          );

          if (ma5.length > 0) {
            const ma5Series = chart.addSeries(LineSeries, {
              color: '#8b5cf6',
              lineWidth: 1,
              crosshairMarkerVisible: false,
              priceLineVisible: false,
            });
            ma5Series.setData(
              ma5.map(d => ({
                time: d.time as UTCTimestamp,
                value: d.value,
              }))
            );
          }

          if (ma20.length > 0) {
            const ma20Series = chart.addSeries(LineSeries, {
              color: '#f59e0b',
              lineWidth: 1,
              crosshairMarkerVisible: false,
              priceLineVisible: false,
            });
            ma20Series.setData(
              ma20.map(d => ({
                time: d.time as UTCTimestamp,
                value: d.value,
              }))
            );
          }
        }
      }
    } catch {
      // 图表数据加载失败,静默处理
    }

    setTimeout(() => setIsLoading(false), 300);

    // 响应式处理
    const handleResize = () => {
      if (chartContainerRef.current && chart) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [timePeriod, currentPrice, chartKey, useLineChart]);

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      if (chartRef.current) {
        try {
          chartRef.current.remove();
        } catch {
          // 忽略错误
        }
      }
    };
  }, []);

  return (
    <Card className={`p-6 ${className}`}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3
            className="text-ui-heading font-semibold"
            data-testid="chart-title"
          >
            {stockName} ({stockCode}) 走势图
          </h3>
          <div className="flex items-center gap-2 mt-2">
            {!useLineChart && (
              <>
                <Badge variant="outline" className="text-purple-600">
                  MA5
                </Badge>
                <Badge variant="outline" className="text-amber-600">
                  MA20
                </Badge>
              </>
            )}
            {timePeriod === 'intraday' && (
              <Badge variant="outline" className="text-yellow-600">
                均价线
              </Badge>
            )}
          </div>
        </div>
      </div>

      {/* 时间周期选择 */}
      <div className="mb-4">
        <div className="flex flex-wrap gap-2 items-center">
          {/* 基础时间周期 */}
          {basicPeriods.map(period => (
            <Button
              key={period.key}
              variant={timePeriod === period.key ? 'default' : 'outline'}
              size="sm"
              onClick={() => {
                setTimePeriod(period.key);
                setChartKey(prev => prev + 1);
              }}
              data-testid={`period-${period.key}`}
            >
              {period.label}
            </Button>
          ))}

          {/* 扩展时间周期 */}
          <div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowExtended(!showExtended)}
              data-testid="toggle-extended"
              className="flex items-center gap-1"
            >
              更多
              <ChevronDown
                className={`h-3 w-3 transition-transform ${showExtended ? 'rotate-180' : ''}`}
              />
            </Button>
          </div>
        </div>

        {/* 桌面扩展选项 */}
        {showExtended && (
          <div className="mt-3 flex flex-wrap gap-2 border-t pt-3">
            {extendedPeriods.map(period => (
              <Button
                key={period.key}
                variant={timePeriod === period.key ? 'default' : 'outline'}
                size="sm"
                onClick={() => {
                  setTimePeriod(period.key);
                  setChartKey(prev => prev + 1);
                }}
                data-testid={`extended-period-${period.key}`}
              >
                {period.label}
              </Button>
            ))}
          </div>
        )}
      </div>

      {/* 图表容器 */}
      <div className="relative">
        {isLoading && (
          <div className="absolute inset-0 bg-background/50 flex items-center justify-center z-10 rounded">
            <div className="text-ui-body text-muted-foreground">
              加载图表数据中...
            </div>
          </div>
        )}
        <div
          key={chartKey}
          ref={chartContainerRef}
          className="w-full h-[400px] rounded border"
          data-testid="chart-container"
        />
      </div>

      {/* 图表说明 */}
      <div className="mt-4 text-ui-label text-muted-foreground">
        <div className="flex justify-between">
          <span>
            {useLineChart ? '分时走势' : 'K线走势'} ·
            {
              [...basicPeriods, ...extendedPeriods].find(
                p => p.key === timePeriod
              )?.label
            }
          </span>
          <span>数据仅供演示使用</span>
        </div>
      </div>
    </Card>
  );
}
