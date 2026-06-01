import { Columns, PanelLeft, History, X } from 'lucide-react';
import * as React from 'react';

import { TradingChart } from '@/components/trading-chart';
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useIsMobile } from '@/hooks/use-mobile';
import { cn } from '@/utils/cn';

import { useCurrentAccount } from '../../dashboard/hooks';
import { AccountInfo } from '../components/AccountInfo';
import { ActiveOrders } from '../components/ActiveOrders';
import { MarketDepth } from '../components/MarketDepth';
import { OrderRecords } from '../components/OrderRecords';
import { TradeRecords } from '../components/TradeRecords';
import { TradingCard } from '../components/TradingCard';
import { useTodayOrders } from '../hooks';

import MobileTradingPage from './MobileTradingPage';

export default function TradingPage() {
  const isMobile = useIsMobile();
  const itemsPerPage = 10;
  const [selectedStock, setSelectedStock] = React.useState<unknown>(null);
  const [leftView, setLeftView] = React.useState<'chart' | 'assets'>('chart');
  const [layoutMode, setLayoutMode] = React.useState<'standard' | 'wide'>(
    'wide'
  );
  const [isHistoryOpen, setIsHistoryOpen] = React.useState(false);
  const [priceUpdate, setPriceUpdate] = React.useState<{
    price: string;
    timestamp: number;
  } | null>(null);

  // 账户数据
  const { data: accountData } = useCurrentAccount();

  // 活跃委托数据 - 用于控制面板显示/隐藏
  const { orders } = useTodayOrders(accountData?.currentAccount?.id);
  const hasActiveOrders = React.useMemo(() => {
    return (orders || []).some((o: any) =>
      ['UNREPORTED', 'WAIT_REPORTING', 'REPORTED', 'PART_SUCC'].includes(
        o.status
      )
    );
  }, [orders]);

  if (isMobile) {
    return <MobileTradingPage />;
  }

  return (
    <div className="h-[calc(100vh-140px)] w-full overflow-hidden flex flex-col bg-slate-50/90 dark:bg-slate-950/95 backdrop-blur-3xl border border-slate-200/40 dark:border-slate-800/40 rounded-2xl shadow-[0_32px_64px_-16px_rgba(0,0,0,0.3)] dark:shadow-[0_32px_64px_-16px_rgba(0,0,0,0.6)] relative max-w-[calc(100%-2rem)]">
      <div className="flex items-center justify-between px-5 h-12 border-b border-slate-200/30 dark:border-slate-800/30 bg-slate-100/20 dark:bg-slate-900/20 shrink-0 z-20">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="relative">
              <div className="w-2.5 h-2.5 rounded-full bg-primary animate-pulse" />
              <div className="absolute inset-0 w-2.5 h-2.5 rounded-full bg-primary blur-[4px] animate-pulse opacity-60" />
            </div>
            <h3 className="text-[11px] font-black tracking-[0.2em] uppercase text-foreground/80">
              QuantX 交易终端
            </h3>
          </div>
          <div className="h-5 w-[1px] bg-border/40" />
          <div
            className={cn(
              'flex gap-3 items-center px-3 py-1 rounded-lg transition-all duration-300 group',
              leftView === 'assets'
                ? 'bg-blue-500/10 ring-1 ring-blue-500/20 shadow-sm shadow-blue-500/10'
                : 'hover:bg-slate-100/40 dark:hover:bg-slate-900/40'
            )}
            onClick={() =>
              setLeftView(leftView === 'assets' ? 'chart' : 'assets')
            }
          >
            <span className="text-[10px] uppercase font-black text-muted-foreground/60 group-hover:text-primary transition-colors">
              账户
            </span>
            <span
              className={cn(
                'text-[10px] font-mono font-bold transition-colors tabular-nums',
                leftView === 'assets'
                  ? 'text-primary'
                  : 'text-muted-foreground/80 group-hover:text-foreground'
              )}
            >
              {accountData?.currentAccount?.accountName || 'DEMO_PRO_001'}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsHistoryOpen(!isHistoryOpen)}
            className={cn(
              'h-9 w-9 flex items-center justify-center rounded-xl transition-all duration-300',
              isHistoryOpen
                ? 'bg-primary/10 text-primary ring-1 ring-primary/20'
                : 'bg-slate-100/20 dark:bg-slate-900/20 text-muted-foreground hover:bg-slate-100/40 dark:hover:bg-slate-900/40 hover:text-foreground'
            )}
            title="交易记录"
          >
            <History className="w-4 h-4" />
          </button>

          <div className="h-5 w-[1px] bg-border/40" />

          <button
            onClick={() =>
              setLayoutMode(layoutMode === 'wide' ? 'standard' : 'wide')
            }
            className={cn(
              'h-9 w-9 flex items-center justify-center rounded-xl transition-all duration-300',
              layoutMode === 'wide'
                ? 'bg-primary/10 text-primary'
                : 'bg-slate-100/20 dark:bg-slate-900/20 text-muted-foreground hover:bg-slate-100/40 dark:hover:bg-slate-900/40 hover:text-foreground'
            )}
            title={layoutMode === 'wide' ? '切换至两栏布局' : '切换至三栏布局'}
          >
            {layoutMode === 'wide' ? (
              <Columns className="w-4 h-4" />
            ) : (
              <PanelLeft className="w-4 h-4" />
            )}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <ResizablePanelGroup direction="horizontal" className="h-full">
          {/* Left Column: Chart - Always visible */}
          <ResizablePanel
            id="trading-left-chart"
            order={1}
            defaultSize={layoutMode === 'wide' ? 60 : 78}
            minSize={40}
            className="relative overflow-hidden bg-slate-100/10 dark:bg-slate-900/10"
          >
            <div className="h-full w-full">
              <TradingChart
                stockCode={
                  typeof selectedStock === 'string'
                    ? selectedStock
                    : (selectedStock as any)?.stockCode ||
                      (selectedStock as any)?.id // No default here
                }
              />
            </div>

            {/* Overlays */}
            {leftView === 'assets' && (
              <div className="absolute inset-4 z-40 bg-slate-50 dark:bg-slate-950 backdrop-blur-2xl rounded-2xl border border-slate-200/40 dark:border-slate-800/40 shadow-2xl animate-in fade-in slide-in-from-top-4 duration-500 p-8 overflow-y-auto custom-scrollbar">
                <div className="max-w-5xl mx-auto">
                  <div className="flex items-center justify-between mb-8">
                    <div className="flex flex-col">
                      <h2 className="text-2xl font-black tracking-tight text-foreground/90">
                        账户资产
                      </h2>
                      <p className="text-xs text-muted-foreground font-medium uppercase tracking-widest mt-1">
                        实时持仓估值与保证金状态进度
                      </p>
                    </div>
                    <button
                      onClick={() => setLeftView('chart')}
                      className="px-4 py-2 rounded-xl bg-muted/40 text-[10px] uppercase font-black text-muted-foreground hover:text-primary hover:bg-primary/10 transition-all duration-300 border border-transparent hover:border-primary/20"
                    >
                      关闭
                    </button>
                  </div>
                  <AccountInfo summary={accountData?.currentAccount} />
                </div>
              </div>
            )}
          </ResizablePanel>

          <ResizableHandle className="w-[1px] bg-slate-200/20 dark:bg-slate-800/20 hover:bg-blue-500/40 transition-colors duration-500" />

          {/* Conditional Layout Rendering */}
          {layoutMode === 'wide' ? (
            <>
              {/* Middle Column: Market Depth */}
              <ResizablePanel
                id="trading-mid-depth"
                order={2}
                defaultSize={20}
                minSize={15}
                className="bg-slate-50/40 dark:bg-slate-950/40 border-r border-slate-200/30 dark:border-slate-800/30 relative overflow-hidden"
              >
                <div className="h-full w-full overflow-hidden">
                  <MarketDepth
                    selectedStock={selectedStock}
                    onPriceSelect={p =>
                      setPriceUpdate({ price: p, timestamp: Date.now() })
                    }
                  />
                </div>
              </ResizablePanel>

              <ResizableHandle className="w-[1px] bg-slate-200/20 dark:bg-slate-800/20 hover:bg-blue-500/40 transition-colors duration-500" />

              {/* Right Column: Trading & Orders */}
              <ResizablePanel
                id="trading-right-trade"
                order={3}
                defaultSize={20}
                minSize={15}
                className="bg-slate-100/10 dark:bg-slate-900/10 flex flex-col overflow-hidden"
              >
                <ResizablePanelGroup
                  direction="vertical"
                  className="h-full w-full"
                >
                  <ResizablePanel
                    id="trading-right-trade-internal"
                    order={1}
                    defaultSize={hasActiveOrders ? 65 : 100}
                    minSize={40}
                    className="relative overflow-hidden"
                  >
                    <div className="h-full w-full overflow-y-auto custom-scrollbar bg-slate-50/20 dark:bg-slate-950/20">
                      <TradingCard
                        onStockSelect={setSelectedStock}
                        priceUpdate={priceUpdate}
                      />
                    </div>
                  </ResizablePanel>

                  {hasActiveOrders && (
                    <>
                      <ResizableHandle className="h-[1px] bg-slate-200/20 dark:bg-slate-800/20 hover:bg-blue-500/40 transition-colors duration-500" />
                      <ResizablePanel
                        id="trading-right-orders-internal"
                        order={2}
                        defaultSize={35}
                        minSize={20}
                        className="relative overflow-hidden bg-white/5 dark:bg-black/5"
                      >
                        <div className="h-full w-full overflow-hidden">
                          <ActiveOrders
                            accountId={accountData?.currentAccount?.id}
                          />
                        </div>
                      </ResizablePanel>
                    </>
                  )}
                </ResizablePanelGroup>
              </ResizablePanel>
            </>
          ) : (
            /* Standard 2-Column Layout (Right Sidebar) */
            <ResizablePanel
              id="trading-right-combined"
              order={2}
              defaultSize={22}
              minSize={20}
              className="bg-slate-100/10 dark:bg-slate-900/10 flex flex-col overflow-hidden border-l border-slate-200/30 dark:border-slate-800/30"
            >
              <ResizablePanelGroup
                direction="vertical"
                className="h-full w-full"
              >
                <ResizablePanel
                  id="trading-right-combined-depth"
                  order={1}
                  defaultSize={30}
                  minSize={10}
                  className="bg-slate-50/40 dark:bg-slate-950/40 relative overflow-hidden"
                >
                  <div className="h-full w-full overflow-hidden">
                    <MarketDepth
                      selectedStock={selectedStock}
                      onPriceSelect={p =>
                        setPriceUpdate({ price: p, timestamp: Date.now() })
                      }
                    />
                  </div>
                </ResizablePanel>

                <ResizableHandle className="h-[1px] bg-slate-200/20 dark:bg-slate-800/20 hover:bg-blue-500/40 transition-colors duration-500" />

                <ResizablePanel
                  id="trading-right-combined-trade"
                  order={2}
                  defaultSize={hasActiveOrders ? 50 : 70}
                  minSize={30}
                  className="relative overflow-hidden"
                >
                  <div className="h-full w-full overflow-y-auto custom-scrollbar bg-slate-50/20 dark:bg-slate-950/20">
                    <TradingCard
                      onStockSelect={setSelectedStock}
                      priceUpdate={priceUpdate}
                    />
                  </div>
                </ResizablePanel>

                {hasActiveOrders && (
                  <>
                    <ResizableHandle className="h-[1px] bg-slate-200/20 dark:bg-slate-800/20 hover:bg-blue-500/40 transition-colors duration-500" />
                    <ResizablePanel
                      id="trading-right-combined-orders"
                      order={3}
                      defaultSize={20}
                      minSize={10}
                      className="relative overflow-hidden bg-white/5 dark:bg-black/5"
                    >
                      <div className="h-full w-full overflow-hidden">
                        <ActiveOrders
                          accountId={accountData?.currentAccount?.id}
                        />
                      </div>
                    </ResizablePanel>
                  </>
                )}
              </ResizablePanelGroup>
            </ResizablePanel>
          )}
        </ResizablePanelGroup>

        {/* History Overlay Panel */}
        {isHistoryOpen && (
          <div className="absolute inset-4 z-50 bg-slate-50/95 dark:bg-slate-950/95 backdrop-blur-xl rounded-2xl border border-slate-200/40 dark:border-slate-800/40 shadow-2xl animate-in fade-in slide-in-from-bottom-2 duration-300 overflow-hidden flex flex-col items-center">
            <div className="w-full max-w-7xl h-full flex flex-col p-6">
              <div className="flex items-center justify-between mb-6 shrink-0">
                <div className="flex flex-col gap-1">
                  <h2 className="text-xl font-black tracking-tight text-foreground/90">
                    交易中心
                  </h2>
                  <p className="text-xs text-muted-foreground font-medium uppercase tracking-widest">
                    所有的委托与成交历史记录
                  </p>
                </div>
                <button
                  onClick={() => setIsHistoryOpen(false)}
                  className="w-8 h-8 flex items-center justify-center rounded-full bg-muted/40 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <Tabs
                defaultValue="today_orders"
                className="flex-1 flex flex-col overflow-hidden"
              >
                <TabsList className="w-fit h-10 bg-slate-100/80 dark:bg-slate-900/80 p-1 mb-6 rounded-xl border border-slate-200/50 dark:border-slate-800/50 backdrop-blur-sm">
                  <TabsTrigger
                    value="today_orders"
                    className="h-full rounded-lg px-4 text-[11px] font-bold uppercase tracking-wider data-[state=active]:bg-white dark:data-[state=active]:bg-blue-600 data-[state=active]:text-blue-600 dark:data-[state=active]:text-white data-[state=active]:shadow-sm transition-all duration-300 text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200"
                  >
                    当日委托
                  </TabsTrigger>
                  <TabsTrigger
                    value="today_trades"
                    className="h-full rounded-lg px-4 text-[11px] font-bold uppercase tracking-wider data-[state=active]:bg-white dark:data-[state=active]:bg-blue-600 data-[state=active]:text-blue-600 dark:data-[state=active]:text-white data-[state=active]:shadow-sm transition-all duration-300 text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200"
                  >
                    当日成交
                  </TabsTrigger>
                  <TabsTrigger
                    value="history_orders"
                    className="h-full rounded-lg px-4 text-[11px] font-bold uppercase tracking-wider data-[state=active]:bg-white dark:data-[state=active]:bg-blue-600 data-[state=active]:text-blue-600 dark:data-[state=active]:text-white data-[state=active]:shadow-sm transition-all duration-300 text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200"
                  >
                    历史委托
                  </TabsTrigger>
                  <TabsTrigger
                    value="history_trades"
                    className="h-full rounded-lg px-4 text-[11px] font-bold uppercase tracking-wider data-[state=active]:bg-white dark:data-[state=active]:bg-blue-600 data-[state=active]:text-blue-600 dark:data-[state=active]:text-white data-[state=active]:shadow-sm transition-all duration-300 text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200"
                  >
                    历史成交
                  </TabsTrigger>
                </TabsList>

                <TabsContent
                  value="today_orders"
                  className="flex-1 overflow-hidden mt-0 data-[state=inactive]:hidden"
                >
                  <OrderRecords
                    accountId={accountData?.currentAccount?.id}
                    viewMode="table"
                    filterType="all"
                  />
                </TabsContent>

                <TabsContent
                  value="today_trades"
                  className="flex-1 overflow-hidden mt-0 data-[state=inactive]:hidden"
                >
                  <TradeRecords
                    initialTimeFilter="today"
                    itemsPerPage={itemsPerPage}
                  />
                </TabsContent>

                <TabsContent
                  value="history_orders"
                  className="flex-1 overflow-hidden mt-0 data-[state=inactive]:hidden"
                >
                  <OrderRecords
                    accountId={accountData?.currentAccount?.id}
                    viewMode="table"
                    filterType="history"
                  />
                </TabsContent>

                <TabsContent
                  value="history_trades"
                  className="flex-1 overflow-hidden mt-0 data-[state=inactive]:hidden"
                >
                  <TradeRecords
                    initialTimeFilter="30days"
                    itemsPerPage={itemsPerPage}
                  />
                </TabsContent>
              </Tabs>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
