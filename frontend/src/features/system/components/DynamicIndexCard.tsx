import { Search, Plus, RefreshCw, BarChart2 } from 'lucide-react';
import React, { useState } from 'react';

import { Input } from '@/components/ui/input';
import { useStockSearch } from '@/hooks/useStockSearch';
import { cn } from '@/utils/cn';

import { IndexDataCard } from './IndexDataCard';

export function DynamicIndexCard() {
  const {
    searchQuery,
    setSearchQuery,
    filteredStocks,
    stocksLoading,
    handleStockSelect,
    selectedStock,
    setSelectedStock,
  } = useStockSearch();

  const [isFocused, setIsFocused] = useState(false);

  // Default state (Custom Monitor Placeholder)
  if (!selectedStock && !isFocused && !searchQuery) {
    return (
      <div
        onClick={() => setIsFocused(true)}
        className="h-full min-h-[160px] flex flex-col justify-between p-4 rounded-xl border border-slate-200/40 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-sm cursor-pointer hover:bg-white/60 dark:hover:bg-white/[0.04] transition-all group relative overflow-hidden"
      >
        <div className="absolute inset-0 bg-gradient-to-br from-indigo-500/5 to-purple-500/5 opacity-0 group-hover:opacity-100 transition-opacity" />

        {/* Header equivalent */}
        <div className="flex justify-between items-start relative z-10">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-500 group-hover:scale-110 transition-transform">
              <BarChart2 className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-700 dark:text-slate-200">
                自选监控
              </h3>
              <p className="text-[9px] font-black uppercase tracking-widest text-slate-400 mt-0.5">
                Custom Monitor
              </p>
            </div>
          </div>
          <div className="p-1 rounded-full border border-dashed border-slate-300 dark:border-white/10 text-slate-400 group-hover:text-indigo-500 group-hover:border-indigo-500/50 transition-colors">
            <Plus className="w-3.5 h-3.5" />
          </div>
        </div>

        {/* Content Placeholder */}
        <div className="relative z-10 mt-4">
          <div className="text-2xl font-black text-slate-300 dark:text-white/10 tracking-tight">
            -.--
          </div>
          <div className="h-4 w-16 bg-slate-100 dark:bg-white/5 rounded mt-2" />
        </div>

        <div className="absolute bottom-4 right-4 text-xs font-medium text-indigo-500 opacity-0 transform translate-y-2 group-hover:opacity-100 group-hover:translate-y-0 transition-all duration-300">
          Click to Add
        </div>
      </div>
    );
  }

  // Active / Search Mode
  return (
    <div
      className={cn(
        'h-full min-h-[160px] flex flex-col p-1 rounded-xl border transition-all relative overflow-visible z-20', // z-20 for dropdown
        selectedStock || isFocused
          ? 'border-indigo-500/20 bg-white dark:bg-slate-900 shadow-xl shadow-indigo-500/10'
          : 'border-slate-200/40 dark:border-white/5'
      )}
    >
      {selectedStock ? (
        <div className="relative h-full">
          <IndexDataCard
            name={selectedStock.name}
            code={selectedStock.stockCode}
            price={selectedStock.quote?.lastPrice?.toFixed(2) || '0.00'}
            change={(selectedStock.quote?.change || 0).toFixed(2)}
            changePercent={`${(selectedStock.quote?.changePercent || 0).toFixed(2)}%`}
            status="normal"
          />

          {/* Reset Button */}
          <button
            onClick={e => {
              e.stopPropagation();
              setSelectedStock(null);
              setSearchQuery('');
              setIsFocused(false);
            }}
            className="absolute top-3 right-3 p-1.5 rounded-lg bg-slate-100 dark:bg-white/10 text-slate-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-500/20 transition-all z-10"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
        </div>
      ) : (
        /* Search Container */
        <div className="h-full flex flex-col p-3">
          <div className="mb-2 px-1">
            <h3 className="text-xs font-bold text-slate-500">Add Index</h3>
          </div>
          <div className="relative flex-1">
            <Search
              className={cn(
                'absolute left-3 top-3.5 w-4 h-4 transition-colors z-10',
                isFocused ? 'text-indigo-500' : 'text-slate-400'
              )}
            />
            <Input
              autoFocus={true}
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              onBlur={() => {
                if (!searchQuery) setTimeout(() => setIsFocused(false), 200);
              }}
              placeholder="Type code..."
              className="pl-9 h-11 rounded-xl bg-slate-50 dark:bg-white/5 border-slate-200 dark:border-white/10 focus-visible:ring-indigo-500/30 text-sm"
            />

            {/* Results */}
            {filteredStocks.length > 0 && (
              <div className="absolute top-14 left-0 right-0 bg-white dark:bg-slate-800 border border-slate-200 dark:border-white/10 rounded-xl shadow-xl max-h-48 overflow-y-auto z-50">
                {filteredStocks.map(stock => (
                  <button
                    key={stock.id}
                    className="w-full text-left px-3 py-2.5 hover:bg-slate-50 dark:hover:bg-white/5 flex items-center justify-between group transition-colors border-b border-slate-50 dark:border-white/5 last:border-0"
                    onMouseDown={e => {
                      e.preventDefault(); // Prevent blur
                      handleStockSelect(stock);
                    }}
                  >
                    <div className="min-w-0">
                      <div className="font-bold text-xs text-slate-700 dark:text-slate-200 truncate group-hover:text-indigo-600 dark:group-hover:text-indigo-400">
                        {stock.name}
                      </div>
                      <div className="text-[10px] text-slate-400 font-mono">
                        {stock.stockCode}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
