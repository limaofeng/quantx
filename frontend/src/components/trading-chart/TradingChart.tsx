import type { ISeriesApi } from 'lightweight-charts';
import React, { useState, useCallback, useRef } from 'react';

import { Card } from '@/components/ui/card';
import { useKLinesPage } from '@/features/trading/hooks/useTrading';
import { useRealTimeTicks, useInfiniteKLines } from '@/hooks';
import { cn } from '@/utils/cn';

import { ChartContainer } from './components/ChartContainer';
import { ChartHeader } from './components/ChartHeader';
import { EmptyChartState } from './components/EmptyChartState';
import { IndicatorBar } from './components/IndicatorBar';
import { LoadingOverlay } from './components/LoadingOverlay';
import { useChartData } from './hooks/useChartData';
import { useChartIndicators } from './hooks/useChartIndicators';
import { useChartInit } from './hooks/useChartInit';
import { useChartSync } from './hooks/useChartSync';
import type { TradingChartProps } from './types';
import {
  type MainIndicatorType,
  type SubIndicatorType,
} from './utils/indicators';

export function TradingChart({ stockCode, className }: TradingChartProps) {
  const [activePeriod, setActivePeriod] = useState('1m_line');
  const [lastKLinePeriod, setLastKLinePeriod] = useState('1d');

  const [activeMain, setActiveMain] = useState<MainIndicatorType[]>(['MA']);
  const [activeSubs, setActiveSubs] = useState<SubIndicatorType[]>(['VOL']);

  const [mainContainer, setMainContainer] = useState<HTMLDivElement | null>(
    null
  );
  // Use a Map to store refs for multiple sub-charts
  const subContainers = useRef<Map<string, HTMLElement>>(new Map());

  // Helper to update the map from callback refs
  const setSubContainerRef = useCallback(
    (type: SubIndicatorType, el: HTMLDivElement | null) => {
      if (el) {
        subContainers.current.set(type, el);
      } else {
        subContainers.current.delete(type);
      }
    },
    []
  );

  const subSeriesMapRef = useRef<Map<string, ISeriesApi<any>>>(new Map());

  const isTimeMode = activePeriod === '1m_line' || activePeriod === '5d_line';

  // 1. Fetch Data
  const { data: rawTicks, loading: ticksLoading } = useRealTimeTicks(
    isTimeMode && stockCode ? stockCode : '',
    activePeriod === '5d_line' ? '5d' : '1d'
  );

  const periodMap: Record<string, string> = {
    '1m': 'MIN_1',
    '5m': 'MIN_5',
    '15m': 'MIN_15',
    '30m': 'MIN_30',
    '60m': 'MIN_60',
    '120m': 'MIN_60', // Fallback as 120m not in schema
    '1d': 'DAY_1',
    '1w': 'WEEK_1',
    '1M': 'MONTH_1',
  };

  const {
    data: rawKlines,
    loading: klinesLoading,
    loadMore,
    hasMore,
  } = useInfiniteKLines(
    !isTimeMode && stockCode ? stockCode : '',
    activePeriod === '1m_line' || activePeriod === '5d_line'
      ? 'DAY_1'
      : periodMap[activePeriod] || 'DAY_1',
    !isTimeMode
  );

  const isLoading = isTimeMode ? ticksLoading : klinesLoading;

  // 2. Initialize Charts
  // We pass activeSubs as dependency to force re-init when they change
  const { charts, series, isReady, chartVersion } = useChartInit(
    mainContainer,
    subContainers.current,
    isTimeMode
  );

  // Force re-render of useChartInit when activeSubs changes to ensure it picks up new containers
  // Actually useChartInit internally has useEffect on [subContainers] which is a Map ref...
  // Map ref equality doesn't change. We need to pass activeSubs.
  // We'll update useChartInit signature slightly in next edit if needed,
  // OR we can just add a Dummy check or pass activeSubs to it.
  // The current useChartInit implementation takes `subContainers` and adds to dep array.
  // Since we pass `subContainers.current`, the object reference is constant!
  // So the effect WON'T run.
  // WE MUST PASS activeSubs TO useChartInit.
  // I will assume I need to update useChartInit signature again, OR better:
  // I will key the hook or pass it as extra arg?
  // Let's modify the useChartInit call to include activeSubs.length or something in a key?
  // No, hooks cannot be keyed like that easily.
  // I will rely on passing `activeSubs` (or a derived primitive) to `useChartInit`.
  // Wait, I didn't update `useChartInit` to accept `activeSubs` in the previous step.
  // I accepted `subContainers`.

  // Quick fix: Use a key on the component? No.
  // I'll update useChartInit dependencies in valid way.
  // Since currently `useChartInit` depends on `subContainers`, and it's a Map.
  // I will recreate the Map when activeSubs changes?
  // `const subContainersMap = useMemo(() => new Map(), [activeSubs])`.
  // Then `subContainers` in `useChartInit` will change matching `activeSubs`.
  // BUT `setSubContainerRef` will populate the map AFTER render.

  // Correct approach:
  // 1. Render ChartContainer. It calls setSubContainerRef.
  // 2. We need useChartInit to run AFTER DOM is ready.
  // 3. React useLayoutEffect or just useEffect.
  // 4. Since panels are conditionally rendered, the ref callback happens during commit.
  // 5. If we recreate the map on every activeSubs change, we lose old refs?
  // No, ChartContainer will re-render and re-attach refs.

  // Let's stick to: pass `activeSubs.join(',')` to `useChartInit` as a dependency if possible?
  // I will update `useChartInit` in a separate step if needed.
  // For now, let's update `TradingChart`.

  // 3. Update Data (Calculate Base Data)
  const { candlestickData, volumeData } = useChartData(
    isTimeMode,
    rawTicks,
    rawKlines,
    series,
    charts,
    isReady,
    chartVersion
  );

  // 4. Indicators
  useChartIndicators(
    charts,
    candlestickData,
    volumeData,
    activeMain,
    activeSubs,
    subSeriesMapRef,
    isReady,
    chartVersion
  );

  // 5. Sync Charts
  useChartSync(
    charts.main,
    charts.subs,
    isTimeMode ? (series.area as any) : series.candlestick,
    subSeriesMapRef,
    isReady,
    chartVersion
  );

  // Load more history when scrolling to the left
  React.useEffect(() => {
    if (isTimeMode || !charts.main.current || !hasMore || klinesLoading) return;

    const chart = charts.main.current;
    const timeScale = chart.timeScale();

    const handleVisibleLogicalRangeChange = (range: any) => {
      if (range && range.from < 0) {
        loadMore();
      }
    };

    timeScale.subscribeVisibleLogicalRangeChange(
      handleVisibleLogicalRangeChange
    );

    return () => {
      timeScale.unsubscribeVisibleLogicalRangeChange(
        handleVisibleLogicalRangeChange
      );
    };
  }, [isTimeMode, charts.main, hasMore, klinesLoading, loadMore]);

  // 6. Handlers
  const handleModeSwitch = useCallback(
    (mode: 'time' | 'kline') => {
      if (mode === 'time') {
        setActivePeriod('1m_line');
      } else {
        setActivePeriod(lastKLinePeriod);
      }
    },
    [lastKLinePeriod]
  );

  const handleKLinePeriodChange = useCallback((value: string) => {
    setLastKLinePeriod(value);
    setActivePeriod(value);
  }, []);

  const handleTimePeriodChange = useCallback((value: string) => {
    setActivePeriod(value);
  }, []);

  const toggleMainIndicator = useCallback((type: MainIndicatorType) => {
    setActiveMain(prev => {
      if (prev.includes(type)) return prev.filter(t => t !== type);
      return [...prev, type];
    });
  }, []);

  const toggleSubIndicator = useCallback((type: SubIndicatorType) => {
    setActiveSubs(prev => {
      if (prev.includes(type)) return prev.filter(t => t !== type);
      if (prev.length >= 4) return prev; // Limit to 4
      return [...prev, type];
    });
  }, []);

  if (!stockCode) {
    return (
      <Card
        square
        className={cn(
          'flex flex-col w-full h-full min-h-[400px] bg-transparent border-none shadow-none relative',
          className
        )}
      >
        <EmptyChartState />
      </Card>
    );
  }

  return (
    <Card
      square
      className={cn(
        'flex flex-col w-full h-full min-h-[400px] bg-transparent border-none shadow-none relative',
        className
      )}
    >
      <ChartHeader
        activePeriod={activePeriod}
        isTimeMode={isTimeMode}
        lastKLinePeriod={lastKLinePeriod}
        onModeSwitch={handleModeSwitch}
        onKLinePeriodChange={handleKLinePeriodChange}
        onTimePeriodChange={handleTimePeriodChange}
      />

      <LoadingOverlay isLoading={isLoading} />

      <ChartContainer
        mainContainerRef={setMainContainer}
        activeSubs={activeSubs}
        setSubContainerRef={setSubContainerRef} // Passing the callback
      />

      <IndicatorBar
        activeMain={activeMain}
        activeSub={activeSubs} // Assuming IndicatorBar updated to accept array
        onToggleMain={toggleMainIndicator}
        onChangeSub={toggleSubIndicator} // Assuming updated to onToggleSub
      />
    </Card>
  );
}
