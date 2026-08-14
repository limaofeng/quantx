import { LoaderCircle, Search, X } from 'lucide-react';
import { Link } from 'wouter';

import { useStockSearch } from '@/hooks/useStockSearch';
import { financialToneClass } from '@/shared/utils/financialColors';

import { formatMarketPercent, formatMarketPrice } from '../marketWorkbench';

export function MarketStockSearch() {
  const { filteredStocks, searchQuery, setSearchQuery, stocksLoading } =
    useStockSearch();
  const showResults = searchQuery.trim().length >= 2;

  return (
    <div className="relative w-full" data-testid="market-stock-search">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
      <input
        aria-label="搜索股票或指数"
        className="h-9 w-full rounded-lg border border-white/10 bg-black/20 pl-9 pr-9 text-xs font-medium text-slate-200 outline-none transition-colors placeholder:text-slate-600 hover:border-white/15 focus:border-red-400/50 focus:ring-2 focus:ring-red-500/10"
        onChange={event => setSearchQuery(event.target.value)}
        placeholder="搜代码 / 名称，直达个股…"
        value={searchQuery}
      />
      {stocksLoading ? (
        <LoaderCircle className="absolute right-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 animate-spin text-slate-500" />
      ) : searchQuery ? (
        <button
          aria-label="清空搜索"
          className="absolute right-2 top-1/2 flex h-6 w-6 -translate-y-1/2 cursor-pointer items-center justify-center rounded-md text-slate-600 transition-colors hover:bg-white/5 hover:text-slate-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70"
          onClick={() => setSearchQuery('')}
          type="button"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      ) : null}

      {showResults ? (
        <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-50 overflow-hidden rounded-lg border border-white/10 bg-[#0b1120] shadow-2xl shadow-black/50">
          {filteredStocks.length > 0 ? (
            <div className="max-h-72 overflow-y-auto p-1.5 custom-scrollbar">
              {filteredStocks.map(stock => {
                const code = stock.stockCode || stock.id;
                const change = stock.quote?.changePercent;
                return (
                  <Link
                    key={code}
                    className="flex cursor-pointer items-center justify-between gap-3 rounded-md px-3 py-2 transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70"
                    href={`/stock/${encodeURIComponent(code)}`}
                    onClick={() => setSearchQuery('')}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-xs font-bold text-slate-200">
                        {stock.name || code}
                      </span>
                      <span className="mt-0.5 block font-mono text-[10px] text-slate-600">
                        {code}
                      </span>
                    </span>
                    <span className="shrink-0 text-right">
                      <span className="block font-mono text-xs font-bold text-slate-300">
                        {formatMarketPrice(stock.quote?.lastPrice)}
                      </span>
                      <span
                        className={`block text-[10px] font-bold ${financialToneClass(change)}`}
                      >
                        {formatMarketPercent(change)}
                      </span>
                    </span>
                  </Link>
                );
              })}
            </div>
          ) : (
            <div className="px-4 py-5 text-center text-xs text-slate-600">
              {stocksLoading ? '正在查询…' : '没有匹配的股票或指数'}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}
