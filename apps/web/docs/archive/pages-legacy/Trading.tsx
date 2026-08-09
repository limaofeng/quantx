import { useQuery, useMutation } from '@apollo/client/react';
import { Search, Download } from 'lucide-react';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useToast } from '@/hooks/use-toast';
import { getStockIconText, formatCurrency } from '@/shared/utils/format';

import {
  GET_STOCKS,
  GET_TRANSACTIONS,
  GET_DASHBOARD_SUMMARY,
  CREATE_TRANSACTION,
} from '@/graphql/queries';
import { type Stock } from '@/lib/types';
import { type EnrichedTransaction } from '@/lib/types';

export default function Trading() {
  const { toast } = useToast();
  const userId = 'demo-user';

  // Trading form states
  const [tradeType, setTradeType] = useState<'buy' | 'sell'>('buy');
  const [selectedStock, setSelectedStock] = useState<Stock | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [orderType, setOrderType] = useState('limit');
  const [quantity, setQuantity] = useState('');
  const [price, setPrice] = useState('');

  // History states
  const [typeFilter, setTypeFilter] = useState<string>('all');
  const [timeFilter, setTimeFilter] = useState<string>('30days');
  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 10;

  const { data: stocksData, loading: stocksLoading } = useQuery(GET_STOCKS);
  const {
    data: transactionsData,
    loading: isTransactionsLoading,
    refetch: refetchTransactions,
  } = useQuery(GET_TRANSACTIONS, {
    variables: { userId },
  });
  const { data: summaryData, refetch: refetchSummary } = useQuery(
    GET_DASHBOARD_SUMMARY,
    {
      variables: { userId },
    }
  );

  const [createTransaction] = useMutation(CREATE_TRANSACTION, {
    onCompleted: () => {
      refetchTransactions();
      refetchSummary();
      toast({
        title: '交易成功',
        description: `${tradeType === 'buy' ? '买入' : '卖出'}订单已提交`,
      });
      // Reset form
      setQuantity('');
      setPrice('');
    },
    onError: () => {
      toast({
        title: '交易失败',
        description: '请检查输入信息后重试',
        variant: 'destructive',
      });
    },
  });

  const stocks = stocksData?.stocks || [];
  const recentTransactions = transactionsData?.transactions || [];
  const summary = summaryData?.dashboardSummary;

  const filteredStocks = stocks.filter(
    stock =>
      stock.name.includes(searchQuery) || stock.code.includes(searchQuery)
  );

  const handleStockSelect = (stock: Stock) => {
    setSelectedStock(stock);
    setPrice(String(stock.currentPrice));
    setSearchQuery('');
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedStock || !quantity || !price) {
      toast({
        title: '信息不完整',
        description: '请填写完整的交易信息',
        variant: 'destructive',
      });
      return;
    }

    const quantityNum = parseInt(quantity);
    const priceNum = parseFloat(price);
    const totalAmount = quantityNum * priceNum;
    const fees = totalAmount * 0.0005; // 0.05% fee

    createTransaction({
      variables: {
        input: {
          userId: 'demo-user',
          stockCode: selectedStock.code,
          type: tradeType,
          quantity: quantityNum,
          price: priceNum.toString(),
          totalAmount: totalAmount.toString(),
          fees: fees.toString(),
          status: 'completed',
        },
      },
    });
  };

  const estimatedAmount =
    quantity && price ? parseInt(quantity) * parseFloat(price) : 0;
  const estimatedFees = estimatedAmount * 0.0005;
  const estimatedTotal = estimatedAmount + estimatedFees;

  // History filtering logic
  const filteredTransactions = recentTransactions.filter(transaction => {
    if (typeFilter !== 'all' && transaction.type !== typeFilter) {
      return false;
    }

    // Time filtering
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
  const totalTransactions = recentTransactions.length;
  const profitableTransactions = recentTransactions.filter(t => {
    return t.id.charCodeAt(0) % 2 === 0;
  }).length;
  const winRate =
    totalTransactions > 0
      ? (profitableTransactions / totalTransactions) * 100
      : 0;
  const totalProfit = recentTransactions.reduce((sum, t) => {
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

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6">
        <h3 className="text-xl font-semibold">交易中心</h3>
        <p className="text-muted-foreground">股票交易下单和历史记录管理</p>
      </div>

      <Tabs defaultValue="trading" className="space-y-6">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="trading">下单交易</TabsTrigger>
          <TabsTrigger value="history">交易历史</TabsTrigger>
        </TabsList>

        {/* 交易下单标签页 */}
        <TabsContent value="trading" className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Trading Form */}
            <Card className="p-6">
              <h4 className="text-lg font-semibold mb-4">下单交易</h4>

              {/* Trade Type Tabs */}
              <div className="flex border-b border-border mb-6">
                <button
                  className={`px-4 py-2 font-medium border-b-2 transition-colors ${
                    tradeType === 'buy'
                      ? 'border-primary text-primary'
                      : 'border-transparent text-muted-foreground hover:text-foreground'
                  }`}
                  onClick={() => setTradeType('buy')}
                  data-testid="trade-type-buy"
                >
                  买入
                </button>
                <button
                  className={`px-4 py-2 font-medium border-b-2 transition-colors ${
                    tradeType === 'sell'
                      ? 'border-primary text-primary'
                      : 'border-transparent text-muted-foreground hover:text-foreground'
                  }`}
                  onClick={() => setTradeType('sell')}
                  data-testid="trade-type-sell"
                >
                  卖出
                </button>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                {/* Stock Search */}
                <div>
                  <Label htmlFor="stock-search">股票代码/名称</Label>
                  <div className="relative">
                    <Input
                      id="stock-search"
                      placeholder="输入股票代码或名称"
                      value={searchQuery}
                      onChange={e => setSearchQuery(e.target.value)}
                      data-testid="stock-search"
                    />
                    <Search className="absolute right-2 top-2 h-5 w-5 text-muted-foreground" />
                  </div>

                  {/* Search Results */}
                  {searchQuery && (
                    <div className="mt-2 border border-border rounded-md bg-card max-h-48 overflow-y-auto">
                      {filteredStocks.map(stock => (
                        <button
                          key={stock.code}
                          type="button"
                          className="w-full text-left px-3 py-2 hover:bg-muted flex items-center justify-between"
                          onClick={() => handleStockSelect(stock)}
                          data-testid={`stock-option-${stock.code}`}
                        >
                          <div className="flex items-center">
                            <div className="w-8 h-8 bg-primary/10 rounded flex items-center justify-center mr-3">
                              <span className="text-primary text-sm font-medium">
                                {getStockIconText(stock.name)}
                              </span>
                            </div>
                            <div>
                              <p className="font-medium">
                                {stock.name} ({stock.code})
                              </p>
                              <p className="text-sm text-muted-foreground">
                                {stock.exchange}
                              </p>
                            </div>
                          </div>
                          <div className="text-right">
                            <p className="font-medium">
                              {formatCurrency(stock.currentPrice)}
                            </p>
                            <p
                              className={`text-sm ${parseFloat(stock.changePercent) >= 0 ? 'text-success' : 'text-destructive'}`}
                            >
                              {parseFloat(stock.changePercent) >= 0 ? '+' : ''}
                              {stock.changePercent}%
                            </p>
                          </div>
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Selected Stock Info */}
                {selectedStock && (
                  <Card className="bg-muted p-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center">
                        <div className="w-8 h-8 bg-primary/10 rounded flex items-center justify-center mr-3">
                          <span className="text-primary text-sm font-medium">
                            {getStockIconText(selectedStock.name)}
                          </span>
                        </div>
                        <div>
                          <p className="font-medium">
                            {selectedStock.name} ({selectedStock.code})
                          </p>
                          <p className="text-sm text-muted-foreground">
                            {selectedStock.exchange}
                          </p>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className="font-medium">
                          {formatCurrency(selectedStock.currentPrice)}
                        </p>
                        <p
                          className={`text-sm ${parseFloat(selectedStock.changePercent) >= 0 ? 'text-success' : 'text-destructive'}`}
                        >
                          {parseFloat(selectedStock.changePercent) >= 0
                            ? '+'
                            : ''}
                          {selectedStock.changePercent}%
                        </p>
                      </div>
                    </div>
                  </Card>
                )}

                {/* Order Type */}
                <div>
                  <Label htmlFor="order-type">订单类型</Label>
                  <Select value={orderType} onValueChange={setOrderType}>
                    <SelectTrigger data-testid="order-type-select">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="limit">限价单</SelectItem>
                      <SelectItem value="market">市价单</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  {/* Quantity */}
                  <div>
                    <Label htmlFor="quantity">数量 (股)</Label>
                    <Input
                      id="quantity"
                      type="number"
                      placeholder="100"
                      value={quantity}
                      onChange={e => setQuantity(e.target.value)}
                      min="100"
                      step="100"
                      data-testid="quantity-input"
                    />
                    <p className="text-xs text-muted-foreground mt-1">
                      最小100股
                    </p>
                  </div>

                  {/* Price */}
                  <div>
                    <Label htmlFor="price">价格 (¥)</Label>
                    <Input
                      id="price"
                      type="number"
                      step="0.01"
                      placeholder="0.00"
                      value={price}
                      onChange={e => setPrice(e.target.value)}
                      data-testid="price-input"
                    />
                    <p className="text-xs text-muted-foreground mt-1">
                      当前价{' '}
                      {selectedStock
                        ? formatCurrency(selectedStock.currentPrice)
                        : '-'}
                    </p>
                  </div>
                </div>

                {/* Order Summary */}
                {estimatedAmount > 0 && (
                  <Card className="bg-secondary p-4 space-y-2">
                    <div className="flex justify-between text-sm">
                      <span>预估金额:</span>
                      <span
                        className="font-medium"
                        data-testid="estimated-amount"
                      >
                        {formatCurrency(estimatedAmount)}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span>手续费:</span>
                      <span data-testid="estimated-fees">
                        {formatCurrency(estimatedFees)}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span>印花税:</span>
                      <span>¥0.00</span>
                    </div>
                    <div className="border-t border-border pt-2 flex justify-between font-medium">
                      <span>总计:</span>
                      <span data-testid="estimated-total">
                        {formatCurrency(estimatedTotal)}
                      </span>
                    </div>
                  </Card>
                )}

                {/* Submit Button */}
                <Button
                  type="submit"
                  className="w-full"
                  disabled={!selectedStock || !quantity || !price}
                  data-testid="submit-trade"
                >
                  {false
                    ? '提交中...'
                    : `确认${tradeType === 'buy' ? '买入' : '卖出'}`}
                </Button>
              </form>
            </Card>

            {/* Market Depth & Account Info */}
            <div className="space-y-6">
              {/* Market Depth - Buy/Sell Orders */}
              <Card className="p-6">
                <h4 className="text-lg font-semibold mb-4">
                  {selectedStock
                    ? `${selectedStock.name} (${selectedStock.code}) 买卖五挡`
                    : '股票五挡行情'}
                </h4>

                {selectedStock ? (
                  <div className="space-y-4">
                    {/* 买卖五挡左右布局 */}
                    <div className="grid grid-cols-2 gap-4">
                      {/* 卖盘 (左侧) */}
                      <div className="space-y-1">
                        <div className="text-center text-sm font-medium text-muted-foreground mb-2">
                          卖盘
                        </div>
                        {(() => {
                          const basePrice = parseFloat(
                            selectedStock.currentPrice
                          );
                          const sellOrders = [
                            {
                              level: 5,
                              price: basePrice + 0.15,
                              volume: Math.floor(Math.random() * 5000) + 1000,
                            },
                            {
                              level: 4,
                              price: basePrice + 0.12,
                              volume: Math.floor(Math.random() * 8000) + 2000,
                            },
                            {
                              level: 3,
                              price: basePrice + 0.09,
                              volume: Math.floor(Math.random() * 12000) + 3000,
                            },
                            {
                              level: 2,
                              price: basePrice + 0.06,
                              volume: Math.floor(Math.random() * 15000) + 5000,
                            },
                            {
                              level: 1,
                              price: basePrice + 0.03,
                              volume: Math.floor(Math.random() * 20000) + 8000,
                            },
                          ];

                          return sellOrders.map(order => (
                            <div
                              key={`sell-${order.level}`}
                              className="flex justify-between items-center py-1 hover:bg-muted/50 rounded px-2"
                              data-testid={`sell-${order.level}`}
                            >
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-muted-foreground w-8">
                                  卖{order.level}
                                </span>
                                <span className="text-sm font-medium text-success">
                                  {order.price.toFixed(2)}
                                </span>
                              </div>
                              <span className="text-xs text-muted-foreground">
                                {order.volume.toLocaleString()}
                              </span>
                            </div>
                          ));
                        })()}
                      </div>

                      {/* 买盘 (右侧) */}
                      <div className="space-y-1">
                        <div className="text-center text-sm font-medium text-muted-foreground mb-2">
                          买盘
                        </div>
                        {(() => {
                          const basePrice = parseFloat(
                            selectedStock.currentPrice
                          );
                          const buyOrders = [
                            {
                              level: 1,
                              price: basePrice - 0.03,
                              volume: Math.floor(Math.random() * 25000) + 10000,
                            },
                            {
                              level: 2,
                              price: basePrice - 0.06,
                              volume: Math.floor(Math.random() * 18000) + 6000,
                            },
                            {
                              level: 3,
                              price: basePrice - 0.09,
                              volume: Math.floor(Math.random() * 15000) + 4000,
                            },
                            {
                              level: 4,
                              price: basePrice - 0.12,
                              volume: Math.floor(Math.random() * 10000) + 2500,
                            },
                            {
                              level: 5,
                              price: basePrice - 0.15,
                              volume: Math.floor(Math.random() * 8000) + 1500,
                            },
                          ];

                          return buyOrders.map(order => (
                            <div
                              key={`buy-${order.level}`}
                              className="flex justify-between items-center py-1 hover:bg-muted/50 rounded px-2"
                              data-testid={`buy-${order.level}`}
                            >
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-muted-foreground w-8">
                                  买{order.level}
                                </span>
                                <span className="text-sm font-medium text-destructive">
                                  {order.price.toFixed(2)}
                                </span>
                              </div>
                              <span className="text-xs text-muted-foreground">
                                {order.volume.toLocaleString()}
                              </span>
                            </div>
                          ));
                        })()}
                      </div>
                    </div>

                    {/* Market Statistics */}
                    <div className="border-t border-border pt-3">
                      <div className="grid grid-cols-3 gap-4 text-xs">
                        <div className="text-center">
                          <div className="text-muted-foreground">成交量</div>
                          <div className="font-medium">
                            {Math.floor(
                              Math.random() * 100000 + 50000
                            ).toLocaleString()}
                          </div>
                        </div>
                        <div className="text-center">
                          <div className="text-muted-foreground">成交额</div>
                          <div className="font-medium">
                            {(Math.random() * 5000 + 1000).toFixed(0)}万
                          </div>
                        </div>
                        <div className="text-center">
                          <div className="text-muted-foreground">换手率</div>
                          <div className="font-medium">
                            {(Math.random() * 5 + 1).toFixed(2)}%
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="text-center py-8 text-muted-foreground">
                    <div className="text-sm">请先选择股票查看五挡行情</div>
                  </div>
                )}
              </Card>

              {/* Account Balance */}
              <Card className="p-6">
                <h4 className="text-lg font-semibold mb-4">账户信息</h4>
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">可用资金</span>
                    <span className="font-medium" data-testid="available-cash">
                      {formatCurrency(summary?.availableCash || 0)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">冻结资金</span>
                    <span className="font-medium" data-testid="frozen-funds">
                      {formatCurrency(summary?.frozenFunds || 0)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">总资产</span>
                    <span
                      className="font-medium"
                      data-testid="account-total-assets"
                    >
                      {formatCurrency(summary?.totalAssets || 0)}
                    </span>
                  </div>
                  <div className="flex justify-between border-t border-border pt-3">
                    <span className="text-muted-foreground">购买力</span>
                    <span
                      className="font-bold text-primary"
                      data-testid="buying-power"
                    >
                      {formatCurrency(
                        (summary?.availableCash || 0) -
                          (summary?.frozenFunds || 0)
                      )}
                    </span>
                  </div>
                </div>
              </Card>
            </div>
          </div>
        </TabsContent>

        {/* 交易历史标签页 */}
        <TabsContent value="history" className="space-y-6">
          <div className="flex justify-between items-center">
            <div>
              <h4 className="text-lg font-semibold">交易历史记录</h4>
              <p className="text-muted-foreground">查看详细的历史交易记录</p>
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
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
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
            {isTransactionsLoading ? (
              <div className="p-8 text-center text-muted-foreground">
                加载交易历史中...
              </div>
            ) : (
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
                          (transaction.id.charCodeAt(0) % 2 === 0
                            ? 0.03
                            : -0.02);

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
                                  <div className="font-medium">
                                    {stock.name}
                                  </div>
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
                                    mockPnL >= 0
                                      ? 'text-success'
                                      : 'text-destructive'
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
            )}

            {/* Pagination */}
            {totalPages > 1 && (
              <div className="bg-muted px-6 py-4 flex items-center justify-between">
                <div
                  className="text-sm text-muted-foreground"
                  data-testid="pagination-info"
                >
                  显示 {startIndex + 1}-
                  {Math.min(
                    startIndex + itemsPerPage,
                    filteredTransactions.length
                  )}{' '}
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
                      Math.max(1, Math.min(totalPages - 4, currentPage - 2)) +
                      i;
                    return (
                      <Button
                        key={pageNum}
                        variant={
                          currentPage === pageNum ? 'default' : 'outline'
                        }
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
        </TabsContent>
      </Tabs>
    </div>
  );
}
