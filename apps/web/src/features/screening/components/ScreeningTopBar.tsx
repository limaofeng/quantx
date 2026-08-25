import {
  AlertTriangle,
  Check,
  ChevronDown,
  ExternalLink,
  Info,
  RefreshCw,
  Search,
  Settings2,
  SlidersHorizontal,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';

import { Input } from '@/components/ui/input';

import {
  type ScreeningCriteria,
  type ScreeningMode,
  type StockScreenUniverse,
  type StockScreeningMeta,
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
}

const STRATEGIES = [
  { id: 'enableOversoldRebound', label: '超跌反弹' },
  { id: 'enableStrongTrend', label: '强势股' },
  { id: 'enableKDJGoldenCross', label: 'KDJ 金叉' },
  { id: 'enableVolumeBreakout', label: '放量突破' },
  { id: 'enableMACrossover', label: '均线金叉' },
  { id: 'enableBollingerLowerRebound', label: '布林下轨反弹' },
  { id: 'enableBollingerUpperBreakout', label: '布林上轨突破' },
  { id: 'enableRSIOversold', label: 'RSI 超卖' },
  { id: 'enableRSIStrong', label: 'RSI 强势' },
] as const;

const UNIVERSE_OPTIONS: Array<{
  label: string;
  value: StockScreenUniverse;
}> = [
  { label: '股票', value: 'STOCK' },
  { label: 'ETF', value: 'ETF' },
  { label: '股票 + ETF', value: 'STOCK_AND_ETF' },
];

const MODE_OPTIONS: Array<{ label: string; value: ScreeningMode }> = [
  { label: '日级', value: 'DAILY' },
  { label: '盘中', value: 'INTRADAY' },
];

function formatInputValue(value: number | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? value : '';
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
      className="group border-b border-white/[0.06] py-ui-section"
    >
      <summary className="flex cursor-pointer list-none items-center justify-between text-ui-body font-semibold text-slate-200 outline-none marker:hidden focus-visible:rounded focus-visible:ring-2 focus-visible:ring-cyan-400/70 [&::-webkit-details-marker]:hidden">
        <span>{label}</span>
        <ChevronDown className="h-4 w-4 text-slate-500 transition-transform group-open:rotate-180 motion-reduce:transition-none" />
      </summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  );
}

function NumberField({
  id,
  label,
  suffix,
  value,
  onChange,
  step = '0.1',
}: {
  id: string;
  label: string;
  suffix?: string;
  value: number | undefined;
  onChange: (value: number | undefined) => void;
  step?: string;
}) {
  return (
    <div className="space-y-1.5">
      <label
        htmlFor={id}
        className="block text-ui-label font-medium text-slate-400"
      >
        {label}
      </label>
      <div className="relative">
        <Input
          id={id}
          type="number"
          inputMode="decimal"
          min="0"
          step={step}
          value={formatInputValue(value)}
          onChange={event => {
            const raw = event.target.value;
            onChange(raw === '' ? undefined : Number(raw));
          }}
          className="h-9 w-full rounded-md border border-white/[0.09] bg-[#09111f] px-3 text-ui-body font-mono text-slate-100 outline-none transition-colors placeholder:text-slate-600 focus:border-cyan-400/80 focus:ring-2 focus:ring-cyan-400/20"
        />
        {suffix && (
          <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-ui-label text-slate-500">
            {suffix}
          </span>
        )}
      </div>
    </div>
  );
}

function ChoiceGroup({
  label,
  options,
  value,
  onChange,
  ariaLabel,
}: {
  label?: string;
  options: Array<{ label: string; value: string }>;
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  return (
    <div className="space-y-2">
      {label && (
        <div className="text-ui-label font-medium text-slate-400">{label}</div>
      )}
      <div
        role="group"
        aria-label={ariaLabel}
        className="grid grid-cols-3 gap-1 rounded-md border border-white/[0.08] bg-[#09111f] p-1"
      >
        {options.map(option => {
          const selected = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(option.value)}
              className={`h-8 rounded px-2 text-ui-label font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80 ${
                selected
                  ? 'bg-blue-600 text-white shadow-sm'
                  : 'text-slate-500 hover:bg-white/[0.06] hover:text-slate-200'
              }`}
            >
              {option.label}
            </button>
          );
        })}
      </div>
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
}: ScreeningTopBarProps) {
  const [industrySearch, setIndustrySearch] = useState('');
  const screeningMode = screeningCriteria.screeningMode ?? 'DAILY';
  const isIntradayMode = screeningMode === 'INTRADAY';
  const universe = screeningCriteria.universe ?? 'STOCK';
  const stockOnlyFiltersEnabled = universe === 'STOCK';
  const latestRunFailed = ['failed', 'partial_failure', 'crashed'].includes(
    (meta.latestRunStatus || '').toLowerCase()
  );
  const missingSnapshotCount = meta.missingSnapshotDates.length;
  const filteredIndustries = useMemo(() => {
    const keyword = industrySearch.trim().toLowerCase();
    if (!keyword) return availableIndustries;
    return availableIndustries.filter(industry =>
      industry.toLowerCase().includes(keyword)
    );
  }, [availableIndustries, industrySearch]);

  const updateCriteria = <K extends keyof ScreeningCriteria>(
    key: K,
    value: ScreeningCriteria[K]
  ) => {
    setScreeningCriteria(previous => ({ ...previous, [key]: value }));
  };

  const updateUniverse = (nextUniverse: StockScreenUniverse) => {
    setScreeningCriteria(previous => {
      if (nextUniverse === 'STOCK') {
        return { ...previous, universe: nextUniverse };
      }
      return {
        ...previous,
        universe: nextUniverse,
        includeIndustries: undefined,
        excludeIndustries: undefined,
        minROE: undefined,
        minNetProfitGrowth: undefined,
        minYoYGrowth: undefined,
      };
    });
  };

  const toggleIndustry = (industry: string) => {
    setScreeningCriteria(previous => {
      const current = previous.includeIndustries ?? [];
      return {
        ...previous,
        includeIndustries: current.includes(industry)
          ? current.filter(item => item !== industry)
          : [...current, industry],
      };
    });
  };

  const selectedIndustries = screeningCriteria.includeIndustries ?? [];
  const selectedSignals = STRATEGIES.filter(strategy =>
    Boolean(screeningCriteria[strategy.id])
  ).length;
  const activeConditions =
    selectedIndustries.length +
    (isIntradayMode
      ? [
          screeningCriteria.intradayVolumePaceMin,
          screeningCriteria.intradayAmountPaceMin,
          screeningCriteria.intradayLast5mVolumeRatioMin,
          screeningCriteria.intradayTurnoverRateMin,
          screeningCriteria.intradayDepthImbalanceMin,
        ]
      : [
          screeningCriteria.minROE,
          screeningCriteria.minNetProfitGrowth,
          screeningCriteria.minYoYGrowth,
          screeningCriteria.priceDropMin,
          screeningCriteria.volumeRatioMin,
          screeningCriteria.volumeRatioMax,
          screeningCriteria.volumeRatio5Min,
          screeningCriteria.amountRatioMin,
          screeningCriteria.turnoverRateMin,
        ]
    ).filter(value => typeof value === 'number' && value > 0).length +
    (isIntradayMode ? 0 : selectedSignals);

  const snapshotStateLabel = snapshotBackfillLoading
    ? '补算中'
    : latestRunFailed
      ? '快照补算失败'
      : meta.isComplete
        ? '最新交易日快照'
        : meta.snapshotDate
          ? `缺少 ${missingSnapshotCount} 个交易日`
          : '尚无快照';
  const snapshotStateClass = snapshotBackfillLoading
    ? 'text-blue-300'
    : latestRunFailed
      ? 'text-rose-300'
      : meta.isComplete
        ? 'text-emerald-300'
        : meta.snapshotDate
          ? 'text-amber-300'
          : 'text-slate-400';

  return (
    <div className="studio-workspace-surface flex h-full min-h-0 w-full shrink-0 flex-col border-r border-white/[0.07] bg-slate-900">
      <header className="shrink-0 border-b border-white/[0.07] px-ui-section py-ui-section">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <SlidersHorizontal className="h-4 w-4 text-blue-400" />
              <h1 className="text-ui-title font-bold tracking-tight text-white">
                选股
              </h1>
            </div>
            <p className="mt-1 text-ui-label text-slate-500">
              构建条件，运行后应用
            </p>
          </div>
          <span className="rounded border border-blue-400/20 bg-blue-500/10 px-1.5 py-0.5 font-mono text-ui-caption text-blue-300">
            {activeConditions} 条条件
          </span>
        </div>
        <div
          role="group"
          aria-label="筛选模式"
          className="mt-4 grid grid-cols-2 gap-1 rounded-md border border-white/[0.08] bg-[#09111f] p-1"
        >
          {MODE_OPTIONS.map(option => {
            const selected = option.value === screeningMode;
            return (
              <button
                key={option.value}
                type="button"
                data-testid={`screening-mode-${option.value}`}
                aria-pressed={selected}
                onClick={() => updateCriteria('screeningMode', option.value)}
                className={`h-9 rounded text-ui-body font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80 ${
                  selected
                    ? option.value === 'INTRADAY'
                      ? 'bg-cyan-500 text-slate-950'
                      : 'bg-blue-600 text-white'
                    : 'text-slate-500 hover:bg-white/[0.06] hover:text-slate-200'
                }`}
              >
                {option.label}
              </button>
            );
          })}
        </div>
        {hasPendingChanges && (
          <div
            aria-live="polite"
            className="mt-3 flex items-center gap-2 rounded-md border border-amber-400/25 bg-amber-500/[0.08] px-3 py-2 text-ui-label text-amber-200"
          >
            <Info className="h-3.5 w-3.5 shrink-0" />
            有未应用更改，点击运行后更新结果
          </div>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-ui-section custom-scrollbar">
        <Section label="筛选范围">
          <ChoiceGroup
            ariaLabel="股票范围"
            options={UNIVERSE_OPTIONS}
            value={universe}
            onChange={value => updateUniverse(value as StockScreenUniverse)}
          />

          {!isIntradayMode && stockOnlyFiltersEnabled && (
            <label
              htmlFor="screening-exclude-st"
              className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-white/[0.08] bg-[#09111f] px-3 text-ui-label text-slate-300 transition-colors hover:border-white/20 focus-within:ring-2 focus-within:ring-cyan-400/70"
            >
              <input
                id="screening-exclude-st"
                type="checkbox"
                checked={screeningCriteria.excludeST !== false}
                onChange={event =>
                  updateCriteria('excludeST', event.target.checked)
                }
                className="h-4 w-4 rounded border-slate-600 bg-transparent accent-blue-500 focus-visible:ring-2 focus-visible:ring-cyan-400/80"
              />
              <span>排除 ST</span>
            </label>
          )}

          {stockOnlyFiltersEnabled && (
            <div className="space-y-2">
              <label
                htmlFor="screening-industry-search"
                className="block text-ui-label font-medium text-slate-400"
              >
                行业（包含）
              </label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-600" />
                <Input
                  id="screening-industry-search"
                  value={industrySearch}
                  onChange={event => setIndustrySearch(event.target.value)}
                  placeholder="搜索申万一级行业"
                  className="h-9 w-full rounded-md border border-white/[0.09] bg-[#09111f] pl-9 pr-3 text-ui-label text-slate-100 outline-none transition-colors placeholder:text-slate-600 focus:border-cyan-400/80 focus:ring-2 focus:ring-cyan-400/20"
                />
              </div>
              {selectedIndustries.length > 0 && (
                <div className="flex flex-wrap gap-1.5" aria-label="已选行业">
                  {selectedIndustries.map(industry => (
                    <button
                      key={industry}
                      type="button"
                      aria-label={`移除行业 ${industry}`}
                      onClick={() => toggleIndustry(industry)}
                      className="inline-flex items-center gap-1 rounded border border-blue-400/30 bg-blue-500/10 px-2 py-1 text-ui-caption text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                    >
                      {industry} <span aria-hidden="true">×</span>
                    </button>
                  ))}
                </div>
              )}
              <div
                role="group"
                aria-label="行业选择"
                className="grid max-h-36 grid-cols-2 gap-1 overflow-y-auto rounded-md border border-white/[0.07] bg-[#09111f] p-1.5 custom-scrollbar"
              >
                {filteredIndustries.length > 0 ? (
                  filteredIndustries.map(industry => {
                    const selected = selectedIndustries.includes(industry);
                    return (
                      <button
                        key={industry}
                        type="button"
                        role="checkbox"
                        aria-checked={selected}
                        onClick={() => toggleIndustry(industry)}
                        className={`flex min-h-8 items-center justify-between rounded px-2 text-left text-ui-label transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80 ${
                          selected
                            ? 'bg-blue-500/15 text-blue-200'
                            : 'text-slate-500 hover:bg-white/[0.06] hover:text-slate-200'
                        }`}
                      >
                        <span className="truncate">{industry}</span>
                        {selected && <Check className="h-3.5 w-3.5 shrink-0" />}
                      </button>
                    );
                  })
                ) : (
                  <span className="col-span-2 px-2 py-3 text-center text-ui-label text-slate-600">
                    行业列表暂不可用
                  </span>
                )}
              </div>
            </div>
          )}
        </Section>

        {isIntradayMode ? (
          <Section label="盘中量能">
            <NumberField
              id="screening-intraday-volume-pace"
              label="量速"
              value={screeningCriteria.intradayVolumePaceMin}
              onChange={value => updateCriteria('intradayVolumePaceMin', value)}
              step="0.1"
            />
            <NumberField
              id="screening-intraday-amount-pace"
              label="额速"
              value={screeningCriteria.intradayAmountPaceMin}
              onChange={value => updateCriteria('intradayAmountPaceMin', value)}
              step="0.1"
            />
            <NumberField
              id="screening-intraday-last-5m"
              label="近 5 分钟放量"
              value={screeningCriteria.intradayLast5mVolumeRatioMin}
              onChange={value =>
                updateCriteria('intradayLast5mVolumeRatioMin', value)
              }
              step="0.1"
            />
            <NumberField
              id="screening-intraday-turnover"
              label="盘中换手"
              suffix="%"
              value={screeningCriteria.intradayTurnoverRateMin}
              onChange={value =>
                updateCriteria('intradayTurnoverRateMin', value)
              }
              step="0.1"
            />
            <NumberField
              id="screening-intraday-depth"
              label="买盘失衡"
              value={screeningCriteria.intradayDepthImbalanceMin}
              onChange={value =>
                updateCriteria('intradayDepthImbalanceMin', value)
              }
              step="0.05"
            />
          </Section>
        ) : (
          <>
            {stockOnlyFiltersEnabled && (
              <Section label="基本面">
                <NumberField
                  id="screening-min-roe"
                  label="最小 ROE（TTM）"
                  suffix="%"
                  value={screeningCriteria.minROE}
                  onChange={value => updateCriteria('minROE', value)}
                />
                <NumberField
                  id="screening-min-net-profit-growth"
                  label="最小净利单季同比"
                  suffix="%"
                  value={screeningCriteria.minNetProfitGrowth}
                  onChange={value =>
                    updateCriteria('minNetProfitGrowth', value)
                  }
                />
                <NumberField
                  id="screening-min-yoy-growth"
                  label="最小营收单季同比"
                  suffix="%"
                  value={screeningCriteria.minYoYGrowth}
                  onChange={value => updateCriteria('minYoYGrowth', value)}
                />
              </Section>
            )}

            <Section label="日级量能与价格">
              <NumberField
                id="screening-price-drop-min"
                label="跌幅最小值"
                suffix="%"
                value={screeningCriteria.priceDropMin}
                onChange={value => updateCriteria('priceDropMin', value)}
              />
              <div className="grid grid-cols-2 gap-3">
                <NumberField
                  id="screening-volume-ratio-min"
                  label="量比下限"
                  value={screeningCriteria.volumeRatioMin}
                  onChange={value => updateCriteria('volumeRatioMin', value)}
                />
                <NumberField
                  id="screening-volume-ratio-max"
                  label="量比上限"
                  value={screeningCriteria.volumeRatioMax}
                  onChange={value => updateCriteria('volumeRatioMax', value)}
                />
              </div>
              <NumberField
                id="screening-volume-ratio-5-min"
                label="5 日量比下限"
                value={screeningCriteria.volumeRatio5Min}
                onChange={value => updateCriteria('volumeRatio5Min', value)}
              />
              <NumberField
                id="screening-amount-ratio-min"
                label="额比下限"
                value={screeningCriteria.amountRatioMin}
                onChange={value => updateCriteria('amountRatioMin', value)}
              />
              <NumberField
                id="screening-turnover-min"
                label="换手率下限"
                suffix="%"
                value={screeningCriteria.turnoverRateMin}
                onChange={value => updateCriteria('turnoverRateMin', value)}
              />
            </Section>

            <Section label="量化信号">
              <div
                className="grid grid-cols-2 gap-2"
                role="group"
                aria-label="量化信号"
              >
                {STRATEGIES.map(strategy => {
                  const id = `screening-${strategy.id}`;
                  const checked = Boolean(screeningCriteria[strategy.id]);
                  return (
                    <label
                      key={strategy.id}
                      htmlFor={id}
                      className={`flex min-h-9 cursor-pointer items-center gap-2 rounded-md border px-2 text-ui-label transition-colors focus-within:ring-2 focus-within:ring-cyan-400/80 ${
                        checked
                          ? 'border-blue-400/40 bg-blue-500/10 text-blue-200'
                          : 'border-white/[0.08] bg-[#09111f] text-slate-500 hover:border-white/20 hover:text-slate-200'
                      }`}
                    >
                      <input
                        id={id}
                        type="checkbox"
                        checked={checked}
                        onChange={event =>
                          updateCriteria(strategy.id, event.target.checked)
                        }
                        className="h-4 w-4 rounded border-slate-600 bg-transparent accent-blue-500"
                      />
                      <span>{strategy.label}</span>
                    </label>
                  );
                })}
              </div>
              {(screeningCriteria.enableRSIOversold ||
                screeningCriteria.enableRSIStrong) && (
                <div className="space-y-3 border-t border-white/[0.06] pt-3">
                  <div className="text-ui-label font-medium text-slate-400">
                    RSI 阈值
                  </div>
                  {screeningCriteria.enableRSIOversold && (
                    <NumberField
                      id="screening-rsi-oversold-threshold"
                      label="RSI 超卖阈值"
                      value={screeningCriteria.rsiOversoldThreshold}
                      onChange={value =>
                        updateCriteria('rsiOversoldThreshold', value)
                      }
                    />
                  )}
                  {screeningCriteria.enableRSIStrong && (
                    <NumberField
                      id="screening-rsi-strong-threshold"
                      label="RSI 强势阈值"
                      value={screeningCriteria.rsiStrongThreshold}
                      onChange={value =>
                        updateCriteria('rsiStrongThreshold', value)
                      }
                    />
                  )}
                </div>
              )}
            </Section>

            <Section label="数据健康">
              <label
                htmlFor="screening-require-fresh"
                className="flex min-h-9 cursor-pointer items-center gap-2 rounded-md border border-white/[0.08] bg-[#09111f] px-3 text-ui-label text-slate-300 focus-within:ring-2 focus-within:ring-cyan-400/70"
              >
                <input
                  id="screening-require-fresh"
                  type="checkbox"
                  checked={Boolean(screeningCriteria.requireFresh)}
                  onChange={event =>
                    updateCriteria('requireFresh', event.target.checked)
                  }
                  className="h-4 w-4 rounded border-slate-600 bg-transparent accent-blue-500"
                />
                <span>只看新鲜信号</span>
              </label>

              <div
                aria-live="polite"
                className="rounded-md border border-white/[0.07] bg-[#09111f] p-3 text-ui-label"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`h-2 w-2 rounded-full ${snapshotBackfillLoading ? 'bg-blue-400' : latestRunFailed ? 'bg-rose-400' : meta.isComplete ? 'bg-emerald-400' : 'bg-amber-400'}`}
                  />
                  <span className={snapshotStateClass}>
                    {snapshotStateLabel}
                  </span>
                </div>
                {meta.snapshotDate && (
                  <div className="mt-2 font-mono text-ui-caption text-slate-500">
                    快照 {meta.snapshotDate}
                  </div>
                )}
                {!meta.isComplete && meta.snapshotDate && (
                  <div className="mt-1 text-amber-300">
                    历史缺口：{missingSnapshotCount} 个交易日
                  </div>
                )}
                {!isIntradayMode && snapshotRunState && (
                  <div className="mt-1 font-mono text-ui-caption text-slate-500">
                    Prefect · {snapshotRunState}
                  </div>
                )}
              </div>

              {!meta.isComplete && (
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={onBackfillSnapshot}
                    disabled={snapshotBackfillLoading}
                    className="inline-flex min-h-9 items-center gap-2 rounded-md border border-amber-400/30 bg-amber-500/10 px-3 text-ui-label font-semibold text-amber-200 transition-colors hover:bg-amber-500/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-300/80 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {snapshotBackfillLoading ? (
                      <RefreshCw className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                    ) : (
                      <AlertTriangle className="h-3.5 w-3.5" />
                    )}
                    {snapshotBackfillLoading ? '补算中' : '立即补算'}
                  </button>
                  {latestRunFailed && onOpenSnapshotRun && (
                    <button
                      type="button"
                      onClick={onOpenSnapshotRun}
                      className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-ui-label text-rose-300 transition-colors hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                      查看日志
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={onOpenAdvancedData}
                    className="inline-flex min-h-9 items-center gap-1 rounded-md px-2 text-ui-label text-slate-400 transition-colors hover:bg-white/[0.06] hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
                  >
                    <Settings2 className="h-3.5 w-3.5" />
                    高级补数
                  </button>
                </div>
              )}
            </Section>
          </>
        )}
      </div>

      <footer className="sticky bottom-0 shrink-0 border-t border-white/[0.08] bg-slate-900 p-ui-section shadow-[0_-8px_20px_rgba(0,0,0,0.18)]">
        <div className="mb-3 text-ui-label text-slate-500">
          <span>
            {hasPendingChanges
              ? `当前草稿 · ${activeConditions} 条，运行后应用`
              : `已应用 ${activeConditions} 条条件（全部为 AND）`}
          </span>
        </div>
        <div className="grid grid-cols-[88px_minmax(0,1fr)] gap-2">
          <button
            type="button"
            onClick={onReset}
            className="h-10 rounded-md border border-white/[0.12] bg-[#09111f] text-ui-body font-semibold text-slate-300 transition-colors hover:border-white/25 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80"
          >
            重置
          </button>
          <button
            type="button"
            onClick={onRunScreening}
            disabled={screeningLoading}
            className="inline-flex h-10 items-center justify-center gap-2 rounded-md bg-blue-600 px-3 text-ui-body font-semibold text-white transition-colors hover:bg-blue-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/80 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {screeningLoading && (
              <RefreshCw className="h-4 w-4 animate-spin motion-reduce:animate-none" />
            )}
            {screeningLoading
              ? '运行中'
              : isIntradayMode
                ? '开始盘中扫描'
                : '运行筛选'}
          </button>
        </div>
      </footer>
    </div>
  );
}
