import {
  Percent,
  Hash,
  ArrowUpRight,
  ArrowDownRight,
  Lock,
  Unlock,
} from 'lucide-react';
import React from 'react';

import { StockSelector } from '@/components/StockSelector';
import { Slider } from '@/components/ui/slider';
import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import { useStockSearch } from '@/hooks/useStockSearch';
import { cn } from '@/utils/cn';

import { type GridConfig, GridType } from '../types';

interface Props {
  config: GridConfig;
  onChange: (newConfig: GridConfig) => void;
}

const InputGroup = ({
  label,
  children,
  icon: Icon,
}: {
  label: string;
  children: React.ReactNode;
  icon?: React.ElementType;
}) => (
  <div className="space-y-1.5">
    <label className="flex items-center gap-1.5 text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
      {Icon && <Icon className="w-3 h-3 opacity-70" />}
      {label}
    </label>
    {children}
  </div>
);

const NumberInput = ({
  value,
  onChange,
  prefix,
  suffix,
  step = 'any',
  className,
}: {
  value: string | number;
  onChange: (val: string) => void;
  prefix?: React.ReactNode;
  suffix?: React.ReactNode;
  step?: string;
  className?: string;
}) => {
  const [localValue, setLocalValue] = React.useState(value.toString());

  React.useEffect(() => {
    // Sync with prop if it changes externally and is not what we are currently typing
    // (fuzzy match to avoid cursor jumping on simple format changes)
    if (value === undefined || Number.isNaN(value)) {
      if (localValue !== '') setLocalValue('');
    } else if (parseFloat(localValue) !== value) {
      // Don't overwrite if it's just a trailing decimal or zero difference while typing
      // e.g. typing "10." -> config has 10. Don't force back to "10"
      if (Math.abs(parseFloat(localValue) - Number(value)) > 0.000001) {
        setLocalValue(value.toString());
      }
    }
  }, [localValue, value]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVal = e.target.value;
    setLocalValue(newVal);
    onChange(newVal);
  };

  return (
    <div
      className={cn(
        'group flex items-center transition-all duration-200',
        'bg-slate-100/50 dark:bg-slate-900/50 border border-slate-200 dark:border-slate-800 rounded-lg',
        'focus-within:ring-1 focus-within:ring-blue-500/40 focus-within:border-blue-500/40 focus-within:bg-slate-200/50 dark:focus-within:bg-slate-800/80',
        className
      )}
    >
      {prefix && (
        <div className="pl-2 text-xs font-mono select-none flex items-center whitespace-nowrap pointer-events-none">
          {typeof prefix === 'string' ? (
            <span className="text-muted-foreground/60">{prefix}</span>
          ) : (
            prefix
          )}
        </div>
      )}
      <input
        type="text"
        inputMode="decimal"
        pattern="[0-9]*\.?[0-9]*"
        value={localValue}
        onKeyDown={e => {
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            const currentVal = parseFloat(localValue) || 0;
            const stepVal = parseFloat(step || '1');
            const precision = step?.includes('.')
              ? step.split('.')[1].length
              : 0;

            let newVal;
            if (e.key === 'ArrowUp') {
              newVal = currentVal + stepVal;
            } else {
              newVal = currentVal - stepVal;
            }

            // Ensure non-negative
            if (newVal < 0) newVal = 0;

            // Format: keep precision but remove trailing zeros if integer
            const formattedVal = parseFloat(
              newVal.toFixed(precision)
            ).toString();
            setLocalValue(formattedVal);
            onChange(formattedVal);
          }
        }}
        onChange={e => {
          const val = e.target.value;
          // Allow numbers and one decimal point
          if (/^\d*\.?\d*$/.test(val)) {
            handleChange(e);
          }
        }}
        className={cn(
          'w-full bg-transparent border-none py-1.5 text-xs font-mono font-bold text-foreground outline-none',
          'flex-1 min-w-0 text-center', // Use text-center for better balance
          !prefix && 'pl-2',
          !suffix && 'pr-2'
        )}
      />
      {suffix && (
        <div className="pr-2 text-[10px] font-bold select-none flex items-center whitespace-nowrap pointer-events-none">
          {typeof suffix === 'string' ? (
            <span className="text-muted-foreground/60">{suffix}</span>
          ) : (
            suffix
          )}
        </div>
      )}
    </div>
  );
};

const InputForm: React.FC<Props> = ({ config, onChange }) => {
  const { holdings } = useHoldings();
  const {
    searchQuery,
    setSearchQuery,
    filteredStocks,
    handleStockSelect,
    selectedStock,
  } = useStockSearch(holdings);

  const [isFocused, setIsFocused] = React.useState(false);
  const matchingHolding = holdings.find(h => h.stockCode === config.symbol);
  const selectedDisplayStock = config.symbol
    ? selectedStock?.id === config.symbol
      ? selectedStock
      : {
          id: config.symbol,
          stockCode: config.symbol,
          code: config.symbol,
          name: matchingHolding?.instrumentName || config.symbol,
          type: 'STOCK',
        }
    : selectedStock;

  const handleChange = (
    field: keyof GridConfig,
    value: string | number | boolean
  ) => {
    if (
      field === 'symbol' ||
      field === 'gridType' ||
      field === 'isStepUnified'
    ) {
      onChange({ ...config, [field]: value });
      return;
    }

    // Handle numeric fields
    const numStr = value.toString();
    // Allow intermediate states like "0." or "-" to be passed as 0 to config,
    // BUT we must be careful.
    // Actually, simply parsing:
    let num = parseFloat(numStr);
    if (isNaN(num)) {
      num = 0;
    }
    onChange({ ...config, [field]: num });
  };

  const handleBucketChange = (
    field: 'lockedCoreShares' | 'coreShares' | 'swingShares',
    value: string | number
  ) => {
    const parsed = parseInt(value.toString(), 10);
    const nextValue = Number.isNaN(parsed) ? 0 : Math.max(0, parsed);
    const nextConfig = {
      ...config,
      [field]: nextValue,
    };
    const totalShares =
      Math.max(0, nextConfig.lockedCoreShares || 0) +
      Math.max(0, nextConfig.coreShares || 0) +
      Math.max(0, nextConfig.swingShares || 0);

    onChange({
      ...nextConfig,
      positionShares: totalShares,
    });
  };

  return (
    <div className="h-full overflow-y-auto custom-scrollbar p-4 space-y-6">
      {/* Section 1: Asset Info */}
      <section className="space-y-3">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-0.5 h-3 bg-blue-500/50 rounded-full" />
          <h3 className="text-xs font-black text-foreground/80 uppercase tracking-widest">
            标的资产
          </h3>
        </div>

        <div className="space-y-3">
          <InputGroup label="标的代码">
            <StockSelector
              searchQuery={searchQuery}
              setSearchQuery={setSearchQuery}
              filteredStocks={filteredStocks}
              onStockSelect={stock => {
                handleStockSelect(stock);
                const matchingHolding = holdings.find(
                  h => h.stockCode === stock.id
                );
                onChange({
                  ...config,
                  symbol: stock.id,
                  basePrice: stock.quote?.lastPrice || config.basePrice,
                  positionShares: matchingHolding
                    ? matchingHolding.volume || 0
                    : 0,
                  avgCost: matchingHolding ? matchingHolding.avgPrice || 0 : 0,
                  lockedCoreShares: 0,
                  coreShares: matchingHolding ? matchingHolding.volume || 0 : 0,
                  swingShares: 0,
                });
              }}
              selectedStock={selectedDisplayStock}
            >
              <div className="relative group">
                <input
                  type="text"
                  placeholder="搜索代码 / 名称 / 拼音"
                  value={searchQuery}
                  onFocus={() => setIsFocused(true)}
                  onBlur={() => {
                    // Slight delay to allow clicks on dropdown
                    setTimeout(() => setIsFocused(false), 200);
                  }}
                  onChange={e => setSearchQuery(e.target.value)}
                  className="w-full px-3 py-1.5 text-xs font-mono font-bold border border-slate-200 dark:border-slate-800 bg-slate-100/50 dark:bg-slate-900/50 rounded-lg focus:ring-1 focus:ring-blue-500/50 focus:border-blue-500/50 outline-none transition-all placeholder:text-muted-foreground/30 text-foreground"
                />

                {/* Name & Symbol Display Overlay */}
                {!isFocused && !searchQuery && selectedDisplayStock && (
                  <div className="absolute inset-x-[1px] inset-y-[1px] flex items-center px-3 pointer-events-none bg-slate-100 dark:bg-slate-900 rounded-lg">
                    <span className="text-xs font-bold text-foreground mr-2 truncate">
                      {selectedDisplayStock.name}
                    </span>
                    <span className="text-[10px] font-mono text-muted-foreground/60 shrink-0">
                      {selectedDisplayStock.id}
                    </span>
                  </div>
                )}

                <div className="absolute right-2 top-1/2 -translate-y-1/2">
                  <ArrowUpRight className="w-3 h-3 text-muted-foreground/40 group-focus-within:text-blue-500 transition-colors" />
                </div>
              </div>
            </StockSelector>
          </InputGroup>

          <div className="grid grid-cols-2 gap-3">
            <InputGroup label="基准价格">
              <NumberInput
                value={config.basePrice}
                onChange={v => handleChange('basePrice', v)}
                prefix="¥"
              />
            </InputGroup>
            <InputGroup label="可用资金">
              <NumberInput
                value={config.cashTotal}
                onChange={v => handleChange('cashTotal', v)}
                prefix="¥"
              />
            </InputGroup>
          </div>

          <div className="space-y-3 px-3 py-3 bg-slate-100/50 dark:bg-slate-900/30 rounded-lg border border-slate-200/50 dark:border-slate-800/50">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1">
                <span className="text-[10px] text-muted-foreground font-medium">
                  总持仓（参数目标）
                </span>
                <p className="text-xs font-mono font-bold text-foreground">
                  {config.positionShares}
                </p>
              </div>
              <div className="space-y-1 text-right">
                <span className="text-[10px] text-muted-foreground font-medium">
                  持仓成本
                </span>
                <p className="text-xs font-mono font-bold text-foreground">
                  ¥{config.avgCost.toFixed(2)}
                </p>
              </div>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <InputGroup label="封存仓">
                <NumberInput
                  value={config.lockedCoreShares || 0}
                  onChange={v => handleBucketChange('lockedCoreShares', v)}
                  step="100"
                />
              </InputGroup>
              <InputGroup label="核心仓">
                <NumberInput
                  value={config.coreShares || 0}
                  onChange={v => handleBucketChange('coreShares', v)}
                  step="100"
                />
              </InputGroup>
              <InputGroup label="活跃仓">
                <NumberInput
                  value={config.swingShares || 0}
                  onChange={v => handleBucketChange('swingShares', v)}
                  step="100"
                />
              </InputGroup>
            </div>

            <p className="text-[9px] leading-relaxed text-muted-foreground/70">
              已有持仓默认归入核心仓；卖出网格只占用活跃仓，避免误卖长期底仓。
            </p>
          </div>
        </div>
      </section>

      {/* Section 2: Grid Strategy */}
      <section className="space-y-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <div className="w-0.5 h-3 bg-purple-500/50 rounded-full" />
            <h3 className="text-xs font-black text-foreground/80 uppercase tracking-widest">
              网格参数
            </h3>
          </div>

          <button
            type="button"
            onClick={() =>
              handleChange(
                'gridType',
                config.gridType === GridType.GEOMETRIC
                  ? GridType.ARITHMETIC
                  : GridType.GEOMETRIC
              )
            }
            className="flex items-center gap-1.5 px-2 py-1 bg-slate-100 dark:bg-slate-900 hover:bg-slate-200 dark:hover:bg-slate-800 text-muted-foreground hover:text-foreground rounded-md border border-slate-200 dark:border-slate-800 transition-colors"
          >
            {config.gridType === GridType.GEOMETRIC ? (
              <Percent className="w-3 h-3 text-purple-500" />
            ) : (
              <Hash className="w-3 h-3 text-blue-500" />
            )}
            <span className="text-[10px] font-bold uppercase">
              {config.gridType === GridType.GEOMETRIC ? '等比' : '等差'}
            </span>
          </button>
        </div>

        <div className="grid grid-cols-12 gap-3 transition-all duration-300 ease-in-out">
          <div
            className={cn(
              'space-y-1.5 transition-all duration-300 ease-in-out',
              config.isStepUnified ? 'col-span-6' : 'col-span-8'
            )}
          >
            <div className="flex items-center justify-between">
              <label className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">
                步长配置
              </label>
              <button
                type="button"
                onClick={() =>
                  handleChange('isStepUnified', config.isStepUnified ? 0 : 1)
                }
                className="text-muted-foreground hover:text-blue-500 transition-colors"
                title={
                  config.isStepUnified ? '点击解锁分开配置' : '点击锁定统一配置'
                }
              >
                {config.isStepUnified ? (
                  <div className="flex items-center gap-1">
                    <span className="text-[9px] opacity-70">统一</span>
                    <Lock className="w-3 h-3" />
                  </div>
                ) : (
                  <div className="flex items-center gap-1">
                    <span className="text-[9px] opacity-70">独立</span>
                    <Unlock className="w-3 h-3" />
                  </div>
                )}
              </button>
            </div>

            {config.isStepUnified ? (
              <NumberInput
                value={config.stepPctDown} // Display one value (using Down as master)
                onChange={v => {
                  // When unified, update both
                  const val = parseFloat(v) || 0;
                  onChange({
                    ...config,
                    stepPctUp: val,
                    stepPctDown: val,
                  });
                }}
                prefix={config.gridType === GridType.ARITHMETIC ? '¥' : ''}
                suffix={config.gridType === GridType.GEOMETRIC ? '%' : ''}
                step={config.gridType === GridType.GEOMETRIC ? '0.1' : '0.01'}
              />
            ) : (
              <div className="flex gap-2">
                <NumberInput
                  value={config.stepPctDown}
                  onChange={v => handleChange('stepPctDown', v)}
                  prefix={
                    <span className="text-green-600 dark:text-green-400 font-bold">
                      {config.gridType === GridType.ARITHMETIC ? '买¥' : '买'}
                    </span>
                  }
                  suffix={config.gridType === GridType.GEOMETRIC ? '%' : ''}
                  step={config.gridType === GridType.GEOMETRIC ? '0.1' : '0.01'}
                  className="border-green-500/20 focus-within:border-green-500/50 focus-within:ring-green-500/20 flex-1 min-w-0"
                />
                <NumberInput
                  value={config.stepPctUp}
                  onChange={v => handleChange('stepPctUp', v)}
                  prefix={
                    <span className="text-red-600 dark:text-red-400 font-bold">
                      {config.gridType === GridType.ARITHMETIC ? '卖¥' : '卖'}
                    </span>
                  }
                  suffix={config.gridType === GridType.GEOMETRIC ? '%' : ''}
                  step={config.gridType === GridType.GEOMETRIC ? '0.1' : '0.01'}
                  className="border-red-500/20 focus-within:border-red-500/50 focus-within:ring-red-500/20 flex-1 min-w-0"
                />
              </div>
            )}
          </div>

          <div
            className={cn(
              'space-y-1.5 transition-all duration-300 ease-in-out',
              config.isStepUnified ? 'col-span-6' : 'col-span-4'
            )}
          >
            <label className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">
              网格档位 (总计)
            </label>
            <div className="flex items-center h-[30px] bg-slate-100/50 dark:bg-slate-900/50 rounded-lg border border-slate-200 dark:border-slate-800">
              <button
                onClick={() => {
                  const total = config.nUp + config.nDown;
                  if (total > 1) {
                    if (config.nDown > 0)
                      handleChange('nDown', config.nDown - 1);
                    else handleChange('nUp', config.nUp - 1);
                  }
                }}
                className="w-8 h-full flex items-center justify-center text-muted-foreground hover:text-red-500 transition-colors"
              >
                -
              </button>
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                className="flex-1 w-full bg-transparent text-center font-mono text-xs font-bold border-x border-slate-200/50 dark:border-slate-800/50 h-full outline-none focus:bg-white/50 dark:focus:bg-black/20 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition-colors"
                value={config.nUp + config.nDown}
                onKeyDown={e => {
                  if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
                    e.preventDefault();
                    const currentTotal = config.nUp + config.nDown;
                    let newTotal =
                      e.key === 'ArrowUp' ? currentTotal + 1 : currentTotal - 1;
                    if (newTotal < 0) newTotal = 0;

                    const half = Math.floor(newTotal / 2);
                    const remainder = newTotal % 2;
                    onChange({
                      ...config,
                      nDown: half + remainder,
                      nUp: half,
                    });
                  }
                }}
                onChange={e => {
                  const val = e.target.value;
                  if (/^\d*$/.test(val)) {
                    const total = parseInt(val) || 0;
                    // Distribute evenly, remainder to nDown (Buy)
                    const half = Math.floor(total / 2);
                    const remainder = total % 2;
                    const newDown = half + remainder;
                    const newUp = half;
                    onChange({ ...config, nDown: newDown, nUp: newUp });
                  }
                }}
              />
              <button
                onClick={() => handleChange('nDown', config.nDown + 1)}
                className="w-8 h-full flex items-center justify-center text-muted-foreground hover:text-green-500 transition-colors"
              >
                +
              </button>
            </div>
          </div>
        </div>

        <div className="pt-2">
          <div className="flex justify-between items-end mb-2">
            <div className="text-[10px] font-bold text-green-600 dark:text-green-400 flex items-center gap-1">
              <ArrowDownRight className="w-3 h-3" />
              买入 {config.nDown}
            </div>
            <div className="text-[10px] font-bold text-red-600 dark:text-red-400 flex items-center gap-1">
              卖出 {config.nUp}
              <ArrowUpRight className="w-3 h-3" />
            </div>
          </div>

          <div className="relative h-2 bg-slate-200 dark:bg-slate-800 rounded-full">
            <input
              type="range"
              min="0"
              max={config.nUp + config.nDown}
              value={config.nDown}
              onChange={e => {
                const buyCount = parseInt(e.target.value);
                const total = config.nUp + config.nDown;
                onChange({ ...config, nDown: buyCount, nUp: total - buyCount });
              }}
              className="absolute inset-0 w-full h-full opacity-0 cursor-ew-resize z-10"
            />
            <div
              className="h-full bg-green-500/80 transition-all duration-300 relative rounded-l-full"
              style={{
                width: `${(config.nDown / (config.nUp + config.nDown || 1)) * 100}%`,
              }}
            >
              {/* Enhanced Drag Handle */}
              <div className="absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 z-20 pointer-events-none flex items-center justify-center">
                <div className="w-2.5 h-4 bg-white shadow-md rounded-[3px]" />
              </div>
            </div>
            <div
              className="absolute top-0 right-0 bottom-0 bg-red-500/80 transition-all duration-300 rounded-r-full"
              style={{
                width: `${(config.nUp / (config.nUp + config.nDown || 1)) * 100}%`,
              }}
            />
          </div>
          <div className="flex justify-between mt-1">
            <span className="text-[10px] font-bold text-muted-foreground/50 uppercase tracking-widest">
              防御区
            </span>
            <span className="text-[10px] font-bold text-muted-foreground/50 uppercase tracking-widest">
              盈利区
            </span>
          </div>
          {config.nUp > 0 && (config.swingShares || 0) <= 0 && (
            <p className="mt-2 text-[9px] leading-relaxed text-amber-600 dark:text-amber-300">
              当前初始活跃仓为 0，卖出水位会等待下方买入成交后再获得可卖库存。
            </p>
          )}
        </div>
      </section>

      {/* Section 3: Risk & Money */}
      <section className="space-y-3">
        <div className="flex items-center gap-2 mb-2">
          <div className="w-0.5 h-3 bg-yellow-500/50 rounded-full" />
          <h3 className="text-xs font-black text-foreground/80 uppercase tracking-widest">
            风控管理
          </h3>
        </div>

        <div className="space-y-4">
          <div className="space-y-2">
            <div className="flex justify-between items-center">
              <label className="text-[10px] font-bold text-muted-foreground">
                最大仓位上限
              </label>
              <span className="text-xs font-mono font-bold text-foreground">
                {config.maxPositionValuePct}%
              </span>
            </div>
            <Slider
              value={[config.maxPositionValuePct]}
              min={0}
              max={100}
              step={1}
              onValueChange={vals =>
                handleChange('maxPositionValuePct', vals[0])
              }
              className="py-1.5"
            />
            <div className="flex justify-between text-[10px] font-mono text-muted-foreground">
              <span>
                已用: ¥
                {Math.round(
                  config.positionShares * config.basePrice
                ).toLocaleString()}
              </span>
              <span>
                上限: ¥
                {Math.round(
                  (config.cashTotal * config.maxPositionValuePct) / 100
                ).toLocaleString()}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <div className="flex justify-between items-center">
                <label className="text-[10px] font-bold text-muted-foreground">
                  买入预算 (%)
                </label>
                <span className="text-xs font-mono font-bold text-foreground">
                  {config.buyBudgetPct ?? 0}%
                </span>
              </div>
              <Slider
                value={[config.buyBudgetPct ?? 0]}
                min={0}
                max={100}
                step={1}
                onValueChange={(vals: number[]) =>
                  handleChange('buyBudgetPct', vals[0])
                }
                className="py-1.5"
              />
            </div>
            <InputGroup label="最小成交额">
              <NumberInput
                value={config.minTradeValue}
                onChange={v => handleChange('minTradeValue', v)}
                prefix="¥"
              />
            </InputGroup>
          </div>
        </div>
      </section>
    </div>
  );
};

export default InputForm;
