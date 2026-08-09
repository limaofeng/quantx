import { Bot, CandlestickChart, Copy, FileX2, ReceiptText } from 'lucide-react';
import * as React from 'react';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import {
  addShanghaiDays,
  getShanghaiDateKey,
} from '@/components/trading-chart/utils/time-utils';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { OrderType } from '@/generated/gql/graphql';
import { formatCurrency } from '@/shared/utils/format';
import { cn } from '@/utils/cn';

import {
  useTodayOrders,
  useCancelOrder,
  useHistoryOrders,
} from '../hooks/useTrading';

type TodayOrder = ReturnType<typeof useTodayOrders>['orders'][number];
type HistoryOrder = ReturnType<typeof useHistoryOrders>['orders'][number];
type DisplayOrder = TodayOrder | HistoryOrder;

interface OrderRecordsProps {
  accountId?: string;
  viewMode?: 'list' | 'table';
  filterType?: 'active' | 'all' | 'history';
}

const CANCELABLE_ORDER_STATUSES = [
  'UNREPORTED',
  'WAIT_REPORTING',
  'REPORTED',
  'PART_SUCC',
];

function copyText(text: string) {
  if (!navigator.clipboard || !text) return;
  void navigator.clipboard.writeText(text);
}

function isOrderCancelable(order: DisplayOrder | undefined) {
  return Boolean(
    order?.status && CANCELABLE_ORDER_STATUSES.includes(order.status)
  );
}

export function OrderRecords({
  accountId,
  viewMode = 'list',
  filterType = 'active',
}: OrderRecordsProps) {
  const [, setLocation] = useLocation();
  const { closeMenu, menu, openAtPointer } = useStudioMenu<DisplayOrder>();
  const actualAccountId = accountId || '';

  const { orders: todayOrders, loading: todayLoading } =
    useTodayOrders(actualAccountId);

  // History orders (default last 30 days)
  const [dateRange] = React.useState(() => {
    const end = new Date();
    const start = addShanghaiDays(end, -30);
    return {
      startDate: getShanghaiDateKey(start),
      endDate: getShanghaiDateKey(end),
    };
  });

  const { orders: historyOrders, loading: historyLoading } = useHistoryOrders(
    filterType === 'history' ? actualAccountId : '',
    dateRange.startDate,
    dateRange.endDate
  );

  const { cancelOrder, fetching: isCancelling } = useCancelOrder();

  // Filter orders based on filterType
  const displayOrders = React.useMemo(() => {
    if (filterType === 'history') {
      return historyOrders || [];
    }

    // For today's orders
    const source = todayOrders || [];

    if (filterType === 'active') {
      return source.filter(o =>
        ['UNREPORTED', 'WAIT_REPORTING', 'REPORTED', 'PART_SUCC'].includes(
          o.status
        )
      );
    }

    // filterType === 'all' -> return everything
    return source;
  }, [todayOrders, historyOrders, filterType]);

  const isLoading = filterType === 'history' ? historyLoading : todayLoading;

  if (isLoading && displayOrders.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground/40 text-xs">
        加载中...
      </div>
    );
  }

  if (displayOrders.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-muted-foreground/40 gap-2">
        <div className="p-3 rounded-full bg-slate-100/50 dark:bg-slate-900/50">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <path d="m9 12 2 2 4-4" />
          </svg>
        </div>
        <span className="text-xs font-medium">
          暂无
          {filterType === 'history'
            ? '历史'
            : filterType === 'active'
              ? '活跃'
              : ''}
          委托记录
        </span>
      </div>
    );
  }

  // Handle cancel using numeric orderId if possible, logic handled in hook but we pass string here
  const handleCancel = async (orderId: string) => {
    if (!actualAccountId) return;
    try {
      await cancelOrder(orderId, actualAccountId);
    } catch (_error) {
      // Handle error silently
    }
  };

  const handleCancelWithConfirm = (order: DisplayOrder) => {
    const label = `${order.stockName || order.stockCode || order.id}`;
    if (!window.confirm(`确认撤销 ${label} 的委托吗？`)) return;
    void handleCancel(order.id);
  };

  const orderMenu = (
    <StudioMenu
      ariaLabel="委托记录菜单"
      items={[
        {
          icon: <ReceiptText className="h-3.5 w-3.5" />,
          id: 'copy-order-id',
          label: '复制委托 ID',
          onSelect: () => copyText(menu?.payload?.id || ''),
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
        { id: 'separator-risk', type: 'separator' },
        {
          danger: true,
          disabled: !isOrderCancelable(menu?.payload) || isCancelling,
          icon: <FileX2 className="h-3.5 w-3.5" />,
          id: 'cancel',
          label: '撤单入口',
          onSelect: () => {
            if (menu?.payload) handleCancelWithConfirm(menu.payload);
          },
        },
      ]}
      menu={menu}
      onClose={closeMenu}
      width={188}
    />
  );

  if (viewMode === 'table') {
    return (
      <div className="h-full overflow-hidden flex flex-col">
        <div className="flex-1 overflow-auto custom-scrollbar border border-border/50 rounded-md bg-muted/5">
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 bg-muted/50 z-10">
              <tr>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                  {filterType === 'history' ? '日期' : '时间'}
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                  证券代码
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                  证券名称
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                  方向
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b text-right">
                  委托价格
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b text-right">
                  委托数量
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b text-right">
                  已成交
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                  状态
                </th>
                <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b text-right">
                  操作
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/50">
              {displayOrders.map(order => (
                <tr
                  key={order.id}
                  onContextMenu={event => openAtPointer(event, order)}
                  className="hover:bg-muted/30 transition-colors group"
                >
                  <td className="px-3 py-1 text-[10px] font-mono text-muted-foreground tabular-nums">
                    {order.time}
                  </td>
                  <td className="px-3 py-1 text-[11px] font-mono font-bold text-foreground/80">
                    {order.stockCode}
                  </td>
                  <td className="px-3 py-1 text-[11px] font-bold text-foreground">
                    {order.stockName}
                  </td>
                  <td className="px-3 py-1">
                    <span
                      className={cn(
                        'px-1.5 py-0.5 rounded-md text-[9px] font-black uppercase tracking-tighter shrink-0',
                        order.type === OrderType.Buy
                          ? 'bg-rose-500/10 text-destructive'
                          : 'bg-emerald-500/10 text-success'
                      )}
                    >
                      {order.type === OrderType.Buy ? '买入' : '卖出'}
                    </span>
                  </td>
                  <td className="px-3 py-1 text-[11px] font-mono font-bold text-right tabular-nums text-foreground/80">
                    {formatCurrency(order.price)}
                  </td>
                  <td className="px-3 py-1 text-[11px] font-mono font-bold text-right text-muted-foreground tabular-nums opacity-60">
                    {order.volume}
                  </td>
                  <td className="px-3 py-1 text-[11px] font-mono font-bold text-right tabular-nums">
                    <span
                      className={
                        order.tradedVolume > 0
                          ? 'text-primary'
                          : 'text-muted-foreground/30'
                      }
                    >
                      {order.tradedVolume}
                    </span>
                  </td>
                  <td className="px-3 py-1 text-[10px] font-bold text-muted-foreground uppercase tracking-tight tabular-nums">
                    {order.status}
                  </td>
                  <td className="px-3 py-1 text-right">
                    {isOrderCancelable(order) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-[10px] text-destructive hover:text-white hover:bg-destructive transition-all duration-300"
                        disabled={isCancelling}
                        onClick={e => {
                          e.stopPropagation();
                          handleCancel(order.id);
                        }}
                      >
                        撤单
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {orderMenu}
      </div>
    );
  }

  return (
    <Card
      square
      className="card-elevated h-full flex flex-col border-none shadow-none bg-slate-50/80 dark:bg-slate-950/80 overflow-hidden px-1 animate-slide-up"
    >
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-slate-200/20 dark:border-slate-800/20 shrink-0 min-h-[40px]">
        <h4 className="text-[10px] font-bold text-muted-foreground/80 uppercase tracking-widest flex items-center gap-2">
          {filterType === 'history'
            ? '历史委托'
            : filterType === 'all'
              ? '当日委托'
              : '当前委托'}
          <span className="px-1.5 py-0.5 bg-blue-500/10 text-primary rounded-full text-[9px] font-mono">
            {displayOrders.length}
          </span>
        </h4>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar p-1.5 space-y-2">
        {displayOrders.map(order => (
          <div
            key={order.id}
            onContextMenu={event => openAtPointer(event, order)}
            className={cn(
              'relative flex items-center justify-between p-2.5 rounded-xl bg-card dark:bg-slate-900 border border-slate-200/40 dark:border-slate-800/40 text-xs shadow-sm hover:shadow hover:bg-card/90 dark:hover:bg-slate-800 transition-all duration-300 group overflow-hidden pl-3',
              order.type === OrderType.Buy
                ? 'border-l-4 border-l-destructive shadow-rose-500/5'
                : 'border-l-4 border-l-success shadow-emerald-500/5'
            )}
          >
            <div className="flex flex-col gap-2 z-10 w-full">
              <div className="flex items-center justify-between pr-7 gap-2">
                <div className="flex items-center gap-1.5 min-w-0 flex-1">
                  <span
                    className={cn(
                      'px-1.5 py-0.5 rounded-md text-[9px] font-black uppercase tracking-tighter shrink-0',
                      order.type === OrderType.Buy
                        ? 'bg-rose-500/10 text-destructive'
                        : 'bg-emerald-500/10 text-success'
                    )}
                  >
                    {order.type === OrderType.Buy ? '买入' : '卖出'}
                  </span>
                  <span className="font-bold text-[12px] text-foreground/90 tracking-tight truncate">
                    {order.stockName}
                  </span>
                  <span className="text-[9px] font-mono text-muted-foreground/40 font-medium shrink-0">
                    {order.stockCode}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-4 text-[11px] font-mono pl-0.5 w-full">
                <div className="flex flex-col">
                  <span className="text-[8px] text-muted-foreground/50 uppercase font-bold tracking-tighter leading-none mb-0.5">
                    价格
                  </span>
                  <span className="text-foreground font-black tracking-tight">
                    {formatCurrency(order.price)}
                  </span>
                </div>
                <div className="w-[1px] h-4 bg-slate-200/20 dark:bg-slate-800/20 mx-1" />
                <div className="flex flex-col">
                  <span className="text-[8px] text-muted-foreground/50 uppercase font-bold tracking-tighter leading-none mb-0.5">
                    数量
                  </span>
                  <span className="text-foreground/80 font-bold">
                    {order.volume}股
                  </span>
                </div>

                {order.tradedVolume > 0 && (
                  <div className="ml-auto flex items-center gap-1.5 px-2 py-0.5 bg-primary/5 rounded-md border border-primary/10">
                    <div className="w-1 h-1 bg-primary rounded-full animate-pulse" />
                    <span className="text-[9px] font-bold text-primary/80 whitespace-nowrap">
                      已成交 {order.tradedVolume}
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="absolute top-2 right-2">
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0 rounded-lg text-muted-foreground/30 hover:text-destructive hover:bg-destructive/10 transition-all duration-200"
                disabled={isCancelling}
                onClick={e => {
                  e.stopPropagation();
                  handleCancel(order.id);
                }}
                title="取消订单"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 6 6 18" />
                  <path d="m6 6 12 12" />
                </svg>
              </Button>
            </div>
          </div>
        ))}
      </div>
      {orderMenu}
    </Card>
  );
}
