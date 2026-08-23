import { Activity } from 'lucide-react';
import { useMemo } from 'react';

import { Card } from '@/components/ui/card';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';
import {
  StrategyInstrumentScope,
  StrategyRunMode,
  StrategyRunStatus,
} from '@/generated/gql/graphql';
import {
  FINANCIAL_CHART_COLORS,
  financialToneClass,
} from '@/shared/utils/financialColors';

import type {
  ExecutionTraceView,
  StrategyDecision,
  StrategyJsonValue,
} from '../domain/types';
import { useStrategyTicks } from '../hooks/useStrategyTicks';

import MonitorSidePanel from './MonitorSidePanel';
import StrategyChart, { type ChartOverlay } from './StrategyChart';

interface Props {
  activeRun: {
    id: string;
    instrumentCode?: string;
    instruments?: string[];
    parameters?: Record<string, StrategyJsonValue> | string;
    mode: StrategyRunMode | string;
    status: StrategyRunStatus | string;
  } | null;
  instrumentScope?: StrategyInstrumentScope | string;
  className?: string;
  strategyId?: string;
  backtestRange?: {
    startTime?: string | null;
    endTime?: string | null;
  } | null;
  backtestId?: string | null;
  backtestVersion?: number | null;
  decisions?: StrategyDecision[];
  executions?: ExecutionTraceView[];
}

export default function StrategyMonitor({
  activeRun,
  instrumentScope,
  className,
  strategyId,
  backtestRange,
  backtestId,
  backtestVersion,
  decisions = [],
  executions = [],
}: Props) {
  // Determine stock code based on scope and instruments
  const stockCode = useMemo(() => {
    if (!activeRun) return undefined;
    if (!activeRun.instruments?.length) return activeRun.instrumentCode;
    // If SINGLE, there should be only one instrument
    if (
      instrumentScope === StrategyInstrumentScope.Single ||
      instrumentScope === 'SINGLE'
    ) {
      return activeRun.instruments[0];
    }
    // Default fallback (e.g. first one) or logic for MULTI (maybe dropdown later)
    return activeRun.instruments[0];
  }, [activeRun, instrumentScope]);

  const overlays = useMemo(() => {
    if (!activeRun?.parameters) return [];
    try {
      // Handle both string and object (urql might normalize it)
      const rawParams: unknown =
        typeof activeRun.parameters === 'string'
          ? JSON.parse(activeRun.parameters)
          : activeRun.parameters;
      if (
        rawParams === null ||
        typeof rawParams !== 'object' ||
        Array.isArray(rawParams)
      ) {
        return [];
      }
      const params = rawParams as Record<string, StrategyJsonValue>;
      const setup =
        params.setup !== null &&
        typeof params.setup === 'object' &&
        !Array.isArray(params.setup)
          ? params.setup
          : {};

      const list: ChartOverlay[] = [];

      // Extract Base Price
      // Check for basePrice or setup.basePrice
      const basePrice = params.basePrice || setup.basePrice;
      if (basePrice) {
        list.push({
          type: 'priceLine',
          price: Number(basePrice),
          color: '#3b82f6',
          title: '基准价',
          lineStyle: 'solid',
          lineWidth: 2,
        });
      }

      // Extract Grid Levels
      // Check for levels or setup.levels
      const levels = params.levels || setup.levels || params.grid_levels;
      if (Array.isArray(levels)) {
        levels.forEach(level => {
          if (
            level !== null &&
            typeof level === 'object' &&
            !Array.isArray(level) &&
            level.price
          ) {
            list.push({
              type: 'priceLine',
              price: Number(level.price),
              color:
                level.side === 'BUY'
                  ? `${FINANCIAL_CHART_COLORS.up}66`
                  : `${FINANCIAL_CHART_COLORS.down}66`,
              title: level.side === 'BUY' ? '买' : '卖',
              lineStyle: 'dashed',
              lineWidth: 1,
            });
          }
        });
      }

      return list;
    } catch (_error) {
      return [];
    }
  }, [activeRun]);

  // 订阅实时Tick数据 - 支持更多状态以捕获早期数据
  const runId = activeRun?.id;
  const isBacktest =
    activeRun?.mode === StrategyRunMode.Backtest ||
    activeRun?.mode === 'BACKTEST';
  const shouldSubscribe =
    !!activeRun &&
    !isBacktest &&
    (activeRun.status === StrategyRunStatus.Running ||
      activeRun.status === StrategyRunStatus.Pending ||
      activeRun.status === StrategyRunStatus.Paused);
  const {
    ticks: liveTicks,
    latestTick,
    isConnected,
  } = useStrategyTicks(runId, {
    paused: !shouldSubscribe,
    maxTicks: 2000,
  });

  return (
    <Card
      className={`relative w-full bg-[#0F1729] border border-white/5 rounded-[2rem] shadow-2xl overflow-hidden ${className}`}
    >
      <ResizablePanelGroup direction="horizontal">
        <ResizablePanel defaultSize={60} minSize={40} maxSize={80}>
          {/* Main Chart Section */}
          <div className="relative w-full h-full">
            {/* Overlay Header - Glassy Top-Left Watermark */}
            <div
              className={`absolute ${isBacktest ? 'top-4 right-4' : 'top-16 left-4'} z-30 flex items-center gap-3 px-3 py-1.5 rounded-lg bg-slate-900/80 backdrop-blur-md border border-white/5 shadow-lg pointer-events-none transition-all hover:bg-slate-900/90 hover:border-white/10`}
            >
              <div className="flex items-center gap-2">
                <span className="relative flex h-2 w-2">
                  {!isBacktest && (
                    <span
                      className={`animate-ping absolute inline-flex h-full w-full rounded-full ${isConnected ? 'bg-emerald-400' : 'bg-slate-400'} opacity-75`}
                    ></span>
                  )}
                  <span
                    className={`relative inline-flex rounded-full h-2 w-2 ${isBacktest ? 'bg-blue-500' : isConnected ? 'bg-emerald-500' : 'bg-slate-500'}`}
                  ></span>
                </span>
                <span className="text-[10px] font-bold text-slate-300 uppercase tracking-wider">
                  {isBacktest ? '回测范围' : '实时监控'}
                </span>
              </div>
              <div className="w-px h-3 bg-white/10" />
              <span className="text-[10px] font-black text-white font-mono tracking-wider">
                {stockCode || '待机'}
              </span>
            </div>

            {/* Real-time Price Indicator */}
            {!isBacktest && latestTick && (
              <div className="absolute top-4 right-4 z-20 px-3 py-2 rounded-lg bg-slate-900/80 backdrop-blur-md border border-white/5 shadow-lg">
                <div className="flex flex-col gap-1">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-[9px] text-slate-400 uppercase tracking-wider">
                      最新价
                    </span>
                    <span
                      className={`text-sm font-bold font-mono ${financialToneClass(
                        latestTick.lastPrice - (latestTick.preClose || 0)
                      )}`}
                    >
                      {latestTick.lastPrice.toFixed(2)}
                    </span>
                  </div>
                  {latestTick.preClose && (
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[9px] text-slate-500">涨跌幅</span>
                      <span
                      className={`text-[10px] font-mono ${financialToneClass(
                        latestTick.lastPrice - latestTick.preClose
                      )}`}
                      >
                        {latestTick.lastPrice - latestTick.preClose >= 0
                          ? '+'
                          : ''}
                        {(
                          ((latestTick.lastPrice - latestTick.preClose) /
                            latestTick.preClose) *
                          100
                        ).toFixed(2)}
                        %
                      </span>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Chart Area */}
            <div className="w-full h-full relative bg-[#0B1120]/30 content-center">
              {activeRun ? (
                <StrategyChart
                  stockCode={stockCode}
                  overlays={overlays}
                  latestTick={latestTick}
                  liveTicks={liveTicks}
                  mode={isBacktest ? 'backtest' : 'live'}
                  backtestRange={backtestRange}
                  decisions={decisions}
                  executions={executions}
                  className="w-full h-full"
                />
              ) : (
                <div className="flex flex-col items-center justify-center text-slate-600 gap-2 h-full w-full">
                  <Activity size={32} className="opacity-20 animate-pulse" />
                  <span className="text-xs font-mono font-bold uppercase tracking-widest opacity-50">
                    系统待机
                  </span>
                </div>
              )}
            </div>
          </div>
        </ResizablePanel>

        <ResizableHandle
          withHandle
          className="bg-white/5 data-[active]:bg-blue-500/50 transition-colors w-1"
        />

        <ResizablePanel defaultSize={40} minSize={20} maxSize={60}>
          {/* Side Panel */}
          <div className="h-full">
            <MonitorSidePanel
              strategyId={strategyId || ''}
              runId={activeRun?.id}
              runMode={activeRun?.mode}
              backtestId={backtestId}
              backtestVersion={backtestVersion}
              decisions={decisions}
              executions={executions}
              isRunning={
                activeRun?.status === StrategyRunStatus.Running ||
                activeRun?.status === 'RUNNING'
              }
            />
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>
    </Card>
  );
}
