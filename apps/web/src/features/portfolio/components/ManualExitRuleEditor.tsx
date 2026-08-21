import {
  Activity,
  BadgeDollarSign,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Gauge,
  MousePointerClick,
  ShieldAlert,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  TrendingDown,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/utils/cn';

import { getExitRuleLabel } from './exitRuleLabels';

export interface ManualExitRuleDraft {
  id: string;
  parametersText: string;
  priority: number;
  ruleType: string;
}

interface ExitRuleCapability {
  category: string;
  label: string;
  parameters: unknown;
  ruleType: string;
}

interface RuleParameterField {
  advanced?: boolean;
  description: string;
  key: string;
  label: string;
  max?: number;
  min?: number;
  step?: number;
  type: 'checkbox' | 'number' | 'time';
  unit?: string;
}

interface RulePresentation {
  defaultParameters: Record<string, boolean | number | string>;
  description: string;
  fields: RuleParameterField[];
  group: 'price' | 'risk' | 'special' | 'trend';
  label: string;
  recommended?: string;
  trigger: string;
  usage: string;
}

const groupMeta: Record<
  RulePresentation['group'],
  { description: string; icon: LucideIcon; label: string }
> = {
  price: {
    description: '到达明确价格或收益目标时触发',
    icon: CircleDollarSign,
    label: '价格与收益',
  },
  risk: {
    description: '限制亏损、持有时间或日内截止时间',
    icon: ShieldAlert,
    label: '风险与时限',
  },
  special: {
    description: '涨停行为和已确认的人工退出',
    icon: Gauge,
    label: '特殊场景',
  },
  trend: {
    description: '先进入盈利区，再自动判断继续跟涨或退出',
    icon: TrendingUp,
    label: '趋势跟随',
  },
};

const rulePresentation: Record<string, RulePresentation> = {
  TARGET_PRICE: {
    defaultParameters: { target_price: 0 },
    description: '价格到达指定目标后立即触发，不继续判断上涨强弱。',
    fields: [
      {
        description: '当前价大于或等于该价格时触发',
        key: 'target_price',
        label: '目标价格',
        min: 0,
        step: 0.01,
        type: 'number',
        unit: '元',
      },
    ],
    group: 'price',
    label: getExitRuleLabel('TARGET_PRICE'),
    trigger: '当前价 ≥ 目标价',
    usage: '适合已经确定卖出价、希望到价兑现的持仓。',
  },
  STOP_PRICE: {
    defaultParameters: { stop_price: 0 },
    description: '价格跌到指定位置时触发，以绝对价格限制下行风险。',
    fields: [
      {
        description: '当前价小于或等于该价格时触发',
        key: 'stop_price',
        label: '止损价格',
        min: 0,
        step: 0.01,
        type: 'number',
        unit: '元',
      },
    ],
    group: 'risk',
    label: getExitRuleLabel('STOP_PRICE'),
    trigger: '当前价 ≤ 止损价',
    usage: '适合你明确知道不能跌破的支撑位。',
  },
  GROSS_TAKE_PROFIT: {
    defaultParameters: { target_profit_pct: 5 },
    description: '按持仓成本计算毛收益率，达到目标后立即触发。',
    fields: [
      {
        description: '不扣除佣金、印花税等交易费用',
        key: 'target_profit_pct',
        label: '目标毛收益率',
        step: 0.1,
        type: 'number',
        unit: '%',
      },
    ],
    group: 'price',
    label: getExitRuleLabel('GROSS_TAKE_PROFIT'),
    trigger: '毛收益率 ≥ 目标值',
    usage: '适合快速估算，不适合对小额持仓精确计算净利润。',
  },
  NET_TAKE_PROFIT: {
    defaultParameters: { target_profit_pct: 5 },
    description: '估算扣除双边费用后的净收益率，达到目标后立即触发。',
    fields: [
      {
        description: '已考虑计划中的佣金、印花税和过户费配置',
        key: 'target_profit_pct',
        label: '目标净收益率',
        step: 0.1,
        type: 'number',
        unit: '%',
      },
    ],
    group: 'price',
    label: getExitRuleLabel('NET_TAKE_PROFIT'),
    trigger: '净收益率 ≥ 目标值',
    usage: '适合对实际到手收益有明确要求的持仓。',
  },
  TRAILING_NET_PROFIT: {
    defaultParameters: {
      base_floor_pct: 0.5,
      gap_slope: 0.25,
      high_profit_arm_pct: 4,
      high_profit_lock_enabled: true,
      high_profit_max_drawdown_pct: 1.2,
      initial_gap_pct: 1.5,
      max_gap_pct: 3,
      target_profit_pct: 2,
    },
    description:
      '达到净收益门槛后抬高保护线，上涨时继续持有，回落到保护线时触发。',
    fields: [
      {
        description: '达到该净收益率后才开始保护利润',
        key: 'target_profit_pct',
        label: '开始跟踪收益率',
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '首次启动后至少保留的净收益率',
        key: 'base_floor_pct',
        label: '基础保盈线',
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '高利润区允许从净收益峰值回吐的最大幅度',
        key: 'high_profit_max_drawdown_pct',
        label: '高利润最大回吐',
        min: 0,
        step: 0.1,
        type: 'number',
        unit: '%',
      },
    ],
    group: 'trend',
    label: getExitRuleLabel('TRAILING_NET_PROFIT'),
    recommended: '稳健跟涨',
    trigger: '先达标，再跌破动态保盈线',
    usage: '适合趋势持仓：希望利润继续扩大，又不愿大幅回吐。',
  },
  ADAPTIVE_VOLUME_PRICE_TRAILING: {
    defaultParameters: {
      arm_target_profit_pct: 2,
      base_floor_pct: 0.5,
      confirm_observations: 2,
      confirm_score: 3,
      immediate_drawdown_pct: 1.2,
      initial_gap_pct: 1.5,
      max_gap_pct: 3,
    },
    description:
      '达标后自动综合回撤、短线斜率、量速和五档盘口，强势跟涨，转弱退出。',
    fields: [
      {
        description: '达到该毛收益率后开始自动识别趋势',
        key: 'arm_target_profit_pct',
        label: '开始跟踪收益率',
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '从可执行价格峰值回撤到该幅度时立即触发',
        key: 'immediate_drawdown_pct',
        label: '立即退出回撤',
        min: 0,
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '连续多少次确认转弱后触发',
        key: 'confirm_observations',
        label: '转弱确认次数',
        min: 1,
        step: 1,
        type: 'number',
        unit: '次',
      },
    ],
    group: 'trend',
    label: getExitRuleLabel('ADAPTIVE_VOLUME_PRICE_TRAILING'),
    recommended: '智能跟涨',
    trigger: '达标后量价转弱，或快速回撤',
    usage: '适合日内活跃行情；系统自动判断强弱，不需要你预判快速上涨。',
  },
  RAPID_PROFIT_REVERSAL: {
    defaultParameters: {
      arm_profit_pct: 4,
      confirm_ticks: 2,
      drawdown_pct: 0.8,
      window_seconds: 15,
    },
    description: '高收益形成新峰值后，短时间内快速回吐时紧急触发。',
    fields: [
      {
        description: '净收益峰值达到该水平后才生效',
        key: 'arm_profit_pct',
        label: '启动收益率',
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '只检查新峰值形成后的这段时间',
        key: 'window_seconds',
        label: '反转窗口',
        min: 1,
        step: 1,
        type: 'number',
        unit: '秒',
      },
      {
        description: '窗口内净收益从峰值回吐的幅度',
        key: 'drawdown_pct',
        label: '收益回吐',
        min: 0,
        step: 0.1,
        type: 'number',
        unit: '%',
      },
    ],
    group: 'trend',
    label: getExitRuleLabel('RAPID_PROFIT_REVERSAL'),
    trigger: '新高后短时快速回吐',
    usage: '适合作为动态保盈的紧急补充，不建议单独承担全部止盈。',
  },
  TRAILING_PRICE_DRAWDOWN: {
    defaultParameters: {
      arm_profit_pct: 0,
      drawdown_pct: 3,
      min_holding_trading_days: 1,
    },
    description: '达到启动收益后，跟踪持仓以来最高价，价格回撤到阈值时触发。',
    fields: [
      {
        description: '最高价对应毛收益达到该水平后开始跟踪',
        key: 'arm_profit_pct',
        label: '启动收益率',
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '当前价相对最高价的回撤幅度',
        key: 'drawdown_pct',
        label: '峰值回撤',
        min: 0,
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '至少持有几个交易日后才允许触发',
        key: 'min_holding_trading_days',
        label: '最短持有日',
        min: 1,
        step: 1,
        type: 'number',
        unit: '日',
      },
    ],
    group: 'trend',
    label: getExitRuleLabel('TRAILING_PRICE_DRAWDOWN'),
    trigger: '最高价回撤达到阈值',
    usage: '适合跨日趋势或打板退出；它按价格而非扣费后净收益计算。',
  },
  HARD_STOP: {
    defaultParameters: { min_holding_trading_days: 1, stop_loss_pct: -0.8 },
    description: '按扣费后的持仓净收益率强制止损，不依赖是否进入止盈区。',
    fields: [
      {
        description: '通常填写负数，例如 -3 表示亏损 3%',
        key: 'stop_loss_pct',
        label: '最大净亏损',
        max: 0,
        step: 0.1,
        type: 'number',
        unit: '%',
      },
      {
        description: '至少持有几个交易日后才允许触发',
        key: 'min_holding_trading_days',
        label: '最短持有日',
        min: 1,
        step: 1,
        type: 'number',
        unit: '日',
      },
    ],
    group: 'risk',
    label: getExitRuleLabel('HARD_STOP'),
    recommended: '风险底线',
    trigger: '净收益率 ≤ 止损线',
    usage: '适合作为其他止盈规则的兜底，通常设置更高 priority。',
  },
  TIME_OF_DAY: {
    defaultParameters: { exit_time: '14:50' },
    description: '每天到达指定时刻时触发，不判断当前盈亏。',
    fields: [
      {
        description: '使用交易日内的北京时间',
        key: 'exit_time',
        label: '退出时间',
        type: 'time',
      },
    ],
    group: 'risk',
    label: getExitRuleLabel('TIME_OF_DAY'),
    trigger: '当日时间 ≥ 指定时刻',
    usage: '适合必须在收盘前结束的日内计划。',
  },
  MAX_HOLDING_DAYS: {
    defaultParameters: {
      exit_time: '14:50',
      max_holding_trading_days: 1,
    },
    description: '达到最大交易日持有期，并到达指定时刻后触发。',
    fields: [
      {
        description: '只计算交易日',
        key: 'max_holding_trading_days',
        label: '最大持有日',
        min: 1,
        step: 1,
        type: 'number',
        unit: '日',
      },
      {
        description: '达到最大持有日后，在该时间执行',
        key: 'exit_time',
        label: '退出时间',
        type: 'time',
      },
    ],
    group: 'risk',
    label: getExitRuleLabel('MAX_HOLDING_DAYS'),
    trigger: '持有日 ≥ 上限，且到达退出时间',
    usage: '适合限制资金占用时间或执行跨日退出纪律。',
  },
  LIMIT_UP_TOUCH: {
    defaultParameters: {
      min_holding_trading_days: 1,
      tolerance_ticks: 0,
    },
    description: '可执行买一价达到涨停价或允许的容差价位时触发。',
    fields: [
      {
        description: '允许买一价距离涨停价几个最小价位',
        key: 'tolerance_ticks',
        label: '涨停容差',
        min: 0,
        step: 1,
        type: 'number',
        unit: 'Tick',
      },
      {
        description: '至少持有几个交易日后才允许触发',
        key: 'min_holding_trading_days',
        label: '最短持有日',
        min: 1,
        step: 1,
        type: 'number',
        unit: '日',
      },
    ],
    group: 'special',
    label: getExitRuleLabel('LIMIT_UP_TOUCH'),
    trigger: '可执行买一价触及涨停',
    usage: '适合触板即兑现；与“封板后开板再卖”不同。',
  },
  LIMIT_UP_BREAK: {
    defaultParameters: {
      break_ticks: 1,
      min_holding_trading_days: 1,
      min_seal_seconds: 0,
      seal_tolerance_ticks: 0,
    },
    description: '先确认封住涨停，随后价格跌破涨停价若干 Tick 时触发。',
    fields: [
      {
        description: '至少连续封板多久才确认有效',
        key: 'min_seal_seconds',
        label: '最短封板时间',
        min: 0,
        step: 1,
        type: 'number',
        unit: '秒',
      },
      {
        description: '跌破涨停价几个最小价位后触发',
        key: 'break_ticks',
        label: '开板跌破',
        min: 1,
        step: 1,
        type: 'number',
        unit: 'Tick',
      },
      {
        description: '至少持有几个交易日后才允许触发',
        key: 'min_holding_trading_days',
        label: '最短持有日',
        min: 1,
        step: 1,
        type: 'number',
        unit: '日',
      },
    ],
    group: 'special',
    label: getExitRuleLabel('LIMIT_UP_BREAK'),
    trigger: '确认封板后再开板',
    usage: '适合打板持仓，希望封板时继续持有、炸板时退出。',
  },
  MANUAL_TRIGGER: {
    defaultParameters: {},
    description: '不等待市场条件，计划进入评估后立即触发。',
    fields: [],
    group: 'special',
    label: getExitRuleLabel('MANUAL_TRIGGER'),
    trigger: '计划激活后立即触发',
    usage: '只用于已经确认的人工清仓，不要把它当作等待按钮。',
  },
};

const ruleTypeIcons: Record<string, LucideIcon> = {
  ADAPTIVE_VOLUME_PRICE_TRAILING: Activity,
  GROSS_TAKE_PROFIT: BadgeDollarSign,
  HARD_STOP: ShieldAlert,
  LIMIT_UP_BREAK: TrendingDown,
  LIMIT_UP_TOUCH: TrendingUp,
  MANUAL_TRIGGER: MousePointerClick,
  MAX_HOLDING_DAYS: Clock3,
  NET_TAKE_PROFIT: CircleDollarSign,
  RAPID_PROFIT_REVERSAL: TrendingDown,
  STOP_PRICE: ShieldAlert,
  TARGET_PRICE: CircleDollarSign,
  TIME_OF_DAY: Clock3,
  TRAILING_NET_PROFIT: TrendingUp,
  TRAILING_PRICE_DRAWDOWN: TrendingDown,
};

const groupOrder: RulePresentation['group'][] = [
  'trend',
  'price',
  'risk',
  'special',
];

function parseParameters(text: string): Record<string, unknown> {
  try {
    const value = JSON.parse(text || '{}') as unknown;
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

function serializeParameters(parameters: Record<string, unknown>) {
  return JSON.stringify(parameters);
}

function fallbackPresentation(
  capability?: ExitRuleCapability
): RulePresentation {
  return {
    defaultParameters: {},
    description: '该规则由退出计划运行时提供，请在高级设置中填写参数。',
    fields: [],
    group: 'special',
    label: capability?.label || capability?.ruleType || '退出规则',
    trigger: '按运行时规则判断',
    usage: '保存前请确认高级参数与对应策略文档。',
  };
}

function getPresentation(ruleType: string, capability?: ExitRuleCapability) {
  const local = rulePresentation[ruleType];
  if (!local) return fallbackPresentation(capability);
  if (!capability?.label) return local;
  return { ...local, label: capability.label };
}

function formatValue(value: unknown, fallback = '--') {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function rulePreview(ruleType: string, parameters: Record<string, unknown>) {
  switch (ruleType) {
    case 'TARGET_PRICE':
      return `价格达到 ${formatValue(parameters.target_price)} 元时触发`;
    case 'STOP_PRICE':
      return `价格跌到 ${formatValue(parameters.stop_price)} 元时触发`;
    case 'GROSS_TAKE_PROFIT':
      return `毛收益达到 ${formatValue(parameters.target_profit_pct)}% 时触发`;
    case 'NET_TAKE_PROFIT':
      return `净收益达到 ${formatValue(parameters.target_profit_pct)}% 时触发`;
    case 'TRAILING_NET_PROFIT':
      return `净收益达到 ${formatValue(parameters.target_profit_pct)}% 后启动，回落到动态保盈线时触发`;
    case 'ADAPTIVE_VOLUME_PRICE_TRAILING':
      return `毛收益达到 ${formatValue(parameters.arm_target_profit_pct)}% 后自动跟踪量价，转弱或快速回撤时触发`;
    case 'RAPID_PROFIT_REVERSAL':
      return `净收益峰值达到 ${formatValue(parameters.arm_profit_pct)}% 后，${formatValue(parameters.window_seconds)} 秒内回吐 ${formatValue(parameters.drawdown_pct)}% 时确认`;
    case 'TRAILING_PRICE_DRAWDOWN':
      return `峰值收益达到 ${formatValue(parameters.arm_profit_pct)}% 后，价格从最高点回撤 ${formatValue(parameters.drawdown_pct)}% 时触发`;
    case 'HARD_STOP':
      return `净收益跌到 ${formatValue(parameters.stop_loss_pct)}% 时强制触发`;
    case 'TIME_OF_DAY':
      return `交易日到达 ${formatValue(parameters.exit_time)} 时触发`;
    case 'MAX_HOLDING_DAYS':
      return `持有达到 ${formatValue(parameters.max_holding_trading_days)} 个交易日，并到达 ${formatValue(parameters.exit_time)} 时触发`;
    case 'LIMIT_UP_TOUCH':
      return '可执行买一价触及涨停价时触发';
    case 'LIMIT_UP_BREAK':
      return `确认封板后，跌破涨停价 ${formatValue(parameters.break_ticks)} Tick 时触发`;
    case 'MANUAL_TRIGGER':
      return '计划激活后立即触发，不等待行情';
    default:
      return '按高级参数评估退出条件';
  }
}

function ParameterInput({
  field,
  inputId,
  onChange,
  value,
}: {
  field: RuleParameterField;
  inputId: string;
  onChange: (value: boolean | number | string | undefined) => void;
  value: unknown;
}) {
  if (field.type === 'checkbox') {
    return (
      <label
        className="flex min-h-20 cursor-pointer items-start gap-3 rounded-lg border border-white/8 bg-black/15 p-3 transition-colors hover:border-blue-400/30 hover:bg-blue-500/[0.04]"
        htmlFor={inputId}
      >
        <input
          checked={Boolean(value)}
          className="mt-0.5 h-4 w-4 accent-blue-500"
          id={inputId}
          onChange={event => onChange(event.target.checked)}
          type="checkbox"
        />
        <span>
          <span className="block text-xs font-black text-slate-200">
            {field.label}
          </span>
          <span className="mt-1 block text-[10px] font-medium leading-4 text-slate-500">
            {field.description}
          </span>
        </span>
      </label>
    );
  }

  return (
    <label className="grid gap-1.5" htmlFor={inputId}>
      <span className="flex items-center justify-between gap-2 text-[11px] font-black text-slate-300">
        {field.label}
        {field.unit && (
          <span className="font-mono text-[9px] text-slate-600">
            {field.unit}
          </span>
        )}
      </span>
      <input
        aria-label={field.label}
        className="h-9 rounded-lg border border-white/10 bg-[#080d18] px-3 font-mono text-xs text-slate-100 outline-none transition-colors hover:border-white/20 focus:border-blue-400/60 focus:ring-2 focus:ring-blue-500/10"
        id={inputId}
        max={field.max}
        min={field.min}
        onChange={event => {
          if (!event.target.value) {
            onChange(undefined);
            return;
          }
          onChange(
            field.type === 'number'
              ? Number(event.target.value)
              : event.target.value
          );
        }}
        step={field.step}
        type={field.type}
        value={value === undefined || value === null ? '' : String(value)}
      />
      <span className="text-[9px] font-medium leading-4 text-slate-600">
        {field.description}
      </span>
    </label>
  );
}

function RuleTypePicker({
  capabilities,
  index,
  onSelect,
  ruleType,
}: {
  capabilities: ExitRuleCapability[];
  index: number;
  onSelect: (ruleType: string) => void;
  ruleType: string;
}) {
  const [pickerOpen, setPickerOpen] = React.useState(false);
  const available = React.useMemo(() => {
    if (capabilities.length > 0) return capabilities;
    return Object.entries(rulePresentation).map(([type, presentation]) => ({
      category: presentation.group,
      label: presentation.label,
      parameters: {},
      ruleType: type,
    }));
  }, [capabilities]);
  const capability = available.find(item => item.ruleType === ruleType);
  const presentation = getPresentation(ruleType, capability);
  const SelectedIcon = ruleTypeIcons[ruleType] || SlidersHorizontal;

  return (
    <Popover onOpenChange={setPickerOpen} open={pickerOpen}>
      <PopoverTrigger asChild>
        <button
          aria-label={`规则 ${index + 1} 类型`}
          className="group flex w-full cursor-pointer items-center gap-3 rounded-xl border border-blue-400/25 bg-blue-500/[0.06] p-3 text-left outline-none transition-colors hover:border-blue-400/45 hover:bg-blue-500/[0.09] focus-visible:ring-2 focus-visible:ring-blue-400/40"
          type="button"
        >
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-blue-400/20 bg-blue-500/10 text-blue-200">
            <SelectedIcon className="h-4 w-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-black text-slate-100">
                {presentation.label}
              </span>
              {presentation.recommended && (
                <span className="rounded-full border border-emerald-400/20 bg-emerald-500/10 px-2 py-0.5 text-[9px] font-black text-emerald-200">
                  {presentation.recommended}
                </span>
              )}
            </span>
            <span className="mt-1 block truncate text-[10px] font-medium text-slate-500">
              {presentation.trigger}
            </span>
          </span>
          <span className="flex shrink-0 items-center gap-2 text-[10px] font-bold text-blue-300">
            更换
            <ChevronDown
              className={cn(
                'h-4 w-4 transition-transform duration-200',
                pickerOpen && 'rotate-180'
              )}
            />
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        className="max-h-[min(720px,80vh)] w-[min(760px,calc(100vw-32px))] overflow-y-auto border-slate-700 bg-[#0b1120] p-0 shadow-2xl shadow-black/50"
        sideOffset={8}
      >
        <div className="sticky top-0 z-10 border-b border-white/8 bg-[#0b1120]/95 p-4 backdrop-blur-xl">
          <div className="flex items-start gap-3">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-500/10 text-blue-200">
              <Sparkles className="h-4 w-4" />
            </span>
            <div>
              <h4 className="text-sm font-black text-slate-100">
                选择卖出策略
              </h4>
              <p className="mt-1 text-[11px] font-medium leading-5 text-slate-400">
                先选择“什么情况下卖”。系统只在条件满足时触发；添加多条规则表示任一条件满足即可退出。
              </p>
            </div>
          </div>
        </div>
        <div className="grid gap-5 p-4">
          {groupOrder.map(group => {
            const meta = groupMeta[group];
            const GroupIcon = meta.icon;
            const options = available.filter(
              item => getPresentation(item.ruleType, item).group === group
            );
            if (options.length === 0) return null;
            return (
              <section aria-labelledby={`exit-rule-group-${group}`} key={group}>
                <div className="mb-2 flex items-center gap-2">
                  <GroupIcon className="h-3.5 w-3.5 text-slate-400" />
                  <h5
                    className="text-[11px] font-black text-slate-300"
                    id={`exit-rule-group-${group}`}
                  >
                    {meta.label}
                  </h5>
                  <span className="text-[9px] font-medium text-slate-600">
                    {meta.description}
                  </span>
                </div>
                <div
                  aria-label={`${meta.label}策略`}
                  className="grid gap-2 sm:grid-cols-2"
                  role="radiogroup"
                >
                  {options.map(option => {
                    const optionPresentation = getPresentation(
                      option.ruleType,
                      option
                    );
                    const OptionIcon =
                      ruleTypeIcons[option.ruleType] || SlidersHorizontal;
                    const selected = option.ruleType === ruleType;
                    return (
                      <button
                        aria-checked={selected}
                        className={cn(
                          'relative flex min-h-28 cursor-pointer items-start gap-3 rounded-xl border p-3 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-blue-400/40',
                          selected
                            ? 'border-blue-400/50 bg-blue-500/10'
                            : 'border-white/8 bg-black/15 hover:border-white/20 hover:bg-white/[0.04]'
                        )}
                        key={option.ruleType}
                        onClick={() => {
                          onSelect(option.ruleType);
                          setPickerOpen(false);
                        }}
                        role="radio"
                        type="button"
                      >
                        <span
                          className={cn(
                            'flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border',
                            selected
                              ? 'border-blue-400/30 bg-blue-500/15 text-blue-200'
                              : 'border-white/8 bg-white/[0.03] text-slate-500'
                          )}
                        >
                          <OptionIcon className="h-4 w-4" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <span className="text-[11px] font-black text-slate-100">
                              {optionPresentation.label}
                            </span>
                            {optionPresentation.recommended && (
                              <span className="rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[8px] font-black text-emerald-200">
                                {optionPresentation.recommended}
                              </span>
                            )}
                          </span>
                          <span className="mt-1 block text-[9px] font-mono text-slate-600">
                            {option.ruleType}
                          </span>
                          <span className="mt-1.5 block text-[10px] font-medium leading-4 text-slate-400">
                            {optionPresentation.description}
                          </span>
                          <span className="mt-1.5 block text-[9px] font-medium leading-4 text-slate-600">
                            适合：{optionPresentation.usage}
                          </span>
                        </span>
                        {selected && (
                          <Check className="absolute right-2 top-2 h-3.5 w-3.5 text-blue-300" />
                        )}
                      </button>
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function ManualExitRuleEditor({
  capabilities,
  canDelete,
  index,
  onChange,
  onDelete,
  rule,
}: {
  capabilities: ExitRuleCapability[];
  canDelete: boolean;
  index: number;
  onChange: (next: ManualExitRuleDraft) => void;
  onDelete: () => void;
  rule: ManualExitRuleDraft;
}) {
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const capability = capabilities.find(item => item.ruleType === rule.ruleType);
  const presentation = getPresentation(rule.ruleType, capability);
  const parameters = parseParameters(rule.parametersText);
  const updateParameter = (
    key: string,
    value: boolean | number | string | undefined
  ) => {
    const next = { ...parameters };
    if (value === undefined) delete next[key];
    else next[key] = value;
    onChange({ ...rule, parametersText: serializeParameters(next) });
  };

  return (
    <article className="rounded-xl border border-white/8 bg-black/15 p-3 shadow-sm shadow-black/20">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex h-6 min-w-6 items-center justify-center rounded-md bg-white/[0.05] px-1.5 font-mono text-[10px] font-black text-slate-400">
            {index + 1}
          </span>
          <div>
            <h4 className="text-[11px] font-black text-slate-200">触发规则</h4>
            <p className="text-[9px] font-medium text-slate-600">
              满足本规则时，申请卖出计划中的剩余数量
            </p>
          </div>
        </div>
        <Button
          aria-label={`删除规则 ${index + 1}`}
          disabled={!canDelete}
          onClick={onDelete}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Trash2 />
        </Button>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-[minmax(280px,0.9fr)_minmax(360px,1.1fr)]">
        <div className="grid content-start gap-3">
          <RuleTypePicker
            capabilities={capabilities}
            index={index}
            onSelect={nextType => {
              if (nextType === rule.ruleType) return;
              const nextCapability = capabilities.find(
                item => item.ruleType === nextType
              );
              const nextPresentation = getPresentation(
                nextType,
                nextCapability
              );
              onChange({
                ...rule,
                parametersText: serializeParameters(
                  nextPresentation.defaultParameters
                ),
                ruleType: nextType,
              });
            }}
            ruleType={rule.ruleType}
          />

          <div className="rounded-xl border border-white/8 bg-[#080d18]/70 p-3">
            <div className="flex items-center gap-2 text-[10px] font-black text-slate-300">
              <ChevronRight className="h-3.5 w-3.5 text-blue-300" />
              如何使用
            </div>
            <p className="mt-2 text-[10px] font-medium leading-5 text-slate-400">
              {presentation.description}
            </p>
            <p className="mt-2 text-[10px] font-medium leading-5 text-slate-500">
              <span className="font-black text-slate-400">适合：</span>
              {presentation.usage}
            </p>
            <div className="mt-3 rounded-lg border border-blue-400/15 bg-blue-500/[0.05] px-3 py-2">
              <span className="text-[9px] font-black text-blue-300">
                当前配置
              </span>
              <p className="mt-1 text-[10px] font-bold leading-4 text-slate-300">
                {rulePreview(rule.ruleType, parameters)}
              </p>
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-white/8 bg-[#080d18]/45 p-3">
          <div className="mb-3">
            <h5 className="text-[11px] font-black text-slate-200">触发参数</h5>
            <p className="mt-1 text-[9px] font-medium text-slate-600">
              百分比直接填写 2 表示 2%，不需要填写 0.02。
            </p>
          </div>
          {presentation.fields.length > 0 ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {presentation.fields
                .filter(field => !field.advanced)
                .map(field => (
                  <ParameterInput
                    field={field}
                    inputId={`exit-rule-${rule.id}-${field.key}`}
                    key={field.key}
                    onChange={value => updateParameter(field.key, value)}
                    value={parameters[field.key]}
                  />
                ))}
            </div>
          ) : (
            <div
              className={cn(
                'rounded-lg border px-3 py-3 text-[10px] font-bold leading-5',
                rule.ruleType === 'MANUAL_TRIGGER'
                  ? 'border-rose-400/20 bg-rose-500/[0.06] text-rose-200'
                  : 'border-white/8 bg-white/[0.02] text-slate-400'
              )}
            >
              {presentation.description}
            </div>
          )}

          <Collapsible
            className="mt-3 border-t border-white/8 pt-3"
            onOpenChange={setAdvancedOpen}
            open={advancedOpen}
          >
            <CollapsibleTrigger asChild>
              <button
                className="flex w-full cursor-pointer items-center justify-between rounded-lg px-2 py-2 text-left text-[10px] font-black text-slate-400 outline-none transition-colors hover:bg-white/[0.03] hover:text-slate-200 focus-visible:ring-2 focus-visible:ring-blue-400/40"
                type="button"
              >
                <span className="flex items-center gap-2">
                  <SlidersHorizontal className="h-3.5 w-3.5" />
                  高级设置
                </span>
                <ChevronDown
                  className={cn(
                    'h-3.5 w-3.5 transition-transform duration-200',
                    advancedOpen && 'rotate-180'
                  )}
                />
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent className="grid gap-3 px-2 pb-1 pt-3 sm:grid-cols-[120px_minmax(0,1fr)]">
              <label className="grid content-start gap-1.5 text-[10px] font-black text-slate-400">
                优先级
                <input
                  aria-label={`规则 ${index + 1} 优先级`}
                  className="h-9 rounded-lg border border-white/10 bg-[#080d18] px-3 font-mono text-xs text-slate-100 outline-none focus:border-blue-400/60"
                  onChange={event =>
                    onChange({
                      ...rule,
                      priority: Number(event.target.value),
                    })
                  }
                  type="number"
                  value={rule.priority}
                />
                <span className="text-[9px] font-medium leading-4 text-slate-600">
                  同时触发时，大值优先。
                </span>
              </label>
              <label className="grid gap-1.5 text-[10px] font-black text-slate-400">
                参数 JSON
                <textarea
                  aria-label={`规则 ${index + 1} 参数 JSON`}
                  className="min-h-20 resize-y rounded-lg border border-white/10 bg-[#080d18] px-3 py-2 font-mono text-[10px] leading-5 text-slate-300 outline-none focus:border-blue-400/60"
                  onChange={event =>
                    onChange({
                      ...rule,
                      parametersText: event.target.value,
                    })
                  }
                  spellCheck={false}
                  value={rule.parametersText}
                />
                <span className="text-[9px] font-medium leading-4 text-slate-600">
                  仅供专家调整未展示参数；普通配置无需修改。
                </span>
              </label>
            </CollapsibleContent>
          </Collapsible>
        </div>
      </div>
    </article>
  );
}
