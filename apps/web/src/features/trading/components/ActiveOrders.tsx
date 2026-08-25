import { Bot, CandlestickChart, Copy, FileX2, ReceiptText } from 'lucide-react';
import * as React from 'react';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { OrderType } from '@/generated/gql/graphql';
import { formatCurrency } from '@/shared/utils/format';
import { cn } from '@/utils/cn';

import { useTodayOrders, useCancelOrder } from '../hooks/useTrading';

type ActiveOrder = ReturnType<typeof useTodayOrders>['orders'][number];

interface ActiveOrdersProps {
  accountId?: string;
  className?: string;
}

function copyText(text: string) {
  if (!navigator.clipboard || !text) return;
  void navigator.clipboard.writeText(text);
}

/**
 * 活跃委托组件 - 专门用于侧边栏展示可撤销的订单
 */
export function ActiveOrders({ accountId, className }: ActiveOrdersProps) {
  const [, setLocation] = useLocation();
  const { confirm: confirmDialog } = useAppDialog();
  const { closeMenu, menu, openAtPointer } = useStudioMenu<ActiveOrder>();
  const actualAccountId = accountId || '';
  const { orders, loading } = useTodayOrders(actualAccountId);
  const { cancelOrder, fetching: isCancelling } = useCancelOrder();

  // 只显示可撤销状态的订单
  const activeOrders = React.useMemo(() => {
    return (orders || []).filter(o =>
      ['UNREPORTED', 'WAIT_REPORTING', 'REPORTED', 'PART_SUCC'].includes(
        o.status
      )
    );
  }, [orders]);

  if (loading && activeOrders.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground/30 text-ui-caption uppercase tracking-widest font-bold animate-pulse">
        Loading...
      </div>
    );
  }

  if (activeOrders.length === 0) {
    return null;
  }

  const handleCancel = async (orderId: string) => {
    if (!actualAccountId) return;
    try {
      await cancelOrder(orderId, actualAccountId);
    } catch (_error) {
      // Error handled by hook/toast
    }
  };

  const handleCancelWithConfirm = async (order: ActiveOrder) => {
    const label = `${order.stockName || order.stockCode || order.id}`;
    const confirmed = await confirmDialog({
      title: '撤销活跃委托',
      description: `确认撤销 ${label} 的活跃委托吗？已成交部分不会受影响。`,
      confirmText: '确认撤单',
      cancelText: '保留委托',
      variant: 'warning',
    });
    if (!confirmed) return;
    await handleCancel(order.id);
  };

  return (
    <Card
      className={cn(
        'h-full flex flex-col border-none shadow-none bg-transparent overflow-hidden',
        className
      )}
    >
      <div className="flex items-center justify-between px-3 py-2 border-b border-slate-200/20 dark:border-slate-800/20 shrink-0">
        <h4 className="text-ui-caption font-black text-muted-foreground uppercase tracking-[0.2em] flex items-center gap-2">
          活跃委托
          <span className="flex h-4 min-w-[16px] items-center justify-center px-1 bg-blue-600/10 text-blue-600 rounded-full text-ui-micro font-mono font-bold">
            {activeOrders.length}
          </span>
        </h4>
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar p-1.5 space-y-1.5">
        {activeOrders.map(order => (
          <div
            key={order.id}
            onContextMenu={event => openAtPointer(event, order)}
            className={cn(
              'group relative flex flex-col p-2 rounded-lg bg-white/40 dark:bg-slate-900/40 border border-slate-200/30 dark:border-slate-800/30 transition-all duration-300 hover:bg-white/60 dark:hover:bg-slate-800/60 overflow-hidden',
              order.type === OrderType.Buy
                ? 'border-l-2 border-l-market-up'
                : 'border-l-2 border-l-market-down'
            )}
          >
            <div className="flex items-center justify-between mb-1">
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="font-bold text-ui-caption text-foreground/90 truncate">
                  {order.stockName}
                </span>
                <span className="text-ui-micro font-mono text-muted-foreground/40 shrink-0">
                  {order.stockCode}
                </span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="h-5 w-5 p-0 rounded-md text-muted-foreground/40 hover:text-white hover:bg-rose-500 transition-all duration-200"
                disabled={isCancelling}
                onClick={e => {
                  e.stopPropagation();
                  handleCancel(order.id);
                }}
                title="撤单"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="10"
                  height="10"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M18 6 6 18" />
                  <path d="m6 6 12 12" />
                </svg>
              </Button>
            </div>

            <div className="flex items-center justify-between text-ui-caption font-mono">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    'font-black uppercase tracking-widest text-ui-micro',
                    order.type === OrderType.Buy
                      ? 'text-market-up'
                      : 'text-market-down'
                  )}
                >
                  {order.type === OrderType.Buy ? '买入' : '卖出'}
                </span>
                <span className="text-foreground/80 font-bold">
                  {formatCurrency(order.price)}
                </span>
              </div>
              <div className="flex items-center gap-1 text-muted-foreground/60">
                <span className="font-bold">{order.volume}</span>
                <span className="text-ui-micro opacity-60">股</span>
              </div>
            </div>

            {order.tradedVolume > 0 && (
              <div className="mt-1.5 h-1 w-full bg-slate-200/20 dark:bg-slate-800/20 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-600 transition-all duration-500"
                  style={{
                    width: `${(order.tradedVolume / order.volume) * 100}%`,
                  }}
                />
              </div>
            )}

            <div className="mt-1 flex justify-between items-center">
              <span className="text-ui-micro text-muted-foreground/30 font-bold uppercase tracking-tight tabular-nums">
                {order.time}
              </span>
              <span className="text-ui-micro text-blue-600/60 font-black tracking-tighter uppercase tabular-nums">
                {order.status}
              </span>
            </div>
          </div>
        ))}
      </div>

      <StudioMenu
        ariaLabel="活跃委托菜单"
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
            disabled: isCancelling,
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
    </Card>
  );
}
