import {
  AlertTriangle,
  Package,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
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
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import type {
  LiquidationCompletionStrategy,
  LiquidationConflictStrategy,
  LiquidationExecutionOptions,
} from '../hooks/useLiquidationActions';
import type { Position } from '../types';

interface CurrentHoldingsSectionProps {
  conditionalOrderStockCodes?: Set<string>;
  holdings: Position[];
  isSubmitting?: boolean;
  selectedHoldings: string[];
  onConfigureConditionalOrder?: (holding: Position) => void;
  onSelectionChange: (selected: string[]) => void;
  onLiquidateSelected: (options: LiquidationExecutionOptions) => void;
  liquidateMultiple: (
    stockCodes: string[],
    options: LiquidationExecutionOptions
  ) => Promise<unknown>;
}

// 获取股票图标文字
function getStockIconText(name: string): string {
  if (!name || name.length === 0) return '?';
  if (name.length === 1) return name;
  return name.charAt(0) + name.charAt(name.length - 1);
}

function normalizeStockCode(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

function getStockCodePrefix(value: unknown) {
  return normalizeStockCode(value).split('.')[0] || '';
}

function hasConditionalOrder(
  conditionalOrderStockCodes: Set<string>,
  stockCode: unknown
) {
  const code = normalizeStockCode(stockCode);
  const prefix = getStockCodePrefix(code);
  return Boolean(
    code &&
    (conditionalOrderStockCodes.has(code) ||
      (prefix && conditionalOrderStockCodes.has(prefix)))
  );
}

function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function getSellableVolume(holding: Position) {
  return Math.max(0, Math.trunc(toFiniteNumber(holding.canUseVolume) ?? 0));
}

function isLiquidatable(holding: Position) {
  return getSellableVolume(holding) > 0;
}

function getEstimatedSellValue(holding: Position) {
  const sellableVolume = getSellableVolume(holding);
  if (sellableVolume <= 0) return 0;

  const volume = toFiniteNumber(holding.volume);
  const marketValue = toFiniteNumber(holding.marketValue);
  if (volume !== null && volume > 0 && marketValue !== null) {
    return (marketValue * sellableVolume) / volume;
  }

  const price =
    toFiniteNumber(holding.lastPrice) ?? toFiniteNumber(holding.avgPrice) ?? 0;
  return sellableVolume * price;
}

export function CurrentHoldingsSection({
  conditionalOrderStockCodes = new Set<string>(),
  holdings,
  isSubmitting = false,
  selectedHoldings,
  onConfigureConditionalOrder,
  onSelectionChange,
  onLiquidateSelected,
  liquidateMultiple,
}: CurrentHoldingsSectionProps) {
  const [isProcessing, setIsProcessing] = useState(false);
  const [completionStrategy, setCompletionStrategy] = useState<
    LiquidationCompletionStrategy | ''
  >('');
  const [conflictStrategy, setConflictStrategy] = useState<
    LiquidationConflictStrategy | ''
  >('');
  const [executionMode, setExecutionMode] = useState<
    'paper' | 'live' | ''
  >('');
  const hasExplicitChoices = Boolean(
    completionStrategy && conflictStrategy && executionMode
  );
  const liquidatableHoldings = holdings.filter(isLiquidatable);
  const selectedSet = new Set(selectedHoldings.map(normalizeStockCode));
  const selectedPositions = liquidatableHoldings.filter(holding =>
    selectedSet.has(normalizeStockCode(holding.stockCode))
  );
  const selectedSellableVolume = selectedPositions.reduce(
    (sum, holding) => sum + getSellableVolume(holding),
    0
  );
  const selectedEstimatedValue = selectedPositions.reduce(
    (sum, holding) => sum + getEstimatedSellValue(holding),
    0
  );
  const allSelected =
    liquidatableHoldings.length > 0 &&
    selectedHoldings.length === liquidatableHoldings.length;
  const partiallySelected =
    selectedHoldings.length > 0 &&
    selectedHoldings.length < liquidatableHoldings.length;

  const handleToggleAll = () => {
    if (allSelected) {
      onSelectionChange([]);
    } else {
      onSelectionChange(
        liquidatableHoldings.map(holding =>
          normalizeStockCode(holding.stockCode)
        )
      );
    }
  };

  const handleToggleHolding = (stockCode: string) => {
    const normalizedStockCode = normalizeStockCode(stockCode);
    if (selectedHoldings.includes(normalizedStockCode)) {
      onSelectionChange(
        selectedHoldings.filter(code => code !== normalizedStockCode)
      );
    } else {
      onSelectionChange([...selectedHoldings, normalizedStockCode]);
    }
  };

  const handleLiquidateIndividual = async (stockCode: string) => {
    setIsProcessing(true);
    try {
      if (!completionStrategy || !conflictStrategy || !executionMode) return;
      await liquidateMultiple([normalizeStockCode(stockCode)], {
        autoExitAuthorized: executionMode === 'paper',
        completionStrategy,
        conflictStrategy,
        executionMode,
      });
    } finally {
      setIsProcessing(false);
    }
  };

  const liquidationChoices = (
    <div className="mt-3 grid gap-2 rounded-md border border-border bg-muted/40 p-3 text-left text-sm">
      <label className="grid gap-1 font-medium">
        完成策略（必选）
        <select
          className="h-9 rounded border border-input bg-background px-2"
          onChange={event =>
            setCompletionStrategy(
              event.target.value as LiquidationCompletionStrategy | ''
            )
          }
          value={completionStrategy}
        >
          <option value="">请选择</option>
          <option value="AVAILABLE_NOW">仅卖确认时可用数量</option>
          <option value="UNTIL_SNAPSHOT_CLEARED">
            持续至本次持仓快照清完
          </option>
        </select>
      </label>
      <label className="grid gap-1 font-medium">
        冲突策略（必选）
        <select
          className="h-9 rounded border border-input bg-background px-2"
          onChange={event =>
            setConflictStrategy(
              event.target.value as LiquidationConflictStrategy | ''
            )
          }
          value={conflictStrategy}
        >
          <option value="">请选择</option>
          <option value="UNALLOCATED_ONLY">只卖未分配数量</option>
          <option value="REPLACE_CANCELLABLE">替换可取消计划</option>
        </select>
      </label>
      <label className="grid gap-1 font-medium">
        执行模式（必选）
        <select
          className="h-9 rounded border border-input bg-background px-2"
          onChange={event =>
            setExecutionMode(event.target.value as 'paper' | 'live' | '')
          }
          value={executionMode}
        >
          <option value="">请选择</option>
          <option value="paper">模拟</option>
          <option value="live">实盘（卖出意图需再次确认）</option>
        </select>
      </label>
      <p className="text-xs text-muted-foreground">
        持续清仓只保护本次确认时的持仓；后续新买不会自动加入。
      </p>
    </div>
  );

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
      <div className="flex items-center justify-between p-4 bg-muted/30 backdrop-blur-md rounded-md border border-white/10 shadow-sm">
        <div className="flex items-center gap-3">
          <Checkbox
            checked={
              allSelected ? true : partiallySelected ? 'indeterminate' : false
            }
            disabled={liquidatableHoldings.length === 0}
            onCheckedChange={handleToggleAll}
            data-testid="select-all-checkbox"
          />
          <span className="text-sm font-medium">
            已选择{' '}
            <span className="text-primary">{selectedHoldings.length}</span> /{' '}
            {liquidatableHoldings.length} 只可清仓股票
          </span>
        </div>

        {selectedHoldings.length > 0 && (
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button
                variant="destructive"
                size="sm"
                className="shadow-md hover:shadow-lg transition-all"
                disabled={isSubmitting || isProcessing}
                data-testid="liquidate-selected-button"
              >
                <AlertTriangle className="mr-2 h-4 w-4" />
                清仓选中股票
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>确认批量清仓</AlertDialogTitle>
                <AlertDialogDescription asChild>
                  <div>
                    您确定要清仓选中的{' '}
                    <span className="font-bold text-destructive">
                      {selectedHoldings.length}
                    </span>{' '}
                    只股票吗？
                    <br />
                    系统将先提交市价风格卖出委托，不会把委托提交成功写成已成交。
                    <div className="mt-3 rounded-md bg-muted p-3 text-sm">
                      <p>
                        可卖数量: {selectedSellableVolume.toLocaleString()} 股
                      </p>
                      <p>
                        估算委托市值: {formatCurrency(selectedEstimatedValue)}
                      </p>
                    </div>
                    {liquidationChoices}
                  </div>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>取消</AlertDialogCancel>
                <AlertDialogAction
                  disabled={!hasExplicitChoices}
                  onClick={() => {
                    if (!completionStrategy || !conflictStrategy || !executionMode)
                      return;
                    onLiquidateSelected({
                      autoExitAuthorized: executionMode === 'paper',
                      completionStrategy,
                      conflictStrategy,
                      executionMode,
                    });
                  }}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  提交清仓委托
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
              <TableHead className="text-right w-[132px]">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {holdings.map(holding => {
              const isProfitable = (holding.profitLoss ?? 0) >= 0;
              const stockCode = normalizeStockCode(holding.stockCode);
              const sellableVolume = getSellableVolume(holding);
              const rowLiquidatable = sellableVolume > 0;
              const isSelected = selectedSet.has(stockCode);
              const hasCondition = hasConditionalOrder(
                conditionalOrderStockCodes,
                stockCode
              );

              return (
                <TableRow
                  key={holding.id}
                  className={cn(
                    'transition-colors hover:bg-muted/30 border-b border-white/5',
                    isSelected && 'bg-primary/5 hover:bg-primary/10',
                    !rowLiquidatable && 'opacity-55'
                  )}
                >
                  <TableCell>
                    <Checkbox
                      checked={isSelected}
                      disabled={!rowLiquidatable}
                      onCheckedChange={() => handleToggleHolding(stockCode)}
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
                        <div className="flex items-center gap-1 text-xs text-muted-foreground">
                          <span className="bg-primary/10 text-primary px-1 rounded-[2px] text-[10px]">
                            Stock
                          </span>
                          {stockCode}
                          {hasCondition && (
                            <span className="rounded border border-rose-400/20 bg-rose-500/10 px-1.5 py-0.5 text-[10px] font-bold text-rose-200">
                              条件清仓
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="font-medium text-sm">
                      {holding.volume.toLocaleString()}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      可卖 {sellableVolume.toLocaleString()} 股
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="text-sm">
                      {formatCurrency(holding.avgPrice ?? 0)}
                    </div>
                    <div className="text-xs text-muted-foreground flex items-center justify-end gap-1">
                      <span className="opacity-70">现价:</span>
                      <span>
                        {holding.lastPrice
                          ? formatCurrency(holding.lastPrice)
                          : '--'}
                      </span>
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
                        financialToneClass(
                          holding.profitLoss ?? 0,
                          'holding'
                        )
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
                        financialToneClass(
                          holding.profitRate ?? 0,
                          'holding'
                        )
                      )}
                    >
                      {formatPercent(holding.profitRate ?? 0)}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn(
                          'h-8 w-8 p-0 hover:bg-rose-500/10',
                          hasCondition
                            ? 'text-rose-300 hover:text-rose-200'
                            : 'text-muted-foreground hover:text-foreground'
                        )}
                        disabled={isProcessing || !onConfigureConditionalOrder}
                        title="设置条件清仓"
                        onClick={() => onConfigureConditionalOrder?.(holding)}
                        data-testid={`conditional-liquidation-${holding.stockCode}`}
                      >
                        <Target className="h-4 w-4" />
                        <span className="sr-only">设置条件清仓</span>
                      </Button>

                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 w-8 p-0 text-destructive hover:text-destructive hover:bg-destructive/10"
                            disabled={
                              isProcessing || isSubmitting || !rowLiquidatable
                            }
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
                            <AlertDialogDescription asChild>
                              <div>
                                您确定要卖出{' '}
                                {holding.instrumentName || holding.stockCode} (
                                {holding.stockCode}) 吗？
                                <br />
                                <div className="mt-2 text-sm bg-muted p-2 rounded-md">
                                  <p>
                                    持仓数量: {holding.volume.toLocaleString()}{' '}
                                    股
                                  </p>
                                  <p>
                                    可卖数量: {sellableVolume.toLocaleString()}{' '}
                                    股
                                  </p>
                                  <p>
                                    当前市值:{' '}
                                    {formatCurrency(holding.marketValue ?? 0)}
                                  </p>
                                </div>
                                {liquidationChoices}
                              </div>
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>取消</AlertDialogCancel>
                            <AlertDialogAction
                              disabled={!hasExplicitChoices}
                              onClick={() =>
                                handleLiquidateIndividual(stockCode)
                              }
                              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            >
                              提交清仓委托
                            </AlertDialogAction>
                          </AlertDialogFooter>
                        </AlertDialogContent>
                      </AlertDialog>
                    </div>
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
