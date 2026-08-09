import { useQuery } from '@apollo/client/react';
import {
  ArrowLeft,
  TrendingUp,
  TrendingDown,
  DollarSign,
  BarChart3,
  Clock,
  Building2,
  Users,
  ArrowUpRight,
  ArrowDownRight,
} from 'lucide-react';
import { useParams, Link } from 'wouter';

import StockChart from '@/components/StockChart';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  getStockIconText,
  formatCurrency,
  formatPercent,
} from '@/shared/utils/format';

import { GET_STOCK, GET_HOLDINGS, GET_TRANSACTIONS } from '@/graphql/queries';
import { type Stock } from '@/lib/types';

export default function StockDetail() {
  const { stockCode } = useParams();
  const userId = 'demo-user';

  const {
    data: stockData,
    loading: stockLoading,
    error: stockError,
  } = useQuery(GET_STOCK, {
    variables: { code: stockCode },
    skip: !stockCode,
  });

  const { data: holdingsData, loading: holdingsLoading } = useQuery(
    GET_HOLDINGS,
    {
      variables: { userId },
    }
  );

  const { data: transactionsData, loading: transactionsLoading } = useQuery(
    GET_TRANSACTIONS,
    {
      variables: { userId },
    }
  );

  const stock = stockData?.stock;
  const holdings = holdingsData?.holdings || [];
  const transactions = transactionsData?.transactions || [];

  const holding = holdings.find((h: any) => h.stockCode === stockCode);
  const stockTransactions = transactions.filter(
    (t: any) => t.stockCode === stockCode
  );

  if (stockLoading) {
    return (
      <div className="text-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-4"></div>
        <p className="text-muted-foreground">加载中...</p>
      </div>
    );
  }

  if (stockError || !stock) {
    return (
      <div className="text-center py-12">
        <h2 className="text-xl font-semibold mb-2">股票未找到</h2>
        <p className="text-muted-foreground mb-4">找不到股票代码 {stockCode}</p>
        <Link href="/holdings">
          <Button>返回持仓</Button>
        </Link>
      </div>
    );
  }

  const changePercent = parseFloat(stock.changePercent);
  const isPositive = changePercent >= 0;

  // Calculate holding metrics if user has this stock
  let holdingValue = 0;
  let holdingCost = 0;
  let holdingPnL = 0;
  let holdingPnLPercent = 0;

  if (holding) {
    holdingValue = holding.quantity * parseFloat(stock.currentPrice);
    holdingCost = holding.quantity * parseFloat(holding.averageCost);
    holdingPnL = holdingValue - holdingCost;
    holdingPnLPercent = ((holdingValue - holdingCost) / holdingCost) * 100;
  }

  // Mock market data
  const marketData = {
    volume: '1,234,567',
    turnover: '65.42亿',
    pe: '23.45',
    pb: '2.18',
    marketCap: '1,428.6亿',
    high52w: parseFloat(stock.currentPrice) * 1.45,
    low52w: parseFloat(stock.currentPrice) * 0.78,
    dividend: '2.34%',
  };

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center">
          <Link href="/holdings">
            <Button
              variant="ghost"
              size="sm"
              className="mr-4"
              data-testid="back-to-holdings"
            >
              <ArrowLeft className="h-4 w-4 mr-1" />
              返回
            </Button>
          </Link>
          <div className="flex items-center">
            <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mr-4">
              <span className="text-primary text-lg font-medium">
                {getStockIconText(stock.name)}
              </span>
            </div>
            <div>
              <h1 className="text-2xl font-bold" data-testid="stock-name">
                {stock.name} ({stock.code})
              </h1>
              <p className="text-muted-foreground">{stock.exchange}</p>
            </div>
          </div>
        </div>
        <div className="flex gap-3">
          <Link href="/trading">
            <Button data-testid="trade-stock">
              <DollarSign className="mr-2 h-4 w-4" />
              交易
            </Button>
          </Link>
        </div>
      </div>

      {/* Price Info */}
      <Card className="p-6 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div>
            <p className="text-sm text-muted-foreground mb-1">当前价格</p>
            <div className="flex items-baseline">
              <span className="text-3xl font-bold" data-testid="current-price">
                ¥{stock.currentPrice}
              </span>
              <div
                className={`ml-3 flex items-center ${isPositive ? 'text-success' : 'text-destructive'}`}
              >
                {isPositive ? (
                  <ArrowUpRight className="h-4 w-4 mr-1" />
                ) : (
                  <ArrowDownRight className="h-4 w-4 mr-1" />
                )}
                <span data-testid="price-change">
                  {formatPercent(changePercent)}
                </span>
              </div>
            </div>
          </div>

          <div>
            <p className="text-sm text-muted-foreground mb-1">52周最高/最低</p>
            <div className="space-y-1">
              <div className="flex justify-between">
                <span className="text-sm">最高:</span>
                <span
                  className="font-medium text-destructive"
                  data-testid="52w-high"
                >
                  ¥{marketData.high52w.toFixed(2)}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm">最低:</span>
                <span
                  className="font-medium text-success"
                  data-testid="52w-low"
                >
                  ¥{marketData.low52w.toFixed(2)}
                </span>
              </div>
            </div>
          </div>

          <div>
            <p className="text-sm text-muted-foreground mb-1">成交量/成交额</p>
            <div className="space-y-1">
              <div className="flex justify-between">
                <span className="text-sm">成交量:</span>
                <span className="font-medium" data-testid="volume">
                  {marketData.volume}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="text-sm">成交额:</span>
                <span className="font-medium" data-testid="turnover">
                  {marketData.turnover}
                </span>
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* Stock Chart */}
      <StockChart
        stockCode={stock.code}
        stockName={stock.name}
        currentPrice={parseFloat(stock.currentPrice)}
        className="mb-6"
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* My Position */}
        {holding ? (
          <Card className="p-6">
            <h3 className="text-lg font-semibold mb-4 flex items-center">
              <Users className="mr-2 h-5 w-5" />
              我的持仓
            </h3>
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-sm text-muted-foreground">持仓数量</p>
                  <p
                    className="text-lg font-bold"
                    data-testid="holding-quantity"
                  >
                    {holding.quantity.toLocaleString()}股
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">成本价</p>
                  <p className="text-lg font-bold" data-testid="holding-cost">
                    {formatCurrency(holding.averageCost)}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">市值</p>
                  <p className="text-lg font-bold" data-testid="holding-value">
                    {formatCurrency(holdingValue)}
                  </p>
                </div>
                <div>
                  <p className="text-sm text-muted-foreground">盈亏</p>
                  <div>
                    <p
                      className={`text-lg font-bold ${holdingPnL >= 0 ? 'text-success' : 'text-destructive'}`}
                      data-testid="holding-pnl"
                    >
                      {holdingPnL >= 0 ? '+' : ''}
                      {formatCurrency(holdingPnL)}
                    </p>
                    <p
                      className={`text-sm ${holdingPnL >= 0 ? 'text-success' : 'text-destructive'}`}
                    >
                      ({formatPercent(holdingPnLPercent)})
                    </p>
                  </div>
                </div>
              </div>
              <div className="flex gap-2 pt-2">
                <Link href="/trading">
                  <Button size="sm" data-testid="buy-more">
                    加仓
                  </Button>
                </Link>
                <Link href="/trading">
                  <Button variant="outline" size="sm" data-testid="sell-some">
                    减仓
                  </Button>
                </Link>
                <Link href="/liquidation">
                  <Button
                    variant="destructive"
                    size="sm"
                    data-testid="liquidate-position"
                  >
                    清仓
                  </Button>
                </Link>
              </div>
            </div>
          </Card>
        ) : (
          <Card className="p-6">
            <h3 className="text-lg font-semibold mb-4 flex items-center">
              <Users className="mr-2 h-5 w-5" />
              我的持仓
            </h3>
            <div className="text-center py-8">
              <p className="text-muted-foreground mb-4">您暂未持有该股票</p>
              <Link href="/trading">
                <Button data-testid="buy-stock">
                  <DollarSign className="mr-2 h-4 w-4" />
                  买入股票
                </Button>
              </Link>
            </div>
          </Card>
        )}

        {/* Market Info */}
        <Card className="p-6">
          <h3 className="text-lg font-semibold mb-4 flex items-center">
            <Building2 className="mr-2 h-5 w-5" />
            市场信息
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex justify-between">
              <span className="text-sm text-muted-foreground">市盈率:</span>
              <span className="font-medium" data-testid="pe-ratio">
                {marketData.pe}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-sm text-muted-foreground">市净率:</span>
              <span className="font-medium" data-testid="pb-ratio">
                {marketData.pb}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-sm text-muted-foreground">总市值:</span>
              <span className="font-medium" data-testid="market-cap">
                {marketData.marketCap}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-sm text-muted-foreground">股息率:</span>
              <span className="font-medium" data-testid="dividend">
                {marketData.dividend}
              </span>
            </div>
          </div>
        </Card>
      </div>

      {/* Transaction History */}
      <Card className="p-6 mt-6">
        <h3 className="text-lg font-semibold mb-4 flex items-center">
          <Clock className="mr-2 h-5 w-5" />
          交易记录
        </h3>
        <div className="space-y-3">
          {stockTransactions.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              暂无该股票的交易记录
            </div>
          ) : (
            stockTransactions.slice(0, 10).map(transaction => (
              <div
                key={transaction.id}
                className="flex items-center justify-between py-3 border-b border-border last:border-b-0"
                data-testid={`stock-transaction-${transaction.id}`}
              >
                <div className="flex items-center">
                  <Badge
                    className={
                      transaction.type === 'buy'
                        ? 'bg-primary/10 text-primary'
                        : 'bg-destructive/10 text-destructive'
                    }
                  >
                    {transaction.type === 'buy' ? '买入' : '卖出'}
                  </Badge>
                  <div className="ml-3">
                    <p className="font-medium">
                      {transaction.quantity}股 @{' '}
                      {formatCurrency(transaction.price)}
                    </p>
                    <p className="text-sm text-muted-foreground">
                      {new Date(transaction.createdAt).toLocaleString()}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="font-medium">
                    {formatCurrency(transaction.totalAmount)}
                  </p>
                  <p className="text-sm text-muted-foreground">
                    手续费: {formatCurrency(transaction.fees)}
                  </p>
                </div>
              </div>
            ))
          )}
        </div>
      </Card>
    </div>
  );
}
