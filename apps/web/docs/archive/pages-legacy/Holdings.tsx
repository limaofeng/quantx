import { useQuery, useMutation } from '@apollo/client/react';
import {
  Download,
  Plus,
  TrendingUp,
  TrendingDown,
  MoreHorizontal,
} from 'lucide-react';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';

import { GET_HOLDINGS, LIQUIDATE_HOLDING } from '@/graphql/queries';
import {
  BackendPosition,
  transformPositionsToHoldings,
  safeNumber,
  formatCurrency,
  formatPercent,
} from '@/lib/dataTransform';

// 获取股票图标文字（从第一个和最后一个字符）
function getStockIconText(name: string): string {
  if (!name || name.length === 0) return '?';
  if (name.length === 1) return name;
  return name.charAt(0) + name.charAt(name.length - 1);
}

export default function Holdings() {
  const { toast } = useToast();

  const {
    data: positionsData,
    loading: isLoading,
    refetch,
    error,
  } = useQuery(GET_HOLDINGS, {
    errorPolicy: 'all',
  });

  const [liquidateHolding] = useMutation(LIQUIDATE_HOLDING, {
    onCompleted: data => {
      if (data.clearPosition?.success) {
        refetch();
        toast({
          title: '清仓成功',
          description: data.clearPosition?.message || '股票已成功清仓',
        });
      } else {
        toast({
          title: '清仓失败',
          description: data.clearPosition?.message || '操作失败',
          variant: 'destructive',
        });
      }
    },
    onError: error => {
      toast({
        title: '清仓失败',
        description: error.message || '请稍后重试',
        variant: 'destructive',
      });
    },
  });

  // 转换后端数据格式
  const positions: BackendPosition[] = positionsData?.positions || [];
  const holdings = transformPositionsToHoldings(positions);

  // Calculate summary metrics using backend data
  let totalValue = 0;
  let totalCost = 0;
  let totalPnL = 0;
  let profitableCount = 0;
  let losingCount = 0;

  positions.forEach(position => {
    const marketValue = safeNumber(position.marketValue, 0);
    const cost =
      safeNumber(position.avgPrice, 0) * safeNumber(position.volume, 0);
    const profitLoss = safeNumber(position.profitLoss, 0);

    totalValue += marketValue;
    totalCost += cost;
    totalPnL += profitLoss;

    if (profitLoss > 0) {
      profitableCount++;
    } else if (profitLoss < 0) {
      losingCount++;
    }
  });

  const todayPnLPercent = 2.8; // Mock today's P&L percentage - TODO: 获取真实数据

  if (isLoading) {
    return <div>加载持仓数据中...</div>;
  }

  if (error) {
    return (
      <div className="text-center p-8">
        <p className="text-destructive mb-4">加载持仓数据失败</p>
        <p className="text-muted-foreground text-sm mb-4">{error.message}</p>
        <Button onClick={() => refetch()}>重新加载</Button>
      </div>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <div>
          <h3 className="text-xl font-semibold">持仓管理</h3>
          <p className="text-muted-foreground">查看和管理您的股票持仓</p>
        </div>
        <div className="flex gap-3">
          <Button variant="secondary" data-testid="export-holdings">
            <Download className="mr-2 h-4 w-4" />
            导出
          </Button>
          <Link href="/trading">
            <Button data-testid="buy-stocks">
              <Plus className="mr-2 h-4 w-4" />
              买入股票
            </Button>
          </Link>
        </div>
      </div>

      {/* Holdings Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">持仓市值</p>
          <p
            className="text-xl font-bold text-foreground"
            data-testid="holdings-total-value"
          >
            {formatCurrency(totalValue)}
          </p>
          <p
            className="text-sm text-success mt-1"
            data-testid="holdings-today-change"
          >
            +{todayPnLPercent}% 今日
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">总盈亏</p>
          <p
            className={`text-xl font-bold ${totalPnL >= 0 ? 'text-success' : 'text-destructive'}`}
            data-testid="holdings-total-pnl"
          >
            {totalPnL >= 0 ? '+' : ''}
            {formatCurrency(totalPnL)}
          </p>
          <p
            className="text-sm text-muted-foreground mt-1"
            data-testid="holdings-total-pnl-percent"
          >
            {totalCost > 0
              ? formatPercent((totalPnL / totalCost) * 100)
              : '0.00%'}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-sm text-muted-foreground">股票数量</p>
          <p
            className="text-xl font-bold text-foreground"
            data-testid="holdings-count"
          >
            {holdings.length}
          </p>
          <p className="text-sm text-muted-foreground mt-1">只股票</p>
        </Card>
      </div>

      {/* Holdings Cards */}
      {holdings.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-muted-foreground">暂无持仓数据</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {positions.map(position => {
            const marketValue = safeNumber(position.marketValue, 0);
            const cost =
              safeNumber(position.avgPrice, 0) * safeNumber(position.volume, 0);
            const pnl = safeNumber(position.profitLoss, 0);
            const pnlPercent = safeNumber(position.profitRate, 0);
            const isProfitable = pnl >= 0;
            const currentPrice = safeNumber(position.lastPrice, 0);
            const avgPrice = safeNumber(position.avgPrice, 0);
            const volume = safeNumber(position.volume, 0);

            return (
              <Card
                key={position.id}
                className="p-4 hover:shadow-md transition-shadow"
                data-testid={`holding-card-${position.stockCode}`}
              >
                <div className="flex items-center justify-between">
                  {/* 左侧：股票信息 */}
                  <Link href={`/stock/${position.stockCode}`}>
                    <div className="flex items-center cursor-pointer hover:bg-muted/50 rounded p-2 -m-2">
                      <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center mr-4">
                        <span className="text-primary text-base font-bold">
                          {getStockIconText(position.stockName)}
                        </span>
                      </div>
                      <div>
                        <div className="font-semibold text-lg">
                          {position.stockName}
                        </div>
                        <div className="text-sm text-muted-foreground">
                          {position.stockCode}
                        </div>
                      </div>
                    </div>
                  </Link>

                  {/* 中间：价格和持仓信息 */}
                  <div className="hidden sm:flex flex-1 justify-around mx-8">
                    <div className="text-center">
                      <div className="text-sm text-muted-foreground">
                        当前价格
                      </div>
                      <div
                        className="font-medium text-lg"
                        data-testid={`holding-${position.stockCode}-price`}
                      >
                        {formatCurrency(currentPrice)}
                      </div>
                    </div>

                    <div className="text-center">
                      <div className="text-sm text-muted-foreground">持仓</div>
                      <div
                        className="font-medium"
                        data-testid={`holding-${position.stockCode}-quantity`}
                      >
                        {volume.toLocaleString()}股
                      </div>
                    </div>

                    <div className="text-center">
                      <div className="text-sm text-muted-foreground">
                        成本价
                      </div>
                      <div data-testid={`holding-${position.stockCode}-cost`}>
                        {formatCurrency(avgPrice)}
                      </div>
                    </div>

                    <div className="text-center">
                      <div className="text-sm text-muted-foreground">市值</div>
                      <div
                        className="font-medium"
                        data-testid={`holding-${position.stockCode}-value`}
                      >
                        {formatCurrency(marketValue)}
                      </div>
                    </div>
                  </div>

                  {/* 右侧：盈亏和操作 */}
                  <div className="flex items-center gap-4">
                    {/* 盈亏信息 */}
                    <div className="text-right">
                      <div className="flex items-center gap-2">
                        {isProfitable ? (
                          <TrendingUp className="h-4 w-4 text-success" />
                        ) : (
                          <TrendingDown className="h-4 w-4 text-destructive" />
                        )}
                        <div>
                          <div
                            className={`font-semibold ${isProfitable ? 'text-success' : 'text-destructive'}`}
                            data-testid={`holding-${position.stockCode}-pnl`}
                          >
                            {pnl >= 0 ? '+' : ''}
                            {formatCurrency(pnl)}
                          </div>
                          <div
                            className={`text-sm ${isProfitable ? 'text-success' : 'text-destructive'}`}
                          >
                            {formatPercent(pnlPercent)}
                          </div>
                        </div>
                      </div>
                    </div>

                    {/* 操作按钮 */}
                    <div className="hidden sm:flex gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        data-testid={`sell-${position.stockCode}`}
                      >
                        卖出
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() =>
                          liquidateHolding({
                            variables: { positionId: parseInt(position.id) },
                          })
                        }
                        disabled={false}
                        data-testid={`liquidate-${position.stockCode}`}
                      >
                        清仓
                      </Button>
                    </div>
                  </div>
                </div>

                {/* 移动端信息和操作（小屏幕显示） */}
                <div className="sm:hidden mt-4 pt-4 border-t space-y-3">
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="text-muted-foreground">当前价格: </span>
                      <span
                        className="font-medium"
                        data-testid={`mobile-holding-${position.stockCode}-price`}
                      >
                        {formatCurrency(currentPrice)}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">持仓: </span>
                      <span
                        className="font-medium"
                        data-testid={`mobile-holding-${position.stockCode}-quantity`}
                      >
                        {volume.toLocaleString()}股
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">成本价: </span>
                      <span
                        data-testid={`mobile-holding-${position.stockCode}-cost`}
                      >
                        {formatCurrency(avgPrice)}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">市值: </span>
                      <span
                        className="font-medium"
                        data-testid={`mobile-holding-${position.stockCode}-value`}
                      >
                        {formatCurrency(marketValue)}
                      </span>
                    </div>
                  </div>

                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      className="flex-1"
                      data-testid={`mobile-sell-${position.stockCode}`}
                    >
                      卖出
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="flex-1"
                      onClick={() =>
                        liquidateHolding({
                          variables: { positionId: parseInt(position.id) },
                        })
                      }
                      disabled={false}
                      data-testid={`mobile-liquidate-${position.stockCode}`}
                    >
                      清仓
                    </Button>
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
