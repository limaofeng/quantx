import { useState, useEffect } from 'react';

import { ScreeningResults } from '../components/ScreeningResults';
import { ScreeningTopBar } from '../components/ScreeningTopBar';
import { useStockScreening } from '../hooks/useStockScreening';
import { type ScreeningCriteria } from '../types';

export default function StockScreeningPage() {
  // 1. Data Logic
  const {
    screeningCriteria,
    setScreeningCriteria,
    results,
    meta,
    error,
    isLoading,
    runScreening,
    resetCriteria,
    availableIndustries,
  } = useStockScreening();

  // 2. Initial criteria state
  const [localCriteria, setLocalCriteria] =
    useState<ScreeningCriteria>(screeningCriteria);

  // Sync local criteria when global criteria resets/changes
  useEffect(() => {
    setLocalCriteria(screeningCriteria);
  }, [screeningCriteria]);

  // Sync back to hook when user clicks "Run"
  const handleRunScreening = () => {
    setScreeningCriteria(localCriteria);
    runScreening();
  };

  const handleReset = () => {
    resetCriteria();
  };

  return (
    <div className="h-[calc(100vh-var(--header-height)-4rem)] overflow-hidden flex flex-col">
      {/* Main Workspace - Vertical Flow Layout */}
      <div className="flex-1 min-h-0 bg-[#0F1729] border border-white/5 rounded-[2rem] shadow-2xl flex flex-col overflow-hidden">
        <ScreeningTopBar
          screeningCriteria={localCriteria}
          setScreeningCriteria={setLocalCriteria}
          availableIndustries={availableIndustries}
          meta={meta}
          onRunScreening={handleRunScreening}
          screeningLoading={isLoading}
          onReset={handleReset}
        />

        <div className="flex-1 overflow-hidden relative p-1 bg-transparent">
          <ScreeningResults
            screeningLoading={isLoading}
            results={results}
            meta={meta}
            error={error?.message}
          />
        </div>
      </div>
    </div>
  );
}
