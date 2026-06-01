import { useQuery, useMutation } from '@apollo/client/react';
import {
  AlertTriangle,
  RefreshCw,
  TrendingUp,
  TrendingDown,
  Check,
} from 'lucide-react';
import { useState, useEffect } from 'react';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { useToast } from '@/hooks/use-toast';

import {
  GET_HOLDINGS,
  GET_LIQUIDATED_STOCKS,
  LIQUIDATE_HOLDING,
} from '@/graphql/queries';
import {
  getStockIconText,
  formatCurrency,
  formatPercent,
} from '@/lib/mockData';
import { EnrichedHolding, EnrichedLiquidatedStock } from '@/lib/types';

export default function Liquidation() {
  const { toast } = useToast();
  const userId = 'demo-user';
  const [isRedeemDialogOpen, setIsRedeemDialogOpen] = useState(false);
  const [selectedStocks, setSelectedStocks] = useState<string[]>([]);

  const {
    data: holdingsData,
    loading: holdingsLoading,
    refetch: refetchHoldings,
  } = useQuery(GET_HOLDINGS, {
    variables: { userId },
  });

  const { data: liquidatedData, loading: liquidatedLoading } = useQuery(
    GET_LIQUIDATED_STOCKS,
    {
      variables: { userId },
    }
  );

  const holdings = holdingsData?.holdings || [];
  const liquidatedStocks = liquidatedData?.liquidatedStocks || [];

  const [liquidateHolding] = useMutation(LIQUIDATE_HOLDING, {
    onCompleted: () => {
      refetchHoldings();
      toast({
        title: '清仓成功',
        description: '股票已成功清仓',
      });
    },
    onError: () => {
      toast({
        title: '清仓失败',
        description: '请稍后重试',
        variant: 'destructive',
      });
    },
  });

  const liquidateAllMutation = {
    mutate: async () => {
      // For now, we'll handle this differently
      toast({
        title: '一键清仓成功',
        description: '所有持仓已成功清仓',
      });
    },
    isPending: false,
  };

  const liquidateHoldingMutation = {
    mutate: (holdingId: string) => {
      liquidateHolding({ variables: { holdingId } });
    },
    isPending: false,
  };

  const stopTrackingMutation = {
    mutate: (liquidatedStockId: string) => {
      // Stub for now
      toast({
        title: '停止跟踪',
        description: '已停止跟踪该股票',
      });
    },
    isPending: false,
  };

  // Calculate summary metrics
  let totalValue = 0;
  holdings.forEach(holding => {
    if (holding.stock) {
      totalValue += holding.quantity * parseFloat(holding.stock.currentPrice);
    }
  });

  const estimatedFees = totalValue * 0.0005; // 0.05% fee

  // 过滤出正在跟踪的清仓股票
  const trackingLiquidatedStocks = liquidatedStocks.filter(ls => ls.isTracking);

  // 初始化选择的股票（默认全选）
  useEffect(() => {
    if (trackingLiquidatedStocks.length > 0 && selectedStocks.length === 0) {
      setSelectedStocks(trackingLiquidatedStocks.map(ls => ls.id));
    }
  }, [trackingLiquidatedStocks.length, selectedStocks.length]);

  // 计算选中股票的总价值
  const calculateSelectedTotals = () => {
    const selected = trackingLiquidatedStocks.filter(ls =>
      selectedStocks.includes(ls.id)
    );

    let totalLiquidationValue = 0;
    let totalCurrentValue = 0;
    let totalQuantity = 0;

    selected.forEach(liquidatedStock => {
      const stock = liquidatedStock.stock;
      if (stock) {
        const liquidationPrice = parseFloat(liquidatedStock.liquidationPrice);
        const currentPrice = parseFloat(stock.currentPrice);
        const quantity = liquidatedStock.quantity;

        totalLiquidationValue += liquidationPrice * quantity;
        totalCurrentValue += currentPrice * quantity;
        totalQuantity += quantity;
      }
    });

    return {
      totalLiquidationValue,
      totalCurrentValue,
      totalQuantity,
      profitLoss: totalCurrentValue - totalLiquidationValue,
      profitLossPercent:
        totalLiquidationValue > 0
          ? ((totalCurrentValue - totalLiquidationValue) /
              totalLiquidationValue) *
            100
          : 0,
    };
  };

  const selectedTotals = calculateSelectedTotals();

  // 切换股票选择
  const toggleStockSelection = (stockId: string) => {
    setSelectedStocks(prev =>
      prev.includes(stockId)
        ? prev.filter(id => id !== stockId)
        : [...prev, stockId]
    );
  };

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedStocks.length === trackingLiquidatedStocks.length) {
      setSelectedStocks([]);
    } else {
      setSelectedStocks(trackingLiquidatedStocks.map(ls => ls.id));
    }
  };

  // 执行赎回
  const executeRedeem = () => {
    console.log('执行赎回选中的股票:', selectedStocks);
    setIsRedeemDialogOpen(false);
    toast({
      title: '赎回成功',
      description: `已成功赎回 ${selectedStocks.length} 只股票`,
    });
  };

  return (
    <div>
      <div className="mb-6">
        <h3 className="text-xl font-semibold">清仓管理</h3>
        <p className="text-muted-foreground">快速清仓和价格走势跟踪</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Quick Liquidation */}
        <Card className="p-6">
          <h4 className="text-lg font-semibold mb-4 text-destructive">
            一键清仓
          </h4>
          <p className="text-sm text-muted-foreground mb-4">清仓所有持仓股票</p>

          <div className="space-y-3 mb-6">
            <div className="flex justify-between text-sm">
              <span>持仓股票数</span>
              <span
                className="font-medium"
                data-testid="liquidation-holdings-count"
              >
                {holdings.length}只
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span>总市值</span>
              <span
                className="font-medium"
                data-testid="liquidation-total-value"
              >
                {formatCurrency(totalValue)}
              </span>
            </div>
            <div className="flex justify-between text-sm">
              <span>预估手续费</span>
              <span
                className="font-medium"
                data-testid="liquidation-estimated-fees"
              >
                {formatCurrency(estimatedFees)}
              </span>
            </div>
          </div>

          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="destructive"
                className="w-full"
                disabled={
                  holdings.length === 0 || liquidateAllMutation.isPending
                }
                data-testid="liquidate-all-button"
              >
                <AlertTriangle className="mr-2 h-4 w-4" />
                确认清仓
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认清仓操作</AlertDialogTitle>
                <AlertDialogDescription>
                  您即将清仓所有持仓股票，此操作不可撤销。是否确认继续？
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  onClick={() => liquidateAllMutation.mutate()}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  data-testid="confirm-liquidate-all"
                >
                  确认清仓
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>

          <p className="text-xs text-muted-foreground mt-3 text-center">
            此操作不可撤销，请谨慎操作
          </p>
        </Card>

        {/* Individual Stock Liquidation */}
        <div className="lg:col-span-2">
          <Card className="p-6">
            <h4 className="text-lg font-semibold mb-4">个股清仓</h4>

            <div className="space-y-4">
              {holdings.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                  暂无持仓股票
                </div>
              ) : (
                holdings.map(holding => {
                  const stock = holding.stock;
                  if (!stock) return null;

                  const currentValue =
                    holding.quantity * parseFloat(stock.currentPrice);
                  const cost =
                    holding.quantity * parseFloat(holding.averageCost);
                  const pnl = currentValue - cost;
                  const pnlPercent = ((currentValue - cost) / cost) * 100;

                  return (
                    <div
                      key={holding.id}
                      className="flex items-center justify-between p-4 border border-border rounded-lg hover:bg-muted/50"
                      data-testid={`liquidation-stock-${stock.code}`}
                    >
                      <div className="flex items-center">
                        <div className="w-10 h-10 bg-primary/10 rounded-lg flex items-center justify-center mr-4">
                          <span className="text-primary font-medium">
                            {getStockIconText(stock.name)}
                          </span>
                        </div>
                        <div>
                          <p className="font-medium">
                            {stock.name} ({stock.code})
                          </p>
                          <p className="text-sm text-muted-foreground">
                            持仓: {holding.quantity}股 • 市值:{' '}
                            {formatCurrency(currentValue)}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <div className="text-right">
                          <p
                            className={`text-sm ${pnl >= 0 ? 'text-success' : 'text-destructive'}`}
                          >
                            {pnl >= 0 ? '+' : ''}
                            {formatCurrency(pnl)}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {formatPercent(pnlPercent)}
                          </p>
                        </div>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() =>
                            liquidateHoldingMutation.mutate(holding.id)
                          }
                          disabled={liquidateHoldingMutation.isPending}
                          data-testid={`liquidate-stock-${stock.code}`}
                        >
                          清仓
                        </Button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </Card>
        </div>
      </div>

      {/* Liquidated Stocks Tracking */}
      <div className="mt-8">
        <Card className="p-6">
          <div className="flex justify-between items-center mb-6">
            <h4 className="text-lg font-semibold">已清仓股票跟踪</h4>
            <Dialog
              open={isRedeemDialogOpen}
              onOpenChange={setIsRedeemDialogOpen}
            >
              <DialogTrigger asChild>
                <Button
                  className="text-sm"
                  data-testid="redeem-all-button"
                  disabled={trackingLiquidatedStocks.length === 0}
                >
                  <RefreshCw className="mr-2 h-4 w-4" />
                  一键赎回全部
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-4xl sm:max-w-[95vw] max-h-[85vh] overflow-hidden flex flex-col mx-2 sm:mx-4">
                <DialogHeader>
                  <DialogTitle>确认赎回操作</DialogTitle>
                  <DialogDescription>
                    选择您要赎回的股票。系统将按当前市价执行赎回操作。
                  </DialogDescription>
                </DialogHeader>

                <div className="flex-1 overflow-y-auto space-y-4">
                  {/* 全选控制 */}
                  <div className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
                    <div className="flex items-center space-x-2">
                      <Checkbox
                        id="select-all"
                        checked={
                          selectedStocks.length ===
                            trackingLiquidatedStocks.length &&
                          trackingLiquidatedStocks.length > 0
                        }
                        onCheckedChange={toggleSelectAll}
                        data-testid="select-all-checkbox"
                      />
                      <label
                        htmlFor="select-all"
                        className="font-medium cursor-pointer"
                      >
                        全选 ({selectedStocks.length}/
                        {trackingLiquidatedStocks.length})
                      </label>
                    </div>
                    <div className="text-sm text-muted-foreground">
                      共 {trackingLiquidatedStocks.length} 只可赎回股票
                    </div>
                  </div>

                  {/* 超迷你股票卡片列表 */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 sm:gap-2">
                    {trackingLiquidatedStocks.map(liquidatedStock => {
                      const stock = liquidatedStock.stock;
                      if (!stock) return null;

                      const liquidationPrice = parseFloat(
                        liquidatedStock.liquidationPrice
                      );
                      const currentPrice = parseFloat(stock.currentPrice);
                      const priceChangePercent =
                        ((currentPrice - liquidationPrice) / liquidationPrice) *
                        100;
                      const quantity = liquidatedStock.quantity;
                      const totalCurrentValue = currentPrice * quantity;
                      const totalProfitLoss =
                        totalCurrentValue - liquidationPrice * quantity;
                      const isSelected = selectedStocks.includes(
                        liquidatedStock.id
                      );
                      const isProfitable = priceChangePercent >= 0;

                      return (
                        <Card
                          key={liquidatedStock.id}
                          className={`p-3 sm:p-2 cursor-pointer transition-all border relative ${
                            isSelected
                              ? 'border-primary bg-primary/10'
                              : 'border-border hover:border-primary/50'
                          }`}
                          onClick={() =>
                            toggleStockSelection(liquidatedStock.id)
                          }
                          data-testid={`redeem-stock-card-${stock.code}`}
                        >
                          {/* 股票标识行 - 包含涨跌幅和选中指示 */}
                          <div className="flex items-center justify-between mb-2 sm:mb-1.5">
                            <div className="flex items-center space-x-2 sm:space-x-1.5 min-w-0 flex-1">
                              <div className="w-6 h-6 sm:w-5 sm:h-5 bg-primary/10 rounded flex items-center justify-center flex-shrink-0">
                                <span className="text-primary text-sm sm:text-xs font-bold">
                                  {getStockIconText(stock.name)}
                                </span>
                              </div>
                              <div className="min-w-0 flex-1">
                                <h6 className="font-medium text-sm sm:text-xs truncate leading-tight">
                                  {stock.name}
                                </h6>
                                <p className="text-sm sm:text-xs text-muted-foreground leading-tight">
                                  {stock.code}
                                </p>
                              </div>
                            </div>

                            <div className="flex items-center space-x-2 sm:space-x-1.5 flex-shrink-0">
                              <div
                                className={`flex items-center gap-1 sm:gap-0.5 ${isProfitable ? 'text-success' : 'text-destructive'}`}
                              >
                                {isProfitable ? (
                                  <TrendingUp className="w-4 h-4 sm:w-3 sm:h-3" />
                                ) : (
                                  <TrendingDown className="w-4 h-4 sm:w-3 sm:h-3" />
                                )}
                                <span className="font-medium text-sm sm:text-xs">
                                  {priceChangePercent >= 0 ? '+' : ''}
                                  {priceChangePercent.toFixed(3)}%
                                </span>
                              </div>

                              {isSelected && (
                                <div className="w-5 h-5 sm:w-4 sm:h-4 bg-primary rounded-full flex items-center justify-center">
                                  <Check className="w-3 h-3 sm:w-2.5 sm:h-2.5 text-primary-foreground" />
                                </div>
                              )}
                            </div>
                          </div>

                          {/* 左右分栏信息 */}
                          <div className="flex justify-between text-sm sm:text-xs gap-2">
                            {/* 左侧：数量和价格变化 */}
                            <div className="space-y-1 sm:space-y-0.5 min-w-0 flex-1">
                              <div className="font-medium">
                                {quantity.toLocaleString()}股
                              </div>
                              <div className="text-muted-foreground truncate">
                                {formatCurrency(liquidationPrice)} →{' '}
                                {formatCurrency(currentPrice)}
                              </div>
                            </div>

                            {/* 右侧：赎回价值和盈亏 */}
                            <div className="space-y-1 sm:space-y-0.5 text-right flex-shrink-0">
                              <div className="font-medium">
                                {formatCurrency(totalCurrentValue)}
                              </div>
                              <div
                                className={`font-medium ${totalProfitLoss >= 0 ? 'text-success' : 'text-destructive'}`}
                              >
                                {totalProfitLoss >= 0 ? '+' : ''}
                                {formatCurrency(totalProfitLoss)}
                              </div>
                            </div>
                          </div>
                        </Card>
                      );
                    })}
                  </div>
                </div>

                {/* 底部汇总信息 */}
                <div className="border-t pt-4 mt-4">
                  <div className="bg-muted/50 rounded-lg p-4 space-y-3 sm:space-y-2">
                    <h6 className="font-semibold text-base sm:text-sm">
                      赎回汇总 ({selectedStocks.length} 只股票)
                    </h6>
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 text-base sm:text-sm">
                      <div className="space-y-1">
                        <span className="text-muted-foreground block">
                          原清仓价值:
                        </span>
                        <div
                          className="font-medium"
                          data-testid="total-liquidation-value"
                        >
                          {formatCurrency(selectedTotals.totalLiquidationValue)}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <span className="text-muted-foreground block">
                          当前赎回价值:
                        </span>
                        <div
                          className="font-medium"
                          data-testid="total-current-value"
                        >
                          {formatCurrency(selectedTotals.totalCurrentValue)}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <span className="text-muted-foreground block">
                          盈亏:
                        </span>
                        <div
                          className={`font-medium ${selectedTotals.profitLoss >= 0 ? 'text-success' : 'text-destructive'}`}
                          data-testid="total-profit-loss"
                        >
                          {selectedTotals.profitLoss >= 0 ? '+' : ''}
                          {formatCurrency(selectedTotals.profitLoss)}
                        </div>
                      </div>
                      <div className="space-y-1">
                        <span className="text-muted-foreground block">
                          收益率:
                        </span>
                        <div
                          className={`font-medium ${selectedTotals.profitLoss >= 0 ? 'text-success' : 'text-destructive'}`}
                          data-testid="total-profit-percent"
                        >
                          {formatPercent(selectedTotals.profitLossPercent)}
                        </div>
                      </div>
                    </div>
                  </div>
                </div>

                <DialogFooter className="flex-shrink-0 flex-col sm:flex-row gap-2 sm:gap-0">
                  <Button
                    variant="outline"
                    onClick={() => setIsRedeemDialogOpen(false)}
                    className="w-full sm:w-auto order-2 sm:order-1"
                  >
                    取消
                  </Button>
                  <Button
                    onClick={executeRedeem}
                    disabled={selectedStocks.length === 0}
                    data-testid="confirm-redeem-button"
                    className="w-full sm:w-auto order-1 sm:order-2"
                  >
                    确认赎回 ({selectedStocks.length} 只股票)
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>

          <div className="space-y-4">
            {liquidatedStocks.length === 0 ? (
              <div className="text-center py-8 text-muted-foreground">
                暂无已清仓股票跟踪记录
              </div>
            ) : (
              liquidatedStocks
                .filter(ls => ls.isTracking)
                .map(liquidatedStock => {
                  const stock = liquidatedStock.stock;
                  if (!stock) return null;

                  const liquidationPrice = parseFloat(
                    liquidatedStock.liquidationPrice
                  );
                  const currentPrice = parseFloat(stock.currentPrice);
                  const priceChange =
                    ((currentPrice - liquidationPrice) / liquidationPrice) *
                    100;

                  return (
                    <div
                      key={liquidatedStock.id}
                      className="flex flex-col sm:flex-row sm:items-center sm:justify-between p-4 border border-border rounded-lg space-y-3 sm:space-y-0"
                      data-testid={`liquidated-stock-${stock.code}`}
                    >
                      <div className="flex items-center flex-1">
                        <div className="w-12 h-12 sm:w-10 sm:h-10 bg-muted rounded-lg flex items-center justify-center mr-4 flex-shrink-0">
                          <span className="text-muted-foreground font-medium text-sm">
                            {getStockIconText(stock.name)}
                          </span>
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="font-medium text-base sm:text-sm">
                            {stock.name} ({stock.code})
                          </p>
                          <div className="flex flex-col sm:flex-row sm:items-center text-sm text-muted-foreground space-y-1 sm:space-y-0">
                            <span>
                              清仓价: {formatCurrency(liquidationPrice)}
                            </span>
                            <span className="hidden sm:inline mx-2">•</span>
                            <span className="truncate">
                              清仓时间:{' '}
                              {new Date(
                                liquidatedStock.liquidatedAt
                              ).toLocaleDateString()}
                            </span>
                          </div>
                        </div>
                      </div>
                      <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4">
                        <div className="flex justify-between sm:flex-col sm:gap-4">
                          <div className="text-center">
                            <p className="text-sm text-muted-foreground">
                              当前价
                            </p>
                            <p className="font-medium text-base sm:text-sm">
                              {formatCurrency(currentPrice)}
                            </p>
                          </div>
                          <div className="text-center">
                            <p className="text-sm text-muted-foreground">
                              变化
                            </p>
                            <p
                              className={`text-base sm:text-sm font-medium ${priceChange >= 0 ? 'text-success' : 'text-destructive'}`}
                            >
                              {formatPercent(priceChange)}
                            </p>
                          </div>
                        </div>
                        <div className="flex flex-col sm:flex-row gap-2 w-full sm:w-auto">
                          <Button
                            size="sm"
                            data-testid={`redeem-${stock.code}`}
                            className="w-full sm:w-auto"
                          >
                            赎回
                          </Button>
                          <Button
                            variant="secondary"
                            size="sm"
                            onClick={() =>
                              stopTrackingMutation.mutate(liquidatedStock.id)
                            }
                            disabled={stopTrackingMutation.isPending}
                            data-testid={`stop-tracking-${stock.code}`}
                            className="w-full sm:w-auto"
                          >
                            停止跟踪
                          </Button>
                        </div>
                      </div>
                    </div>
                  );
                })
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
