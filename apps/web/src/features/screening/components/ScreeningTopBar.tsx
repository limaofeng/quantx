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
  FACTOR_OPERATOR_LABELS,
  FACTOR_TEMPLATES,
  describeFactorCondition,
  validateFactorConditions,
} from '../factorModel';
import type {
  FactorCondition,
  FactorDefinition,
  FactorOperator,
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
  factors: FactorDefinition[];
  catalogLoading?: boolean;
  catalogError?: string;
  onRetryCatalog: () => void;
  onOpenFactorReport: (factorId: string) => void;
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
  value,
  onChange,
  step = '0.1',
}: {
  id: string;
  label: string;
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
  factors,
  catalogLoading,
  catalogError,
  onRetryCatalog,
  onOpenFactorReport,
  onOpenJointReport,
}: ScreeningTopBarProps) {
  const [industrySearch, setIndustrySearch] = useState('');
  const [factorSearch, setFactorSearch] = useState('');
  const mode = screeningCriteria.screeningMode ?? 'DAILY';
  const intraday = mode === 'INTRADAY';
  const universe = screeningCriteria.universe ?? 'STOCK';
  const conditions = screeningCriteria.factorConditions ?? [];
  const conditionError = validateFactorConditions(conditions);
  const selectedIndustries = screeningCriteria.includeIndustries ?? [];
  const filteredIndustries = availableIndustries.filter(item =>
    item.includes(industrySearch.trim())
  );
  const filteredFactors = factors.filter(factor =>
    `${factor.label} ${factor.id} ${factor.category}`
      .toLowerCase()
      .includes(factorSearch.trim().toLowerCase())
  );
  const distinctFactors = new Set(conditions.map(item => item.factorId)).size;
  const update = <K extends keyof ScreeningCriteria>(
    key: K,
    value: ScreeningCriteria[K]
  ) => setScreeningCriteria(previous => ({ ...previous, [key]: value }));
  const updateCondition = (index: number, patch: Partial<FactorCondition>) =>
    update(
      'factorConditions',
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
          {intraday ? '盘中选股' : '因子选股'}
        </h1>
        <ChoiceGroup
          label="选股模式"
          options={[
            { label: '日级', value: 'DAILY' },
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
          {!intraday && universe === 'STOCK' && (
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
          {universe === 'STOCK' && (
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
        ) : (
          <>
            <Section label={`因子条件 · ${conditions.length} 条 AND`}>
              <details>
                <summary className="cursor-pointer text-ui-label text-blue-300 focus-visible:outline-blue-400">
                  条件模板（展开查看实际阈值）
                </summary>
                <div className="mt-2 space-y-2">
                  {FACTOR_TEMPLATES.filter(template =>
                    template.conditions.every(condition =>
                      factors.some(factor => factor.id === condition.factorId)
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
                          update('factorConditions', [
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
                          key={condition.factorId}
                          className="mt-1 text-ui-caption text-slate-400"
                        >
                          {describeFactorCondition(condition, factors)}
                        </p>
                      ))}
                    </div>
                  ))}
                </div>
              </details>
              {!conditions.length && (
                <p className="text-ui-label text-slate-400">
                  未预选因子。添加数值范围或二值条件，所有条件取交集。
                </p>
              )}
              {conditions.map((condition, index) => {
                const factor = factors.find(
                  item => item.id === condition.factorId
                );
                return (
                  <div
                    key={`${condition.factorId}:${index}`}
                    className="space-y-2 rounded-md border border-white/10 p-2"
                  >
                    <div className="flex items-center gap-1">
                      <span className="min-w-0 flex-1 text-ui-label font-medium">
                        {factor?.label ?? condition.factorId}
                      </span>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`查看 ${factor?.label ?? condition.factorId} 报告`}
                        onClick={() => onOpenFactorReport(condition.factorId)}
                      >
                        <BookOpen className="h-4 w-4" />
                      </Button>
                      <Button
                        size="icon"
                        variant="ghost"
                        aria-label={`移除 ${factor?.label ?? condition.factorId} 条件`}
                        onClick={() =>
                          update(
                            'factorConditions',
                            conditions.filter(
                              (_, position) => position !== index
                            )
                          )
                        }
                      >
                        <X className="h-4 w-4" />
                      </Button>
                    </div>
                    {factor?.kind === 'binary' ? (
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
                        <SelectTrigger aria-label={`${factor.label} 条件`}>
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
                              operator: operator as FactorOperator,
                            })
                          }
                        >
                          <SelectTrigger
                            aria-label={`${factor?.label ?? condition.factorId} 比较方式`}
                          >
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {Object.entries(FACTOR_OPERATOR_LABELS)
                              .filter(
                                ([operator]) =>
                                  !factor?.operators.length ||
                                  factor.operators.includes(operator)
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
                            aria-label={`${factor?.label ?? condition.factorId} ${condition.operator === 'between' ? '下限' : '数值'}`}
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
                              aria-label={`${factor?.label ?? condition.factorId} 上限`}
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
                            {factor?.unit}
                          </span>
                        </div>
                      </>
                    )}
                    {factor && !factor.researchSupported && (
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
              {distinctFactors >= 1 && (
                <Button
                  variant="outline"
                  className="w-full"
                  disabled={Boolean(conditionError)}
                  onClick={onOpenJointReport}
                >
                  <BookOpen className="mr-2 h-4 w-4" />
                  {distinctFactors >= 2
                    ? '查看当前组合报告'
                    : '查看当前条件报告'}
                </Button>
              )}
            </Section>
            <Section label="因子目录">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-400" />
                <Input
                  aria-label="搜索因子"
                  placeholder="名称、类别或指标代码"
                  value={factorSearch}
                  onChange={event => setFactorSearch(event.target.value)}
                  className="pl-9"
                />
              </div>
              {catalogLoading && !factors.length && (
                <p role="status" className="text-ui-label text-slate-400">
                  正在读取因子目录…
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
              {!catalogLoading && !catalogError && !filteredFactors.length && (
                <p className="text-ui-label text-slate-400">没有匹配的因子</p>
              )}
              {filteredFactors.map(factor => (
                <div
                  key={factor.id}
                  className="flex items-center gap-1 border-b border-white/5 pb-2"
                >
                  <div className="min-w-0 flex-1">
                    <div className="text-ui-label text-slate-200">
                      {factor.label}
                    </div>
                    <div className="text-ui-caption text-slate-400">
                      {factor.category} · {factor.lookback} 日 ·{' '}
                      {factor.researchSupported ? '量价研究' : '研究待覆盖'}
                    </div>
                  </div>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`查看 ${factor.label} 报告`}
                    onClick={() => onOpenFactorReport(factor.id)}
                  >
                    <BookOpen className="h-4 w-4" />
                  </Button>
                  <Button
                    size="icon"
                    variant="ghost"
                    aria-label={`添加 ${factor.label} 条件`}
                    onClick={() =>
                      update('factorConditions', [
                        ...conditions,
                        {
                          factorId: factor.id,
                          operator: factor.kind === 'binary' ? 'eq' : 'gte',
                          value: factor.kind === 'binary' ? 1 : null,
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
                  {meta.calculationVersion ?? '因子版本未就绪'}
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
            : '结果对应已应用条件（全部为 AND）'}
        </p>
        <div className="flex gap-2">
          <Button variant="outline" onClick={onReset}>
            重置
          </Button>
          <Button
            className="flex-1"
            disabled={
              screeningLoading || (!intraday && Boolean(conditionError))
            }
            onClick={onRunScreening}
          >
            {screeningLoading
              ? '筛选中…'
              : intraday
                ? '开始盘中扫描'
                : '应用筛选'}
          </Button>
        </div>
      </footer>
    </aside>
  );
}
