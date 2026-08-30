import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Database,
  Gauge,
  GitBranch,
  Loader2,
  RefreshCw,
  Scale,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  TimerReset,
  Zap,
} from 'lucide-react';
import * as React from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/utils/cn';

import {
  supportedRequiredFields,
  supportedSessionCodes,
  type SignalPolicyLike,
} from './signalPolicy';
import type { SignalPolicyForm, SignalPolicyFormValue } from './types';

export type SignalPolicyPreviewLike = {
  valid: boolean;
  configVersion: number;
  errors: readonly { code: string; field?: string | null; message: string }[];
  warnings: readonly { code: string; field?: string | null; message: string }[];
  changedFields: readonly string[];
  requiresRewarm: boolean;
  normalizedPolicy?: {
    policyVersion: string;
    featureSchemaVersion: string;
  } | null;
};

type PolicyFieldProps = {
  field: keyof SignalPolicyForm;
  label: string;
  suffix?: string;
  value: string;
  inputMode?: 'decimal' | 'numeric' | 'text';
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
};

function PolicyField({
  field,
  label,
  suffix,
  value,
  inputMode = 'decimal',
  onChange,
}: PolicyFieldProps) {
  const id = `t-trade-policy-${field}`;
  return (
    <div className="space-y-1.5" data-policy-field={field}>
      <Label htmlFor={id} className="text-ui-caption font-bold text-slate-400">
        {label}
      </Label>
      <div className="relative">
        <Input
          id={id}
          inputMode={inputMode}
          value={value}
          onChange={event => onChange(field, event.target.value)}
          className={cn(
            'h-control-default rounded-control border-white/10 bg-[#07111f] font-mono text-ui-label tabular-nums focus-visible:ring-primary/70',
            suffix && 'pr-14'
          )}
          aria-describedby={suffix ? `${id}-unit` : undefined}
        />
        {suffix && (
          <span
            id={`${id}-unit`}
            className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-ui-caption font-semibold text-slate-500"
          >
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}

type StringPolicyFieldKey = {
  [Field in keyof SignalPolicyForm]: SignalPolicyForm[Field] extends string
    ? Field
    : never;
}[keyof SignalPolicyForm];

type StringPolicyEditorField = {
  field: StringPolicyFieldKey;
  label: string;
  suffix?: string;
  kind?: 'number' | 'time';
};

type PolicyEditorField =
  | StringPolicyEditorField
  | {
      field: 'momentumEnabled';
      label: string;
      kind: 'toggle';
    }
  | {
      field: 'pullbackRequiredFields' | 'momentumRequiredFields';
      label: string;
      kind: 'required-fields';
    }
  | {
      field: 'allowedSessionCodes';
      label: string;
      kind: 'sessions';
    };

type PolicyEditorGroup = {
  title: string;
  description: string;
  defaultOpen?: boolean;
  fields: readonly PolicyEditorField[];
};

const signalPolicyEditorGroups: readonly PolicyEditorGroup[] = [
  {
    title: '数据健康与状态窗口',
    description: '分别约束两条路径的样本、覆盖、必需字段、报价年龄与稀疏降级。',
    defaultOpen: true,
    fields: [
      { field: 'maxSamples', label: '窗口样本上限', suffix: '条' },
      { field: 'maxQuoteAgeMs', label: '报价最大年龄', suffix: 'ms' },
      { field: 'pullbackMinSamples', label: '回撤最少样本', suffix: '条' },
      {
        field: 'pullbackMinCoverageSeconds',
        label: '回撤最小覆盖',
        suffix: '秒',
      },
      { field: 'momentumMinSamples', label: '动量最少样本', suffix: '条' },
      {
        field: 'momentumMinCoverageSeconds',
        label: '动量最小覆盖',
        suffix: '秒',
      },
      {
        field: 'sparseDegradedGapSeconds',
        label: '稀疏降级间隔',
        suffix: '秒',
      },
      {
        field: 'pullbackRequiredFields',
        label: '回撤必需行情字段',
        kind: 'required-fields',
      },
      {
        field: 'momentumRequiredFields',
        label: '动量必需行情字段',
        kind: 'required-fields',
      },
    ],
  },
  {
    title: '交易时段与收盘保护',
    description:
      '时段由 Engine 分类；规则只在显式启用且可容纳完整候选生命周期的窗口内发射。',
    defaultOpen: true,
    fields: [
      {
        field: 'allowedSessionCodes',
        label: '允许的连续竞价时段',
        kind: 'sessions',
      },
      { field: 'continuousAmStartTime', label: '上午开始', kind: 'time' },
      { field: 'continuousAmEndTime', label: '上午结束', kind: 'time' },
      { field: 'continuousPmStartTime', label: '下午开始', kind: 'time' },
      { field: 'continuousPmEndTime', label: '下午结束', kind: 'time' },
      { field: 'closeProtectionSeconds', label: '收盘保护', suffix: '秒' },
    ],
  },
  {
    title: '回撤反弹路径',
    description: '回撤形成、低点稳定、反弹确认与量能短窗共同构成回撤 FSM。',
    defaultOpen: true,
    fields: [
      { field: 'pullbackLookbackSeconds', label: '回撤回看窗口', suffix: '秒' },
      {
        field: 'pullbackStabilizationSeconds',
        label: '低点稳定时长',
        suffix: '秒',
      },
      { field: 'pullbackThresholdPct', label: '最低回撤', suffix: '%' },
      {
        field: 'pullbackFormationThresholdMultiplier',
        label: '形成阶段倍率',
        suffix: '0–1',
      },
      { field: 'pullbackReboundThresholdPct', label: '最低反弹', suffix: '%' },
      { field: 'pullbackMaxSpreadTicks', label: '最大价差', suffix: 'tick' },
      {
        field: 'pullbackVolumeShortWindowSeconds',
        label: '量能短窗',
        suffix: '秒',
      },
      {
        field: 'pullbackVolumeBaselineWindowSeconds',
        label: '量能基线窗口',
        suffix: '秒',
      },
    ],
  },
  {
    title: '早期动量路径',
    description:
      '短窗涨幅、持续性、成交速度、VWAP 甜蜜区和流动性共同确认早期加速。',
    defaultOpen: true,
    fields: [
      { field: 'momentumEnabled', label: '启用动量路径', kind: 'toggle' },
      { field: 'momentumWindowSeconds', label: '动量短窗', suffix: '秒' },
      { field: 'momentumMinRisePct', label: '最低涨幅', suffix: '%' },
      {
        field: 'momentumFormationThresholdMultiplier',
        label: '形成阶段倍率',
        suffix: '0–1',
      },
      { field: 'momentumMinMoveSeconds', label: '最短持续', suffix: '秒' },
      { field: 'momentumBaselineSeconds', label: '成交基线窗口', suffix: '秒' },
      {
        field: 'momentumBaselineCoverageRatio',
        label: '基线覆盖率',
        suffix: '0–1',
      },
      {
        field: 'momentumMinAmountVelocityRatio',
        label: '成交速度倍率',
        suffix: '倍',
      },
      {
        field: 'momentumMinVwapPremiumPct',
        label: 'VWAP 甜蜜区下界',
        suffix: '%',
      },
      {
        field: 'momentumMaxVwapPremiumPct',
        label: 'VWAP 追涨上限',
        suffix: '%',
      },
      {
        field: 'momentumHighToleranceTicks',
        label: '高点容差',
        suffix: 'tick',
      },
      { field: 'momentumMaxSpreadTicks', label: '最大价差', suffix: 'tick' },
      { field: 'momentumMaxSpreadPct', label: '价差占比上限', suffix: '%' },
    ],
  },
  {
    title: 'D-1 画像安全夹取',
    description:
      '限制历史画像对实时阈值的影响范围，防止画像异常放大或压低规则。',
    fields: [
      {
        field: 'profilePullbackThresholdMinMultiplier',
        label: '回撤阈值倍率下界',
        suffix: '倍',
      },
      {
        field: 'profilePullbackThresholdMaxMultiplier',
        label: '回撤阈值倍率上界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumRiseMinMultiplier',
        label: '动量涨幅倍率下界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumRiseMaxMultiplier',
        label: '动量涨幅倍率上界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumVelocityMinRatio',
        label: '成交速度倍率下界',
        suffix: '倍',
      },
      {
        field: 'profileMomentumVelocityMaxRatio',
        label: '成交速度倍率上界',
        suffix: '倍',
      },
    ],
  },
  {
    title: '正向贡献权重',
    description: '每条路径的七项权重必须各自精确合计为 100；硬门禁不参与加权。',
    fields: [
      { field: 'pullbackDepthWeight', label: '回撤·深度', suffix: '分' },
      { field: 'pullbackReboundWeight', label: '回撤·反弹', suffix: '分' },
      {
        field: 'pullbackStabilizationWeight',
        label: '回撤·稳定',
        suffix: '分',
      },
      {
        field: 'pullbackTurnSlopeWeight',
        label: '回撤·转折斜率',
        suffix: '分',
      },
      { field: 'pullbackVwapWeight', label: '回撤·VWAP', suffix: '分' },
      { field: 'pullbackLiquidityWeight', label: '回撤·流动性', suffix: '分' },
      { field: 'pullbackVolumeWeight', label: '回撤·量能', suffix: '分' },
      { field: 'momentumRiseWeight', label: '动量·涨幅', suffix: '分' },
      { field: 'momentumTurnoverWeight', label: '动量·成交速度', suffix: '分' },
      { field: 'momentumSlopeWeight', label: '动量·斜率', suffix: '分' },
      {
        field: 'momentumPersistenceWeight',
        label: '动量·持续性',
        suffix: '分',
      },
      { field: 'momentumVwapWeight', label: '动量·VWAP', suffix: '分' },
      { field: 'momentumLiquidityWeight', label: '动量·流动性', suffix: '分' },
      {
        field: 'momentumBookImbalanceWeight',
        label: '动量·盘口',
        suffix: '分',
      },
    ],
  },
  {
    title: '回撤评分归一化',
    description: '定义各回撤特征从原始观测值映射到贡献分的上下边界。',
    fields: [
      {
        field: 'pullbackDepthScoreMinPct',
        label: '深度·零分下界',
        suffix: '%',
      },
      {
        field: 'pullbackDepthScoreTargetMultiplier',
        label: '深度·目标倍率',
        suffix: '倍',
      },
      {
        field: 'pullbackReboundScoreMinPct',
        label: '反弹·零分下界',
        suffix: '%',
      },
      {
        field: 'pullbackReboundScoreMaxPct',
        label: '反弹·满分上界',
        suffix: '%',
      },
      {
        field: 'pullbackStabilizationScoreMinSeconds',
        label: '稳定·零分时长',
        suffix: '秒',
      },
      {
        field: 'pullbackStabilizationScoreMaxSeconds',
        label: '稳定·满分时长',
        suffix: '秒',
      },
      {
        field: 'pullbackTurnSlopeScoreMinPctPerSecond',
        label: '转折斜率·零分',
        suffix: '%/秒',
      },
      {
        field: 'pullbackTurnSlopeScoreMaxPctPerSecond',
        label: '转折斜率·满分',
        suffix: '%/秒',
      },
      {
        field: 'pullbackVwapFullScoreMaxPremiumPct',
        label: 'VWAP·满分溢价上界',
        suffix: '%',
      },
      {
        field: 'pullbackVwapZeroScorePremiumPct',
        label: 'VWAP·零分溢价',
        suffix: '%',
      },
      {
        field: 'pullbackLiquidityFullScoreSpreadTicks',
        label: '流动性·满分价差',
        suffix: 'tick',
      },
      {
        field: 'pullbackLiquidityZeroScoreSpreadTicks',
        label: '流动性·零分价差',
        suffix: 'tick',
      },
      {
        field: 'pullbackVolumeScoreMinRatio',
        label: '量能·零分倍率',
        suffix: '倍',
      },
      {
        field: 'pullbackVolumeScoreMaxRatio',
        label: '量能·满分倍率',
        suffix: '倍',
      },
    ],
  },
  {
    title: '动量评分归一化',
    description:
      '定义涨幅、成交速度、斜率、持续性、VWAP、流动性与盘口的计分边界。',
    fields: [
      { field: 'momentumRiseScoreMinPct', label: '涨幅·零分下界', suffix: '%' },
      {
        field: 'momentumRiseScoreTargetMultiplier',
        label: '涨幅·目标倍率',
        suffix: '倍',
      },
      {
        field: 'momentumTurnoverScoreMinRatio',
        label: '成交速度·零分倍率',
        suffix: '倍',
      },
      {
        field: 'momentumTurnoverScoreTargetMultiplier',
        label: '成交速度·目标倍率',
        suffix: '倍',
      },
      {
        field: 'momentumSlopeScoreMinPctPerSecond',
        label: '斜率·零分下界',
        suffix: '%/秒',
      },
      {
        field: 'momentumSlopeScoreTargetMultiplier',
        label: '斜率·目标倍率',
        suffix: '倍',
      },
      {
        field: 'momentumPersistenceScoreMinRatio',
        label: '持续性·零分比例',
        suffix: '0–1',
      },
      {
        field: 'momentumPersistenceScoreMaxRatio',
        label: '持续性·满分比例',
        suffix: '0–1',
      },
      {
        field: 'momentumVwapZeroScoreMinPremiumPct',
        label: 'VWAP·低位零分',
        suffix: '%',
      },
      {
        field: 'momentumVwapZeroScoreMaxPremiumPct',
        label: 'VWAP·高位零分',
        suffix: '%',
      },
      {
        field: 'momentumLiquidityFullScoreSpreadTicks',
        label: '流动性·满分价差',
        suffix: 'tick',
      },
      {
        field: 'momentumLiquidityZeroScoreSpreadTicks',
        label: '流动性·零分价差',
        suffix: 'tick',
      },
      {
        field: 'momentumBookImbalanceScoreMinRatio',
        label: '盘口·零分比例',
        suffix: '-1–1',
      },
      {
        field: 'momentumBookImbalanceScoreMaxRatio',
        label: '盘口·满分比例',
        suffix: '-1–1',
      },
    ],
  },
  {
    title: '显式诊断惩罚',
    description: '惩罚独立于正向权重展示；起点、满额边界和扣分上限均可审计。',
    fields: [
      {
        field: 'pullbackDataQualityPenaltyPoints',
        label: '回撤·数据降级扣分',
        suffix: '分',
      },
      {
        field: 'pullbackChasePenaltyStartPremiumPct',
        label: '回撤·追涨惩罚起点',
        suffix: '%',
      },
      {
        field: 'pullbackChasePenaltyFullPremiumPct',
        label: '回撤·追涨满额边界',
        suffix: '%',
      },
      {
        field: 'pullbackChasePenaltyPoints',
        label: '回撤·追涨最大扣分',
        suffix: '分',
      },
      {
        field: 'momentumDataQualityPenaltyPoints',
        label: '动量·数据降级扣分',
        suffix: '分',
      },
      {
        field: 'momentumOverextensionPenaltyStartPremiumPct',
        label: '动量·过度延伸起点',
        suffix: '%',
      },
      {
        field: 'momentumOverextensionPenaltyFullPremiumPct',
        label: '动量·过度延伸满额边界',
        suffix: '%',
      },
      {
        field: 'momentumOverextensionPenaltyPoints',
        label: '动量·过度延伸最大扣分',
        suffix: '分',
      },
    ],
  },
  {
    title: '阈值与候选生命周期',
    description:
      '四条分数阈值形成迟滞；确认、TTL 与再武装共同约束候选生命周期。',
    defaultOpen: true,
    fields: [
      { field: 'previewScore', label: '重点观察阈值', suffix: '分' },
      { field: 'candidateScore', label: '候选锁存阈值', suffix: '分' },
      { field: 'revalidateScore', label: '确认重验阈值', suffix: '分' },
      { field: 'rearmScore', label: '再武装阈值', suffix: '分' },
      { field: 'candidateConfirmSeconds', label: '候选确认停留', suffix: '秒' },
      {
        field: 'candidateConfirmTicks',
        label: '最少 source identity',
        suffix: '次',
      },
      { field: 'candidateTtlSeconds', label: '候选有效期', suffix: '秒' },
      { field: 'rearmSeconds', label: '再武装持续', suffix: '秒' },
    ],
  },
];

type PolicyViewId =
  | 'overview'
  | 'data-health'
  | 'sessions'
  | 'pullback-path'
  | 'momentum-path'
  | 'profile-clamp'
  | 'weights'
  | 'pullback-normalization'
  | 'momentum-normalization'
  | 'penalties'
  | 'lifecycle';

type GroupPresentation = {
  id: Exclude<PolicyViewId, 'overview'>;
  navLabel: string;
  icon: React.ComponentType<{ className?: string }>;
  constraints: readonly string[];
};

const groupPresentationByTitle: Record<string, GroupPresentation> = {
  数据健康与状态窗口: {
    id: 'data-health',
    navLabel: '数据健康',
    icon: Database,
    constraints: [
      '窗口样本上限必须覆盖两条路径的最少样本数',
      'READY 覆盖必须容纳各路径的稳定与基线要求',
      '必需行情字段必须非空、无重复且受支持',
    ],
  },
  交易时段与收盘保护: {
    id: 'sessions',
    navLabel: '交易时段',
    icon: Clock3,
    constraints: [
      '上午、下午连续竞价窗口必须有序且不能重叠',
      '启用窗口必须容纳确认、TTL、再武装与收盘保护',
    ],
  },
  回撤反弹路径: {
    id: 'pullback-path',
    navLabel: '回撤反弹路径',
    icon: GitBranch,
    constraints: [
      '低点稳定时长必须小于回撤回看窗口',
      '量能短窗不能长于量能基线窗口',
      '形成阶段倍率必须在 (0, 1] 区间',
    ],
  },
  早期动量路径: {
    id: 'momentum-path',
    navLabel: '早期动量路径',
    icon: Zap,
    constraints: [
      '动量短窗不能长于成交基线窗口',
      '最短持续时间不能长于动量短窗',
      'VWAP 甜蜜区下界必须低于追涨上限',
    ],
  },
  'D-1 画像安全夹取': {
    id: 'profile-clamp',
    navLabel: 'D-1 画像夹取',
    icon: SlidersHorizontal,
    constraints: [
      '每个画像倍率下界必须严格小于上界',
      '画像只夹取实时阈值，不改变原始观测事实',
    ],
  },
  正向贡献权重: {
    id: 'weights',
    navLabel: '正向贡献权重',
    icon: Scale,
    constraints: [
      '单项权重不得小于 0',
      '两条路径的七项权重必须分别合计 100',
      '硬门禁不参与加权',
    ],
  },
  回撤评分归一化: {
    id: 'pullback-normalization',
    navLabel: '回撤评分归一化',
    icon: BarChart3,
    constraints: [
      '各特征零分与满分边界必须保持正确顺序',
      '回撤深度下界必须低于画像夹取后的目标',
    ],
  },
  动量评分归一化: {
    id: 'momentum-normalization',
    navLabel: '动量评分归一化',
    icon: Activity,
    constraints: [
      '甜蜜区必须位于 VWAP 两个零分边界之间',
      '涨幅、成交速度与斜率下界必须低于解析目标',
    ],
  },
  显式诊断惩罚: {
    id: 'penalties',
    navLabel: '诊断惩罚',
    icon: ShieldAlert,
    constraints: [
      '惩罚分不得小于 0',
      '惩罚起点必须低于满额边界',
      '动量过度延伸起点必须等于 VWAP 追涨上限',
    ],
  },
  阈值与候选生命周期: {
    id: 'lifecycle',
    navLabel: '候选生命周期',
    icon: TimerReset,
    constraints: [
      '0 ≤ 再武装 < 重点观察 < 确认重验 < 候选锁存 ≤ 100',
      '确认停留、TTL 与再武装持续时间必须大于 0',
    ],
  },
};

function groupPresentation(group: PolicyEditorGroup) {
  return groupPresentationByTitle[group.title];
}

function groupForField(field: string | null | undefined) {
  if (!field) return undefined;
  return signalPolicyEditorGroups.find(group =>
    group.fields.some(definition => definition.field === field)
  );
}

function isStringPolicyEditorField(
  field: PolicyEditorField
): field is StringPolicyEditorField {
  return (
    field.kind !== 'toggle' &&
    field.kind !== 'required-fields' &&
    field.kind !== 'sessions'
  );
}

function PolicyEditorControl({
  field,
  form,
  onChange,
}: {
  field: PolicyEditorField;
  form: SignalPolicyForm;
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
}) {
  if (field.kind === 'toggle') {
    return (
      <label
        className="col-span-2 flex min-h-10 cursor-pointer items-center justify-between rounded-control border border-white/[0.07] bg-[#07111f]/60 px-3 text-ui-caption font-semibold text-slate-300 transition-colors hover:border-primary/25 lg:col-span-1"
        data-policy-field={field.field}
      >
        {field.label}
        <input
          type="checkbox"
          checked={form[field.field]}
          onChange={event => onChange(field.field, event.target.checked)}
          className="h-4 w-4 accent-blue-500 focus-visible:ring-2 focus-visible:ring-primary/70"
        />
      </label>
    );
  }

  if (field.kind === 'required-fields' || field.kind === 'sessions') {
    const options =
      field.kind === 'required-fields'
        ? supportedRequiredFields
        : supportedSessionCodes;
    const selected = form[field.field];
    return (
      <fieldset
        className="col-span-2 rounded-control border border-white/[0.07] bg-[#07111f]/60 p-3 lg:col-span-2"
        data-policy-field={field.field}
      >
        <legend className="px-1 text-ui-caption font-bold text-slate-400">
          {field.label}
        </legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {options.map(option => (
            <label
              key={option}
              className="flex cursor-pointer items-center gap-2 font-mono text-ui-caption text-slate-300"
            >
              <input
                type="checkbox"
                checked={selected.includes(option)}
                onChange={event =>
                  onChange(
                    field.field,
                    event.target.checked
                      ? [...selected, option]
                      : selected.filter(value => value !== option)
                  )
                }
                className="h-3.5 w-3.5 accent-blue-500 focus-visible:ring-2 focus-visible:ring-primary/70"
              />
              {option}
            </label>
          ))}
        </div>
      </fieldset>
    );
  }

  return (
    <PolicyField
      field={field.field}
      label={field.label}
      suffix={field.suffix}
      value={form[field.field]}
      inputMode={field.kind === 'time' ? 'text' : 'decimal'}
      onChange={onChange}
    />
  );
}

function OverviewMetric({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="rounded-control border border-white/[0.06] bg-white/[0.02] p-2.5">
      <div className="text-ui-caption text-slate-500">{label}</div>
      <div className="mt-1 font-mono text-ui-title font-semibold tabular-nums text-slate-100">
        {value}
      </div>
    </div>
  );
}

function FsmOverviewCard({
  enabled = true,
  metrics,
  nodes,
  onEdit,
  title,
  tone,
}: {
  enabled?: boolean;
  metrics: readonly { label: string; value: React.ReactNode }[];
  nodes: readonly string[];
  onEdit: () => void;
  title: string;
  tone: 'momentum' | 'pullback';
}) {
  const Icon = tone === 'pullback' ? GitBranch : Zap;
  return (
    <article className="rounded-panel border border-white/[0.07] bg-[#0b1728] p-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-ui-title font-semibold text-slate-100">
          <span className="flex h-control-compact w-control-compact items-center justify-center rounded-full bg-primary/10 text-primary">
            <Icon className="h-4 w-4" aria-hidden="true" />
          </span>
          {title}
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'inline-flex items-center gap-1 text-ui-caption font-semibold',
              enabled ? 'text-emerald-300' : 'text-slate-500'
            )}
          >
            {enabled ? (
              <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            {enabled ? '已启用' : '未启用'}
          </span>
          <button
            type="button"
            aria-label={`编辑${title}`}
            className="flex h-control-compact cursor-pointer items-center gap-1 rounded-sm px-2 text-ui-caption font-semibold text-slate-400 outline-none transition-colors hover:bg-primary/[0.07] hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-primary/70"
            onClick={onEdit}
          >
            编辑
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>

      <div className="mt-3 flex items-start justify-between gap-1 overflow-x-auto pb-1">
        {nodes.map((node, index) => (
          <React.Fragment key={node}>
            <div className="flex min-w-20 flex-1 flex-col items-center gap-1.5 text-center">
              <span className="flex h-9 w-9 items-center justify-center rounded-full border border-primary/35 bg-primary/[0.06] font-mono text-ui-caption font-semibold text-blue-200">
                {index + 1}
              </span>
              <span className="text-ui-caption font-semibold text-slate-300">
                {node}
              </span>
            </div>
            {index < nodes.length - 1 && (
              <ArrowRight
                className="mt-2.5 h-4 w-4 shrink-0 text-primary/70"
                aria-hidden="true"
              />
            )}
          </React.Fragment>
        ))}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 xl:grid-cols-4">
        {metrics.map(metric => (
          <OverviewMetric
            key={metric.label}
            label={metric.label}
            value={metric.value}
          />
        ))}
      </div>
    </article>
  );
}

function PolicyOverview({
  form,
  onSelect,
}: {
  form: SignalPolicyForm;
  onSelect: (view: PolicyViewId) => void;
}) {
  const pullbackWeight = [
    form.pullbackDepthWeight,
    form.pullbackReboundWeight,
    form.pullbackStabilizationWeight,
    form.pullbackTurnSlopeWeight,
    form.pullbackVwapWeight,
    form.pullbackLiquidityWeight,
    form.pullbackVolumeWeight,
  ].reduce((total, value) => total + (Number(value) || 0), 0);
  const momentumWeight = [
    form.momentumRiseWeight,
    form.momentumTurnoverWeight,
    form.momentumSlopeWeight,
    form.momentumPersistenceWeight,
    form.momentumVwapWeight,
    form.momentumLiquidityWeight,
    form.momentumBookImbalanceWeight,
  ].reduce((total, value) => total + (Number(value) || 0), 0);

  const lifecycle = [
    { label: '再武装', value: form.rearmScore },
    { label: '重点观察', value: form.previewScore },
    { label: '确认重验', value: form.revalidateScore },
    { label: '候选锁存', value: form.candidateScore },
  ];

  return (
    <div className="p-3">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.06] pb-3">
        <div>
          <div className="flex items-center gap-2 text-ui-title font-semibold text-slate-100">
            <Sparkles className="h-4 w-4 text-primary" aria-hidden="true" />
            双路径策略蓝图
          </div>
          <p className="mt-1 text-ui-caption leading-4 text-slate-500">
            先理解信号如何形成，再进入单个模块调整参数。
          </p>
        </div>
        <span className="rounded-sm border border-emerald-400/15 bg-emerald-400/[0.06] px-2 py-1 text-ui-caption font-semibold text-emerald-300">
          双 FSM 独立评分 · 共同候选生命周期
        </span>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-2">
        <FsmOverviewCard
          title="回撤反弹 FSM"
          tone="pullback"
          nodes={['回撤形成', '低点稳定', '反弹确认', '候选锁存']}
          onEdit={() => onSelect('pullback-path')}
          metrics={[
            { label: '回看窗口', value: `${form.pullbackLookbackSeconds} 秒` },
            { label: '最低回撤', value: `${form.pullbackThresholdPct}%` },
            {
              label: '最低反弹',
              value: `${form.pullbackReboundThresholdPct}%`,
            },
            { label: '权重合计', value: pullbackWeight },
          ]}
        />
        <FsmOverviewCard
          enabled={form.momentumEnabled}
          title="早期动量 FSM"
          tone="momentum"
          nodes={['短窗加速', '成交确认', 'VWAP 甜蜜区', '候选锁存']}
          onEdit={() => onSelect('momentum-path')}
          metrics={[
            { label: '动量短窗', value: `${form.momentumWindowSeconds} 秒` },
            { label: '最低涨幅', value: `${form.momentumMinRisePct}%` },
            {
              label: '成交速度',
              value: `${form.momentumMinAmountVelocityRatio}×`,
            },
            { label: '权重合计', value: momentumWeight },
          ]}
        />
      </div>

      <div className="mt-3 rounded-panel border border-white/[0.07] bg-[#081422] p-3">
        <div className="flex items-center gap-2 text-ui-label font-semibold text-slate-200">
          <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" />
          共同硬门禁
        </div>
        <div className="mt-3 grid divide-y divide-white/[0.06] rounded-control border border-white/[0.06] bg-white/[0.02] lg:grid-cols-3 lg:divide-x lg:divide-y-0">
          <button
            type="button"
            className="flex min-h-12 cursor-pointer items-center justify-center gap-2 px-3 text-ui-caption text-slate-300 outline-none transition-colors hover:bg-primary/[0.05] focus-visible:ring-2 focus-visible:ring-primary/70"
            onClick={() => onSelect('data-health')}
          >
            <Gauge className="h-4 w-4 text-primary" aria-hidden="true" />
            报价新鲜 ≤ {form.maxQuoteAgeMs} ms
          </button>
          <button
            type="button"
            className="flex min-h-12 cursor-pointer items-center justify-center gap-2 px-3 text-ui-caption text-slate-300 outline-none transition-colors hover:bg-primary/[0.05] focus-visible:ring-2 focus-visible:ring-primary/70"
            onClick={() => onSelect('sessions')}
          >
            <Clock3 className="h-4 w-4 text-primary" aria-hidden="true" />
            连续竞价 AM / PM
          </button>
          <button
            type="button"
            className="flex min-h-12 cursor-pointer items-center justify-center gap-2 px-3 text-ui-caption text-slate-300 outline-none transition-colors hover:bg-primary/[0.05] focus-visible:ring-2 focus-visible:ring-primary/70"
            onClick={() => onSelect('data-health')}
          >
            <Database className="h-4 w-4 text-primary" aria-hidden="true" />
            价差与必需字段门禁
          </button>
        </div>
      </div>

      <div className="mt-3 rounded-panel border border-white/[0.07] bg-[#081422] p-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-ui-label font-semibold text-slate-200">
            <TimerReset className="h-4 w-4 text-primary" aria-hidden="true" />
            候选生命周期
          </div>
          <button
            type="button"
            className="flex h-control-compact cursor-pointer items-center gap-1 rounded-sm px-2 text-ui-caption font-semibold text-slate-400 outline-none transition-colors hover:bg-primary/[0.07] hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-primary/70"
            onClick={() => onSelect('lifecycle')}
          >
            编辑生命周期
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
        <div className="mt-3 flex items-center gap-2 overflow-x-auto pb-1">
          {lifecycle.map((stage, index) => (
            <React.Fragment key={stage.label}>
              <div className="min-w-32 flex-1 rounded-control border border-primary/20 bg-primary/[0.035] px-3 py-2.5 text-center">
                <div className="text-ui-caption font-semibold text-slate-300">
                  {stage.label}
                </div>
                <div className="mt-1 font-mono text-ui-heading font-semibold tabular-nums text-blue-200">
                  {stage.value}
                  <span className="ml-1 text-ui-caption text-slate-500">
                    分
                  </span>
                </div>
              </div>
              {index < lifecycle.length - 1 && (
                <ArrowRight
                  className="h-4 w-4 shrink-0 text-primary/70"
                  aria-hidden="true"
                />
              )}
            </React.Fragment>
          ))}
        </div>
        <div className="mt-2 grid grid-cols-3 divide-x divide-white/[0.06] rounded-control border border-white/[0.06] bg-white/[0.02] text-center text-ui-caption text-slate-400">
          <div className="p-2.5">
            确认{' '}
            <span className="font-mono text-slate-200">
              {form.candidateConfirmSeconds} 秒 / {form.candidateConfirmTicks}{' '}
              次
            </span>
          </div>
          <div className="p-2.5">
            TTL{' '}
            <span className="font-mono text-slate-200">
              {form.candidateTtlSeconds} 秒
            </span>
          </div>
          <div className="p-2.5">
            再武装{' '}
            <span className="font-mono text-slate-200">
              {form.rearmSeconds} 秒
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function ConstraintStrip({ constraints }: { constraints: readonly string[] }) {
  return (
    <div className="mt-3 rounded-control border border-white/[0.07] bg-white/[0.02] p-3">
      <div className="flex items-center gap-2 text-ui-label font-semibold text-slate-200">
        <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" />
        关联约束
      </div>
      <div className="mt-2 grid gap-2 lg:grid-cols-3">
        {constraints.map(constraint => (
          <div
            key={constraint}
            className="flex items-start gap-2 text-ui-caption leading-4 text-slate-400"
          >
            <CheckCircle2
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-300"
              aria-hidden="true"
            />
            {constraint}
          </div>
        ))}
      </div>
    </div>
  );
}

function WeightColumn({
  fields,
  form,
  icon: Icon,
  onChange,
  title,
}: {
  fields: readonly StringPolicyEditorField[];
  form: SignalPolicyForm;
  icon: React.ComponentType<{ className?: string }>;
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
  title: string;
}) {
  const total = fields.reduce(
    (sum, definition) => sum + (Number(form[definition.field]) || 0),
    0
  );
  const valid = Math.abs(total - 100) < 1e-8;

  return (
    <section className="rounded-panel border border-white/[0.07] bg-[#081422] p-3">
      <div className="flex items-center gap-2 text-ui-title font-semibold text-slate-100">
        <span className="flex h-control-compact w-control-compact items-center justify-center rounded-full bg-primary/10 text-primary">
          <Icon className="h-4 w-4" aria-hidden="true" />
        </span>
        {title}
      </div>
      <div className="mt-3 space-y-2">
        {fields.map(definition => {
          const numericValue = Math.max(
            0,
            Math.min(100, Number(form[definition.field]) || 0)
          );
          const label = definition.label.replace(/^回撤·|^动量·/, '');
          const id = `t-trade-policy-${definition.field}`;
          return (
            <div
              key={definition.field}
              className="grid grid-cols-[minmax(5.5rem,0.8fr)_minmax(4rem,1fr)_5.5rem_auto] items-center gap-2"
              data-policy-field={definition.field}
            >
              <Label
                htmlFor={id}
                className="truncate text-ui-caption font-semibold text-slate-300"
              >
                {label}
              </Label>
              <div
                aria-hidden="true"
                className="h-1.5 overflow-hidden rounded-sm bg-slate-700/60"
              >
                <div
                  className="h-full rounded-sm bg-primary transition-[width] duration-150 motion-reduce:transition-none"
                  style={{ width: `${numericValue}%` }}
                />
              </div>
              <Input
                id={id}
                inputMode="decimal"
                value={form[definition.field]}
                onChange={event =>
                  onChange(definition.field, event.target.value)
                }
                className="h-control-default rounded-control border-white/10 bg-[#07111f] px-2 font-mono text-ui-label tabular-nums focus-visible:ring-primary/70"
                aria-describedby={`${id}-unit`}
              />
              <span
                id={`${id}-unit`}
                className="text-ui-caption font-semibold text-slate-500"
              >
                分
              </span>
            </div>
          );
        })}
      </div>
      <div className="mt-3 flex items-center justify-between border-t border-white/[0.06] pt-3">
        <span className="font-mono text-ui-label tabular-nums text-slate-300">
          合计 <span className="font-semibold text-slate-50">{total}</span> /
          100
        </span>
        <span
          className={cn(
            'inline-flex items-center gap-1 text-ui-caption font-semibold',
            valid ? 'text-emerald-300' : 'text-rose-300'
          )}
        >
          {valid ? (
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          {valid ? '权重有效' : '需要调整'}
        </span>
      </div>
    </section>
  );
}

function PolicyGroupEditor({
  editedCount,
  form,
  group,
  onChange,
  onOverview,
}: {
  editedCount: number;
  form: SignalPolicyForm;
  group: PolicyEditorGroup;
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
  onOverview: () => void;
}) {
  const presentation = groupPresentation(group);
  const Icon = presentation.icon;
  const isWeightGroup = presentation.id === 'weights';
  const weightFields = group.fields.filter(isStringPolicyEditorField);

  return (
    <div className="p-3">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/[0.06] pb-3">
        <div>
          <button
            type="button"
            className="mb-1 flex cursor-pointer items-center gap-1 text-ui-caption text-slate-500 outline-none transition-colors hover:text-blue-200 focus-visible:ring-2 focus-visible:ring-primary/70"
            onClick={onOverview}
          >
            策略概览
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
            <span className="text-slate-300">{presentation.navLabel}</span>
          </button>
          <h3 className="flex items-center gap-2 text-ui-heading font-semibold text-slate-100">
            <Icon className="h-4 w-4 text-primary" aria-hidden="true" />
            {group.title}
          </h3>
          <p className="mt-1 max-w-3xl text-ui-caption leading-4 text-slate-500">
            {group.description}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded-sm border border-white/[0.07] bg-white/[0.025] px-2 py-1 font-mono text-ui-caption text-slate-400">
            {group.fields.length} 个参数
          </span>
          {editedCount > 0 && (
            <span className="rounded-sm border border-primary/20 bg-primary/[0.08] px-2 py-1 text-ui-caption font-semibold text-blue-200">
              已修改 {editedCount} 项
            </span>
          )}
        </div>
      </div>

      {isWeightGroup ? (
        <div className="mt-3 grid gap-3 xl:grid-cols-2">
          <WeightColumn
            title="回撤反弹 FSM"
            icon={GitBranch}
            fields={weightFields.slice(0, 7)}
            form={form}
            onChange={onChange}
          />
          <WeightColumn
            title="早期动量 FSM"
            icon={Zap}
            fields={weightFields.slice(7)}
            form={form}
            onChange={onChange}
          />
        </div>
      ) : (
        <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-3 2xl:grid-cols-4">
          {group.fields.map(field => (
            <PolicyEditorControl
              key={field.field}
              field={field}
              form={form}
              onChange={onChange}
            />
          ))}
        </div>
      )}

      <ConstraintStrip constraints={presentation.constraints} />
    </div>
  );
}

function PolicyNavigation({
  activeView,
  editedFields,
  issueFields,
  onSelect,
}: {
  activeView: PolicyViewId;
  editedFields: ReadonlySet<keyof SignalPolicyForm>;
  issueFields: ReadonlySet<string>;
  onSelect: (view: PolicyViewId) => void;
}) {
  return (
    <aside className="rounded-panel border border-white/[0.07] bg-[#081422] p-2">
      <nav
        aria-label="策略参数模块"
        className="grid grid-cols-2 gap-1 lg:grid-cols-1"
      >
        <button
          type="button"
          aria-current={activeView === 'overview' ? 'page' : undefined}
          className={cn(
            'flex min-h-10 cursor-pointer items-center gap-2 rounded-control border px-2.5 text-left text-ui-caption font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary/70',
            activeView === 'overview'
              ? 'border-primary/45 bg-primary/[0.09] text-blue-100'
              : 'border-transparent text-slate-400 hover:border-white/[0.06] hover:bg-white/[0.025] hover:text-slate-200'
          )}
          onClick={() => onSelect('overview')}
        >
          <Sparkles className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="min-w-0 flex-1">策略概览</span>
        </button>

        {signalPolicyEditorGroups.map(group => {
          const presentation = groupPresentation(group);
          const Icon = presentation.icon;
          const editedCount = group.fields.filter(field =>
            editedFields.has(field.field)
          ).length;
          const issueCount = group.fields.filter(field =>
            issueFields.has(field.field)
          ).length;
          const active = activeView === presentation.id;
          return (
            <button
              key={group.title}
              type="button"
              aria-current={active ? 'page' : undefined}
              aria-label={`${presentation.navLabel}，${group.fields.length} 个参数${editedCount > 0 ? `，已修改 ${editedCount} 项` : ''}${issueCount > 0 ? `，${issueCount} 项需要处理` : ''}`}
              className={cn(
                'flex min-h-10 cursor-pointer items-center gap-2 rounded-control border px-2.5 text-left text-ui-caption font-semibold outline-none transition-colors focus-visible:ring-2 focus-visible:ring-primary/70',
                active
                  ? 'border-primary/45 bg-primary/[0.09] text-blue-100'
                  : 'border-transparent text-slate-400 hover:border-white/[0.06] hover:bg-white/[0.025] hover:text-slate-200'
              )}
              onClick={() => onSelect(presentation.id)}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="min-w-0 flex-1 truncate">
                {presentation.navLabel}
              </span>
              {issueCount > 0 ? (
                <span className="inline-flex min-w-5 items-center justify-center rounded-sm border border-rose-400/20 bg-rose-400/[0.08] px-1 font-mono text-ui-caption text-rose-200">
                  {issueCount}
                </span>
              ) : (
                <span className="inline-flex min-w-5 items-center justify-center rounded-sm border border-white/[0.06] bg-white/[0.03] px-1 font-mono text-ui-caption text-slate-500">
                  {group.fields.length}
                </span>
              )}
              {editedCount > 0 && (
                <span
                  className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary"
                  aria-hidden="true"
                />
              )}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

function policyDisplayValue(value: SignalPolicyFormValue | number | undefined) {
  if (Array.isArray(value)) return value.join(', ');
  return String(value ?? '—');
}

export function TTradeSignalPolicyEditor({
  conflictVersion,
  conflictPolicy,
  form,
  localErrors,
  onChange,
  onPreview,
  preview,
  previewLoading,
  serverConfigVersion,
  showPreviewAction = true,
}: {
  conflictVersion?: number | null;
  conflictPolicy?: SignalPolicyLike | null;
  form: SignalPolicyForm;
  localErrors: readonly string[];
  onChange: (
    field: keyof SignalPolicyForm,
    value: SignalPolicyFormValue
  ) => void;
  onPreview: () => void;
  preview?: SignalPolicyPreviewLike | null;
  previewLoading: boolean;
  serverConfigVersion: number;
  showPreviewAction?: boolean;
}) {
  const [activeView, setActiveView] = React.useState<PolicyViewId>('overview');
  const [editedFields, setEditedFields] = React.useState<
    Set<keyof SignalPolicyForm>
  >(() => new Set());

  React.useEffect(() => {
    setEditedFields(new Set());
  }, [serverConfigVersion]);

  const handleChange = React.useCallback(
    (field: keyof SignalPolicyForm, value: SignalPolicyFormValue) => {
      setEditedFields(current => {
        const next = new Set(current);
        next.add(field);
        return next;
      });
      onChange(field, value);
    },
    [onChange]
  );

  const conflictRows = conflictPolicy
    ? signalPolicyEditorGroups
        .flatMap(group => group.fields)
        .map(field => ({
          field: field.field,
          label: field.label,
          serverValue: policyDisplayValue(conflictPolicy[field.field]),
          draftValue: policyDisplayValue(form[field.field]),
        }))
        .filter(row => row.serverValue !== row.draftValue)
    : [];

  const activeGroup = signalPolicyEditorGroups.find(
    group => groupPresentation(group).id === activeView
  );
  const issueFields = new Set(
    preview?.errors
      .map(issue => issue.field)
      .filter((field): field is string => Boolean(field)) ?? []
  );
  const activeEditedCount = activeGroup
    ? activeGroup.fields.filter(field => editedFields.has(field.field)).length
    : 0;
  const policyFieldCount = signalPolicyEditorGroups.reduce(
    (total, group) => total + group.fields.length,
    0
  );

  const openIssueModule = (field: string | null | undefined) => {
    const group = groupForField(field);
    if (group) setActiveView(groupPresentation(group).id);
  };

  return (
    <div className="space-y-3">
      {conflictVersion != null && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-panel border border-rose-400/25 bg-rose-400/[0.08] p-3 text-ui-caption leading-4 text-rose-100"
        >
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <div className="font-semibold">配置版本冲突，草稿已保留</div>
            <div className="mt-1 text-rose-200/75">
              你的草稿基于旧版本；服务端已更新到 v{conflictVersion}
              。请比较并重新验证，系统不会自动覆盖或合并交易参数。
            </div>
            {conflictPolicy && conflictRows.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[520px] text-left text-ui-caption">
                  <caption className="sr-only">
                    服务端最新配置与当前本地草稿对比
                  </caption>
                  <thead className="text-rose-200/60">
                    <tr>
                      <th className="pb-1">参数</th>
                      <th>服务端 v{conflictVersion}</th>
                      <th>当前草稿</th>
                    </tr>
                  </thead>
                  <tbody>
                    {conflictRows.map(row => (
                      <tr
                        key={row.field}
                        className="border-t border-rose-200/10"
                      >
                        <td className="py-1.5">{row.label}</td>
                        <td className="font-mono">{row.serverValue}</td>
                        <td className="font-mono">{row.draftValue}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {conflictPolicy && conflictRows.length === 0 && (
              <div className="mt-2 text-ui-caption text-rose-200/65">
                100 个策略字段与服务端一致；冲突来自其他全局配置字段。
              </div>
            )}
          </div>
        </div>
      )}

      <div
        className="flex flex-wrap items-center gap-2 rounded-panel border border-white/[0.07] bg-[#081422] p-2.5"
        aria-label="策略规则摘要"
      >
        <span className="rounded-sm border border-white/[0.07] bg-white/[0.025] px-2 py-1 font-mono text-ui-caption text-slate-300">
          {policyFieldCount} 个参数
        </span>
        <span className="rounded-sm border border-white/[0.07] bg-white/[0.025] px-2 py-1 font-mono text-ui-caption text-slate-300">
          {signalPolicyEditorGroups.length} 个模块
        </span>
        {localErrors.length === 0 ? (
          <span className="inline-flex items-center gap-1 rounded-sm border border-emerald-400/15 bg-emerald-400/[0.06] px-2 py-1 text-ui-caption font-semibold text-emerald-300">
            <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
            本地校验通过
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-sm border border-rose-400/20 bg-rose-400/[0.07] px-2 py-1 text-ui-caption font-semibold text-rose-200">
            <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
            {localErrors.length} 项阻断
          </span>
        )}
        {editedFields.size > 0 && (
          <span className="rounded-sm border border-primary/20 bg-primary/[0.08] px-2 py-1 text-ui-caption font-semibold text-blue-200">
            已修改 {editedFields.size} 项
          </span>
        )}
        <span className="ml-auto font-mono text-ui-caption text-slate-500">
          配置版本 v{serverConfigVersion}
        </span>
      </div>

      <div className="grid min-w-0 gap-3 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <PolicyNavigation
          activeView={activeView}
          editedFields={editedFields}
          issueFields={issueFields}
          onSelect={setActiveView}
        />
        <section
          aria-label="策略模块内容"
          className="min-w-0 rounded-panel border border-white/[0.07] bg-[#07111f]/55"
        >
          {activeGroup ? (
            <PolicyGroupEditor
              editedCount={activeEditedCount}
              form={form}
              group={activeGroup}
              onChange={handleChange}
              onOverview={() => setActiveView('overview')}
            />
          ) : (
            <PolicyOverview form={form} onSelect={setActiveView} />
          )}
        </section>
      </div>

      <section
        className="sticky bottom-0 z-10 rounded-panel border border-primary/20 bg-[#0b1728] p-3"
        aria-labelledby="t-trade-policy-preview-heading"
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <span
              className={cn(
                'flex h-control-default w-control-default shrink-0 items-center justify-center rounded-full',
                localErrors.length > 0
                  ? 'bg-rose-400/10 text-rose-300'
                  : 'bg-emerald-400/10 text-emerald-300'
              )}
            >
              {localErrors.length > 0 ? (
                <AlertTriangle className="h-4 w-4" aria-hidden="true" />
              ) : (
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              )}
            </span>
            <div className="min-w-0">
              <h3
                id="t-trade-policy-preview-heading"
                className={cn(
                  'text-ui-label font-semibold',
                  localErrors.length > 0 ? 'text-rose-100' : 'text-slate-100'
                )}
              >
                {localErrors.length > 0 ? '本地校验未通过' : '本地校验通过'}
              </h3>
              <p className="mt-0.5 text-ui-caption text-slate-500">
                {showPreviewAction ? `配置版本 v${serverConfigVersion} · ` : ''}
                {showPreviewAction
                  ? preview
                    ? '已生成服务端纯校验预览'
                    : editedFields.size > 0
                      ? '服务端预览待更新'
                      : '尚无未验证修改'
                  : '启动回放时服务端会再次校验'}
              </p>
            </div>
          </div>
          {showPreviewAction && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-control-compact rounded-control border-primary/35 text-ui-caption text-blue-100 hover:bg-primary/[0.08]"
              disabled={previewLoading || localErrors.length > 0}
              onClick={onPreview}
            >
              {previewLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" />
              )}
              验证配置
            </Button>
          )}
        </div>

        {localErrors.length > 0 && (
          <ul
            role="alert"
            className="mt-3 space-y-1 rounded-control border border-rose-400/15 bg-rose-400/[0.04] p-2.5 text-ui-caption text-rose-200"
          >
            {localErrors.map(message => (
              <li key={message}>• {message}</li>
            ))}
          </ul>
        )}

        {preview && (
          <div
            aria-atomic="true"
            aria-live="polite"
            className="mt-3 border-t border-white/[0.06] pt-3"
            role="status"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-sm border px-2 py-1 text-ui-caption font-semibold',
                  preview.valid
                    ? 'border-emerald-400/20 bg-emerald-400/[0.07] text-emerald-200'
                    : 'border-rose-400/20 bg-rose-400/[0.07] text-rose-200'
                )}
              >
                {preview.valid ? (
                  <CheckCircle2 className="h-3.5 w-3.5" />
                ) : (
                  <AlertTriangle className="h-3.5 w-3.5" />
                )}
                {preview.valid ? '服务端校验通过' : '存在阻断错误'}
              </span>
              {preview.requiresRewarm && (
                <span className="inline-flex items-center gap-1.5 rounded-sm border border-amber-400/20 bg-amber-400/[0.07] px-2 py-1 text-ui-caption font-semibold text-amber-200">
                  <AlertTriangle className="h-3.5 w-3.5" />
                  保存后需要重热
                </span>
              )}
              {preview.normalizedPolicy && (
                <span className="font-mono text-ui-caption text-slate-500">
                  {preview.normalizedPolicy.policyVersion} · feature{' '}
                  {preview.normalizedPolicy.featureSchemaVersion}
                </span>
              )}
            </div>
            {preview.requiresRewarm && (
              <p className="mt-2 text-ui-caption leading-4 text-amber-100">
                旧待确认信号会失效并重新进入
                WARMING；已真实成交批次、BucketLedger 与退出计划不受影响。
              </p>
            )}
            {preview.changedFields.length > 0 && (
              <p className="mt-2 text-ui-caption text-slate-500">
                规范化变更：{preview.changedFields.join('、')}
              </p>
            )}
            {preview.errors.length > 0 && (
              <ul className="mt-2 space-y-1 text-ui-caption text-rose-200">
                {preview.errors.map(issue => {
                  const issueGroup = groupForField(issue.field);
                  return (
                    <li key={`${issue.code}:${issue.field || ''}`}>
                      {issueGroup ? (
                        <button
                          type="button"
                          className="cursor-pointer rounded-sm text-left outline-none transition-colors hover:text-rose-100 hover:underline focus-visible:ring-2 focus-visible:ring-primary/70"
                          onClick={() => openIssueModule(issue.field)}
                        >
                          {issue.field ? `${issue.field}：` : ''}
                          {issue.message} · 前往
                          {groupPresentation(issueGroup).navLabel}
                        </button>
                      ) : (
                        <>
                          {issue.field ? `${issue.field}：` : ''}
                          {issue.message}
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            {preview.warnings.length > 0 && (
              <ul className="mt-2 space-y-1 text-ui-caption text-amber-200">
                {preview.warnings.map(issue => (
                  <li key={`${issue.code}:${issue.field || ''}`}>
                    {issue.field ? `${issue.field}：` : ''}
                    {issue.message}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
