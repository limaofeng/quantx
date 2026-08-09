import { useQuery, useMutation, useSubscription } from '@apollo/client';
import { useState, useEffect } from 'react';

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

import { CREATE_TRANSACTION, UPDATE_STOCK_PRICE } from '@/graphql/mutations';
import {
  GET_STOCKS,
  GET_DASHBOARD_SUMMARY,
  GET_MARKET_DEPTH,
  GET_TRANSACTIONS,
} from '@/graphql/queries';
import {
  STOCK_PRICE_UPDATED,
  MARKET_DEPTH_UPDATED,
  PORTFOLIO_UPDATED,
  TRANSACTION_CREATED,
} from '@/graphql/subscriptions';

const formatCurrency = (amount: number) => {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
  }).format(amount);
};

const getStockIconText = (name: string) => {
  const firstChar = name.charAt(0);
  return firstChar;
};

export default function TradingGraphQL() {
  const { toast } = useToast();
  const userId = 'demo-user';

  // State management
  const [selectedStock, setSelectedStock] = useState<any>(null);
  const [tradeType, setTradeType] = useState<'buy' | 'sell'>('buy');
  const [orderType, setOrderType] = useState('limit');
  const [quantity, setQuantity] = useState('');
  const [price, setPrice] = useState('');

  // GraphQL queries
  const { data: stocksData, loading: stocksLoading } = useQuery(GET_STOCKS);
  const { data: summaryData, loading: summaryLoading } = useQuery(
    GET_DASHBOARD_SUMMARY,
    {
      variables: { userId },
    }
  );
  const { data: transactionsData } = useQuery(GET_TRANSACTIONS, {
    variables: { userId },
  });
  const { data: marketDepthData } = useQuery(GET_MARKET_DEPTH, {
    variables: { symbol: selectedStock?.code || '' },
    skip: !selectedStock,
  });

  // GraphQL mutations
  const [createTransaction] = useMutation(CREATE_TRANSACTION, {
    refetchQueries: [
      { query: GET_DASHBOARD_SUMMARY, variables: { userId } },
      { query: GET_TRANSACTIONS, variables: { userId } },
    ],
  });
  const [updateStockPrice] = useMutation(UPDATE_STOCK_PRICE);

  // GraphQL subscriptions for real-time updates
  useSubscription(STOCK_PRICE_UPDATED, {
    variables: { symbols: selectedStock ? [selectedStock.code] : undefined },
    skip: !selectedStock,
    onData: ({ data }) => {
      if (data.data?.stockPriceUpdated) {
        const updatedStock = data.data.stockPriceUpdated;
        if (selectedStock && updatedStock.code === selectedStock.code) {
          setSelectedStock(updatedStock);
          setPrice(updatedStock.currentPrice.toString());
        }
        toast({
          title: '实时价格更新',
          description: `${updatedStock.name}(${updatedStock.code}) 价格已更新至 ${formatCurrency(updatedStock.currentPrice)}`,
        });
      }
    },
  });

  useSubscription(MARKET_DEPTH_UPDATED, {
    variables: { symbol: selectedStock?.code || '' },
    skip: !selectedStock,
    onData: ({ data }) => {
      if (data.data?.marketDepthUpdated) {
        toast({
          title: '市场深度更新',
          description: `${selectedStock?.name} 盘口数据已更新`,
        });
      }
    },
  });

  useSubscription(PORTFOLIO_UPDATED, {
    variables: { userId },
    onData: ({ data }) => {
      if (data.data?.portfolioUpdated) {
        toast({
          title: '投资组合更新',
          description: '您的投资组合已实时更新',
        });
      }
    },
  });

  useSubscription(TRANSACTION_CREATED, {
    variables: { userId },
    onData: ({ data }) => {
      if (data.data?.transactionCreated) {
        const transaction = data.data.transactionCreated;
        toast({
          title: '交易成功',
          description: `${transaction.type === 'buy' ? '买入' : '卖出'} ${transaction.stock?.name} ${transaction.quantity}股`,
        });
      }
    },
  });

  // Calculate estimated amounts
  const estimatedAmount =
    quantity && price ? parseFloat(quantity) * parseFloat(price) : 0;
  const estimatedFees = estimatedAmount * 0.001; // 0.1% fee
  const estimatedTotal =
    tradeType === 'buy'
      ? estimatedAmount + estimatedFees
      : estimatedAmount - estimatedFees;

  // Handle form submission
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedStock || !quantity || !price) {
      toast({
        title: '错误',
        description: '请填写所有必填字段',
        variant: 'destructive',
      });
      return;
    }

    try {
      await createTransaction({
        variables: {
          input: {
            userId,
            stockCode: selectedStock.code,
            type: tradeType,
            quantity: parseInt(quantity),
            price: parseFloat(price),
            totalAmount: estimatedAmount,
            fees: estimatedFees,
          },
        },
      });

      // Reset form
      setQuantity('');
      setPrice('');

      toast({
        title: '交易提交成功',
        description: `${tradeType === 'buy' ? '买入' : '卖出'}订单已提交`,
      });
    } catch (error) {
      toast({
        title: '交易失败',
        description: '提交交易时发生错误',
        variant: 'destructive',
      });
    }
  };

  // Demo function to simulate real-time price updates
  const simulateRealTimeUpdate = async () => {
    if (!selectedStock) return;

    const randomChange = (Math.random() - 0.5) * 0.1; // ±5% random change
    const newPrice =
      parseFloat(selectedStock.currentPrice) * (1 + randomChange);
    const newChangePercent =
      ((newPrice - parseFloat(selectedStock.currentPrice)) /
        parseFloat(selectedStock.currentPrice)) *
      100;

    try {
      await updateStockPrice({
        variables: {
          code: selectedStock.code,
          price: newPrice,
          changePercent: newChangePercent,
        },
      });
    } catch (error) {
      console.error('Error updating stock price:', error);
    }
  };

  useEffect(() => {
    if (selectedStock && selectedStock.currentPrice) {
      setPrice(selectedStock.currentPrice.toString());
    }
  }, [selectedStock]);

  const stocks = stocksData?.stocks || [];
  const summary = summaryData?.dashboardSummary;
  const recentTransactions = transactionsData?.transactions?.slice(0, 5) || [];
  const marketDepth = marketDepthData?.marketDepth;

  if (stocksLoading || summaryLoading) {
    return (
      <div className="p-6">
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-gray-200 rounded w-1/4"></div>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="h-96 bg-gray-200 rounded"></div>
            <div className="h-96 bg-gray-200 rounded"></div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className="text-3xl font-bold">GraphQL交易终端</h2>
          <p className="text-muted-foreground">实时交易执行与市场数据订阅</p>
        </div>

        {selectedStock && (
          <div className="flex gap-2">
            <Button onClick={simulateRealTimeUpdate} variant="outline">
              模拟价格更新
            </Button>
            <Badge
              variant={
                parseFloat(selectedStock.changePercent) >= 0
                  ? 'default'
                  : 'destructive'
              }
            >
              实时数据
            </Badge>
          </div>
        )}
      </div>

      <Tabs defaultValue="trading" className="space-y-6">
        <TabsList>
          <TabsTrigger value="trading">实时交易</TabsTrigger>
          <TabsTrigger value="subscriptions">订阅状态</TabsTrigger>
        </TabsList>

        <TabsContent value="trading" className="space-y-6">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* 交易下单表单 */}
            <Card className="p-6">
              <form onSubmit={handleSubmit} className="space-y-6">
                <div className="flex justify-between items-center">
                  <h4 className="text-lg font-semibold">
                    GraphQL {tradeType === 'buy' ? '买入' : '卖出'}下单
                  </h4>
                  <div className="flex gap-2">
                    <Button
                      type="button"
                      variant={tradeType === 'buy' ? 'default' : 'outline'}
                      onClick={() => setTradeType('buy')}
                      className="text-success"
                      data-testid="buy-button"
                    >
                      买入
                    </Button>
                    <Button
                      type="button"
                      variant={tradeType === 'sell' ? 'default' : 'outline'}
                      onClick={() => setTradeType('sell')}
                      className="text-destructive"
                      data-testid="sell-button"
                    >
                      卖出
                    </Button>
                  </div>
                </div>

                {/* 股票选择 */}
                <div>
                  <Label htmlFor="stock-select">选择股票 (实时订阅)</Label>
                  <Select
                    value={selectedStock?.code || ''}
                    onValueChange={value => {
                      const stock = stocks.find((s: any) => s.code === value);
                      setSelectedStock(stock);
                    }}
                  >
                    <SelectTrigger data-testid="stock-select">
                      <SelectValue placeholder="请选择要交易的股票" />
                    </SelectTrigger>
                    <SelectContent>
                      {stocks.map((stock: any) => (
                        <SelectItem key={stock.id} value={stock.code}>
                          {stock.name} ({stock.code}) -{' '}
                          {formatCurrency(stock.currentPrice)}
                          <span
                            className={`ml-2 ${parseFloat(stock.changePercent) >= 0 ? 'text-success' : 'text-destructive'}`}
                          >
                            {parseFloat(stock.changePercent) >= 0 ? '+' : ''}
                            {stock.changePercent}%
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                {/* 已选股票信息 */}
                {selectedStock && (
                  <Card className="p-4 bg-secondary">
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

                {/* 订单类型 */}
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
                  {/* 数量 */}
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

                  {/* 价格 */}
                  <div>
                    <Label htmlFor="price">价格 (¥) - 实时更新</Label>
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

                {/* 订单摘要 */}
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
                    <div className="border-t border-border pt-2 flex justify-between font-medium">
                      <span>总计:</span>
                      <span data-testid="estimated-total">
                        {formatCurrency(estimatedTotal)}
                      </span>
                    </div>
                  </Card>
                )}

                {/* 提交按钮 */}
                <Button
                  type="submit"
                  className="w-full"
                  disabled={!selectedStock || !quantity || !price}
                  data-testid="submit-trade"
                >
                  确认{tradeType === 'buy' ? '买入' : '卖出'} (GraphQL)
                </Button>
              </form>
            </Card>

            {/* 实时市场深度与账户信息 */}
            <div className="space-y-6">
              {/* 实时市场深度 */}
              {selectedStock && marketDepth && (
                <Card className="p-6">
                  <h4 className="text-lg font-semibold mb-4">
                    实时市场深度 - {selectedStock.name} ({selectedStock.code})
                  </h4>

                  <div className="space-y-4">
                    <div className="grid grid-cols-2 gap-4">
                      {/* 卖盘 */}
                      <div className="space-y-1">
                        <div className="text-center text-sm font-medium text-muted-foreground mb-2">
                          卖盘
                        </div>
                        {marketDepth.sellOrders.map((order: any) => (
                          <div
                            key={`sell-${order.level}`}
                            className="flex justify-between items-center py-1 hover:bg-muted/50 rounded px-2"
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
                        ))}
                      </div>

                      {/* 买盘 */}
                      <div className="space-y-1">
                        <div className="text-center text-sm font-medium text-muted-foreground mb-2">
                          买盘
                        </div>
                        {marketDepth.buyOrders.map((order: any) => (
                          <div
                            key={`buy-${order.level}`}
                            className="flex justify-between items-center py-1 hover:bg-muted/50 rounded px-2"
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
                        ))}
                      </div>
                    </div>

                    <div className="border-t border-border pt-3">
                      <div className="grid grid-cols-3 gap-4 text-xs">
                        <div className="text-center">
                          <div className="text-muted-foreground">成交量</div>
                          <div className="font-medium">
                            {marketDepth.volume.toLocaleString()}
                          </div>
                        </div>
                        <div className="text-center">
                          <div className="text-muted-foreground">成交额</div>
                          <div className="font-medium">
                            {marketDepth.turnover.toFixed(0)}万
                          </div>
                        </div>
                        <div className="text-center">
                          <div className="text-muted-foreground">换手率</div>
                          <div className="font-medium">
                            {marketDepth.turnoverRate.toFixed(2)}%
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </Card>
              )}

              {/* 账户信息 */}
              {summary && (
                <Card className="p-6">
                  <h4 className="text-lg font-semibold mb-4">
                    账户信息 (实时订阅)
                  </h4>
                  <div className="space-y-3">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">可用资金</span>
                      <span
                        className="font-medium"
                        data-testid="available-cash"
                      >
                        {formatCurrency(summary.availableCash)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">冻结资金</span>
                      <span className="font-medium" data-testid="frozen-funds">
                        {formatCurrency(summary.frozenFunds)}
                      </span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">总资产</span>
                      <span
                        className="font-medium"
                        data-testid="account-total-assets"
                      >
                        {formatCurrency(summary.totalAssets)}
                      </span>
                    </div>
                    <div className="flex justify-between border-t border-border pt-3">
                      <span className="text-muted-foreground">购买力</span>
                      <span
                        className="font-bold text-primary"
                        data-testid="buying-power"
                      >
                        {formatCurrency(
                          summary.availableCash - summary.frozenFunds
                        )}
                      </span>
                    </div>
                  </div>
                </Card>
              )}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="subscriptions" className="space-y-6">
          <Card className="p-6">
            <h4 className="text-lg font-semibold mb-4">实时订阅状态</h4>
            <div className="space-y-4">
              <div className="flex items-center justify-between p-3 border rounded">
                <div>
                  <p className="font-medium">股价实时更新</p>
                  <p className="text-sm text-muted-foreground">
                    {selectedStock
                      ? `订阅 ${selectedStock.code} 价格变化`
                      : '未选择股票'}
                  </p>
                </div>
                <Badge variant={selectedStock ? 'default' : 'secondary'}>
                  {selectedStock ? '已连接' : '未连接'}
                </Badge>
              </div>

              <div className="flex items-center justify-between p-3 border rounded">
                <div>
                  <p className="font-medium">市场深度订阅</p>
                  <p className="text-sm text-muted-foreground">
                    {selectedStock
                      ? `订阅 ${selectedStock.code} 盘口变化`
                      : '未选择股票'}
                  </p>
                </div>
                <Badge variant={selectedStock ? 'default' : 'secondary'}>
                  {selectedStock ? '已连接' : '未连接'}
                </Badge>
              </div>

              <div className="flex items-center justify-between p-3 border rounded">
                <div>
                  <p className="font-medium">投资组合订阅</p>
                  <p className="text-sm text-muted-foreground">
                    用户 {userId} 的投资组合变化
                  </p>
                </div>
                <Badge variant="default">已连接</Badge>
              </div>

              <div className="flex items-center justify-between p-3 border rounded">
                <div>
                  <p className="font-medium">交易创建订阅</p>
                  <p className="text-sm text-muted-foreground">
                    用户 {userId} 的新交易通知
                  </p>
                </div>
                <Badge variant="default">已连接</Badge>
              </div>
            </div>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
