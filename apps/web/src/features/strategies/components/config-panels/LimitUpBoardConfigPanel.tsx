import {
  Activity,
  BarChart3,
  Clock3,
  Play,
  Save,
  ShieldAlert,
  SlidersHorizontal,
  TrendingUp,
} from 'lucide-react';
import React, { useEffect, useMemo } from 'react';

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
import { Switch } from '@/components/ui/switch';
import {
  type ParameterProperty,
  type Strategy,
  StrategyRunMode,
} from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

import { validateLimitUpBoardConfiguration } from '../../domain/limitUpBoardConfiguration';
import { type StrategyConfigValue } from '../../hooks/types';
import { StrategyInstrumentSelector } from '../StrategyInstrumentSelector';

interface LimitUpBoardConfigPanelProps {
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

type ParameterEntry = {
  key: string;
  value: ParameterProperty;
};

const GROUPS = [
  {
    id: 'entry',
    title: '入场触发',
    description: '控制何时接近涨停、何种盘口质量才允许产生一次买入意图。',
    icon: TrendingUp,
  },
  {
    id: 'risk',
    title: '风险过滤',
    description: '数据质量、市场环境、重复入场和一字板保护。',
    icon: ShieldAlert,
  },
  {
    id: 'execution',
    title: '确认与委托',
    description: '人工确认窗口、价格偏离和短时委托撤单策略。',
    icon: Clock3,
  },
  {
    id: 'exit',
    title: '卖出计划',
    description: '真实成交后由 Engine 统一管理破板、回撤和持有期退出。',
    icon: Activity,
  },
  {
    id: 'backtest',
    title: '回测撮合',
    description: 'Tick 回放、费用、滑点、盘口参与率和严格数据门禁。',
    icon: BarChart3,
  },
  {
    id: 'data',
    title: '历史数据降级',
    description:
      '仅用于旧回测数据缺失涨跌停价时的显式保守推导，绝不用于模拟盘或实盘。',
    icon: ShieldAlert,
  },
] as const;

const UNIT_LABELS: Record<string, string> = {
  ratio: '比例',
  percent: '%',
  ticks: '档',
  shares: '股',
  CNY: '元',
  bps: 'bp',
  ms: 'ms',
  seconds: '秒',
  days: '交易日',
};

const ENUM_LABELS: Record<string, string> = {
  AUTO: '自动执行',
  MANUAL_CONFIRM: '人工确认',
};

function numberValue(value: string, integer: boolean) {
  if (value.trim() === '') return null;
  const parsed = integer ? Number.parseInt(value, 10) : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function ParameterControl({
  entry,
  value,
  onChange,
}: {
  entry: ParameterEntry;
  value: StrategyConfigValue | undefined;
  onChange: (value: StrategyConfigValue) => void;
}) {
  const { key, value: parameter } = entry;
  const resolved = value ?? (parameter.default as StrategyConfigValue);
  const label = parameter.title || key;
  const unit = parameter.unit
    ? UNIT_LABELS[parameter.unit] || parameter.unit
    : '';

  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-3">
        <Label
          htmlFor={`board-${key}`}
          className="text-[11px] font-bold text-slate-700 dark:text-slate-200"
        >
          {label}
        </Label>
        {unit && (
          <span className="shrink-0 font-mono text-[9px] text-slate-400">
            {unit}
          </span>
        )}
      </div>

      {parameter.type === 'boolean' ? (
        <div className="flex h-10 items-center justify-between rounded-lg border border-slate-200 bg-slate-50 px-3 dark:border-white/10 dark:bg-white/[0.03]">
          <span className="text-[10px] font-medium text-slate-500">
            {resolved === true ? '已开启' : '已关闭'}
          </span>
          <Switch
            id={`board-${key}`}
            checked={resolved === true}
            onCheckedChange={onChange}
            aria-label={String(label)}
          />
        </div>
      ) : parameter.enum?.length ? (
        <Select value={String(resolved ?? '')} onValueChange={onChange}>
          <SelectTrigger
            id={`board-${key}`}
            className="h-10 rounded-lg border-slate-200 bg-slate-50 text-xs font-semibold dark:border-white/10 dark:bg-white/[0.03]"
          >
            <SelectValue placeholder="请选择" />
          </SelectTrigger>
          <SelectContent>
            {parameter.enum.map(option => (
              <SelectItem key={option} value={option}>
                {ENUM_LABELS[option] || option}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : parameter.type === 'number' || parameter.type === 'integer' ? (
        <Input
          id={`board-${key}`}
          type="number"
          min={parameter.minimum ?? undefined}
          max={parameter.maximum ?? undefined}
          step={parameter.step ?? (parameter.type === 'integer' ? 1 : 'any')}
          value={
            resolved === null || resolved === undefined ? '' : String(resolved)
          }
          onChange={event =>
            onChange(
              numberValue(event.target.value, parameter.type === 'integer')
            )
          }
          className="h-10 rounded-lg border-slate-200 bg-slate-50 font-mono text-xs dark:border-white/10 dark:bg-white/[0.03]"
        />
      ) : (
        <Input
          id={`board-${key}`}
          type={key.endsWith('_time') ? 'time' : 'text'}
          value={String(resolved ?? '')}
          onChange={event => onChange(event.target.value)}
          className="h-10 rounded-lg border-slate-200 bg-slate-50 font-mono text-xs dark:border-white/10 dark:bg-white/[0.03]"
        />
      )}

      {parameter.description && (
        <p className="text-[10px] leading-relaxed text-slate-500">
          {parameter.description}
        </p>
      )}
    </div>
  );
}

export function LimitUpBoardConfigPanel({
  strategy,
  stockCodes,
  setStockCodes,
  strategyConfig,
  setStrategyConfig,
  runMode,
  onSubmit,
  onSave,
  saveLabel = '保存配置',
  submitLabel = '保存并运行',
  saveDisabled = false,
  submitDisabled = false,
  showSubmit = true,
}: LimitUpBoardConfigPanelProps) {
  useEffect(() => {
    if (runMode !== StrategyRunMode.Backtest) return;
    setStrategyConfig(previous => {
      const additions: Record<string, StrategyConfigValue> = {};
      if (previous.auto_approve_manual_intents === undefined) {
        additions.auto_approve_manual_intents = true;
      }
      if (previous.strict_market_data === undefined) {
        additions.strict_market_data = true;
      }
      if (previous.strict_limit_data === undefined) {
        additions.strict_limit_data = true;
      }
      return Object.keys(additions).length
        ? { ...previous, ...additions }
        : previous;
    });
  }, [runMode, setStrategyConfig]);

  const entries = useMemo(
    () => (strategy.parameterSchema?.properties || []) as ParameterEntry[],
    [strategy.parameterSchema]
  );
  const errors = validateLimitUpBoardConfiguration(strategyConfig, runMode);
  const isBacktest = runMode === StrategyRunMode.Backtest;
  const entryMode = String(
    strategyConfig.entry_execution_mode || 'MANUAL_CONFIRM'
  );
  const autoExit = Boolean(strategyConfig.auto_exit_authorized);

  const handleInstrumentChange = (instrumentCode: string) => {
    setStockCodes(instrumentCode);
    setStrategyConfig(previous => ({
      ...previous,
      instrument_code: instrumentCode,
      instrumentCode,
      stockCodes: instrumentCode ? [instrumentCode] : [],
    }));
  };

  return (
    <div className="space-y-5">
      <Card className="overflow-hidden rounded-2xl border-slate-200 bg-white shadow-xl dark:border-white/10 dark:bg-[#0d1425]">
        <div className="border-b border-slate-200 bg-slate-950 px-5 py-4 dark:border-white/10">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.22em] text-red-400">
                <SlidersHorizontal className="h-3.5 w-3.5" />
                Limit-up board controls
              </div>
              <h2 className="mt-1 text-lg font-bold text-white">
                打板参数与安全门禁
              </h2>
              <p className="mt-1 max-w-2xl text-[11px] leading-relaxed text-slate-400">
                只在尚未封死且接近涨停时产生一次意图；订单、T+1、退出和成交均由统一状态流收敛。
              </p>
            </div>
            <div className="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-3">
              <div className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2">
                <div className="text-slate-500">入场授权</div>
                <div className="mt-1 font-bold text-slate-100">
                  {entryMode === 'AUTO' ? '自动' : '人工确认'}
                </div>
              </div>
              <div className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2">
                <div className="text-slate-500">退出授权</div>
                <div
                  className={cn(
                    'mt-1 font-bold',
                    autoExit ? 'text-emerald-300' : 'text-amber-300'
                  )}
                >
                  {autoExit ? '自动退出' : '人工确认'}
                </div>
              </div>
              <div className="col-span-2 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 sm:col-span-1">
                <div className="text-slate-500">回测粒度</div>
                <div className="mt-1 font-bold text-slate-100">
                  {isBacktest ? 'Tick + 日线' : '实时 Tick'}
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="grid gap-5 p-5 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-2">
            <Label className="text-[10px] font-black uppercase tracking-[0.18em] text-slate-500">
              绑定标的
            </Label>
            <StrategyInstrumentSelector
              value={stockCodes}
              onChange={handleInstrumentChange}
              placeholder="搜索 A 股代码 / 名称 / 拼音"
            />
            <p className="text-[10px] leading-relaxed text-slate-500">
              一个实例只绑定一个标的；已有持仓、未完成买单或活跃卖出计划时不会重复入场。
            </p>
          </div>

          <div
            className={cn(
              'rounded-xl border px-4 py-3 text-[11px] leading-relaxed',
              runMode === StrategyRunMode.Live
                ? 'border-rose-500/25 bg-rose-500/10 text-rose-700 dark:text-rose-200'
                : 'border-amber-500/25 bg-amber-500/10 text-amber-700 dark:text-amber-200'
            )}
          >
            <div className="flex items-center gap-2 font-bold">
              <ShieldAlert className="h-4 w-4" />
              {runMode === StrategyRunMode.Live
                ? '实盘高风险提示'
                : '保守执行口径'}
            </div>
            <p className="mt-2">
              {runMode === StrategyRunMode.Live
                ? `交易账户 ${String(strategyConfig.account_id || '--')}。实例创建后保持待启动；默认人工确认且不授权自动退出。`
                : '一字涨停不买、涨停封死不假成交、当日买入不可卖；无可卖库存时退出会等待下一交易日。'}
            </p>
          </div>
        </div>
      </Card>

      {GROUPS.filter(
        group => !['backtest', 'data'].includes(group.id) || isBacktest
      ).map(group => {
        const groupEntries = entries.filter(
          entry => (entry.value.group || 'entry') === group.id
        );
        if (!groupEntries.length) return null;
        const Icon = group.icon;
        return (
          <Card
            key={group.id}
            className="rounded-2xl border-slate-200 bg-white p-5 shadow-lg dark:border-white/10 dark:bg-[#0d1425]"
          >
            <div className="mb-5 flex items-start gap-3 border-b border-slate-100 pb-4 dark:border-white/5">
              <div className="rounded-lg bg-red-500/10 p-2 text-red-500">
                <Icon className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-slate-900 dark:text-slate-100">
                  {group.title}
                </h3>
                <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
                  {group.description}
                </p>
              </div>
            </div>

            <div className="grid gap-x-6 gap-y-5 md:grid-cols-2 xl:grid-cols-3">
              {groupEntries.map(entry => (
                <ParameterControl
                  key={entry.key}
                  entry={entry}
                  value={strategyConfig[entry.key]}
                  onChange={value =>
                    setStrategyConfig(previous => ({
                      ...previous,
                      [entry.key]: value,
                    }))
                  }
                />
              ))}
            </div>
          </Card>
        );
      })}

      {errors.length > 0 && (
        <div className="rounded-xl border border-rose-500/25 bg-rose-500/10 px-4 py-3 text-xs text-rose-700 dark:text-rose-200">
          {errors.join('；')}
        </div>
      )}

      <div className="flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
        {onSave && (
          <Button
            type="button"
            variant="outline"
            onClick={onSave}
            disabled={saveDisabled || errors.length > 0 || !stockCodes.trim()}
            className="h-11 min-w-36 rounded-xl"
          >
            <Save className="mr-2 h-4 w-4" />
            {saveLabel}
          </Button>
        )}
        {showSubmit && onSubmit && (
          <Button
            type="button"
            onClick={onSubmit}
            disabled={submitDisabled || errors.length > 0 || !stockCodes.trim()}
            className="h-11 min-w-40 rounded-xl bg-red-600 text-white hover:bg-red-500"
          >
            <Play className="mr-2 h-4 w-4" />
            {submitLabel}
          </Button>
        )}
      </div>
    </div>
  );
}
