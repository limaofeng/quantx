import { AlertOctagon, Columns, Settings2 } from 'lucide-react';
import React, { useMemo, useState } from 'react';

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';
import { cn } from '@/utils/cn';

import AIAdvisor from './components/AIAdvisor';
import GridChart from './components/GridChart';
import GridTable from './components/GridTable';
import InputForm from './components/InputForm';
import { DEFAULT_CONFIG } from './constants';
import { generateGridStrategy } from './services/gridEngine';
import { type GridConfig, type GridResult } from './types';

interface GridCalculatorProps {
  initialConfig?: Partial<GridConfig>;
  onConfigChange?: (config: GridConfig) => void;
  onResultChange?: (result: GridResult) => void;
  className?: string;
}

export const GridCalculator: React.FC<GridCalculatorProps> = ({
  initialConfig,
  onConfigChange,
  onResultChange,
  className = '',
}) => {
  const initialConfigSignature = useMemo(
    () => JSON.stringify(initialConfig || {}),
    [initialConfig]
  );
  const resolvedInitialConfig = useMemo(
    () => ({
      ...DEFAULT_CONFIG,
      ...initialConfig,
    }),
    [initialConfig, initialConfigSignature]
  );
  const [config, setConfig] = useState<GridConfig>(() => resolvedInitialConfig);

  const result: GridResult = useMemo(() => {
    return generateGridStrategy(config);
  }, [config]);

  React.useEffect(() => {
    setConfig(prev => {
      if (JSON.stringify(prev) === JSON.stringify(resolvedInitialConfig)) {
        return prev;
      }
      return resolvedInitialConfig;
    });
  }, [resolvedInitialConfig]);

  React.useEffect(() => {
    onResultChange?.(result);
  }, [result, onResultChange]);

  const handleConfigChange = (newConfig: GridConfig) => {
    setConfig(newConfig);
    onConfigChange?.(newConfig);
  };

  return (
    <div
      className={cn(
        'h-[calc(100vh-140px)] w-full overflow-hidden flex flex-col bg-slate-50/90 dark:bg-slate-950/95 backdrop-blur-3xl border border-slate-200/40 dark:border-slate-800/40 rounded-2xl shadow-[0_32px_64px_-16px_rgba(0,0,0,0.3)] dark:shadow-[0_32px_64px_-16px_rgba(0,0,0,0.6)] relative',
        className
      )}
    >
      {/* Header Bar */}
      <div className="flex items-center justify-between px-5 h-12 border-b border-slate-200/30 dark:border-slate-800/30 bg-slate-100/20 dark:bg-slate-900/20 shrink-0 z-20">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="relative">
              <div className="w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse" />
              <div className="absolute inset-0 w-2.5 h-2.5 rounded-full bg-blue-500 blur-[4px] animate-pulse opacity-60" />
            </div>
            <h3 className="text-[11px] font-black tracking-[0.2em] uppercase text-foreground/80">
              网格策略计算器
            </h3>
          </div>
          <div className="h-5 w-[1px] bg-border/40" />
          <div className="flex gap-4">
            <div className="flex items-center gap-2">
              <span className="text-[10px] font-bold text-muted-foreground/60 uppercase">
                状态
              </span>
              <span
                className={cn(
                  'text-[10px] font-mono font-bold px-1.5 py-0.5 rounded',
                  result.isValid
                    ? 'bg-green-500/10 text-green-600 dark:text-green-400'
                    : 'bg-red-500/10 text-red-600 dark:text-red-400'
                )}
              >
                {result.isValid ? '有效' : '无效'}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <ResizablePanelGroup direction="horizontal" className="h-full">
          {/* Left Panel: Configuration */}
          <ResizablePanel
            defaultSize={25}
            minSize={20}
            maxSize={40}
            className="bg-slate-50/40 dark:bg-slate-950/40 border-r border-slate-200/30 dark:border-slate-800/30"
          >
            <div className="h-full w-full overflow-hidden flex flex-col">
              <div className="px-4 py-3 border-b border-slate-200/30 dark:border-slate-800/30 bg-white/5">
                <h4 className="text-[10px] font-black uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                  <Settings2 className="w-3 h-3" /> 参数配置
                </h4>
              </div>
              <div className="flex-1 overflow-hidden">
                <InputForm config={config} onChange={handleConfigChange} />
              </div>
            </div>
          </ResizablePanel>

          <ResizableHandle className="w-[1px] bg-slate-200/20 dark:bg-slate-800/20 hover:bg-blue-500/40 transition-colors duration-500" />

          {/* Center Panel: Visualization */}
          <ResizablePanel
            defaultSize={45}
            minSize={30}
            className="bg-slate-100/10 dark:bg-slate-900/10"
          >
            <ResizablePanelGroup direction="vertical" className="h-full w-full">
              {/* Chart Panel - Resizable */}
              <ResizablePanel
                defaultSize={55}
                minSize={30}
                className="p-4 pb-0"
              >
                <GridChart result={result} stockCode={config.symbol} />
              </ResizablePanel>

              <ResizableHandle className="h-[6px] bg-transparent hover:bg-blue-500/30 transition-colors duration-300 mx-4 my-1 rounded-full" />

              {/* Scrollable Content Area */}
              <ResizablePanel
                defaultSize={45}
                minSize={20}
                className="px-4 pb-4 pt-2"
              >
                <div className="h-full overflow-y-auto custom-scrollbar space-y-4">
                  {!result.isValid && (
                    <div className="bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg p-3 flex items-start gap-3">
                      <AlertOctagon className="w-5 h-5 text-red-600 dark:text-red-400 shrink-0 mt-0.5" />
                      <div>
                        <h3 className="text-sm font-bold text-red-800 dark:text-red-300">
                          配置错误
                        </h3>
                        <ul className="list-disc list-inside text-xs text-red-700 dark:text-red-400 mt-1">
                          {result.errors.map((e, i) => (
                            <li key={i}>{e}</li>
                          ))}
                        </ul>
                      </div>
                    </div>
                  )}

                  {/* Budget Warning */}
                  {(() => {
                    const currentPosValue =
                      config.positionShares * config.basePrice;
                    const availableCash = Math.max(
                      0,
                      config.cashTotal - currentPosValue
                    );
                    const intentBudget =
                      availableCash * (config.buyBudgetPct / 100);
                    const maxPosValue =
                      config.cashTotal * (config.maxPositionValuePct / 100);
                    const remainingQuota = Math.max(
                      0,
                      maxPosValue - currentPosValue
                    );

                    if (intentBudget > remainingQuota) {
                      return (
                        <div className="bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-800 rounded-lg p-3 flex items-start gap-3">
                          <AlertOctagon className="w-5 h-5 text-yellow-600 dark:text-yellow-400 shrink-0 mt-0.5" />
                          <div>
                            <h3 className="text-sm font-bold text-yellow-800 dark:text-yellow-300">
                              预算超出风控限制
                            </h3>
                            <div className="text-xs text-yellow-700 dark:text-yellow-400 mt-1">
                              <p>
                                意图买入预算 ({config.buyBudgetPct}%)
                                超出最大仓位限制。
                              </p>
                              <div className="flex gap-4 mt-1 font-mono">
                                <span>
                                  意图: ¥
                                  {Math.round(intentBudget).toLocaleString()}
                                </span>
                                <span className="font-bold">
                                  实际: ¥
                                  {Math.round(remainingQuota).toLocaleString()}
                                </span>
                              </div>
                            </div>
                          </div>
                        </div>
                      );
                    }
                    return null;
                  })()}

                  <AIAdvisor config={config} result={result} />
                </div>
              </ResizablePanel>
            </ResizablePanelGroup>
          </ResizablePanel>

          <ResizableHandle className="w-[1px] bg-slate-200/20 dark:bg-slate-800/20 hover:bg-blue-500/40 transition-colors duration-500" />

          {/* Right Panel: Data Table */}
          <ResizablePanel
            defaultSize={30}
            minSize={20}
            className="bg-white/5 dark:bg-black/5"
          >
            <div className="h-full w-full overflow-hidden flex flex-col">
              <div className="px-4 py-3 border-b border-slate-200/30 dark:border-slate-800/30 bg-white/5">
                <h4 className="text-[10px] font-black uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                  <Columns className="w-3 h-3" /> 网格明细
                </h4>
              </div>
              <div className="flex-1 overflow-hidden">
                <GridTable result={result} />
              </div>
            </div>
          </ResizablePanel>
        </ResizablePanelGroup>
      </div>
    </div>
  );
};

export default GridCalculator;
