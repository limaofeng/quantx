import { Download } from 'lucide-react';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { getStockIconText, formatCurrency } from '@/shared/utils/format';

import { EnrichedTransaction } from '@/lib/types';

// 模拟交易历史数据
const mockTransactions: EnrichedTransaction[] = [
  {
    id: '1',
    type: 'buy',
    quantity: 100,
    price: 12.5,
    totalAmount: 1250.0,
    status: 'completed',
    createdAt: '2024-01-15T09:30:00Z',
    stock: { id: '000001', code: '000001', name: '平安银行' },
  },
  {
    id: '2',
    type: 'sell',
    quantity: 50,
    price: 13.2,
    totalAmount: 660.0,
    status: 'completed',
    createdAt: '2024-01-10T14:15:00Z',
    stock: { id: '000002', code: '000002', name: '万科A' },
  },
  {
    id: '3',
    type: 'buy',
    quantity: 200,
    price: 8.75,
    totalAmount: 1750.0,
    status: 'completed',
    createdAt: '2024-01-08T10:45:00Z',
    stock: { id: '600519', code: '600519', name: '贵州茅台' },
  },
];

export default function History() {
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [timeFilter, setTimeFilter] = useState<string>('30days');
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;

  // 使用模拟数据替代 REST API 调用
  const transactions = mockTransactions;
  const isLoading = false;

  // Filter transactions
  const filteredTransactions = transactions.filter(transaction => {
    if (typeFilter !== 'all' && transaction.type !== typeFilter) {
      return false;
    }

    // Time filtering (mock implementation)
    const transactionDate = new Date(transaction.createdAt);
    const now = new Date();
    const daysDiff = Math.floor(
      (now.getTime() - transactionDate.getTime()) / (1000 * 60 * 60 * 24)
    );

    switch (timeFilter) {
      case 'today':
        return daysDiff === 0;
      case '7days':
        return daysDiff <= 7;
      case '30days':
        return daysDiff <= 30;
      default:
        return true;
    }
  });

  // Pagination
  const totalPages = Math.ceil(filteredTransactions.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const paginatedTransactions = filteredTransactions.slice(
    startIndex,
    startIndex + itemsPerPage
  );

  // Calculate summary statistics
  const totalTransactions = transactions.length;
  const profitableTransactions = transactions.filter(t => {
    // Mock calculation: assume transactions with even IDs are profitable
    return t.id.charCodeAt(0) % 2 === 0;
  }).length;
  const winRate =
    totalTransactions > 0
      ? (profitableTransactions / totalTransactions) * 100
      : 0;
  const totalProfit = transactions.reduce((sum, t) => {
    // Mock profit calculation
    const profit =
      parseFloat(t.totalAmount) * (t.id.charCodeAt(0) % 2 === 0 ? 0.03 : -0.02);
    return sum + profit;
  }, 0);

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return <Badge className="bg-success/10 text-success">已成交</Badge>;
      case 'pending':
        return <Badge className="bg-warning/10 text-warning">待成交</Badge>;
      case 'cancelled':
        return (
          <Badge className="bg-destructive/10 text-destructive">已取消</Badge>
        );
      default:
        return <Badge variant="secondary">{status}</Badge>;
    }
  };

  const getTypeBadge = (type: string) => {
    return type === 'buy' ? (
      <Badge className="bg-primary/10 text-primary">买入</Badge>
    ) : (
      <Badge className="bg-destructive/10 text-destructive">卖出</Badge>
    );
  };

  if (isLoading) {
    return <div>加载交易历史中...</div>;
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <div>
          <h3 className="text-xl font-semibold">交易历史</h3>
          <p className="text-muted-foreground">查看历史交易记录</p>
        </div>
        <div className="flex gap-3">
          <Select value={typeFilter} onValueChange={setTypeFilter}>
            <SelectTrigger className="w-32" data-testid="type-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部类型</SelectItem>
              <SelectItem value="buy">买入</SelectItem>
              <SelectItem value="sell">卖出</SelectItem>
            </SelectContent>
          </Select>

          <Select value={timeFilter} onValueChange={setTimeFilter}>
            <SelectTrigger className="w-32" data-testid="time-filter">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="30days">最近30天</SelectItem>
              <SelectItem value="7days">最近7天</SelectItem>
              <SelectItem value="today">今日</SelectItem>
              <SelectItem value="custom">自定义</SelectItem>
            </SelectContent>
          </Select>

          <Button variant="secondary" data-testid="export-history">
            <Download className="mr-2 h-4 w-4" />
            导出
          </Button>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-6">
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">总交易次数</p>
          <p
            className="text-xl font-bold text-foreground"
            data-testid="total-transactions"
          >
            {totalTransactions}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">盈利交易</p>
          <p
            className="text-xl font-bold text-success"
            data-testid="profitable-transactions"
          >
            {profitableTransactions}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">胜率</p>
          <p
            className="text-xl font-bold text-foreground"
            data-testid="win-rate"
          >
            {winRate.toFixed(1)}%
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">累计收益</p>
          <p
            className={`text-xl font-bold ${totalProfit >= 0 ? 'text-success' : 'text-destructive'}`}
            data-testid="total-profit"
          >
            {totalProfit >= 0 ? '+' : ''}
            {formatCurrency(totalProfit)}
          </p>
        </Card>
      </div>

      {/* Transaction History Table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-muted">
              <tr>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  时间
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  股票
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  类型
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  数量
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  价格
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  金额
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  状态
                </th>
                <th className="px-6 py-3 text-left text-xs font-medium text-muted-foreground uppercase">
                  盈亏
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {paginatedTransactions.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="px-6 py-8 text-center text-muted-foreground"
                  >
                    {filteredTransactions.length === 0
                      ? '暂无交易记录'
                      : '当前筛选条件下无交易记录'}
                  </td>
                </tr>
              ) : (
                paginatedTransactions.map(transaction => {
                  const stock = transaction.stock;
                  if (!stock) return null;

                  // Mock P&L calculation
                  const mockPnL =
                    parseFloat(transaction.totalAmount) *
                    (transaction.id.charCodeAt(0) % 2 === 0 ? 0.03 : -0.02);

                  return (
                    <tr
                      key={transaction.id}
                      className="hover:bg-muted/50"
                      data-testid={`transaction-${transaction.id}`}
                    >
                      <td
                        className="px-6 py-4 text-sm"
                        data-testid={`transaction-${transaction.id}-time`}
                      >
                        {new Date(transaction.createdAt).toLocaleString()}
                      </td>
                      <td className="px-6 py-4">
                        <div className="flex items-center">
                          <div className="w-8 h-8 bg-primary/10 rounded flex items-center justify-center mr-3">
                            <span className="text-primary text-sm font-medium">
                              {getStockIconText(stock.name)}
                            </span>
                          </div>
                          <div>
                            <div className="font-medium">{stock.name}</div>
                            <div className="text-sm text-muted-foreground">
                              {stock.code}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td
                        className="px-6 py-4"
                        data-testid={`transaction-${transaction.id}-type`}
                      >
                        {getTypeBadge(transaction.type)}
                      </td>
                      <td
                        className="px-6 py-4 font-medium"
                        data-testid={`transaction-${transaction.id}-quantity`}
                      >
                        {transaction.quantity.toLocaleString()}股
                      </td>
                      <td
                        className="px-6 py-4"
                        data-testid={`transaction-${transaction.id}-price`}
                      >
                        {formatCurrency(transaction.price)}
                      </td>
                      <td
                        className="px-6 py-4 font-medium"
                        data-testid={`transaction-${transaction.id}-amount`}
                      >
                        {formatCurrency(transaction.totalAmount)}
                      </td>
                      <td
                        className="px-6 py-4"
                        data-testid={`transaction-${transaction.id}-status`}
                      >
                        {getStatusBadge(transaction.status)}
                      </td>
                      <td
                        className="px-6 py-4"
                        data-testid={`transaction-${transaction.id}-pnl`}
                      >
                        {transaction.type === 'sell' ? (
                          <span
                            className={
                              mockPnL >= 0 ? 'text-success' : 'text-destructive'
                            }
                          >
                            {mockPnL >= 0 ? '+' : ''}
                            {formatCurrency(mockPnL)}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="bg-muted px-6 py-4 flex items-center justify-between">
            <div
              className="text-sm text-muted-foreground"
              data-testid="pagination-info"
            >
              显示 {startIndex + 1}-
              {Math.min(startIndex + itemsPerPage, filteredTransactions.length)}{' '}
              条，共 {filteredTransactions.length} 条记录
            </div>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setCurrentPage(Math.max(1, currentPage - 1))}
                disabled={currentPage === 1}
                data-testid="prev-page"
              >
                上一页
              </Button>

              {/* Page numbers */}
              {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                const pageNum =
                  Math.max(1, Math.min(totalPages - 4, currentPage - 2)) + i;
                return (
                  <Button
                    key={pageNum}
                    variant={currentPage === pageNum ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setCurrentPage(pageNum)}
                    data-testid={`page-${pageNum}`}
                  >
                    {pageNum}
                  </Button>
                );
              })}

              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setCurrentPage(Math.min(totalPages, currentPage + 1))
                }
                disabled={currentPage === totalPages}
                data-testid="next-page"
              >
                下一页
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
