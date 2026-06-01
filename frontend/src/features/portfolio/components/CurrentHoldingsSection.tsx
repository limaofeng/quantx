import { AlertTriangle, TrendingUp, TrendingDown, Package } from 'lucide-react';
import { useState } from 'react';

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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { cn } from '@/utils/cn';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import type { Position } from '../types';

interface CurrentHoldingsSectionProps {
  holdings: Position[];
  selectedHoldings: string[];
  onSelectionChange: (selected: string[]) => void;
  onLiquidateSelected: () => void;
  liquidateMultiple: (holdingIds: string[]) => Promise<void>;
}

// 获取股票图标文字
function getStockIconText(name: string): string {
  if (!name || name.length === 0) return '?';
  if (name.length === 1) return name;
  return name.charAt(0) + name.charAt(name.length - 1);
}

export function CurrentHoldingsSection({
  holdings,
  selectedHoldings,
  onSelectionChange,
  onLiquidateSelected,
  liquidateMultiple,
}: CurrentHoldingsSectionProps) {
  const [isProcessing, setIsProcessing] = useState(false);

  const handleToggleAll = () => {
    if (selectedHoldings.length === holdings.length) {
      onSelectionChange([]);
    } else {
      onSelectionChange(holdings.map(h => h.id));
    }
  };

  const handleToggleHolding = (holdingId: string) => {
    if (selectedHoldings.includes(holdingId)) {
      onSelectionChange(selectedHoldings.filter(id => id !== holdingId));
    } else {
      onSelectionChange([...selectedHoldings, holdingId]);
    }
  };

  const handleLiquidateIndividual = async (holdingId: string) => {
    setIsProcessing(true);
    try {
      await liquidateMultiple([holdingId]);
    } finally {
      setIsProcessing(false);
    }
  };

  if (holdings.length === 0) {
    return (
      <Card className="p-12 text-center bg-background/60 backdrop-blur-sm border-muted/20">
        <div className="flex flex-col items-center gap-4">
          <div className="w-16 h-16 rounded-full bg-muted/20 flex items-center justify-center">
            <Package className="w-8 h-8 text-muted-foreground" />
          </div>
          <div>
            <h3 className="text-lg font-semibold text-foreground">暂无持仓</h3>
            <p className="text-muted-foreground mt-1">当前没有任何持仓股票</p>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* Batch Operations Bar */}
      <div className="flex items-center justify-between p-4 bg-muted/30 backdrop-blur-md rounded-xl border border-white/10 shadow-sm">
        <div className="flex items-center gap-3">
          <Checkbox
            checked={selectedHoldings.length === holdings.length}
            onCheckedChange={handleToggleAll}
            data-testid="select-all-checkbox"
          />
          <span className="text-sm font-medium">
            已选择{' '}
            <span className="text-primary">{selectedHoldings.length}</span> /{' '}
            {holdings.length} 只股票
          </span>
        </div>

        {selectedHoldings.length > 0 && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="destructive"
                size="sm"
                className="shadow-md hover:shadow-lg transition-all"
                data-testid="liquidate-selected-button"
              >
                <AlertTriangle className="mr-2 h-4 w-4" />
                清仓选中股票
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认批量清仓</AlertDialogTitle>
                <AlertDialogDescription>
                  您确定要清仓选中的{' '}
                  <span className="font-bold text-destructive">
                    {selectedHoldings.length}
                  </span>{' '}
                  只股票吗？
                  <br />
                  此操作将以市价卖出所有选中的持仓，且不可撤销。
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  onClick={onLiquidateSelected}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  确认清仓
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>

      {/* Glassmorphism Table Container */}
      <Card className="overflow-hidden border-0 shadow-lg bg-background/40 backdrop-blur-xl ring-1 ring-white/10">
        <Table>
          <TableHeader className="bg-muted/30">
            <TableRow className="hover:bg-transparent border-b border-white/5">
              <TableHead className="w-[50px]"></TableHead>
              <TableHead>股票信息</TableHead>
              <TableHead className="text-right">持仓量</TableHead>
              <TableHead className="text-right">成本/现价</TableHead>
              <TableHead className="text-right">市值</TableHead>
              <TableHead className="text-right">盈亏</TableHead>
              <TableHead className="text-right w-[100px]">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {holdings.map(holding => {
              const isProfitable = (holding.profitLoss ?? 0) >= 0;
              const isSelected = selectedHoldings.includes(holding.id);

              return (
                <TableRow
                  key={holding.id}
                  className={cn(
                    'transition-colors hover:bg-muted/30 border-b border-white/5',
                    isSelected && 'bg-primary/5 hover:bg-primary/10'
                  )}
                >
                  <TableCell>
                    <Checkbox
                      checked={isSelected}
                      onCheckedChange={() => handleToggleHolding(holding.id)}
                      data-testid={`checkbox-${holding.stockCode}`}
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 bg-gradient-to-br from-primary/20 to-primary/5 rounded-lg flex items-center justify-center shrink-0 border border-primary/10">
                        <span className="text-primary text-xs font-bold">
                          {getStockIconText(
                            holding.instrumentName || holding.stockCode
                          )}
                        </span>
                      </div>
                      <div>
                        <div className="font-medium text-sm">
                          {holding.instrumentName || holding.stockCode}
                        </div>
                        <div className="text-xs text-muted-foreground flex items-center">
                          <span className="bg-primary/10 text-primary px-1 rounded-[2px] mr-1 text-[10px]">
                            Stock
                          </span>
                          {holding.stockCode}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="font-medium text-sm">
                      {holding.volume.toLocaleString()}
                    </div>
                    <div className="text-xs text-muted-foreground">股</div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="text-sm">
                      {formatCurrency(holding.avgPrice ?? 0)}
                    </div>
                    <div className="text-xs text-muted-foreground flex items-center justify-end gap-1">
                      <span className="opacity-70">现价:</span>
                      <span>---</span>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="font-medium">
                      {formatCurrency(holding.marketValue ?? 0)}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div
                      className={cn(
                        'font-medium flex items-center justify-end gap-1',
                        isProfitable ? 'text-success' : 'text-destructive'
                      )}
                    >
                      {isProfitable ? (
                        <TrendingUp className="h-3 w-3" />
                      ) : (
                        <TrendingDown className="h-3 w-3" />
                      )}
                      {isProfitable ? '+' : ''}
                      {formatCurrency(holding.profitLoss ?? 0)}
                    </div>
                    <div
                      className={cn(
                        'text-xs',
                        isProfitable ? 'text-success' : 'text-destructive'
                      )}
                    >
                      {formatPercent(holding.profitRate ?? 0)}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-destructive hover:text-destructive hover:bg-destructive/10"
                          disabled={isProcessing}
                          data-testid={`liquidate-${holding.stockCode}`}
                        >
                          <AlertTriangle className="h-4 w-4" />
                          <span className="sr-only">清仓</span>
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>
                            确认清仓{' '}
                            {holding.instrumentName || holding.stockCode}
                          </AlertDialogTitle>
                          <AlertDialogDescription>
                            您确定要卖出{' '}
                            {holding.instrumentName || holding.stockCode} (
                            {holding.stockCode}) 吗？
                            <br />
                            <div className="mt-2 text-sm bg-muted p-2 rounded-md">
                              <p>
                                持仓数量: {holding.volume.toLocaleString()} 股
                              </p>
                              <p>
                                当前市值:{' '}
                                {formatCurrency(holding.marketValue ?? 0)}
                              </p>
                            </div>
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>取消</AlertDialogCancel>
                          <AlertDialogAction
                            onClick={() =>
                              handleLiquidateIndividual(holding.id)
                            }
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                          >
                            确认清仓
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
