import * as React from 'react';

import { StockSelector } from '@/components/StockSelector';
import { TradingChart } from '@/components/trading-chart';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import { useStockSearch } from '@/hooks/useStockSearch';
import { formatCurrency } from '@/shared/utils/format';
import { cn } from '@/utils/cn';

import { OrderRecords } from '../components/OrderRecords';
import { useFormState } from '../components/TradingCard/hooks/useFormState';
import { useTradingCalculation } from '../components/TradingCard/hooks/useTradingCalculation';
import { useTradingSubmit } from '../components/TradingCard/hooks/useTradingSubmit';

export default function MobileTradingPage() {
  const [activeTab, setActiveTab] = React.useState('trade');
  const { data: accountData } = useCurrentAccount();
  const userId = 'demo-user';

  // ---------------------------------------------------------------------------
  // Trading Logic (Reused from TradingCard)
  // ---------------------------------------------------------------------------
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
  } = useFormState();

  const { holdings, portfolioSummary } = useHoldings();

  const {
    selectedStock,
    searchQuery,
    setSearchQuery,
    filteredStocks,
    handleStockSelect,
  } = useStockSearch(holdings);

  // Initialize with first holding if available
  const hasAutoSelectedRef = React.useRef(false);
  React.useEffect(() => {
    if (!hasAutoSelectedRef.current && !selectedStock && holdings.length > 0) {
      const first = holdings[0];
      handleStockSelect(
        {
          ...first,
          id: first.stockCode,
          stockCode: first.stockCode,
          name: first.instrumentName || '',
          quote: {
            lastPrice: first.lastPrice || 0,
            changePercent: first.profitRate || 0,
          },
        },
        setPrice
      );
      hasAutoSelectedRef.current = true;
    }
  }, [holdings, selectedStock, handleStockSelect, setPrice]);

  const { estimatedAmount } = useTradingCalculation(quantity, price);

  const { handleSubmit, isSubmitting } = useTradingSubmit(userId, () => {
    resetForm();
    // Optional: Show toast
  });

  const handlePercentClick = (percent: number) => {
    const maxQty = 10000; // Mock max quantity
    setQuantity(Math.floor(maxQty * percent).toString());
  };

  const priceChange = selectedStock?.quote?.changePercent ?? 0;
  const isUp = priceChange >= 0;

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Mobile Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-card/50 backdrop-blur-md sticky top-0 z-10">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary font-bold text-xs ring-2 ring-primary/20">
            QX
          </div>
          <div>
            <h1 className="text-sm font-bold leading-none">QuantX Mobile</h1>
            <p className="text-[10px] text-muted-foreground mt-0.5 font-mono">
              {accountData?.currentAccount?.accountName || 'DEMO_ACC'}
            </p>
          </div>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
            Asset
          </span>
          <span className="text-xs font-mono font-bold text-foreground">
            {formatCurrency(portfolioSummary?.totalAsset || 0)}
          </span>
        </div>
      </div>

      {/* Main Content Area */}
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex-1 flex flex-col overflow-hidden"
      >
        {/* Top Navigation (Segmented Control) */}
        <div className="px-4 py-2 shrink-0">
          <TabsList className="w-full h-9 bg-muted/50 p-1 rounded-lg grid grid-cols-3">
            <TabsTrigger
              value="chart"
              className="rounded-md text-[10px] font-bold uppercase tracking-wider data-[state=active]:bg-background data-[state=active]:text-primary data-[state=active]:shadow-sm transition-all"
            >
              Chart
            </TabsTrigger>
            <TabsTrigger
              value="trade"
              className="rounded-md text-[10px] font-bold uppercase tracking-wider data-[state=active]:bg-background data-[state=active]:text-primary data-[state=active]:shadow-sm transition-all"
            >
              Trade
            </TabsTrigger>
            <TabsTrigger
              value="orders"
              className="rounded-md text-[10px] font-bold uppercase tracking-wider data-[state=active]:bg-background data-[state=active]:text-primary data-[state=active]:shadow-sm transition-all"
            >
              Orders
            </TabsTrigger>
          </TabsList>
        </div>

        <div className="flex-1 overflow-y-auto">
          {/* Tab: Trade */}
          <TabsContent
            value="trade"
            className="m-0 min-h-full pb-20 p-4 space-y-6"
          >
            {/* Stock Selector Area */}
            <div className="space-y-2">
              <label className="text-xs font-medium text-muted-foreground ml-1">
                Instrument
              </label>
              <StockSelector
                searchQuery={searchQuery}
                setSearchQuery={setSearchQuery}
                filteredStocks={filteredStocks}
                onStockSelect={s => handleStockSelect(s, setPrice)}
                selectedStock={selectedStock}
                className="w-full"
              />
              {selectedStock && (
                <div className="flex items-center justify-between px-2 py-1 bg-muted/20 rounded-md">
                  <span className="text-xs text-muted-foreground">
                    Current Price
                  </span>
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        'text-lg font-mono font-bold',
                        isUp ? 'text-emerald-500' : 'text-rose-500'
                      )}
                    >
                      {selectedStock?.quote?.lastPrice?.toFixed(2)}
                    </span>
                    <span
                      className={cn(
                        'text-xs font-mono px-1.5 py-0.5 rounded',
                        isUp
                          ? 'bg-emerald-500/10 text-emerald-500'
                          : 'bg-rose-500/10 text-rose-500'
                      )}
                    >
                      {isUp ? '+' : ''}
                      {priceChange}%
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Buy/Sell Switcher */}
            <div className="bg-muted/30 p-1 rounded-xl flex relative">
              <div
                className={cn(
                  'absolute inset-y-1 w-[calc(50%-4px)] rounded-lg transition-all duration-300 shadow-sm',
                  tradeType === 'buy'
                    ? 'left-1 bg-rose-500'
                    : 'left-[calc(50%)] bg-emerald-500'
                )}
              />

              <button
                onClick={() => setTradeType('buy')}
                className={cn(
                  'flex-1 py-3 text-sm font-bold z-10 transition-colors uppercase tracking-widest',
                  tradeType === 'buy' ? 'text-white' : 'text-muted-foreground'
                )}
              >
                Long / Buy
              </button>
              <button
                onClick={() => setTradeType('sell')}
                className={cn(
                  'flex-1 py-3 text-sm font-bold z-10 transition-colors uppercase tracking-widest',
                  tradeType === 'sell' ? 'text-white' : 'text-muted-foreground'
                )}
              >
                Short / Sell
              </button>
            </div>

            {/* Main Inputs */}
            <div className="space-y-4">
              {/* Price Input */}
              <div className="space-y-2">
                <div className="flex justify-between items-center px-1">
                  <label className="text-xs font-medium text-muted-foreground">
                    Price (CNY)
                  </label>
                  <div className="flex gap-2 text-[10px] font-bold">
                    <span
                      className="text-rose-500"
                      onClick={() =>
                        setPrice(
                          ((parseFloat(price || '0') || 0) * 0.9).toFixed(2)
                        )
                      }
                    >
                      Limit Down
                    </span>
                    <span
                      className="text-emerald-500"
                      onClick={() =>
                        setPrice(
                          ((parseFloat(price || '0') || 0) * 1.1).toFixed(2)
                        )
                      }
                    >
                      Limit Up
                    </span>
                  </div>
                </div>
                <div className="flex gap-2">
                  <Select
                    value={orderType}
                    onValueChange={(v: string) => setOrderType(v)}
                  >
                    <SelectTrigger className="h-12 w-24 bg-card border rounded-xl px-2 text-xs font-bold shadow-none ring-0 focus:ring-0">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="limit">Limit</SelectItem>
                      <SelectItem value="market">Market</SelectItem>
                    </SelectContent>
                  </Select>
                  <div className="flex-1 relative">
                    <Input
                      type="number"
                      value={price}
                      onChange={e => setPrice(e.target.value)}
                      className="h-12 text-lg font-mono font-bold text-right pr-4 bg-card rounded-xl"
                      placeholder="0.00"
                    />
                  </div>
                </div>
              </div>

              {/* Quantity Input */}
              <div className="space-y-2">
                <div className="flex justify-between items-center px-1">
                  <label className="text-xs font-medium text-muted-foreground">
                    Quantity
                  </label>
                  <span className="text-[10px] text-muted-foreground">
                    Max: 10000
                  </span>
                </div>
                <div className="relative">
                  <Input
                    type="number"
                    value={quantity}
                    onChange={e => setQuantity(e.target.value)}
                    className="h-12 text-lg font-mono font-bold text-right pr-12 bg-card rounded-xl"
                    placeholder="0"
                  />
                  <span className="absolute right-4 top-1/2 -translate-y-1/2 text-xs text-muted-foreground">
                    Vol
                  </span>
                </div>

                {/* Percent Grid */}
                <div className="grid grid-cols-4 gap-2">
                  {[0.25, 0.5, 0.75, 1].map(p => (
                    <button
                      key={p}
                      onClick={() => handlePercentClick(p)}
                      className="py-1.5 rounded-lg bg-muted/40 text-xs font-bold text-muted-foreground hover:bg-primary/10 hover:text-primary transition-colors"
                    >
                      {p * 100}%
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Summary */}
            <div className="bg-card border rounded-xl p-4 flex justify-between items-center shadow-sm">
              <div className="flex flex-col">
                <span className="text-[10px] uppercase text-muted-foreground font-bold">
                  Est. Amount
                </span>
                <span className="text-lg font-mono font-bold">
                  {formatCurrency(estimatedAmount)}
                </span>
              </div>
              <div className="flex flex-col items-end">
                <span className="text-[10px] uppercase text-muted-foreground font-bold">
                  Details
                </span>
                <span className="text-xs text-muted-foreground">Fee: --</span>
              </div>
            </div>

            {/* Action Button - Floating looking but static in scroll */}
            <Button
              size="lg"
              className={cn(
                'w-full h-14 text-lg font-black uppercase tracking-widest shadow-xl transition-all active:scale-[0.98]',
                tradeType === 'buy'
                  ? 'bg-rose-500 hover:bg-rose-600 shadow-rose-500/20'
                  : 'bg-emerald-500 hover:bg-emerald-600 shadow-emerald-500/20'
              )}
              onClick={e =>
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
              disabled={isSubmitting || !selectedStock || !price || !quantity}
            >
              {isSubmitting
                ? 'Processing...'
                : `${tradeType === 'buy' ? 'Buy' : 'Sell'} ${selectedStock?.stockCode || ''}`}
            </Button>
          </TabsContent>

          {/* Tab: Chart */}
          <TabsContent value="chart" className="m-0 h-full">
            <div className="h-full w-full p-2">
              <div className="h-[400px] w-full bg-card rounded-xl overflow-hidden border">
                <TradingChart stockCode={selectedStock?.stockCode} />
              </div>
              <div className="mt-4 px-2">
                <h3 className="text-sm font-bold mb-2">Market Depth</h3>
                {/* Simplified Depth Placeholders */}
                <div className="space-y-1 opacity-60">
                  <div className="flex justify-between text-xs text-rose-500">
                    <span className="text-muted-foreground">Ask 5</span>{' '}
                    <span>102.50</span> <span>500</span>
                  </div>
                  <div className="flex justify-between text-xs text-rose-500">
                    <span className="text-muted-foreground">Ask 1</span>{' '}
                    <span>102.10</span> <span>1200</span>
                  </div>
                  <div className="h-px bg-border my-1" />
                  <div className="flex justify-between text-xs text-emerald-500">
                    <span className="text-muted-foreground">Bid 1</span>{' '}
                    <span>102.00</span> <span>3000</span>
                  </div>
                  <div className="flex justify-between text-xs text-emerald-500">
                    <span className="text-muted-foreground">Bid 5</span>{' '}
                    <span>101.50</span> <span>800</span>
                  </div>
                </div>
              </div>
            </div>
          </TabsContent>

          {/* Tab: Orders */}
          <TabsContent
            value="orders"
            className="m-0 h-full overflow-hidden flex flex-col"
          >
            <div className="flex-1 overflow-y-auto px-2 py-4">
              <h2 className="text-lg font-bold px-2 mb-4">Active Orders</h2>
              <OrderRecords accountId={accountData?.currentAccount?.id} />
            </div>
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
