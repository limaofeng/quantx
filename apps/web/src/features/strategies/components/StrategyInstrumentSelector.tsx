import { Search } from 'lucide-react';
import { useMemo, useState } from 'react';

import { StockSelector } from '@/components/StockSelector';
import { Input } from '@/components/ui/input';
import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import { useStockSearch } from '@/hooks/useStockSearch';
import type { Stock } from '@/shared/types';
import { cn } from '@/utils/cn';

interface StrategyInstrumentSelectorProps {
  value: string;
  onChange: (instrumentCode: string, stock?: Stock) => void;
  className?: string;
  inputClassName?: string;
  placeholder?: string;
}

export function StrategyInstrumentSelector({
  value,
  onChange,
  className,
  inputClassName,
  placeholder = '搜索代码 / 名称 / 拼音',
}: StrategyInstrumentSelectorProps) {
  const [isSearching, setIsSearching] = useState(false);
  const { holdings } = useHoldings();
  const {
    searchQuery,
    setSearchQuery,
    filteredStocks,
    selectedStock,
    handleStockSelect,
  } = useStockSearch(holdings);

  const selectedInstrument = useMemo<Stock | null>(() => {
    if (selectedStock) {
      const code = value.trim() || resolveInstrumentCode(selectedStock);
      return {
        ...selectedStock,
        id: code,
        stockCode: code,
      };
    }

    const code = value.trim();
    if (!code) return null;

    return {
      id: code,
      stockCode: code,
      name: code,
      type: 'STOCK',
    };
  }, [selectedStock, value]);

  const selectedLabel = selectedInstrument
    ? `${selectedInstrument.name} ${selectedInstrument.stockCode || selectedInstrument.id}`
    : '';

  return (
    <StockSelector
      searchQuery={searchQuery}
      setSearchQuery={setSearchQuery}
      filteredStocks={filteredStocks}
      onStockSelect={stock => {
        const instrumentCode = resolveInstrumentCode(stock);
        handleStockSelect(stock);
        onChange(instrumentCode, stock);
        setIsSearching(false);
      }}
      selectedStock={selectedInstrument}
      placeholder={placeholder}
      className={className}
    >
      <div className="relative group">
        <Input
          type="text"
          placeholder={placeholder}
          value={isSearching ? searchQuery : searchQuery || selectedLabel}
          onFocus={() => setIsSearching(true)}
          onBlur={() => {
            setTimeout(() => {
              setIsSearching(false);
              setSearchQuery('');
            }, 160);
          }}
          onChange={event => {
            if (!isSearching) setIsSearching(true);
            setSearchQuery(event.target.value);
          }}
          className={cn(
            'w-full h-11 rounded-panel bg-slate-50 font-mono text-ui-caption font-bold shadow-inner transition-all focus:border-blue-500/50 focus:ring-blue-500/20 dark:border-white/5 dark:bg-white/[0.03]',
            'border px-3 pr-9 outline-none placeholder:text-slate-400',
            selectedInstrument &&
              !isSearching &&
              'text-blue-600 dark:text-blue-300',
            inputClassName
          )}
          autoComplete="off"
        />
        <Search className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 transition-colors group-focus-within:text-blue-500" />
      </div>
    </StockSelector>
  );
}

function resolveInstrumentCode(stock: Stock) {
  const rawCode = String(stock.stockCode || stock.id || '')
    .trim()
    .toUpperCase();
  if (!rawCode) return '';
  if (rawCode.includes('.')) return rawCode;

  const market = String(stock.market || '')
    .trim()
    .toUpperCase();
  if (['SH', 'SSE', 'XSHG'].includes(market)) return `${rawCode}.SH`;
  if (['SZ', 'SZSE', 'XSHE'].includes(market)) return `${rawCode}.SZ`;
  if (['BJ', 'BSE', 'XBEI'].includes(market)) return `${rawCode}.BJ`;

  if (/^6/.test(rawCode)) return `${rawCode}.SH`;
  if (/^[03]/.test(rawCode)) return `${rawCode}.SZ`;
  if (/^[48]/.test(rawCode)) return `${rawCode}.BJ`;
  return rawCode;
}
