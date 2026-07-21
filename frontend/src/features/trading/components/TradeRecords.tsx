import { Bot, CandlestickChart, Copy, ReceiptText } from 'lucide-react';
import { useLocation } from 'wouter';

import { StudioMenu, useStudioMenu } from '@/components/studio-workbench';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { EnrichedTransaction } from '@/shared/types';
import { formatCurrency } from '@/shared/utils/format';

import { useTradeRecords } from '../hooks/useTradeRecords';

interface TradeRecordsProps {
  accountId?: string;
  itemsPerPage?: number;
  initialTimeFilter?: string;
}

function copyText(text: string) {
  if (!navigator.clipboard || !text) return;
  void navigator.clipboard.writeText(text);
}

function getTransactionStockCode(transaction?: EnrichedTransaction | null) {
  const stock = transaction?.stock;
  return stock?.code || stock?.stockCode || transaction?.stockCode || '';
}

function getTransactionStockName(transaction?: EnrichedTransaction | null) {
  const stock = transaction?.stock;
  return stock?.name || transaction?.stockName || '';
}

export function TradeRecords({
  accountId,
  itemsPerPage = 10,
  initialTimeFilter = '30days',
}: TradeRecordsProps) {
  const [, setLocation] = useLocation();
  const { closeMenu, menu, openAtPointer } =
    useStudioMenu<EnrichedTransaction>();
  const {
    typeFilter,
    setTypeFilter,
    timeFilter,
    setTimeFilter,
    filteredTransactions,
    paginatedTransactions,
    isLoading: isTransactionsLoading,
    totalAmount,
    totalPages,
    currentPage,
    setCurrentPage,
    startIndex,
  } = useTradeRecords(accountId, itemsPerPage, initialTimeFilter);
  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return <span className="text-[10px] text-success">已成交</span>;
      case 'pending':
        return <span className="text-[10px] text-warning">待成交</span>;
      case 'cancelled':
        return <span className="text-[10px] text-destructive">已取消</span>;
      default:
        return (
          <span className="text-[10px] text-muted-foreground">{status}</span>
        );
    }
  };

  const getTypeBadge = (type: string) => {
    return type === 'buy' ? (
      <span className="text-[10px] text-success font-bold">买入</span>
    ) : (
      <span className="text-[10px] text-destructive font-bold">卖出</span>
    );
  };

  const tradeMenu = (
    <StudioMenu
      ariaLabel="成交记录菜单"
      items={[
        {
          icon: <ReceiptText className="h-3.5 w-3.5" />,
          id: 'copy-trade-id',
          label: '复制成交 ID',
          onSelect: () => copyText(menu?.payload?.id || ''),
        },
        {
          icon: <CandlestickChart className="h-3.5 w-3.5" />,
          id: 'stock-detail',
          label: '查看个股详情',
          onSelect: () => {
            const stockCode = getTransactionStockCode(menu?.payload);
            if (stockCode) setLocation(`/stock/${stockCode}`);
          },
        },
        {
          icon: <Bot className="h-3.5 w-3.5" />,
          id: 'create-strategy',
          label: '创建策略',
          onSelect: () => {
            const stockCode = getTransactionStockCode(menu?.payload);
            if (stockCode) setLocation(`/strategies/run?symbol=${stockCode}`);
          },
        },
        { id: 'separator-copy', type: 'separator' },
        {
          icon: <Copy className="h-3.5 w-3.5" />,
          id: 'copy-code',
          label: '复制代码',
          onSelect: () => copyText(getTransactionStockCode(menu?.payload)),
        },
        {
          icon: <Copy className="h-3.5 w-3.5" />,
          id: 'copy-name',
          label: '复制名称',
          onSelect: () => copyText(getTransactionStockName(menu?.payload)),
        },
      ]}
      menu={menu}
      onClose={closeMenu}
      width={188}
    />
  );

  return (
    <div className="space-y-3 h-full flex flex-col">
      {/* Inline Toolbar & Summary */}
      <div className="flex justify-between items-center gap-4 px-1">
        <div className="flex gap-4 items-center">
          <div className="flex items-center gap-1.5 text-[10px] font-mono">
            <span className="text-muted-foreground uppercase">成交笔数:</span>
            <span className="font-bold">{filteredTransactions.length}</span>
          </div>
          <div className="flex items-center gap-1.5 text-[10px] font-mono">
            <span className="text-muted-foreground uppercase">成交金额:</span>
            <span className="font-bold">{formatCurrency(totalAmount)}</span>
          </div>
        </div>

        <div className="flex gap-2">
          <Select value={typeFilter} onValueChange={setTypeFilter}>
            <SelectTrigger className="h-7 w-24 text-[10px] border-none bg-muted/30">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部类型</SelectItem>
              <SelectItem value="buy">买入</SelectItem>
              <SelectItem value="sell">卖出</SelectItem>
            </SelectContent>
          </Select>

          <Select value={timeFilter} onValueChange={setTimeFilter}>
            <SelectTrigger className="h-7 w-24 text-[10px] border-none bg-muted/30">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="30days">最近30天</SelectItem>
              <SelectItem value="7days">最近7天</SelectItem>
              <SelectItem value="today">今日</SelectItem>
              <SelectItem value="custom">自定义</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Transaction History Table */}
      <div className="flex-1 overflow-hidden border border-border/50 rounded-md bg-muted/5 flex flex-col">
        {isTransactionsLoading ? (
          <div className="flex-1 flex items-center justify-center text-[11px] text-muted-foreground italic">
            同步历史数据中...
          </div>
        ) : (
          <div className="flex-1 overflow-auto">
            <table className="w-full text-left border-collapse">
              <thead className="sticky top-0 bg-muted/50 z-10">
                <tr>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                    时间
                  </th>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                    股票
                  </th>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                    类型
                  </th>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                    数量
                  </th>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b text-right">
                    价格
                  </th>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b text-right">
                    金额
                  </th>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b">
                    属性/状态
                  </th>
                  <th className="px-3 py-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider border-b text-right">
                    盈亏口径
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/50">
                {paginatedTransactions.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="h-[300px] text-center">
                      <div className="flex flex-col items-center justify-center text-muted-foreground/40 gap-2">
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
                          {filteredTransactions.length === 0
                            ? '暂无成交记录'
                            : '无符合条件的记录'}
                        </span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  paginatedTransactions.map(
                    (transaction: EnrichedTransaction) => {
                      const stock = transaction.stock;
                      if (!stock) return null;

                      return (
                        <tr
                          key={transaction.id}
                          onContextMenu={event =>
                            openAtPointer(event, transaction)
                          }
                          className="hover:bg-muted/30 transition-colors group"
                        >
                          <td className="px-3 py-1 text-[10px] font-mono text-muted-foreground">
                            {transaction.createdAt
                              ? new Date(
                                  transaction.createdAt
                                ).toLocaleTimeString([], {
                                  timeZone: 'Asia/Shanghai',
                                  hour12: false,
                                  hour: '2-digit',
                                  minute: '2-digit',
                                  second: '2-digit',
                                })
                              : '-'}
                          </td>
                          <td className="px-3 py-1">
                            <div className="flex items-center gap-2">
                              <span className="text-[11px] font-bold">
                                {stock.name}
                              </span>
                              <span className="text-[9px] font-mono text-muted-foreground">
                                {stock.code}
                              </span>
                            </div>
                          </td>
                          <td className="px-3 py-1">
                            {getTypeBadge(transaction.type)}
                          </td>
                          <td className="px-3 py-1 text-[11px] font-mono">
                            {transaction.quantity}
                          </td>
                          <td className="px-3 py-1 text-[11px] font-mono text-right">
                            {transaction.price}
                          </td>
                          <td className="px-3 py-1 text-[11px] font-mono text-right font-medium">
                            {formatCurrency(transaction.totalAmount)}
                          </td>
                          <td className="px-3 py-1">
                            {getStatusBadge(transaction.status)}
                          </td>
                          <td className="px-3 py-1 text-right">
                            <span className="text-[10px] text-muted-foreground">
                              --
                            </span>
                          </td>
                        </tr>
                      );
                    }
                  )
                )}
              </tbody>
            </table>
          </div>
        )}
        {tradeMenu}

        {/* Dense Pagination */}
        {totalPages > 1 && (
          <div className="px-2 py-1.5 border-t border-border flex items-center justify-between bg-muted/20">
            <div className="text-[9px] text-muted-foreground font-mono">
              REC: {startIndex + 1}-
              {Math.min(startIndex + itemsPerPage, filteredTransactions.length)}{' '}
              / TOTAL: {filteredTransactions.length}
            </div>
            <div className="flex gap-1">
              <Button
                variant="ghost"
                className="h-5 px-1.5 text-[9px] hover:bg-muted"
                onClick={() => setCurrentPage(Math.max(1, currentPage - 1))}
                disabled={currentPage === 1}
              >
                PREV
              </Button>
              <span className="flex items-center text-[9px] font-mono px-2 bg-muted/50 rounded">
                PAGE {currentPage} OF {totalPages}
              </span>
              <Button
                variant="ghost"
                className="h-5 px-1.5 text-[9px] hover:bg-muted"
                onClick={() =>
                  setCurrentPage(Math.min(totalPages, currentPage + 1))
                }
                disabled={currentPage === totalPages}
              >
                NEXT
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
