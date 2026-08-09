import { Shield, Play } from 'lucide-react';
import React from 'react';

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
import {
  type ParameterProperty,
  type Strategy,
  type StrategyRunMode,
} from '@/generated/gql/graphql';

import { type StrategyConfigValue } from '../../hooks/types';
import { StrategyInstrumentSelector } from '../StrategyInstrumentSelector';

interface DefaultConfigPanelProps {
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
  onSubmit: () => void;
}

type ParameterDisplayCopy = {
  label: string;
  description?: string;
  unit?: string;
};

const PARAMETER_DISPLAY_COPY: Record<string, ParameterDisplayCopy> = {
  target_positions: {
    label: '目标持仓数',
    description: '策略同时持有的目标标的数量。',
  },
  min_position_pct: {
    label: '最小仓位占比',
    description: '单个持仓或策略目标仓位的最小配置比例。',
  },
  max_position_pct: {
    label: '最大仓位占比',
    description: '单个持仓或策略目标仓位的最大配置比例。',
  },
  buy_threshold_pct: {
    label: '箱体买入阈值',
    description: '价格接近支撑位上方该比例以内时允许买入。',
  },
  buy_threshold_pct_60m: {
    label: '60分钟买入阈值',
    description: '60分钟K线级别的买入触发阈值。',
  },
  stop_loss_pct: {
    label: '止损比例',
    description: '亏损达到该比例时触发止损。',
  },
  take_profit_pct: {
    label: '止盈比例',
    description: '盈利达到该比例时触发止盈。',
  },
  structure_break_pct: {
    label: '结构跌破比例',
    description: '价格跌破支撑位该比例后视为结构破坏。',
  },
  time_stop_bars_daily: {
    label: '日线时间止损K线数',
    description: '日线周期内持仓超过该K线数且未盈利时触发时间止损。',
  },
  time_stop_bars_60m: {
    label: '60分钟时间止损K线数',
    description: '60分钟周期内持仓超过该K线数且未盈利时触发时间止损。',
  },
  box_window_daily: {
    label: '日线箱体窗口',
    description: '用于日线箱体识别的历史回看窗口。',
  },
  box_window_60m: {
    label: '60分钟箱体窗口',
    description: '用于60分钟箱体识别的历史回看窗口。',
  },
  max_daily_loss_pct: {
    label: '单日最大亏损',
    description: '单日亏损达到该比例后停止开新仓。',
  },
  max_drawdown_pct: {
    label: '最大回撤',
    description: '回撤达到该比例后触发清仓保护。',
  },
  loss_streak_reduce: {
    label: '连续亏损降仓次数',
    description: '连续亏损达到该次数后降低仓位规模。',
  },
  loss_streak_stop: {
    label: '连续亏损停开次数',
    description: '连续亏损达到该次数后停止开新交易。',
  },
  max_turnover_per_day: {
    label: '单日最大换仓数',
    description: '每个交易日允许新增持仓的最大数量。',
  },
  max_price_history: {
    label: '价格历史长度',
    description: '每个标的保留的最大历史价格数量。',
  },
  market: {
    label: '交易市场',
    description: '用于交易日历判断的市场代码。',
  },
  neutral_position_pct: {
    label: '中性仓位占比',
    description: '市场中性状态下的目标总仓位比例。',
  },
  cash_buffer_pct: {
    label: '现金缓冲比例',
    description: '策略需要保留的现金缓冲比例。',
  },
  core_base_share: {
    label: '核心仓基准占比',
    description: '核心仓在总仓位中的基准占比。',
  },
  core_min_share: {
    label: '核心仓最小占比',
    description: '核心仓允许下降到的最低占比。',
  },
  core_max_share: {
    label: '核心仓最大占比',
    description: '核心仓允许提升到的最高占比。',
  },
  balance_beta: {
    label: '平衡调节系数',
    description: '控制价格偏离基准时的仓位调节强度。',
  },
  inventory_gamma: {
    label: '库存调节系数',
    description: '控制已有库存对仓位调节的影响。',
  },
  ema20_weight: {
    label: 'EMA20权重',
    description: 'EMA20在基准价格计算中的权重。',
  },
  ema60_weight: {
    label: 'EMA60权重',
    description: 'EMA60在基准价格计算中的权重。',
  },
  ema120_weight: {
    label: 'EMA120权重',
    description: 'EMA120在基准价格计算中的权重。',
  },
  volume_poc_weight: {
    label: '成交密集价权重',
    description: '成交密集价在基准价格计算中的权重。',
  },
  atr_period: {
    label: 'ATR周期',
    description: '计算ATR波动率时使用的周期。',
  },
  grid_atr_multiplier: {
    label: '网格ATR倍数',
    description: '用ATR推导网格步长时使用的倍数。',
  },
  min_grid_step_pct: {
    label: '最小网格步长',
    description: '网格交易允许的最小价格间距比例。',
  },
  max_grid_step_pct: {
    label: '最大网格步长',
    description: '网格交易允许的最大价格间距比例。',
  },
  rebalance_threshold_pct: {
    label: '再平衡阈值',
    description: '偏离目标仓位达到该比例后触发再平衡。',
  },
  daily_core_add_limit_pct: {
    label: '单日核心仓加仓上限',
    description: '单日允许新增核心仓的最大比例。',
  },
  single_order_limit_pct: {
    label: '单笔订单上限',
    description: '单笔交易金额占资产的最大比例。',
  },
  min_order_amount: {
    label: '最小订单金额',
    description: '低于该金额的订单将被过滤。',
    unit: '元',
  },
  min_expected_profit_bps: {
    label: '最小预期收益',
    description: '低于该预期收益的交易意图将被过滤。',
    unit: '基点',
  },
  high_distribution_volume_ratio: {
    label: '高位放量阈值',
    description: '识别高位派发状态的成交量放大倍数。',
  },
  downtrend_grid_buy_block: {
    label: '下行趋势禁买',
    description: '开启后，下行趋势中会阻止网格买入。',
  },
  consecutive_down_days_limit: {
    label: '连续下跌天数限制',
    description: '连续下跌达到该天数后进入保守状态。',
  },
  high_reversal_reduce_threshold: {
    label: '高位反转减仓阈值',
    description: '高位反转评分达到该阈值后触发减仓。',
  },
};

function hasChineseText(value?: string | null) {
  return Boolean(value && /[\u4e00-\u9fa5]/.test(value));
}

function formatParameterLabel(key: string, param: ParameterProperty) {
  const copy = PARAMETER_DISPLAY_COPY[key];
  if (!copy) return hasChineseText(param.title) ? param.title : key;
  const sourceTitle = typeof param.title === 'string' ? param.title.trim() : '';
  if (
    !sourceTitle ||
    sourceTitle === copy.label ||
    hasChineseText(sourceTitle)
  ) {
    return copy.label;
  }
  return `${copy.label} / ${sourceTitle}`;
}

function formatParameterDescription(key: string, param: ParameterProperty) {
  const copy = PARAMETER_DISPLAY_COPY[key];
  if (copy?.description) return copy.description;
  return param.description;
}

export function DefaultConfigPanel({
  strategy,
  strategyName: _strategyName,
  setStrategyName: _setStrategyName,
  stockCodes,
  setStockCodes,
  strategyConfig,
  setStrategyConfig,
  runMode: _runMode,
  setRunMode: _setRunMode,
  onSubmit,
}: DefaultConfigPanelProps) {
  const parameterSchema = strategy.parameterSchema;

  const handleInstrumentChange = (instrumentCode: string) => {
    setStockCodes(instrumentCode);
    setStrategyConfig(prev => ({
      ...prev,
      instrument_code: instrumentCode,
      instrumentCode,
      symbol: instrumentCode,
      stockCodes: instrumentCode ? [instrumentCode] : [],
    }));
  };

  return (
    <div className="space-y-8">
      {/* 1. Bound Instrument */}
      <Card className="p-6 bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-white/10 rounded-[2rem] shadow-xl">
        <div className="space-y-6">
          <div className="space-y-2">
            <Label
              htmlFor="stock-codes"
              className="text-[9px] font-black uppercase tracking-[0.2em] text-slate-400 flex items-center gap-2"
            >
              <Shield size={10} className="text-blue-500" />
              绑定标的 *
            </Label>
            <StrategyInstrumentSelector
              value={stockCodes}
              onChange={handleInstrumentChange}
              placeholder="搜索 A 股代码 / 名称 / 拼音"
            />
            <div className="flex items-center gap-2 text-[9px] text-slate-500 italic font-medium px-2">
              <span className="w-1 h-1 rounded-full bg-blue-500" />
              一个 A 股策略实例只绑定一个标的；换股请复制为新实例。
            </div>
          </div>
        </div>
      </Card>

      {/* 2. 运行时参数 */}
      <Card className="p-6 bg-white dark:bg-slate-900/60 border border-slate-200 dark:border-white/10 rounded-[2rem] shadow-xl">
        <div className="flex items-center gap-4 mb-6">
          <div className="w-1 h-5 bg-emerald-600 rounded-full" />
          <h3 className="text-[11px] font-black text-slate-900 dark:text-white uppercase tracking-[0.2em] italic">
            运行时参数
          </h3>
        </div>

        {parameterSchema && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-6">
            {parameterSchema.properties?.map(({ key, value: param }) => (
              <div key={key} className="grid gap-3 group">
                <Label
                  htmlFor={key}
                  className="text-[9px] font-black uppercase tracking-[0.2em] text-slate-400 flex items-center justify-between"
                >
                  <span className="group-hover:text-blue-500 transition-colors">
                    {formatParameterLabel(key, param)}
                    {parameterSchema.required?.includes(key) && (
                      <span className="text-rose-500 ml-1">*</span>
                    )}
                  </span>
                </Label>

                {param.type === 'number' || param.type === 'integer' ? (
                  <div className="relative group">
                    <Input
                      id={key}
                      type="number"
                      value={strategyConfig[key] ?? param.default}
                      onChange={e =>
                        setStrategyConfig(prev => ({
                          ...prev,
                          [key]:
                            param.type === 'integer'
                              ? parseInt(e.target.value) || param.default
                              : parseFloat(e.target.value) || param.default,
                        }))
                      }
                      className="rounded-lg h-9 bg-slate-50 dark:bg-white/[0.02] border-slate-200 dark:border-white/5 pr-10 pl-3 text-[11px] font-black tabular-nums transition-all"
                    />
                    <div className="absolute right-3 top-1/2 -translate-y-1/2 text-[9px] font-black text-slate-400 uppercase tracking-widest pointer-events-none">
                      {PARAMETER_DISPLAY_COPY[key]?.unit ||
                        param.unit ||
                        '单位'}
                    </div>
                  </div>
                ) : param.enum && param.enum.length > 0 ? (
                  <Select
                    value={String(strategyConfig[key] ?? param.default)}
                    onValueChange={value =>
                      setStrategyConfig(prev => ({ ...prev, [key]: value }))
                    }
                  >
                    <SelectTrigger className="rounded-lg h-9 bg-slate-50 dark:bg-white/[0.02] border-slate-200 dark:border-white/5 px-3 text-[11px] font-bold">
                      <SelectValue
                        placeholder={param.placeholder || '请选择'}
                      />
                    </SelectTrigger>
                    <SelectContent className="rounded-2xl border-white/10 bg-slate-900 text-slate-200">
                      {param.enum.map(option => (
                        <SelectItem key={option} value={option}>
                          {option}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : param.type === 'boolean' ? (
                  <div
                    onClick={() =>
                      setStrategyConfig(prev => ({
                        ...prev,
                        [key]: !(strategyConfig[key] ?? param.default),
                      }))
                    }
                    className={`flex items-center justify-between px-4 rounded-lg h-9 border cursor-pointer transition-all duration-300 ${
                      (strategyConfig[key] ?? param.default)
                        ? 'bg-blue-600/10 border-blue-500/30'
                        : 'bg-slate-50 dark:bg-white/[0.02] border-slate-200 dark:border-white/5 opacity-60 hover:opacity-100'
                    }`}
                  >
                    <span
                      className={`text-[10px] font-black uppercase tracking-widest ${(strategyConfig[key] ?? param.default) ? 'text-blue-500' : 'text-slate-500'}`}
                    >
                      {formatParameterLabel(key, param)}
                    </span>
                    <div
                      className={`w-8 h-4 rounded-full relative transition-colors ${(strategyConfig[key] ?? param.default) ? 'bg-blue-600' : 'bg-slate-300 dark:bg-white/10'}`}
                    >
                      <div
                        className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all ${(strategyConfig[key] ?? param.default) ? 'left-[18px]' : 'left-0.5'}`}
                      />
                    </div>
                  </div>
                ) : (
                  <Input
                    id={key}
                    type="text"
                    value={String(strategyConfig[key] ?? param.default ?? '')}
                    onChange={e =>
                      setStrategyConfig(prev => ({
                        ...prev,
                        [key]: e.target.value,
                      }))
                    }
                    className="rounded-lg h-9 bg-slate-50 dark:bg-white/[0.02] border-slate-200 dark:border-white/5 px-3 text-[11px] font-bold"
                  />
                )}
                {formatParameterDescription(key, param) && (
                  <p className="text-[9px] text-slate-500 italic font-medium leading-relaxed px-1">
                    {formatParameterDescription(key, param)}
                  </p>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
      {/* Submit Section */}
      <div className="pt-8 pb-4">
        <Button
          onClick={onSubmit}
          disabled={!stockCodes.trim()}
          className="w-full h-12 rounded-xl bg-blue-600 hover:bg-blue-700 text-white font-black uppercase tracking-[0.2em] text-[10px] shadow-lg shadow-blue-500/20 active:scale-95 transition-all group overflow-hidden"
        >
          <div className="absolute top-0 -left-[100%] w-full h-full bg-gradient-to-r from-transparent via-white/20 to-transparent group-hover:left-[100%] transition-all duration-1000" />
          <Play className="mr-3 h-3 w-3 fill-current" />
          确认启动
        </Button>
      </div>
    </div>
  );
}
