import { Play, Save } from 'lucide-react';
import React, { useCallback } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { type Strategy, type StrategyRunMode } from '@/generated/gql/graphql';

import { type StrategyConfigValue } from '../../hooks/types';
import {
  GridCalculator,
  type GridConfig,
  type GridResult,
} from '../grid-calculator';

interface PullbackGridConfigPanelProps {
  strategy: Strategy;
  strategyName: string;
  setStrategyName: (name: string) => void;
  stockCodes: string;
  setStockCodes: (codes: string) => void;
  strategyConfig: Record<string, StrategyConfigValue>;
  setStrategyConfig: React.Dispatch<
    React.SetStateAction<Record<string, StrategyConfigValue>>
  >;
  runMode: StrategyRunMode;
  setRunMode: (mode: StrategyRunMode) => void;
  onSubmit?: () => void;
  onSave?: () => void;
  saveLabel?: string;
  submitLabel?: string;
  saveDisabled?: boolean;
  submitDisabled?: boolean;
  showSubmit?: boolean;
}

export function PullbackGridConfigPanel({
  strategy: _strategy,
  strategyName: _strategyName,
  setStrategyName: _setStrategyName,
  stockCodes,
  setStockCodes,
  strategyConfig,
  setStrategyConfig,
  runMode: _runMode,
  setRunMode: _setRunMode,
  onSubmit,
  onSave,
  saveLabel = '保存配置',
  submitLabel = '保存并运行',
  saveDisabled = false,
  submitDisabled = false,
  showSubmit = true,
}: PullbackGridConfigPanelProps) {
  // Sync grid calculator config with strategy config
  const handleGridConfigChange = useCallback(
    (config: GridConfig) => {
      // Update stock code if symbol changed
      if (config.symbol !== stockCodes) {
        setStockCodes(config.symbol);
      }
      // Sync all grid params to strategy config
      setStrategyConfig(prev => ({
        ...prev,
        // 基础配置
        instrument_code: config.symbol,
        stockCodes: config.symbol,
        base_price: config.basePrice,
        cash_total: config.cashTotal,
        position_shares: config.positionShares,
        avg_cost: config.avgCost,
        locked_core_shares: config.lockedCoreShares,
        core_shares: config.coreShares,
        swing_shares: config.swingShares,
        position_bucket_mode: 'CORE_SWING',
        // 网格参数
        grid_type: config.gridType,
        grid_count: config.nDown,
        grid_count_up: config.nUp,
        // Map down step as the primary spacing for backend (assuming backend only takes one)
        grid_spacing_pct: config.stepPctDown,
        // 风控参数
        buy_budget_pct: config.buyBudgetPct,
        max_position_pct: config.maxPositionValuePct,
        min_trade_value: config.minTradeValue,
      }));
    },
    [stockCodes, setStockCodes, setStrategyConfig]
  );

  // Sync grid result (levels) to strategy config - 这是最重要的数据
  const handleGridResultChange = useCallback(
    (result: GridResult) => {
      // 将 GridLevel[] 转换为普通对象数组，确保类型兼容
      const levelsData = result.levels.map(level => ({
        id: level.id,
        levelIndex: level.levelIndex,
        side: level.side as string,
        role: level.role,
        price: level.price,
        shares: level.shares,
        amount: level.amount,
        pctFromBase: level.pctFromBase,
        expectedProfit: level.expectedProfit,
        bucket: level.side === 'SELL' ? 'swing' : 'swing',
      }));

      setStrategyConfig(prev => ({
        ...prev,
        // 网格明细 - 对齐后端 grid_levels 参数
        grid_levels: levelsData,
        // 基准价 - 对齐后端 base_price 参数
        base_price: result.basePrice,
        // 前端计算汇总（供展示用）
        _grid_total_invested: result.guards.totalInvested,
        _grid_max_position_value: result.guards.maxPositionValue,
        _grid_buy_budget: result.guards.buyBudget,
        _grid_is_valid: result.isValid,
      }));
    },
    [setStrategyConfig]
  );

  const initialPositionShares =
    (strategyConfig['position_shares'] as number | undefined) ?? 0;
  const initialLockedCoreShares =
    (strategyConfig['locked_core_shares'] as number | undefined) ?? 0;
  const initialSwingShares =
    (strategyConfig['swing_shares'] as number | undefined) ?? 0;
  const initialCoreShares =
    (strategyConfig['core_shares'] as number | undefined) ??
    Math.max(
      0,
      initialPositionShares - initialLockedCoreShares - initialSwingShares
    );

  return (
    <div className="flex flex-col gap-6">
      {/* Top: Grid Calculator */}
      <div>
        <GridCalculator
          initialConfig={{
            symbol: stockCodes,
            basePrice: (strategyConfig['base_price'] as number) ?? 10.0,
            cashTotal: (strategyConfig['cash_total'] as number) ?? 100000,
            positionShares: initialPositionShares,
            avgCost: (strategyConfig['avg_cost'] as number) ?? 0,
            lockedCoreShares: initialLockedCoreShares,
            coreShares: initialCoreShares,
            swingShares: initialSwingShares,
            // Init both steps with the single backend value
            stepPctUp: (strategyConfig['grid_spacing_pct'] as number) ?? 2.0,
            stepPctDown: (strategyConfig['grid_spacing_pct'] as number) ?? 2.0,
            isStepUnified: true,
            nUp: (strategyConfig['grid_count_up'] as number) ?? 5,
            nDown: (strategyConfig['grid_count'] as number) ?? 5,
            buyBudgetPct: (strategyConfig['buy_budget_pct'] as number) ?? 100,
            maxPositionValuePct:
              (strategyConfig['max_position_pct'] as number) ?? 100,
            minTradeValue:
              (strategyConfig['min_trade_value'] as number) ?? 10000,
          }}
          onConfigChange={handleGridConfigChange}
          onResultChange={handleGridResultChange}
          className="h-full"
        />
      </div>

      {/* Bottom: Strategy Parameters & Actions */}
      <div className="rounded-2xl border border-slate-200 dark:border-white/[0.06] bg-white dark:bg-[#0d1425] p-5 space-y-5">
        {/* Section Title */}
        <h3 className="text-[11px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest">
          策略参数
        </h3>

        {/* 3-column param grid */}
        <div className="grid grid-cols-3 gap-4">
          {/* 趋势 EMA 周期 */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-medium text-slate-500 dark:text-slate-400">
              趋势 EMA 周期
            </Label>
            <Select
              value={String(
                strategyConfig['trend_ema_period'] ??
                  strategyConfig['ma_period'] ??
                  60
              )}
              onValueChange={v =>
                setStrategyConfig(prev => ({
                  ...prev,
                  trend_ema_period: parseInt(v),
                }))
              }
            >
              <SelectTrigger className="h-9 bg-slate-50 dark:bg-white/[0.04] border-slate-200 dark:border-white/[0.06] text-xs font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="20">EMA 20</SelectItem>
                <SelectItem value="60">EMA 60</SelectItem>
                <SelectItem value="120">EMA 120</SelectItem>
                <SelectItem value="200">EMA 200</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-[9px] text-slate-400 dark:text-slate-600">
              慢线，判断主趋势方向
            </p>
          </div>

          {/* 快速 EMA 周期 */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-medium text-slate-500 dark:text-slate-400">
              快速 EMA 周期
            </Label>
            <Select
              value={String(strategyConfig['fast_ema_period'] ?? 20)}
              onValueChange={v =>
                setStrategyConfig(prev => ({
                  ...prev,
                  fast_ema_period: parseInt(v),
                }))
              }
            >
              <SelectTrigger className="h-9 bg-slate-50 dark:bg-white/[0.04] border-slate-200 dark:border-white/[0.06] text-xs font-mono">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="5">EMA 5</SelectItem>
                <SelectItem value="10">EMA 10</SelectItem>
                <SelectItem value="20">EMA 20</SelectItem>
                <SelectItem value="60">EMA 60</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-[9px] text-slate-400 dark:text-slate-600">
              快线 {'>'} 慢线 = 允许买入
            </p>
          </div>

          {/* 反弹确认幅度 */}
          <div className="space-y-1.5">
            <Label className="text-[10px] font-medium text-slate-500 dark:text-slate-400">
              反弹确认幅度
            </Label>
            <div className="relative">
              <Input
                type="number"
                step={0.1}
                min={0}
                max={2}
                value={
                  ((strategyConfig['pullback_confirm_pct'] as number) ??
                    0.002) * 100
                }
                onChange={e =>
                  setStrategyConfig(prev => ({
                    ...prev,
                    pullback_confirm_pct:
                      (parseFloat(e.target.value) || 0) / 100,
                  }))
                }
                className="h-9 bg-slate-50 dark:bg-white/[0.04] border-slate-200 dark:border-white/[0.06] text-xs font-mono pr-7"
              />
              <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-slate-400 pointer-events-none">
                %
              </span>
            </div>
            <p className="text-[9px] text-slate-400 dark:text-slate-600">
              触网后需反弹多少才买入
            </p>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex gap-3 pt-2">
          {onSave && (
            <Button
              onClick={onSave}
              disabled={saveDisabled}
              variant="outline"
              className="flex-1 h-10 rounded-xl border-slate-200 dark:border-white/[0.08] text-slate-600 dark:text-slate-300 text-xs font-semibold hover:bg-slate-50 dark:hover:bg-white/[0.04] active:scale-[0.98] transition-all"
            >
              <Save className="mr-2 h-3.5 w-3.5" />
              {saveLabel}
            </Button>
          )}
          {showSubmit && onSubmit && (
            <Button
              onClick={onSubmit}
              disabled={submitDisabled}
              className="flex-1 h-10 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-xs font-semibold shadow-sm active:scale-[0.98] transition-all"
            >
              <Play className="mr-2 h-3.5 w-3.5 fill-current" />
              {submitLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
