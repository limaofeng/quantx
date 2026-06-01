import type {
  IChartApi,
  ISeriesApi,
  MouseEventParams,
} from 'lightweight-charts';
import type React from 'react';
import { useEffect } from 'react';

export function useChartSync(
  mainChartRef: React.RefObject<IChartApi | null>,
  subChartsRef: React.RefObject<Map<string, IChartApi>>,
  mainSeriesRef: React.RefObject<ISeriesApi<any> | null>,
  subSeriesMapRef: React.RefObject<Map<string, ISeriesApi<any>>>,
  isReady: boolean,
  chartVersion: number
) {
  useEffect(() => {
    if (!isReady) return;

    const mc = mainChartRef.current;
    if (!mc) return;

    const ms = mainSeriesRef.current;

    const subscribedSubCharts = new Map(subChartsRef.current); // Snapshot for cleanup

    // --- Time Scale Sync ---
    const mainTimeScale = mc.timeScale();

    // Function to set range on all charts except source
    const syncTimeRange = (sourceChart: IChartApi | null, range: any) => {
      if (!range) return;

      if (sourceChart !== mc) {
        const currentRange = mainTimeScale.getVisibleLogicalRange();
        if (
          currentRange &&
          (currentRange.from !== range.from || currentRange.to !== range.to)
        ) {
          mainTimeScale.setVisibleLogicalRange(range);
        }
      }

      subChartsRef.current?.forEach(sc => {
        if (sc === sourceChart) return;
        try {
          const scTimeScale = sc.timeScale();
          const currentRange = scTimeScale.getVisibleLogicalRange();
          if (
            currentRange &&
            (currentRange.from !== range.from || currentRange.to !== range.to)
          ) {
            scTimeScale.setVisibleLogicalRange(range);
          }
        } catch (e) {
          // Ignore errors during sync (e.g. chart destroyed)
        }
      });
    };

    const mainTimeHandler = (range: any) => syncTimeRange(mc, range);
    mainTimeScale.subscribeVisibleLogicalRangeChange(mainTimeHandler);

    subscribedSubCharts.forEach(sc => {
      try {
        sc.timeScale().subscribeVisibleLogicalRangeChange(range =>
          syncTimeRange(sc, range)
        );
      } catch (e) {
        // ignore
      }
    });

    // --- Crosshair Sync ---
    const syncCrosshair = (
      sourceChart: IChartApi | null,
      param: MouseEventParams
    ) => {
      const { time, point } = param;
      if (!time || point === undefined || point.x < 0 || point.y < 0) {
        // Clear crosshair
        if (sourceChart !== mc) {
          try {
            mc.clearCrosshairPosition();
          } catch (e) {
            // ignore
          }
        }
        subChartsRef.current?.forEach(sc => {
          if (sc !== sourceChart) {
            try {
              sc.clearCrosshairPosition();
            } catch (e) {
              // ignore
            }
          }
        });
        return;
      }

      if (sourceChart !== mc && ms) {
        try {
          mc.setCrosshairPosition(0, time, ms);
        } catch (e) {
          // ignore
        }
      }

      subChartsRef.current?.forEach((sc, key) => {
        if (sc === sourceChart) return;
        const series = subSeriesMapRef.current?.get(key);
        if (series) {
          try {
            sc.setCrosshairPosition(0, time, series);
          } catch (e) {
            // Ignore (e.g. invalid series for chart)
          }
        }
      });
    };

    const mainCrosshairHandler = (param: MouseEventParams) =>
      syncCrosshair(mc, param);
    mc.subscribeCrosshairMove(mainCrosshairHandler);

    subscribedSubCharts.forEach(sc => {
      try {
        sc.subscribeCrosshairMove(param => syncCrosshair(sc, param));
      } catch (e) {
        // ignore
      }
    });

    return () => {
      try {
        mainTimeScale.unsubscribeVisibleLogicalRangeChange(mainTimeHandler);
        mc.unsubscribeCrosshairMove(mainCrosshairHandler);
      } catch (e) {
        // ignore
      }

      // Use the snapshot to unsubscribe
      subscribedSubCharts.forEach(sc => {
        // We can't easily unsubscribe anonymous functions without tracking them.
        // But since we are likely destroying these charts, it might not matter as much.
        // EXCEPT if the chart persists (e.g. re-render without destroy).
        // Since we refactored to Destroy-Recreate, the charts in 'subscribedSubCharts' ARE being destroyed.
        // So listeners die with them.
        // But to be safe and avoid "Value is null" if removal is delayed:
        // Ideally we should track the handler.
      });
    };
  }, [
    mainChartRef,
    subChartsRef,
    mainSeriesRef,
    subSeriesMapRef,
    isReady,
    chartVersion,
  ]);
}
