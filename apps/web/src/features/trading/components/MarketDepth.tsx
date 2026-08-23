import { Bot, CandlestickChart, Copy, DollarSign } from 'lucide-react';
import type { MouseEvent as ReactMouseEvent } from 'react';
import { gql as urqlGql, useSubscription } from 'urql';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Card } from '@/components/ui/card';
import { gql } from '@/generated/gql';
import {
  financialDirection,
  financialToneBadgeClass,
  financialToneClass,
} from '@/shared/utils/financialColors';

import {
  type DepthLevel,
  formatDepthVolume,
  type MarketDepthStockLike,
  resolveMarketSnapshot,
  resolveStockCode,
  resolveStockName,
} from './marketDepthUtils';

interface MarketDepthProps {
  selectedStock: MarketDepthStockLike | string | null;
  onPriceSelect?: (price: string) => void;
}

interface DepthMenuPayload {
  level?: number;
  price?: number | null;
  side: 'bid' | 'ask' | 'quote' | 'stock';
  stockCode: string;
  stockName: string;
}

interface MarketDepthData {
  marketDepth?: {
    asks?: DepthLevel[];
    bids?: DepthLevel[];
    stockCode?: string;
    time?: string;
  };
}

interface MarketDepthTickData {
  marketTicks?: {
    lastPrice?: number | null;
    preClose?: number | null;
    stockCode?: string;
    time?: string;
  };
}

const MarketDepthSubscription = gql(`
  subscription Trading_MarketDepth($stockList: [String!]!, $levels: Int = 5) {
    marketDepth(stockList: $stockList, levels: $levels) {
      stockCode
      time
      bids {
        price
        volume
      }
      asks {
        price
        volume
      }
    }
  }
`);

const MarketDepthTickSubscription = urqlGql`
  subscription Trading_MarketDepthTick($stockList: [String!]!) {
    marketTicks(stockList: $stockList) {
      stockCode
      time
      lastPrice
      preClose
    }
  }
`;

const formatPrice = (value: number | null | undefined) =>
  value && value > 0 ? value.toFixed(value >= 10 ? 2 : 3) : '--';

const formatSignedPercent = (value: number | null) =>
  value === null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;

const getLevel = (levels: DepthLevel[], level: number) => levels[level - 1];

function copyText(text: string) {
  if (!navigator.clipboard || !text) return;
  void navigator.clipboard.writeText(text);
}

export function MarketDepth({
  selectedStock,
  onPriceSelect,
}: MarketDepthProps) {
  const [, setLocation] = useLocation();
  const { closeMenu, menu, openAtPointer } = useStudioMenu<DepthMenuPayload>();
  const stockCode = resolveStockCode(selectedStock);
  const stockName = resolveStockName(selectedStock, stockCode);
  const [depthResult] = useSubscription<MarketDepthData>({
    query: MarketDepthSubscription,
    variables: { stockList: stockCode ? [stockCode] : [], levels: 5 },
    pause: !stockCode,
  });
  const [tickResult] = useSubscription<MarketDepthTickData>({
    query: MarketDepthTickSubscription,
    variables: { stockList: stockCode ? [stockCode] : [] },
    pause: !stockCode,
  });

  const marketDepth = depthResult.data?.marketDepth;
  const marketTick =
    tickResult.data?.marketTicks?.stockCode === stockCode
      ? tickResult.data.marketTicks
      : null;
  const asks = (marketDepth?.asks || []) as DepthLevel[];
  const bids = (marketDepth?.bids || []) as DepthLevel[];
  const bestAsk = asks[0]?.price ?? null;
  const bestBid = bids[0]?.price ?? null;
  const { changePercent, price: basePrice } = resolveMarketSnapshot({
    bestAsk,
    bestBid,
    selectedStock,
    tick: marketTick,
  });
  const priceToneClass = financialToneClass(changePercent);
  const priceBadgeClass = financialToneBadgeClass(changePercent);
  const priceIndicatorClass =
    financialDirection(changePercent) === 'up'
      ? 'bg-market-up'
      : financialDirection(changePercent) === 'down'
        ? 'bg-market-down'
        : 'bg-market-flat';
  const maxVol = Math.max(
    1,
    ...asks.map(level => level.volume || 0),
    ...bids.map(level => level.volume || 0)
  );
  const isReady = Boolean(stockCode);

  const handlePriceSelect = (price?: number | null) => {
    if (price && price > 0) {
      onPriceSelect?.(formatPrice(price));
    }
  };

  const openDepthMenu = (
    event: ReactMouseEvent<Element>,
    payload: DepthMenuPayload
  ) => {
    if (!payload.stockCode) return;
    openAtPointer(event, payload);
  };

  return (
    <Card
      square
      className="card-elevated p-3 h-full flex flex-col border-none shadow-none bg-slate-50/80 dark:bg-slate-950/80 overflow-hidden px-1 animate-fade-in group"
    >
      <div
        className="flex items-center justify-between mb-3 px-2"
        onContextMenu={event =>
          openDepthMenu(event, {
            side: 'stock',
            stockCode,
            stockName,
          })
        }
      >
        <div className="flex items-center gap-2">
          <h4 className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/80">
            五档行情
          </h4>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[11px] font-mono font-bold text-foreground/90">
            {isReady ? stockName : '未选择标的'}
          </span>
          <span className="text-[9px] font-mono text-muted-foreground opacity-60">
            {isReady ? stockCode : '请选择证券'}
          </span>
        </div>
      </div>

      <div className="flex-1 flex flex-col overflow-auto font-mono text-xs custom-scrollbar">
        {/* Sell Orders (Top) - Reversed 5 -> 1 */}
        <div className="flex flex-col gap-[1px] mb-1">
          {[5, 4, 3, 2, 1].map(level => {
            const order = getLevel(asks, level);
            const volume = order?.volume || 0;
            return (
              <div
                key={`sell-${level}`}
                className="relative flex h-5.5 cursor-pointer items-center justify-between px-2 transition-colors duration-200 hover:bg-market-down/5"
                onClick={() => handlePriceSelect(order?.price)}
                onContextMenu={event =>
                  openDepthMenu(event, {
                    level,
                    price: order?.price,
                    side: 'ask',
                    stockCode,
                    stockName,
                  })
                }
              >
                <div
                  className="absolute right-0 h-[80%] my-auto bg-gradient-to-l from-market-down/20 to-transparent transition-all duration-700 rounded-l-sm"
                  style={{ width: `${(volume / maxVol) * 70}%` }}
                />

                <div className="flex items-center gap-2 z-10 w-1/4">
                  <span className="text-[10px] text-muted-foreground/60 w-6 text-left font-medium">
                    卖{level}
                  </span>
                </div>

                <span className="text-market-down font-bold z-10 w-1/3 text-right tabular-nums">
                  {formatPrice(order?.price)}
                </span>

                <span className="text-[10px] font-medium text-muted-foreground/80 z-10 w-1/3 text-right tabular-nums">
                  {formatDepthVolume(order?.volume)}
                </span>
              </div>
            );
          })}
        </div>

        {/* Current Price Bar (Middle) */}
        <div
          className="flex items-center justify-between px-3 h-9 my-1 bg-slate-100/20 dark:bg-slate-900/20 border-y border-slate-200/20 dark:border-slate-800/20 shadow-inner overflow-hidden animate-price-flash"
          onClick={() => handlePriceSelect(basePrice)}
          onContextMenu={event =>
            openDepthMenu(event, {
              price: basePrice,
              side: 'quote',
              stockCode,
              stockName,
            })
          }
        >
          <div className="flex items-baseline gap-2">
            <span
              className={`text-base font-black font-mono ${priceToneClass} tracking-tight drop-shadow-sm`}
            >
              {formatPrice(basePrice)}
            </span>
            <span
              className={`text-[10px] font-bold font-mono ${priceBadgeClass} px-1 py-0.5 rounded leading-none`}
            >
              {formatSignedPercent(changePercent)}
            </span>
          </div>
          <div className="flex flex-col items-end leading-none">
            <span className="text-[9px] text-muted-foreground/60 font-bold uppercase tracking-tighter">
              最新价
            </span>
            <div
              className={`w-1 h-1 ${priceIndicatorClass} rounded-full mt-0.5 animate-pulse`}
            />
          </div>
        </div>

        {/* Buy Orders (Bottom) - 1 -> 5 */}
        <div className="flex flex-col gap-[1px]">
          {[1, 2, 3, 4, 5].map(level => {
            const order = getLevel(bids, level);
            const volume = order?.volume || 0;
            return (
              <div
                key={`buy-${level}`}
                className="relative flex h-5.5 cursor-pointer items-center justify-between px-2 transition-colors duration-200 hover:bg-market-up/5"
                onClick={() => handlePriceSelect(order?.price)}
                onContextMenu={event =>
                  openDepthMenu(event, {
                    level,
                    price: order?.price,
                    side: 'bid',
                    stockCode,
                    stockName,
                  })
                }
              >
                <div
                  className="absolute right-0 h-[80%] my-auto bg-gradient-to-l from-market-up/20 to-transparent transition-all duration-700 rounded-l-sm"
                  style={{ width: `${(volume / maxVol) * 70}%` }}
                />
                <div className="flex items-center gap-2 z-10 w-1/4">
                  <span className="text-[10px] text-muted-foreground/60 w-6 text-left font-medium">
                    买{level}
                  </span>
                </div>

                <span className="text-market-up font-bold z-10 w-1/3 text-right tabular-nums">
                  {formatPrice(order?.price)}
                </span>

                <span className="text-[10px] font-medium text-muted-foreground/80 z-10 w-1/3 text-right tabular-nums">
                  {formatDepthVolume(order?.volume)}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      <StudioMenu
        ariaLabel="盘口上下文菜单"
        items={[
          {
            icon: <DollarSign className="h-3.5 w-3.5" />,
            id: 'fill-price',
            label: '填入价格',
            disabled: !menu?.payload?.price,
            onSelect: () => handlePriceSelect(menu?.payload?.price),
          },
          {
            icon: <CandlestickChart className="h-3.5 w-3.5" />,
            id: 'stock-detail',
            label: '查看个股详情',
            onSelect: () => {
              if (menu?.payload?.stockCode) {
                setLocation(`/stock/${menu.payload.stockCode}`);
              }
            },
          },
          {
            icon: <Bot className="h-3.5 w-3.5" />,
            id: 'create-strategy',
            label: '创建策略',
            onSelect: () => {
              if (menu?.payload?.stockCode) {
                setLocation(`/strategies/run?symbol=${menu.payload.stockCode}`);
              }
            },
          },
          { id: 'separator-copy', type: 'separator' },
          {
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-code',
            label: '复制代码',
            onSelect: () => copyText(menu?.payload?.stockCode || ''),
          },
          {
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-name',
            label: '复制名称',
            onSelect: () => copyText(menu?.payload?.stockName || ''),
          },
          {
            icon: <Copy className="h-3.5 w-3.5" />,
            id: 'copy-price',
            label: '复制价格',
            disabled: !menu?.payload?.price,
            onSelect: () =>
              copyText(
                menu?.payload?.price ? formatPrice(menu.payload.price) : ''
              ),
          },
        ]}
        menu={menu}
        onClose={closeMenu}
        width={188}
      />
    </Card>
  );
}
