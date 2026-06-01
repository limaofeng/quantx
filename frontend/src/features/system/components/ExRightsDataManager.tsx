import { Search, Loader2, Database, AlertCircle } from 'lucide-react';
import React, { useState } from 'react';
import { useQuery } from 'urql';

import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { gql } from '@/generated/gql';
import { useStockSearch } from '@/hooks/useStockSearch';

const GET_DIVID_FACTORS = gql(`
  query GetDividFactors($stockCode: String!) {
    dividFactors(stockCode: $stockCode) {
      stockCode
      exDate
      interest
      stockBonus
      stockGift
      allotNum
      allotPrice
      dr
      time
    }
  }
`);

export function ExRightsDataManager() {
  const {
    searchQuery,
    setSearchQuery,
    filteredStocks, // Autocomplete suggestions
    stocksLoading,
    handleStockSelect,
    selectedStock,
  } = useStockSearch();

  const [searchInputFocused, setSearchInputFocused] = useState(false);

  // Query validation: only run if we have a selected stock
  const [{ data, fetching: dataLoading, error }] = useQuery({
    query: GET_DIVID_FACTORS as any,
    variables: { stockCode: selectedStock?.stockCode || '' },
    pause: !selectedStock,
  });

  const factors = data?.dividFactors || [];

  return (
    <div className="h-full flex flex-col bg-white/50 dark:bg-white/[0.02] rounded-3xl border border-slate-200 dark:border-white/5 backdrop-blur-sm overflow-hidden">
      {/* Heavy Header Section */}
      <div className="p-6 border-b border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.01]">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h3 className="text-lg font-bold text-slate-900 dark:text-white flex items-center gap-2">
              <Database className="w-5 h-5 text-indigo-500" />
              除权除息数据
            </h3>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-1">
              查询股票的历史分红配股及除权因子信息
            </p>
          </div>

          {/* Stock Search Input */}
          <div className="relative w-full md:w-80 group z-50">
            <div className="relative">
              <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 group-focus-within:text-indigo-500 transition-colors" />
              <Input
                value={searchQuery}
                onFocus={() => setSearchInputFocused(true)}
                onBlur={() => {
                  // Delay hiding suggestions to allow click event
                  setTimeout(() => setSearchInputFocused(false), 200);
                }}
                onChange={e => setSearchQuery(e.target.value)}
                placeholder="搜索股票代码/名称..."
                className="pl-10 h-10 rounded-2xl bg-white/50 dark:bg-white/[0.02] border-slate-200 dark:border-white/5 focus-visible:ring-indigo-500/20 focus-visible:border-indigo-500 transition-all shadow-sm backdrop-blur-md"
              />
              {stocksLoading && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2">
                  <Loader2 className="w-4 h-4 animate-spin text-indigo-500" />
                </div>
              )}
            </div>

            {/* Suggestions Dropdown */}
            {searchInputFocused && filteredStocks.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-2 bg-white dark:bg-slate-900 border border-slate-200 dark:border-white/10 rounded-xl shadow-xl max-h-80 overflow-y-auto z-50 animate-in fade-in slide-in-from-top-2 duration-200">
                {filteredStocks.map(stock => (
                  <button
                    key={stock.id}
                    className="w-full text-left px-4 py-3 hover:bg-slate-50 dark:hover:bg-white/5 flex items-center justify-between group/item transition-colors"
                    onClick={() => handleStockSelect(stock)}
                  >
                    <div>
                      <div className="font-bold text-slate-700 dark:text-slate-200 group-hover/item:text-indigo-600 dark:group-hover/item:text-indigo-400 transition-colors">
                        {stock.name}
                      </div>
                      <div className="text-xs text-slate-400 font-mono mt-0.5">
                        {stock.stockCode}
                      </div>
                    </div>
                    {(stock as any).market && (
                      <Badge
                        variant="secondary"
                        className="font-mono text-[10px]"
                      >
                        {(stock as any).market}
                      </Badge>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Selected Stock Info Bar */}
        {selectedStock && (
          <div className="mt-4 flex items-center gap-4 animate-in fade-in slide-in-from-top-2">
            <div className="flex items-center gap-2 px-3 py-1.5 bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-300 rounded-lg text-sm font-bold border border-indigo-100 dark:border-indigo-500/20">
              <span>{selectedStock.name}</span>
              <span className="opacity-60">|</span>
              <span className="font-mono">{selectedStock.stockCode}</span>
            </div>

            {dataLoading && (
              <div className="flex items-center gap-2 text-xs text-slate-500 animate-pulse">
                <Loader2 className="w-3 h-3 animate-spin" />
                正在获取数据...
              </div>
            )}
          </div>
        )}
      </div>

      {/* Content Area */}
      <div className="flex-1 overflow-auto p-0 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-white/10 scrollbar-track-transparent">
        {!selectedStock ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-400">
            <div className="w-16 h-16 bg-slate-50 dark:bg-white/5 rounded-full flex items-center justify-center mb-4">
              <Search className="w-6 h-6 opacity-50" />
            </div>
            <p className="text-sm">请搜索并选择一只股票以查看除权数据</p>
          </div>
        ) : error ? (
          <div className="h-full flex flex-col items-center justify-center text-red-500">
            <AlertCircle className="w-8 h-8 mb-2" />
            <p className="text-sm font-medium">数据加载失败</p>
            <p className="text-xs opacity-70 mt-1">{error.message}</p>
          </div>
        ) : factors.length === 0 && !dataLoading ? (
          <div className="h-full flex flex-col items-center justify-center text-slate-400">
            <p className="text-sm">该股票暂无除权除息记录</p>
          </div>
        ) : (
          <table className="w-full text-sm text-left">
            <thead className="text-xs text-slate-500 dark:text-slate-400 bg-slate-50/50 dark:bg-white/5 sticky top-0 backdrop-blur-sm z-10">
              <tr>
                <th className="px-6 py-3 font-medium">除权日</th>
                <th className="px-6 py-3 font-medium text-right">分红(元)</th>
                <th className="px-6 py-3 font-medium text-right">送股(股)</th>
                <th className="px-6 py-3 font-medium text-right">转增(股)</th>
                <th className="px-6 py-3 font-medium text-right">配股(股)</th>
                <th className="px-6 py-3 font-medium text-right">配股价(元)</th>
                <th className="px-6 py-3 font-medium text-right">除权因子</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-white/5">
              {factors.map((factor: any, idx: number) => (
                <tr
                  key={idx}
                  className="hover:bg-indigo-50/30 dark:hover:bg-indigo-500/5 transition-colors group"
                >
                  <td className="px-6 py-3 font-mono text-slate-600 dark:text-slate-300">
                    {factor.exDate}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-700 dark:text-slate-200 group-hover:text-indigo-600 dark:group-hover:text-indigo-400">
                    {factor.interest?.toFixed(4)}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-500 dark:text-slate-400">
                    {factor.stockBonus?.toFixed(4)}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-500 dark:text-slate-400">
                    {factor.stockGift?.toFixed(4)}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-500 dark:text-slate-400">
                    {factor.allotNum?.toFixed(4)}
                  </td>
                  <td className="px-6 py-3 text-right font-mono text-slate-500 dark:text-slate-400">
                    {factor.allotPrice?.toFixed(4)}
                  </td>
                  <td className="px-6 py-3 text-right font-mono font-bold text-slate-900 dark:text-white">
                    {factor.dr?.toFixed(4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
