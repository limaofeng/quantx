import * as React from 'react';

import { StockSelector } from '@/components/StockSelector';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type {
  PortfolioSummaryData,
  Position,
} from '@/features/portfolio/types';
import { useStockSearch } from '@/hooks/useStockSearch';
import type { Stock } from '@/shared/types';
import { formatCurrency } from '@/shared/utils/format';
import { cn } from '@/utils/cn';

import { useFormState } from './hooks/useFormState';
import { useTradingCalculation } from './hooks/useTradingCalculation';
import { useTradingSubmit } from './hooks/useTradingSubmit';

interface TradingCardProps {
  holdings: Position[];
  initialStockCode?: string;
  initialSide?: 'BUY' | 'SELL';
  onSuccess?: () => void;
  onStockSelect?: (stock: Stock | null) => void;
  portfolioSummary?: Pick<PortfolioSummaryData, 'cash'>;
  priceUpdate?: { price: string; timestamp: number } | null;
}

interface HoldingLike {
  canUseVolume?: number | null;
  instrumentName?: string | null;
  lastPrice?: number | null;
  name?: string | null;
  profitRate?: number | null;
  stockCode?: string | null;
  volume?: number | null;
  [key: string]: unknown;
}

const normalizeStockCode = (value: unknown) =>
  typeof value === 'string' ? value.trim().toUpperCase() : '';

const getStockCode = (stock: unknown) => {
  if (!stock || typeof stock !== 'object') return '';
  const candidate = stock as { id?: unknown; stockCode?: unknown };
  return normalizeStockCode(candidate.stockCode || candidate.id);
};

const toPositiveNumber = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
};

const toNonNegativeInteger = (value: unknown) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
};

const BUY_LOT_SIZE = 100;

const QUICK_QUANTITY_PRESETS = [
  { label: '1/4', value: 0.25, type: 'percent' },
  { label: '1/2', value: 0.5, type: 'percent' },
  { label: '全仓', value: 1, type: 'percent' },
  { label: '1W', value: 10000, type: 'amount' },
] as const;

type QuickQuantityPreset = (typeof QUICK_QUANTITY_PRESETS)[number];

const clampQuantity = (value: number, maxQuantity: number) => {
  const quantity = toNonNegativeInteger(value);
  if (quantity <= 0) return 0;
  return maxQuantity > 0 ? Math.min(quantity, maxQuantity) : quantity;
};

const toBuyLotQuantity = (value: number) =>
  Math.floor(toNonNegativeInteger(value) / BUY_LOT_SIZE) * BUY_LOT_SIZE;

const resolveQuickQuantity = ({
  availableQuantity,
  currentOrderPrice,
  preset,
  tradeType,
}: {
  availableQuantity: number;
  currentOrderPrice: number;
  preset: QuickQuantityPreset;
  tradeType: 'buy' | 'sell';
}) => {
  if (availableQuantity <= 0) return 0;

  const requestedQuantity =
    preset.type === 'amount'
      ? currentOrderPrice > 0
        ? preset.value / currentOrderPrice
        : 0
      : availableQuantity * preset.value;
  const boundedQuantity = Math.min(
    toNonNegativeInteger(requestedQuantity),
    availableQuantity
  );

  return tradeType === 'buy'
    ? toBuyLotQuantity(boundedQuantity)
    : boundedQuantity;
};

const stockFromHolding = (holding: HoldingLike, stockCode: string) => ({
  ...holding,
  id: holding?.stockCode || stockCode,
  stockCode: holding?.stockCode || stockCode,
  name: holding?.instrumentName || holding?.name || stockCode,
  quote: {
    lastPrice: holding?.lastPrice || 0,
    changePercent: holding?.profitRate || 0,
  },
});

const fallbackStock = (stockCode: string) => ({
  id: stockCode,
  stockCode,
  name: stockCode,
  quote: {
    lastPrice: 0,
    changePercent: 0,
  },
});

/**
 * 交易卡片 - 紧凑型下单面板
 */
export function TradingCard({
  holdings,
  initialStockCode,
  initialSide = 'BUY',
  onSuccess,
  onStockSelect,
  portfolioSummary,
  priceUpdate,
}: TradingCardProps) {
  // Form State
  const {
    tradeType,
    setTradeType,
    orderType,
    setOrderType,
    quantity,
    setQuantity,
    price,
    setPrice,
    resetForm,
  } = useFormState(initialSide === 'SELL' ? 'sell' : 'buy');

  const {
    selectedStock,
    searchQuery,
    setSearchQuery,
    filteredStocks,
    handleStockSelect,
  } = useStockSearch(holdings);

  const normalizedInitialStockCode = normalizeStockCode(initialStockCode);
  const selectedStockCode = getStockCode(selectedStock);
  const selectedHolding = React.useMemo(
    () =>
      holdings.find(
        item => normalizeStockCode(item?.stockCode) === selectedStockCode
      ) || null,
    [holdings, selectedStockCode]
  );

  const selectStock = React.useCallback(
    (stock: Stock) => {
      handleStockSelect(stock, setPrice);
      onStockSelect?.(stock);
    },
    [handleStockSelect, onStockSelect, setPrice]
  );

  React.useEffect(() => {
    if (!normalizedInitialStockCode) return;
    const holding = holdings.find(
      item => normalizeStockCode(item?.stockCode) === normalizedInitialStockCode
    );
    const selectedCode = selectedStockCode;
    const selectedName =
      selectedStock && typeof selectedStock === 'object'
        ? String((selectedStock as { name?: unknown }).name || '')
        : '';
    const selectedPrice =
      selectedStock && typeof selectedStock === 'object'
        ? Number(
            (selectedStock as { quote?: { lastPrice?: unknown } }).quote
              ?.lastPrice || 0
          )
        : 0;
    const nextStock = holding
      ? stockFromHolding(holding, normalizedInitialStockCode)
      : fallbackStock(normalizedInitialStockCode);
    const nextName = String(nextStock.name || '');
    const nextPrice = Number(nextStock.quote?.lastPrice || 0);
    if (
      selectedCode === normalizedInitialStockCode &&
      selectedName === nextName &&
      selectedPrice === nextPrice
    ) {
      return;
    }
    const shouldHydrateFromHolding =
      !!holding &&
      selectedCode === normalizedInitialStockCode &&
      (selectedName === normalizedInitialStockCode || selectedPrice <= 0);
    if (
      selectedCode === normalizedInitialStockCode &&
      !shouldHydrateFromHolding
    ) {
      return;
    }

    handleStockSelect(nextStock, setPrice);
  }, [
    handleStockSelect,
    holdings,
    normalizedInitialStockCode,
    selectedStock,
    selectedStockCode,
    setPrice,
  ]);

  // Default to first holding if no stock selected
  const hasAutoSelectedRef = React.useRef(false);
  React.useEffect(() => {
    if (
      !normalizedInitialStockCode &&
      !hasAutoSelectedRef.current &&
      !selectedStock &&
      holdings.length > 0
    ) {
      const first = holdings[0];
      selectStock(stockFromHolding(first, first.stockCode));
      hasAutoSelectedRef.current = true;
    }
  }, [holdings, normalizedInitialStockCode, selectedStock, selectStock]);

  const { estimatedAmount, estimatedFees } = useTradingCalculation(
    quantity,
    price
  );

  const { handleSubmit, isSubmitting } = useTradingSubmit(() => {
    resetForm();
    onSuccess?.();
  });

  const currentOrderPrice = React.useMemo(() => {
    return (
      toPositiveNumber(price) ||
      toPositiveNumber(selectedStock?.quote?.lastPrice) ||
      toPositiveNumber(selectedStock?.currentPrice)
    );
  }, [price, selectedStock?.currentPrice, selectedStock?.quote?.lastPrice]);

  const buyAvailableQuantity = toBuyLotQuantity(
    currentOrderPrice > 0
      ? toNonNegativeInteger((portfolioSummary?.cash ?? 0) / currentOrderPrice)
      : 0
  );
  const sellAvailableQuantity = toNonNegativeInteger(
    selectedHolding?.canUseVolume
  );
  const availableQuantity =
    tradeType === 'buy' ? buyAvailableQuantity : sellAvailableQuantity;
  const availableQuantityLabel =
    tradeType === 'buy'
      ? currentOrderPrice > 0
        ? buyAvailableQuantity.toLocaleString()
        : '--'
      : selectedHolding
        ? sellAvailableQuantity.toLocaleString()
        : '0';
  const quantityMax = availableQuantity > 0 ? availableQuantity : undefined;
  const quantityMin = tradeType === 'buy' ? 100 : 1;
  const quantityStep = tradeType === 'buy' ? 100 : 1;
  const quantityNumber = toNonNegativeInteger(quantity);
  const canSubmitPrice = toPositiveNumber(price) > 0;
  const canSubmitQuantity =
    quantityNumber > 0 &&
    (tradeType === 'buy'
      ? buyAvailableQuantity > 0 &&
        quantityNumber >= BUY_LOT_SIZE &&
        quantityNumber % BUY_LOT_SIZE === 0 &&
        quantityNumber <= buyAvailableQuantity
      : sellAvailableQuantity > 0 && quantityNumber <= sellAvailableQuantity);

  React.useEffect(() => {
    if (priceUpdate?.price) {
      setPrice(priceUpdate.price);
    }
  }, [priceUpdate, setPrice]);

  React.useEffect(() => {
    if (!quantity) return;
    const parsedQuantity = toNonNegativeInteger(quantity);
    if (parsedQuantity <= 0) {
      setQuantity('');
      return;
    }
    if (tradeType === 'sell') {
      const nextQuantity =
        sellAvailableQuantity > 0
          ? clampQuantity(parsedQuantity, sellAvailableQuantity)
          : 0;
      if (nextQuantity !== parsedQuantity) {
        setQuantity(nextQuantity > 0 ? nextQuantity.toString() : '');
      }
      return;
    }
    if (buyAvailableQuantity > 0 && parsedQuantity > buyAvailableQuantity) {
      setQuantity(buyAvailableQuantity.toString());
    }
  }, [
    buyAvailableQuantity,
    quantity,
    sellAvailableQuantity,
    setQuantity,
    tradeType,
  ]);

  const handleQuantityChange = (value: string) => {
    if (!value) {
      setQuantity('');
      return;
    }
    const parsedQuantity = toNonNegativeInteger(value);
    if (parsedQuantity <= 0) {
      setQuantity('');
      return;
    }
    if (tradeType === 'sell' && sellAvailableQuantity <= 0) {
      setQuantity('');
      return;
    }
    setQuantity(clampQuantity(parsedQuantity, availableQuantity).toString());
  };

  const handleTradeTypeChange = (nextTradeType: 'buy' | 'sell') => {
    if (nextTradeType === tradeType) return;
    setTradeType(nextTradeType);
    setQuantity('');
  };

  return (
    <Card className="p-3 border-none shadow-none bg-background/40 backdrop-blur-md h-full flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-500">
      {/* 顶部标题与买卖切换 */}
      <div className="flex flex-col gap-3 mb-4 px-1">
        <div className="flex items-center justify-between">
          <h4 className="text-[9px] font-bold uppercase tracking-[0.2em] text-muted-foreground/50">
            交易控制台
          </h4>
          <div className="flex items-center gap-1.5 px-1.5 py-0.5 bg-blue-500/5 rounded-full border border-blue-500/10">
            <div className="w-1 h-1 bg-blue-500 rounded-full animate-pulse" />
            <span className="text-[8px] font-bold text-blue-500/70 uppercase tracking-tighter">
              Live
            </span>
          </div>
        </div>

        {/* 现代分段切换控件 - 压缩高度 */}
        <div className="relative flex p-0.5 bg-slate-100/50 dark:bg-slate-900/50 rounded-lg border border-slate-200/20 dark:border-slate-800/20 shadow-inner">
          <div
            className={cn(
              'absolute top-0.5 bottom-0.5 w-[calc(50%-2px)] transition-all duration-300 ease-spring rounded-md shadow-sm ring-1 ring-black/5',
              tradeType === 'buy'
                ? 'left-0.5 bg-market-up'
                : 'left-[calc(50%+0.5px)] bg-market-down'
            )}
          />
          <button
            type="button"
            onClick={() => handleTradeTypeChange('buy')}
            className={cn(
              'flex-1 relative z-10 py-1.5 text-[10px] font-black uppercase tracking-widest transition-colors duration-300',
              tradeType === 'buy'
                ? 'text-white'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            买入
          </button>
          <button
            type="button"
            onClick={() => handleTradeTypeChange('sell')}
            className={cn(
              'flex-1 relative z-10 py-1.5 text-[10px] font-black uppercase tracking-widest transition-colors duration-300',
              tradeType === 'sell'
                ? 'text-white'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            平仓
          </button>
        </div>
      </div>

      <form
        onSubmit={e =>
          handleSubmit(
            e,
            tradeType,
            orderType,
            selectedStock,
            quantity,
            price,
            resetForm
          )
        }
        className="flex-1 flex flex-col min-h-0"
      >
        <ScrollArea className="flex-1 -mr-3">
          <div className="space-y-3 pr-3 pb-2">
            {/* 第一部分：标的选择 */}
            <div className="group/section bg-slate-50/30 dark:bg-slate-900/10 rounded-xl border border-slate-200/20 dark:border-slate-800/20 p-2 pb-2.5 hover:border-slate-300/40 dark:hover:border-slate-700/40 transition-all duration-300">
              <div className="flex items-center justify-between mb-1.5 px-1">
                <Label className="text-[8px] font-black text-muted-foreground/40 uppercase tracking-widest">
                  证券标的
                </Label>
                {selectedStock && (
                  <span className="text-[8px] font-mono font-bold text-primary/60">
                    {selectedStock.stockCode}
                  </span>
                )}
              </div>
              <StockSelector
                searchQuery={searchQuery}
                setSearchQuery={setSearchQuery}
                filteredStocks={filteredStocks}
                onStockSelect={selectStock}
                selectedStock={selectedStock}
              />
            </div>

            {/* 第二部分：价格设置 */}
            <div className="group/section bg-slate-50/30 dark:bg-slate-900/10 rounded-xl border border-slate-200/20 dark:border-slate-800/20 p-2 pb-2.5 hover:border-slate-300/40 dark:hover:border-slate-700/40 transition-all duration-300">
              <div className="flex items-center justify-between mb-1.5 px-1">
                <Label className="text-[8px] font-black text-muted-foreground/40 uppercase tracking-widest">
                  价格设置
                </Label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      const p = (
                        parseFloat(
                          String(
                            selectedStock?.quote?.lastPrice ??
                              selectedStock?.currentPrice ??
                              '0'
                          )
                        ) * 0.9
                      ).toFixed(2);
                      if (Number(p) > 0) setPrice(p);
                    }}
                    className="text-[8px] font-black text-market-down/60 hover:text-market-down transition-colors uppercase"
                  >
                    跌停{' '}
                    {(
                      parseFloat(
                        String(
                          selectedStock?.quote?.lastPrice ??
                            selectedStock?.currentPrice ??
                            '0'
                        )
                      ) * 0.9
                    ).toFixed(2)}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const p = (
                        parseFloat(
                          String(
                            selectedStock?.quote?.lastPrice ??
                              selectedStock?.currentPrice ??
                              '0'
                          )
                        ) * 1.1
                      ).toFixed(2);
                      if (Number(p) > 0) setPrice(p);
                    }}
                    className="text-[8px] font-black text-market-up/60 hover:text-market-up transition-colors uppercase"
                  >
                    涨停{' '}
                    {(
                      parseFloat(
                        String(
                          selectedStock?.quote?.lastPrice ??
                            selectedStock?.currentPrice ??
                            '0'
                        )
                      ) * 1.1
                    ).toFixed(2)}
                  </button>
                </div>
              </div>

              <div className="flex items-center gap-2 bg-white/50 dark:bg-slate-950/50 p-1 rounded-lg border border-slate-200/40 dark:border-slate-800/40 focus-within:ring-1 focus-within:ring-primary/20 focus-within:border-primary/40 transition-all">
                <Select value={orderType} onValueChange={setOrderType}>
                  <SelectTrigger className="w-[80px] h-7 text-[11px] border-none shadow-none bg-slate-100/50 dark:bg-slate-900/50 focus:ring-0 focus:ring-offset-0 ring-0 outline-none px-2 font-bold rounded-md transition-colors">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="rounded-lg border-slate-200/40 dark:border-slate-800/40">
                    <SelectItem value="limit">限价单</SelectItem>
                    <SelectItem value="market">市价单</SelectItem>
                  </SelectContent>
                </Select>

                <div className="h-4 w-px bg-border/20" />

                <div className="flex-1 flex items-center justify-end gap-1 px-1">
                  <span className="text-[9px] font-bold text-muted-foreground/30 font-mono">
                    CNY
                  </span>
                  <Input
                    className="w-full max-w-[80px] h-7 text-[13px] font-black font-mono bg-transparent border-none shadow-none focus-visible:ring-0 text-right p-0 no-spin placeholder:text-muted-foreground/20"
                    type="number"
                    step="0.01"
                    value={price}
                    onChange={e => setPrice(e.target.value)}
                    placeholder="0.00"
                  />
                </div>
              </div>
            </div>

            {/* 第三部分：委托数量 */}
            <div className="group/section bg-slate-50/30 dark:bg-slate-900/10 rounded-xl border border-slate-200/20 dark:border-slate-800/20 p-2 pb-2.5 hover:border-slate-300/40 dark:hover:border-slate-700/40 transition-all duration-300">
              <div className="flex items-center justify-between mb-1.5 px-1">
                <Label className="text-[8px] font-black text-muted-foreground/40 uppercase tracking-widest">
                  委托数量
                </Label>
                <div className="flex items-center gap-1 text-[8px] font-bold uppercase tracking-tighter">
                  <span className="text-muted-foreground/50">可用</span>
                  <span className="text-primary tabular-nums">
                    {availableQuantityLabel}
                  </span>
                  <span className="text-muted-foreground/30">股</span>
                </div>
              </div>

              <div className="relative mb-2">
                <Input
                  className="h-9 text-[14px] font-black font-mono bg-white/50 dark:bg-slate-950/50 border border-slate-200/40 dark:border-slate-800/40 focus-visible:ring-1 focus-visible:ring-primary/20 focus-visible:border-primary/40 rounded-lg px-3 no-spin transition-all"
                  type="number"
                  placeholder="100"
                  value={quantity}
                  onChange={e => handleQuantityChange(e.target.value)}
                  min={quantityMin}
                  max={quantityMax}
                  step={quantityStep}
                />
                <div className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-bold text-muted-foreground/30 uppercase">
                  股
                </div>
              </div>

              <div className="grid grid-cols-4 gap-1.5">
                {QUICK_QUANTITY_PRESETS.map(preset => {
                  const quickQuantity = resolveQuickQuantity({
                    availableQuantity,
                    currentOrderPrice,
                    preset,
                    tradeType,
                  });
                  const isActive =
                    quickQuantity > 0 && quantityNumber === quickQuantity;
                  const isDisabled = quickQuantity <= 0;
                  const unavailableReason =
                    availableQuantity <= 0
                      ? tradeType === 'buy'
                        ? '可用资金不足，无法填写委托数量'
                        : '当前没有可卖数量'
                      : '该快捷额度不足以形成有效委托数量';

                  return (
                    <Button
                      key={preset.label}
                      type="button"
                      variant="outline"
                      aria-pressed={isActive}
                      disabled={isDisabled}
                      title={
                        isDisabled
                          ? unavailableReason
                          : `填入 ${quickQuantity.toLocaleString()} 股`
                      }
                      className={cn(
                        'h-6 rounded-md border-slate-200/40 p-0 text-[9px] font-black text-muted-foreground transition-colors dark:border-slate-800/40',
                        isActive
                          ? 'border-primary/20 bg-primary/10 text-primary hover:border-primary/40 hover:bg-primary/20'
                          : 'bg-muted/10 hover:border-primary/40 hover:bg-muted/30'
                      )}
                      onClick={() => setQuantity(quickQuantity.toString())}
                    >
                      {preset.label}
                    </Button>
                  );
                })}
              </div>
            </div>
          </div>
        </ScrollArea>

        {/* 第四部分：费用预览与提交 */}
        <div className="space-y-3 pt-3 border-t border-slate-200/20 dark:border-slate-800/20 shrink-0">
          <div className="grid grid-cols-2 gap-3 px-1">
            <div className="flex flex-col gap-0.5">
              <span className="text-[7px] font-black text-muted-foreground/40 uppercase tracking-widest leading-none">
                预计总额
              </span>
              <span className="text-[12px] font-black font-mono text-foreground tabular-nums drop-shadow-sm">
                {formatCurrency(estimatedAmount)}
              </span>
            </div>
            <div className="flex flex-col gap-0.5 text-right">
              <span className="text-[7px] font-black text-muted-foreground/40 uppercase tracking-widest leading-none">
                预计规费
              </span>
              <span className="text-[12px] font-black font-mono text-muted-foreground/60 tabular-nums">
                {formatCurrency(estimatedFees)}
              </span>
            </div>
          </div>

          <Button
            type="submit"
            className={cn(
              'w-full h-10 text-[10px] font-black uppercase tracking-[0.2em] rounded-xl shadow-lg transition-all duration-300 active:scale-[0.98]',
              tradeType === 'buy'
                ? 'bg-market-buy-cta text-white hover:bg-market-buy-cta/90 shadow-market-buy-cta/20 hover:shadow-market-buy-cta/40'
                : 'bg-market-down text-white hover:bg-market-down/90 shadow-market-down/20 hover:shadow-market-down/40'
            )}
            disabled={
              !selectedStock ||
              !canSubmitPrice ||
              !canSubmitQuantity ||
              isSubmitting
            }
          >
            {isSubmitting ? (
              <div className="flex items-center gap-2">
                <div className="w-2.5 h-2.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                <span>处理中...</span>
              </div>
            ) : (
              `确认${tradeType === 'buy' ? '买入' : '平仓'}`
            )}
          </Button>
        </div>
      </form>
    </Card>
  );
}
