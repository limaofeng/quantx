import {
  BookOpen,
  ChevronDown,
  ExternalLink,
  Info,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { cn } from '@/utils/cn';

import {
  INDICATOR_OPERATOR_LABELS,
  INDICATOR_TEMPLATES,
  describeIndicatorCondition,
  validateIndicatorConditions,
} from '../indicatorModel';
import type {
  IndicatorCondition,
  IndicatorDefinition,
  IndicatorOperator,
  ScreeningCriteria,
  ScreeningMode,
  StockScreenUniverse,
  StockScreeningMeta,
} from '../types';

interface ScreeningTopBarProps {
  screeningCriteria: ScreeningCriteria;
  setScreeningCriteria: (
    criteria:
      ScreeningCriteria | ((prev: ScreeningCriteria) => ScreeningCriteria)
  ) => void;
  availableIndustries: string[];
  meta: StockScreeningMeta;
  onRunScreening: () => void;
  screeningLoading: boolean;
  onReset: () => void;
  onBackfillSnapshot: () => void;
  onOpenAdvancedData: () => void;
  onOpenSnapshotRun?: () => void;
  snapshotBackfillLoading: boolean;
  snapshotRunState?: string | null;
  hasPendingChanges?: boolean;
  indicators: IndicatorDefinition[];
  catalogLoading?: boolean;
  catalogError?: string;
  onRetryCatalog: () => void;
  probabilityModels?: Array<{
    modelVersion: string;
    stage: 'ACTIVE' | 'SHADOW';
  }>;
  probabilityModelsLoading?: boolean;
  probabilityModelsError?: string;
  onRetryProbabilityModels?: () => void;
  onOpenIndicatorReport: (indicatorId: string) => void;
  onOpenJointReport: () => void;
}

function Section({
  children,
  label,
  open = true,
}: {
  children: ReactNode;
  label: string;
  open?: boolean;
}) {
  return (
    <details
      open={open}
      className="group border-b border-white/10 py-ui-section"
    >
      <summary className="flex cursor-pointer list-none items-center justify-between text-ui-body font-semibold text-slate-200 focus-visible:outline-blue-400">
        {label}
        <ChevronDown className="h-4 w-4 text-slate-500" />
      </summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  );
}

function NumberField({
  id,
  label,
  max,
  min,
  value,
  onChange,
  step = '0.1',
}: {
  id: string;
  label: string;
  max?: number;
  min?: number;
  value?: number;
  onChange: (value: number | undefined) => void;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-ui-label text-slate-400">
        {label}
      </label>
      <Input
        id={id}
        type="number"
        max={max}
        min={min}
        step={step}
        value={value ?? ''}
        onChange={event =>
          onChange(
            event.target.value === '' ? undefined : Number(event.target.value)
          )
        }
        className="font-mono"
      />
    </div>
  );
}

function ChoiceGroup({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ label: string; value: string }>;
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex gap-1 rounded-md border border-white/10 p-1"
    >
      {options.map(option => (
        <Button
          key={option.value}
          size="sm"
          variant={option.value === value ? 'default' : 'ghost'}
          className="flex-1"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}

export function ScreeningTopBar({
  screeningCriteria,
  setScreeningCriteria,
  availableIndustries,
  meta,
  onRunScreening,
  screeningLoading,
  onReset,
  onBackfillSnapshot,
  onOpenAdvancedData,
  onOpenSnapshotRun,
  snapshotBackfillLoading,
  snapshotRunState,
  hasPendingChanges = false,
  indicators,
  catalogLoading,
  catalogError,
  onRetryCatalog,
  probabilityModels = [],
  probabilityModelsLoading = false,
  probabilityModelsError,
  onRetryProbabilityModels,
  onOpenIndicatorReport,
  onOpenJointReport,
}: ScreeningTopBarProps) {
  const [industrySearch, setIndustrySearch] = useState('');
  const [indicatorSearch, setIndicatorSearch] = useState('');
  const mode = screeningCriteria.screeningMode ?? 'INDICATOR';
  const intraday = mode === 'INTRADAY';
  const probability = mode === 'PROBABILITY';
  const indicatorMode = mode === 'INDICATOR';
  const universe = screeningCriteria.universe ?? 'STOCK';
  const conditions = screeningCriteria.indicatorConditions ?? [];
  const conditionError = validateIndicatorConditions(conditions);
  const selectedIndustries = screeningCriteria.includeIndustries ?? [];
  const filteredIndustries = availableIndustries.filter(item =>
    item.includes(industrySearch.trim())
  );
  const filteredIndicators = indicators.filter(indicator =>
    `${indicator.label} ${indicator.id} ${indicator.category}`
      .toLowerCase()
      .includes(indicatorSearch.trim().toLowerCase())
  );
  const distinctIndicators = new Set(conditions.map(item => item.indicatorId))
    .size;
  const update = <K extends keyof ScreeningCriteria>(
    key: K,
    value: ScreeningCriteria[K]
  ) => setScreeningCriteria(previous => ({ ...previous, [key]: value }));
  const updateCondition = (index: number, patch: Partial<IndicatorCondition>) =>
    update(
      'indicatorConditions',
      conditions.map((condition, position) =>
        position === index ? { ...condition, ...patch } : condition
      )
    );
  const toggleIndustry = (industry: string) =>
    update(
      'includeIndustries',
      selectedIndustries.includes(industry)
        ? selectedIndustries.filter(item => item !== industry)
        : [...selectedIndustries, industry]
    );
  const updateUniverse = (next: StockScreenUniverse) =>
    setScreeningCriteria(previous => ({
      ...previous,
      universe: next,
      ...(next === 'STOCK'
        ? {}
        : { includeIndustries: [], excludeIndustries: [] }),
    }));
  const failed = ['failed', 'partial_failure', 'crashed'].includes(
    (meta.latestRunStatus ?? '').toLowerCase()
  );
  const scopedRun =
    (meta.latestRunStatus ?? '').toLowerCase() === 'scoped_success';

  return (
    <aside
      className="studio-workspace-surface flex min-h-0 flex-col border-r border-white/10 text-slate-200"
      aria-label="选股条件"
    >
      <header className="shrink-0 border-b border-white/10 p-ui-section">
        <h1 className="mb-3 flex items-center gap-2 text-ui-title font-semibold">
          <SlidersHorizontal className="h-4 w-4 text-blue-400" />
          {intraday ? '盘中选股' : probability ? '次日概率候选' : '指标选股'}
        </h1>
        <ChoiceGroup
          label="选股模式"
          options={[
            { label: '指标', value: 'INDICATOR' },
            { label: '次日概率', value: 'PROBABILITY' },
            { label: '盘中', value: 'INTRADAY' },
          ]}
          value={mode}
          onChange={value => update('screeningMode', value as ScreeningMode)}
        />
        {hasPendingChanges && (
          <div
            role="status"
            className="mt-3 flex items-start gap-2 text-ui-label text-amber-200"
          >
            <Info className="h-4 w-4 shrink-0" />
            有未应用更改；报告对应当前草稿，结果仍为上次筛选。
          </div>
        )}
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-ui-section custom-scrollbar">
        <Section label="筛选范围">
          {probability ? (
            <div className="space-y-2 rounded-md border border-amber-400/20 bg-amber-400/5 p-3 text-ui-label text-slate-300">
              <p className="font-medium text-amber-200">固定研究范围</p>
              <p>
                仅沪深普通 A 股；自动排除当前
                ST、停牌、退市整理和数据不完整标的。
              </p>
              <p className="text-ui-caption text-slate-400">
                候选只用于研究与人工决策，不会创建策略实例或订单。
              </p>
            </div>
          ) : (
            <ChoiceGroup
              label="股票范围"
              options={[
                { label: 'A 股', value: 'STOCK' },
                { label: 'ETF', value: 'ETF' },
                { label: '股票 + ETF', value: 'STOCK_AND_ETF' },
              ]}
              value={universe}
              onChange={value => updateUniverse(value as StockScreenUniverse)}
            />
          )}
          {indicatorMode && universe === 'STOCK' && (
            <label className="flex items-center gap-2 text-ui-label">
              <input
                type="checkbox"
                checked={screeningCriteria.excludeST !== false}
                onChange={event => update('excludeST', event.target.checked)}
                className="accent-blue-500"
              />
              排除当前 ST
            </label>
          )}
          {!probability && universe === 'STOCK' && (
            <details>
              <summary className="cursor-pointer text-ui-label text-slate-400 focus-visible:outline-blue-400">
                行业（包含）· 已选 {selectedIndustries.length}
              </summary>
              <Input
                className="my-2"
                aria-label="搜索行业"
                placeholder="搜索申万一级行业"
                value={industrySearch}
                onChange={event => setIndustrySearch(event.target.value)}
              />
              <div
                className="flex flex-wrap gap-1"
                role="group"
                aria-label="行业选择"
              >
                {filteredIndustries.map(industry => (
                  <Button
                    key={industry}
                    size="sm"
                    variant={
                      selectedIndustries.includes(industry)
                        ? 'default'
                        : 'outline'
                    }
                    aria-pressed={selectedIndustries.includes(industry)}
                    onClick={() => toggleIndustry(industry)}
                  >
                    {industry}
                  </Button>
                ))}
              </div>
            </details>
          )}
        </Section>
        {intraday ? (
          <Section label="盘中量能">
            <NumberField
              id="screening-intraday-volume-pace"
              label="量速"
              value={screeningCriteria.intradayVolumePaceMin}
              onChange={value => update('intradayVolumePaceMin', value)}
            />
            <NumberField
              id="screening-intraday-amount-pace"
              label="额速"
              value={screeningCriteria.intradayAmountPaceMin}
              onChange={value => update('intradayAmountPaceMin', value)}
            />
            <NumberField
              id="screening-intraday-last-5m"
              label="近 5 分钟放量"
              value={screeningCriteria.intradayLast5mVolumeRatioMin}
              onChange={value => update('intradayLast5mVolumeRatioMin', value)}
            />
            <NumberField
              id="screening-intraday-turnover"
              label="盘中换手（%）"
              value={screeningCriteria.intradayTurnoverRateMin}
              onChange={value => update('intradayTurnoverRateMin', value)}
            />
            <NumberField
              id="screening-intraday-depth"
              label="买盘失衡"
              value={screeningCriteria.intradayDepthImbalanceMin}
              step="0.05"
              onChange={value => update('intradayDepthImbalanceMin', value)}
            />
          </Section>
        ) : probability ? (
          <>
            <Section label="候选阈值">
              <NumberField
                id="screening-probability-minimum"
                label="最低校准概率"
                min={0}
                max={1}
                step="0.01"
                value={screeningCriteria.probabilityMinimum}
                onChange={value => update('probabilityMinimum', value)}
              />
              <p className="text-ui-caption text-slate-400">
                正式候选最低为 0.60；更低输入不会绕过服务端资格门槛。
              </p>
              <div className="space-y-2">
                <span className="text-ui-label text-slate-400">候选等级</span>
                {(['A', 'B'] as const).map(level => {
                  const levels = screeningCriteria.probabilityLevels ?? [
                    'A',
                    'B',
                  ];
                  return (
                    <label
                      key={level}
                      className="flex items-center gap-2 text-ui-label"
                    >
                      <input
                        type="checkbox"
                        checked={levels.includes(level)}
                        onChange={event =>
                          update(
                            'probabilityLevels',
                            event.target.checked
                              ? Array.from(new Set([...levels, level])).sort()
                              : levels.filter(item => item !== level)
                          )
                        }
                        className="accent-blue-500"
                      />
                      {level} 级 ·{' '}
                      {level === 'A' ? '全市场排名 1–20' : '全市场排名 21–50'}
                    </label>
                  );
                })}
              </div>
              <div className="space-y-1.5">
                <label
                  htmlFor="screening-probability-search"
                  className="block text-ui-label text-slate-400"
                >
                  代码 / 名称
                </label>
                <Input
                  id="screening-probability-search"
                  value={screeningCriteria.probabilitySearch ?? ''}
                  placeholder="例如 600519"
                  onChange={event =>
                    update('probabilitySearch', event.target.value)
                  }
                />
              </div>
            </Section>
            <Section label="模型与数据">
              <div className="space-y-1.5">
                <label className="block text-ui-label text-slate-400">
                  候选模型
                </label>
                <Select
                  value={
                    screeningCriteria.probabilityModelVersion ??
                    '__AUTO_ACTIVE__'
                  }
                  onValueChange={value =>
                    update(
                      'probabilityModelVersion',
                      value === '__AUTO_ACTIVE__' ? undefined : value
                    )
                  }
                >
                  <SelectTrigger
                    aria-label="候选模型"
                    disabled={
                      probabilityModelsLoading && probabilityModels.length === 0
                    }
                  >
                    <SelectValue placeholder="选择 ACTIVE 或 SHADOW 模型" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="__AUTO_ACTIVE__">
                      自动（ACTIVE 优先）
                    </SelectItem>
                    {probabilityModels.map(model => (
                      <SelectItem
                        key={model.modelVersion}
                        value={model.modelVersion}
                      >
                        {model.stage} · {model.modelVersion}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {probabilityModelsLoading && (
                  <p className="text-ui-caption text-slate-500">
                    加载模型目录…
                  </p>
                )}
                {probabilityModelsError && (
                  <div className="flex items-center justify-between gap-2 text-ui-caption text-rose-300">
                    <span>模型目录加载失败</span>
                    {onRetryProbabilityModels && (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={onRetryProbabilityModels}
                      >
                        重试
                      </Button>
                    )}
                  </div>
                )}
                {!probabilityModelsLoading &&
                  !probabilityModelsError &&
                  probabilityModels.length === 0 && (
                    <p className="text-ui-caption text-amber-200">
                      尚无 ACTIVE 或 SHADOW
                      模型；请先在研究中心登记并人工切换阶段。
                    </p>
                  )}
              </div>
              <div
                role="status"
                className={cn(
                  'space-y-1 text-ui-label',
                  meta.probabilityShowingShadow
                    ? 'text-amber-200'
                    : 'text-slate-300'
                )}
              >
                <p>
                  {!meta.probabilityModelVersion
                    ? '尚无可展示模型'
                    : meta.probabilityShowingShadow
                      ? '当前展示影子模型候选'
                      : '当前展示已激活模型候选'}
                </p>
                <p className="break-all font-mono text-ui-caption text-slate-400">
                  {meta.probabilityModelVersion ?? '尚无可展示模型'}
                </p>
                <p className="font-mono text-ui-caption text-slate-400">
                  预测日 {meta.snapshotDate ?? '--'} · 目标日{' '}
                  {meta.probabilityTargetDate ?? '--'}
                </p>
              </div>
            </Section>
          </>
        ) : (
          <>
            <Section label={`指标条件 · ${conditions.length} 条 AND`}>
              <details>
                <summary className="cursor-pointer text-ui-label text-blue-300 focus-visible:outline-blue-400">
                  条件模板（展开查看实际阈值）
                </summary>
                <div className="mt-2 space-y-2">
                  {INDICATOR_TEMPLATES.filter(template =>
                    template.conditions.every(condition =>
                      indicators.some(
                        indicator => indicator.id === condition.indicatorId
                      )
                    )
                  ).map(template => (
                    <div
                      key={template.label}
                      className="rounded-md border border-white/10 p-2"
                    >
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() =>
                          update('indicatorConditions', [
                            ...conditions,
                            ...template.conditions.map(condition => ({
                              ...condition,
                            })),
                          ])
                        }
                      >
                        添加 {template.label}
                      </Button>
                      {template.conditions.map(condition => (
                        <p
                          key={condition.indicatorId}
                          className="mt-1 text-ui-caption text-slate-400"
                        >
                          {describeIndicatorCondition(condition, indicators)}
                        </p>
                      ))}
                    </div>
                  ))}
                </div>
              </details>
              {!conditions.length && (
                <p className="text-ui-label text-slate-400">
                  未预选指标。添加数值范围或二值条件，所有条件取交集。
                </p>
              )}
              {conditions.map((condition, index) => {
                const indicator = indicators.find(
                  item => item.id === condition.indicatorId
                );
                return (
                  <div
                    key={`${condition.indicatorId}:${index}`}
                    className="space-y-2 rounded-md border border-white/10 p-2"
                  >
                    <div className="flex items-center gap-1">
                      <span className="min-w-0 flex-1 text-ui-label font-medium">
                        {indicator?.label ?? condition.indicatorId}
                      </span>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`查看 ${indicator?.label ?? condition.indicatorId} 报告`}
                        onClick={() =>
                          onOpenIndicatorReport(condition.indicatorId)
                        }
                      >
                        <BookOpen className="h-4 w-4" />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`移除 ${indicator?.label ?? condition.indicatorId} 条件`}
                        onClick={() =>
                          update(
                            'indicatorConditions',
                            conditions.filter(
                              (_, position) => position !== index
                            )
                          )
                        }
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    </div>
                    {indicator?.kind === 'binary' ? (
                      <Select
                        value={String(condition.value ?? 1)}
                        onValueChange={value =>
                          updateCondition(index, {
                            operator: 'eq',
                            value: Number(value),
                            valueTo: null,
                          })
                        }
                      >
                        <SelectTrigger aria-label={`${indicator.label} 条件`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="1">成立（1）</SelectItem>
                          <SelectItem value="0">不成立（0）</SelectItem>
                        </SelectContent>
                      </Select>
                    ) : (
                      <>
                        <Select
                          value={condition.operator}
                          onValueChange={operator =>
                            updateCondition(index, {
                              operator: operator as IndicatorOperator,
                            })
                          }
                        >
                          <SelectTrigger
                            aria-label={`${indicator?.label ?? condition.indicatorId} 比较方式`}
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(INDICATOR_OPERATOR_LABELS)
                              .filter(
                                ([operator]) =>
                                  !indicator?.operators.length ||
                                  indicator.operators.includes(operator)
                              )
                              .map(([operator, label]) => (
                                <SelectItem key={operator} value={operator}>
                                  {label}
                                </SelectItem>
                              ))}
                          </SelectContent>
                        </Select>
                        <div className="flex items-center gap-2">
                          <Input
                            type="number"
                            step="any"
                            aria-label={`${indicator?.label ?? condition.indicatorId} ${condition.operator === 'between' ? '下限' : '数值'}`}
                            value={condition.value ?? ''}
                            onChange={event =>
                              updateCondition(index, {
                                value:
                                  event.target.value === ''
                                    ? null
                                    : Number(event.target.value),
                              })
                            }
                            className="min-w-0 font-mono"
                          />
                          {condition.operator === 'between' && (
                            <Input
                              type="number"
                              step="any"
                              aria-label={`${indicator?.label ?? condition.indicatorId} 上限`}
                              value={condition.valueTo ?? ''}
                              onChange={event =>
                                updateCondition(index, {
                                  valueTo:
                                    event.target.value === ''
                                      ? null
                                      : Number(event.target.value),
                                })
                              }
                              className="min-w-0 font-mono"
                            />
                          )}
                          <span className="text-ui-caption text-slate-400">
                            {indicator?.unit}
                          </span>
                        </div>
                      </>
                    )}
                    {indicator && !indicator.researchSupported && (
                      <p className="text-ui-caption text-amber-200">
                        可选股；历史研究未覆盖
                      </p>
                    )}
                  </div>
                );
              })}
              {conditionError && (
                <p role="alert" className="text-ui-label text-amber-200">
                  {conditionError}
                </p>
              )}
              {distinctIndicators >= 1 && (
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={Boolean(conditionError)}
                  onClick={onOpenJointReport}
                >
                  <BookOpen className="mr-2 h-4 w-4" />
                  {distinctIndicators >= 2
                    ? '查看当前组合报告'
                    : '查看当前条件报告'}
                </Button>
              )}
            </Section>
            <Section label="指标目录">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
                <Input
                  aria-label="搜索指标"
                  placeholder="名称、类别或指标代码"
                  value={indicatorSearch}
                  onChange={event => setIndicatorSearch(event.target.value)}
                  className="pl-9"
                />
              </div>
              {catalogLoading && !indicators.length && (
                <p role="status" className="text-ui-label text-slate-400">
                  正在读取指标目录…
                </p>
              )}
              {catalogError && (
                <div role="alert" className="text-ui-label text-rose-300">
                  {catalogError}
                  <Button size="sm" variant="outline" onClick={onRetryCatalog}>
                    重试
                  </Button>
                </div>
              )}
              {!catalogLoading &&
                !catalogError &&
                !filteredIndicators.length && (
                  <p className="text-ui-label text-slate-400">没有匹配的指标</p>
                )}
              {filteredIndicators.map(indicator => (
                <div
                  key={indicator.id}
                  className="flex items-center gap-1 border-b border-white/5 pb-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-ui-label text-slate-200">
                      {indicator.label}
                    </div>
                    <div className="text-ui-caption text-slate-400">
                      {indicator.category} · {indicator.lookback} 日 ·{' '}
                      {indicator.researchSupported ? '量价研究' : '研究待覆盖'}
                    </div>
                  </div>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`查看 ${indicator.label} 报告`}
                    onClick={() => onOpenIndicatorReport(indicator.id)}
                  >
                    <BookOpen className="h-4 w-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`添加 ${indicator.label} 条件`}
                    onClick={() =>
                      update('indicatorConditions', [
                        ...conditions,
                        {
                          indicatorId: indicator.id,
                          operator: indicator.kind === 'binary' ? 'eq' : 'gte',
                          value: indicator.kind === 'binary' ? 1 : null,
                        },
                      ])
                    }
                  >
                    <Plus className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </Section>
            <Section label="数据健康">
              <label className="flex items-center gap-2 text-ui-label">
                <input
                  type="checkbox"
                  checked={Boolean(screeningCriteria.requireFresh)}
                  onChange={event =>
                    update('requireFresh', event.target.checked)
                  }
                  className="accent-blue-500"
                />
                只使用应有日期的完整快照
              </label>
              <div
                role="status"
                className={cn(
                  'text-ui-label',
                  failed
                    ? 'text-rose-300'
                    : meta.isComplete
                      ? 'text-slate-300'
                      : 'text-amber-200'
                )}
              >
                {snapshotBackfillLoading
                  ? '正在核对 / 补算快照'
                  : failed
                    ? '最近补算失败'
                    : meta.isComplete
                      ? '快照已就绪'
                      : '快照未完整就绪'}
                <div className="mt-1 font-mono text-ui-caption">
                  {meta.snapshotDate ?? '--'} ·{' '}
                  {meta.calculationVersion ?? '指标版本未就绪'}
                </div>
                {!meta.isComplete && (
                  <div>
                    历史缺口 {meta.missingSnapshotDates.length} 个交易日
                  </div>
                )}
                {scopedRun && (
                  <p className="mt-1 text-amber-200">
                    最近仅完成指定标的补算，不代表全市场快照就绪。
                  </p>
                )}
                {snapshotRunState && (
                  <div className="font-mono">Prefect · {snapshotRunState}</div>
                )}
              </div>
              {!meta.isComplete && (
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={snapshotBackfillLoading}
                    onClick={onBackfillSnapshot}
                  >
                    <RefreshCw
                      className={cn(
                        'mr-2 h-4 w-4',
                        snapshotBackfillLoading &&
                          'animate-spin motion-reduce:animate-none'
                      )}
                    />
                    立即补算
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={onOpenAdvancedData}
                  >
                    高级补数
                  </Button>
                </div>
              )}
              {onOpenSnapshotRun && (
                <Button size="sm" variant="ghost" onClick={onOpenSnapshotRun}>
                  <ExternalLink className="mr-2 h-4 w-4" />
                  查看补算日志
                </Button>
              )}
            </Section>
          </>
        )}
      </div>
      <footer className="shrink-0 border-t border-white/10 p-ui-section">
        <p className="mb-2 text-ui-caption text-slate-400">
          {hasPendingChanges
            ? '当前草稿 · 应用后更新结果'
            : probability
              ? '结果来自已发布的完整预测批次'
              : '结果对应已应用条件（全部为 AND）'}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onReset}>
            重置
          </Button>
          <Button
            className="flex-1"
            disabled={
              screeningLoading ||
              (indicatorMode && Boolean(conditionError)) ||
              (probability &&
                !(screeningCriteria.probabilityLevels ?? []).length)
            }
            onClick={onRunScreening}
          >
            {screeningLoading
              ? '筛选中…'
              : intraday
                ? '开始盘中扫描'
                : probability
                  ? '刷新概率候选'
                  : '应用筛选'}
          </Button>
        </div>
      </footer>
    </aside>
  );
}
