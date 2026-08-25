import { Search } from 'lucide-react';
import * as React from 'react';

import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverAnchor,
} from '@/components/ui/popover';
import type { Stock } from '@/shared/types';
import { financialToneClass } from '@/shared/utils/financialColors';
import { formatCurrency } from '@/shared/utils/format';
import { cn } from '@/utils/cn';

interface StockSelectorProps {
  searchQuery: string;
  setSearchQuery: (query: string) => void;
  filteredStocks: Stock[];
  onStockSelect: (stock: Stock) => void;
  selectedStock: Stock | null;
  className?: string;
  inputClassName?: string;
  placeholder?: string;
  children?: React.ReactNode;
}

export function StockSelector({
  searchQuery,
  setSearchQuery,
  filteredStocks,
  onStockSelect,
  selectedStock,
  className,
  inputClassName,
  placeholder,
  children,
}: StockSelectorProps) {
  const [open, setOpen] = React.useState(false);
  const anchorRef = React.useRef<HTMLDivElement>(null);
  const inputId = React.useId();
  const showingSelectedStock = Boolean(selectedStock && !searchQuery);

  return (
    <div className={cn('w-full', className)}>
      <Popover open={open} onOpenChange={setOpen} modal={false}>
        <PopoverAnchor asChild>
          <div
            ref={anchorRef}
            className="relative group cursor-pointer"
            onClick={() => setOpen(true)}
          >
            {children || (
              <>
                <Input
                  id={inputId}
                  placeholder={
                    placeholder ||
                    (selectedStock
                      ? `${selectedStock.name} ${selectedStock.id}`
                      : '输入代码/名称')
                  }
                  value={searchQuery}
                  onChange={e => {
                    setSearchQuery(e.target.value);
                    if (!open) setOpen(true);
                  }}
                  onFocus={() => setOpen(true)}
                  className={cn(
                    'h-8 text-ui-label font-mono bg-muted/30 border-none focus-visible:ring-1 pr-8 transition-all',
                    showingSelectedStock &&
                      'bg-white/70 placeholder:font-black placeholder:text-slate-900 dark:bg-slate-950/70 dark:placeholder:text-slate-100 ring-1 ring-primary/20 focus-visible:ring-primary/40',
                    inputClassName
                  )}
                  autoComplete="off"
                />
                <Search
                  className={cn(
                    'absolute right-2 top-2 h-4 w-4 transition-colors',
                    showingSelectedStock
                      ? 'text-slate-700 group-hover:text-slate-900 dark:text-slate-200 dark:group-hover:text-white'
                      : 'text-muted-foreground group-hover:text-foreground'
                  )}
                />
              </>
            )}
          </div>
        </PopoverAnchor>
        <PopoverContent
          className="p-0 border-white/20 dark:border-white/10 bg-popover/95 dark:bg-slate-900/98 backdrop-blur-3xl shadow-2xl min-w-[240px] w-[var(--radix-popover-trigger-width)] overflow-hidden rounded-xl"
          align="start"
          sideOffset={4}
          onOpenAutoFocus={e => e.preventDefault()}
          onCloseAutoFocus={e => e.preventDefault()}
          onInteractOutside={e => {
            // Only close if we didn't click this selector again.
            if (
              e.target instanceof Node &&
              anchorRef.current?.contains(e.target)
            ) {
              e.preventDefault();
            } else {
              setOpen(false);
            }
          }}
        >
          {filteredStocks.length > 0 ? (
            <div className="max-h-[280px] overflow-y-auto divide-y divide-border/30 custom-scrollbar">
              {(!searchQuery || searchQuery.length < 2) && (
                <div className="px-3 py-1.5 bg-muted/30 text-ui-caption font-bold text-muted-foreground uppercase tracking-widest border-b border-border/30">
                  当前持仓 / 推荐
                </div>
              )}
              {filteredStocks.map(stock => (
                <button
                  key={stock.id}
                  type="button"
                  className="w-full text-left px-3 py-2 hover:bg-muted/50 transition-colors flex items-center justify-between group"
                  onClick={() => {
                    onStockSelect(stock);
                    setOpen(false);
                    setSearchQuery('');
                  }}
                >
                  <div className="flex flex-col">
                    <span className="text-ui-caption font-bold group-hover:text-primary transition-colors">
                      {stock.name}
                    </span>
                    <span className="text-ui-caption font-mono text-muted-foreground">
                      {stock.id}
                    </span>
                  </div>
                  <div className="text-right flex flex-col">
                    <span className="text-ui-caption font-mono font-bold">
                      {stock.quote
                        ? formatCurrency(stock.quote.lastPrice ?? 0)
                        : '--'}
                    </span>
                    <span
                      className={cn(
                        'text-ui-caption font-mono font-medium',
                        financialToneClass(stock.quote?.changePercent)
                      )}
                    >
                      {(stock.quote?.changePercent ?? 0) >= 0 ? '+' : ''}
                      {stock.quote?.changePercent?.toFixed(3) ?? '--'}%
                    </span>
                  </div>
                </button>
              ))}
            </div>
          ) : searchQuery ? (
            <div className="px-3 py-4 text-center text-ui-caption text-muted-foreground italic">
              未找到匹配股票
            </div>
          ) : null}
        </PopoverContent>
      </Popover>
    </div>
  );
}
