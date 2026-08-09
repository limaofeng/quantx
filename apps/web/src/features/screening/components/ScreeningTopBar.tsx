import {
  AlertTriangle,
  Check,
  ExternalLink,
  RefreshCw,
  Search,
  Settings2,
  Target,
  X,
} from 'lucide-react';
import React, { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { ScrollArea } from '@/components/ui/scroll-area';

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
}

const STRATEGIES = [
  { id: 'enableOversoldRebound', label: '超跌反弹' },
  { id: 'enableStrongTrend', label: '强势股' },
  { id: 'enableKDJGoldenCross', label: 'KDJ金叉' },
  { id: 'enableVolumeBreakout', label: '放量突破' },
  { id: 'enableMACrossover', label: '均线金叉' },
  { id: 'enableBollingerLowerRebound', label: '布林下轨反弹' },
  { id: 'enableBollingerUpperBreakout', label: '布林上轨突破' },
  { id: 'enableRSIOversold', label: 'RSI超卖' },
  { id: 'enableRSIStrong', label: 'RSI强势' },
] as const;

const UNIVERSE_OPTIONS: Array<{ label: string; value: StockScreenUniverse }> = [
  { label: '股票', value: 'STOCK' },
  { label: 'ETF', value: 'ETF' },
  { label: '股票+ETF', value: 'STOCK_AND_ETF' },
];

const MODE_OPTIONS: Array<{ label: string; value: ScreeningMode }> = [
  { label: '日级', value: 'DAILY' },
  { label: '盘中', value: 'INTRADAY' },
];

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
}: ScreeningTopBarProps) {
  const [industrySearch, setIndustrySearch] = useState('');
  const screeningMode = screeningCriteria.screeningMode ?? 'DAILY';
  const isIntradayMode = screeningMode === 'INTRADAY';
  const universe = screeningCriteria.universe ?? 'STOCK';
  const stockUniverseEnabled = universe !== 'ETF';
  const stockOnlyFiltersEnabled = universe === 'STOCK';
  const excludeST = screeningCriteria.excludeST !== false;
  const latestRunFailed = ['failed', 'partial_failure'].includes(
    (meta.latestRunStatus || '').toLowerCase()
  );
  const missingSnapshotCount = meta.missingSnapshotDates.length;
  const snapshotStateLabel = isIntradayMode
    ? meta.calculatedAt
      ? '盘中扫描'
      : '待接入'
    : snapshotBackfillLoading
      ? '补算中'
      : latestRunFailed
        ? '快照补算失败'
        : meta.isComplete
          ? '最新交易日快照'
          : meta.snapshotDate
            ? `缺少 ${missingSnapshotCount} 个交易日`
            : '尚无快照';
  const snapshotStateClass = isIntradayMode
    ? meta.calculatedAt
      ? 'text-cyan-300'
      : 'text-slate-400'
    : snapshotBackfillLoading
      ? 'text-blue-300'
      : latestRunFailed
        ? 'text-red-300'
        : meta.isComplete
          ? 'text-emerald-400'
          : meta.snapshotDate
            ? 'text-amber-400'
            : 'text-slate-400';
  const snapshotDotClass = isIntradayMode
    ? meta.calculatedAt
      ? 'bg-cyan-400'
      : 'bg-slate-500'
    : snapshotBackfillLoading
      ? 'bg-blue-400'
      : latestRunFailed
        ? 'bg-red-500'
        : meta.isComplete
          ? 'bg-emerald-500'
          : meta.snapshotDate
            ? 'bg-amber-500'
            : 'bg-slate-500';

  // --- Helpers for updating state ---
  const updateCriteria = <K extends keyof ScreeningCriteria>(
    key: K,
    value: ScreeningCriteria[K]
  ) => {
    setScreeningCriteria(prev => ({ ...prev, [key]: value }));
  };

  const updateUniverse = (nextUniverse: StockScreenUniverse) => {
    setScreeningCriteria(prev => {
      const nextCriteria: ScreeningCriteria = {
        ...prev,
        universe: nextUniverse,
      };
      if (nextUniverse !== 'STOCK') {
        nextCriteria.includeIndustries = undefined;
        nextCriteria.excludeIndustries = undefined;
        nextCriteria.minROE = undefined;
        nextCriteria.minNetProfitGrowth = undefined;
        nextCriteria.minYoYGrowth = undefined;
      }
      return nextCriteria;
    });
  };

  const toggleIndustry = (industry: string) => {
    setScreeningCriteria(prev => {
      const current = prev.includeIndustries || [];
      if (current.includes(industry)) {
        return {
          ...prev,
          includeIndustries: current.filter(i => i !== industry),
        };
      } else {
        return { ...prev, includeIndustries: [...current, industry] };
      }
    });
  };

  const removeTag = (key: keyof ScreeningCriteria, industryName?: string) => {
    if (key === 'includeIndustries' && industryName) {
      toggleIndustry(industryName);
    } else {
      updateCriteria(key, undefined);
    }
  };

  // --- Active Tags Rendering ---
  const activeTags: React.ReactNode[] = [];

  if (isIntradayMode) {
    activeTags.push(
      <Badge
        key="screeningMode"
        variant="secondary"
        className="bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
      >
        模式: 盘中
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => updateCriteria('screeningMode', 'DAILY')}
        />
      </Badge>
    );
  }

  if (universe !== 'STOCK') {
    const universeLabel =
      UNIVERSE_OPTIONS.find(option => option.value === universe)?.label ||
      '股票';
    activeTags.push(
      <Badge
        key="universe"
        variant="secondary"
        className="bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
      >
        范围: {universeLabel}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => updateUniverse('STOCK')}
        />
      </Badge>
    );
  }

  if (stockUniverseEnabled && !excludeST) {
    activeTags.push(
      <Badge
        key="includeST"
        variant="secondary"
        className="bg-red-500/10 text-red-300 border border-red-500/25"
      >
        包含 ST
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => updateCriteria('excludeST', true)}
        />
      </Badge>
    );
  }

  if (
    stockOnlyFiltersEnabled &&
    screeningCriteria.minROE &&
    screeningCriteria.minROE > 0
  ) {
    activeTags.push(
      <Badge
        key="minROE"
        variant="secondary"
        className="bg-purple-500/10 text-purple-400 border border-purple-500/20"
      >
        ROE &gt; {screeningCriteria.minROE}%
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('minROE')}
        />
      </Badge>
    );
  }
  if (
    stockOnlyFiltersEnabled &&
    screeningCriteria.minNetProfitGrowth &&
    screeningCriteria.minNetProfitGrowth > 0
  ) {
    activeTags.push(
      <Badge
        key="minNetProfitGrowth"
        variant="secondary"
        className="bg-purple-500/10 text-purple-400 border border-purple-500/20"
      >
        净利单季同比 &gt; {screeningCriteria.minNetProfitGrowth}%
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('minNetProfitGrowth')}
        />
      </Badge>
    );
  }
  if (
    stockOnlyFiltersEnabled &&
    screeningCriteria.minYoYGrowth &&
    screeningCriteria.minYoYGrowth > 0
  ) {
    activeTags.push(
      <Badge
        key="minYoYGrowth"
        variant="secondary"
        className="bg-purple-500/10 text-purple-400 border border-purple-500/20"
      >
        营收单季同比 &gt; {screeningCriteria.minYoYGrowth}%
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('minYoYGrowth')}
        />
      </Badge>
    );
  }

  if (!isIntradayMode && screeningCriteria.requireFresh) {
    activeTags.push(
      <Badge
        key="requireFresh"
        variant="secondary"
        className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
      >
        今日信号
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('requireFresh')}
        />
      </Badge>
    );
  }

  // Technical Params Tags
  if (screeningCriteria.priceDropMin && screeningCriteria.priceDropMin > 0) {
    activeTags.push(
      <Badge
        key="priceDropMin"
        variant="secondary"
        className="bg-amber-500/10 text-amber-400 border border-amber-500/20"
      >
        跌幅最小值 &gt; {screeningCriteria.priceDropMin}%
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('priceDropMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.volumeRatioMin &&
    screeningCriteria.volumeRatioMin > 0
  ) {
    activeTags.push(
      <Badge
        key="volumeRatioMin"
        variant="secondary"
        className="bg-amber-500/10 text-amber-400 border border-amber-500/20"
      >
        量比 &gt; {screeningCriteria.volumeRatioMin}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('volumeRatioMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.volumeRatioMax &&
    screeningCriteria.volumeRatioMax > 0
  ) {
    activeTags.push(
      <Badge
        key="volumeRatioMax"
        variant="secondary"
        className="bg-amber-500/10 text-amber-400 border border-amber-500/20"
      >
        量比 &lt; {screeningCriteria.volumeRatioMax}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('volumeRatioMax')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.volumeRatio5Min &&
    screeningCriteria.volumeRatio5Min > 0
  ) {
    activeTags.push(
      <Badge
        key="volumeRatio5Min"
        variant="secondary"
        className="bg-amber-500/10 text-amber-400 border border-amber-500/20"
      >
        5日量比 &gt; {screeningCriteria.volumeRatio5Min}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('volumeRatio5Min')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.amountRatioMin &&
    screeningCriteria.amountRatioMin > 0
  ) {
    activeTags.push(
      <Badge
        key="amountRatioMin"
        variant="secondary"
        className="bg-amber-500/10 text-amber-400 border border-amber-500/20"
      >
        额比 &gt; {screeningCriteria.amountRatioMin}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('amountRatioMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.turnoverRateMin &&
    screeningCriteria.turnoverRateMin > 0
  ) {
    activeTags.push(
      <Badge
        key="turnoverRateMin"
        variant="secondary"
        className="bg-amber-500/10 text-amber-400 border border-amber-500/20"
      >
        换手 &gt; {screeningCriteria.turnoverRateMin}%
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('turnoverRateMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.intradayVolumePaceMin &&
    screeningCriteria.intradayVolumePaceMin > 0
  ) {
    activeTags.push(
      <Badge
        key="intradayVolumePaceMin"
        variant="secondary"
        className="bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
      >
        盘中量速 &gt; {screeningCriteria.intradayVolumePaceMin}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('intradayVolumePaceMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.intradayAmountPaceMin &&
    screeningCriteria.intradayAmountPaceMin > 0
  ) {
    activeTags.push(
      <Badge
        key="intradayAmountPaceMin"
        variant="secondary"
        className="bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
      >
        盘中额速 &gt; {screeningCriteria.intradayAmountPaceMin}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('intradayAmountPaceMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.intradayLast5mVolumeRatioMin &&
    screeningCriteria.intradayLast5mVolumeRatioMin > 0
  ) {
    activeTags.push(
      <Badge
        key="intradayLast5mVolumeRatioMin"
        variant="secondary"
        className="bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
      >
        5m放量 &gt; {screeningCriteria.intradayLast5mVolumeRatioMin}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('intradayLast5mVolumeRatioMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.intradayTurnoverRateMin &&
    screeningCriteria.intradayTurnoverRateMin > 0
  ) {
    activeTags.push(
      <Badge
        key="intradayTurnoverRateMin"
        variant="secondary"
        className="bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
      >
        盘中换手 &gt; {screeningCriteria.intradayTurnoverRateMin}%
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('intradayTurnoverRateMin')}
        />
      </Badge>
    );
  }
  if (
    screeningCriteria.intradayDepthImbalanceMin &&
    screeningCriteria.intradayDepthImbalanceMin > 0
  ) {
    activeTags.push(
      <Badge
        key="intradayDepthImbalanceMin"
        variant="secondary"
        className="bg-cyan-500/10 text-cyan-300 border border-cyan-500/20"
      >
        买盘失衡 &gt; {screeningCriteria.intradayDepthImbalanceMin}
        <X
          className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
          onClick={() => removeTag('intradayDepthImbalanceMin')}
        />
      </Badge>
    );
  }

  STRATEGIES.forEach(strategy => {
    if (screeningCriteria[strategy.id as keyof ScreeningCriteria]) {
      activeTags.push(
        <Badge
          key={strategy.id}
          variant="secondary"
          className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
        >
          {strategy.label}
          <X
            className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
            onClick={() => removeTag(strategy.id as keyof ScreeningCriteria)}
          />
        </Badge>
      );
    }
  });

  if (stockOnlyFiltersEnabled) {
    screeningCriteria.includeIndustries?.forEach(ind => {
      activeTags.push(
        <Badge
          key={`ind-${ind}`}
          variant="secondary"
          className="bg-blue-500/10 text-blue-400 border border-blue-500/20"
        >
          行业: {ind}
          <X
            className="ml-1 w-3 h-3 cursor-pointer hover:text-white"
            onClick={() => removeTag('includeIndustries', ind)}
          />
        </Badge>
      );
    });
  }

  return (
    <div className="flex flex-col gap-4 p-4 shrink-0 bg-[#0F1729]/80 backdrop-blur-md border-b border-white/5 z-20 shadow-xl">
      <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4">
        {/* Left Side: Filter Buttons */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Identity & Header Combined */}
          <div className="flex items-center gap-4 mr-2">
            <div className="w-10 h-10 rounded-xl bg-purple-600/10 border border-purple-500/20 flex items-center justify-center text-purple-500 shrink-0 shadow-[0_0_15px_rgba(147,51,234,0.2)]">
              <Target size={20} strokeWidth={1.5} />
            </div>
            <div className="space-y-0.5 hidden sm:block">
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-black text-white tracking-tight uppercase italic">
                  量化选股器
                </h1>
                <div className="px-1.5 py-0 rounded text-[9px] font-black uppercase tracking-widest border bg-purple-500/10 border-purple-500/20 text-purple-500">
                  专业版
                </div>
              </div>
              <p className="text-slate-400 text-[10px] font-medium leading-none">
                多因子策略筛选
              </p>
            </div>
          </div>

          <div className="w-[1px] h-6 bg-white/10 mx-2" />

          <div className="flex h-8 items-center rounded-lg border border-white/10 bg-slate-950/50 p-0.5">
            {MODE_OPTIONS.map(option => {
              const isSelected = screeningMode === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => updateCriteria('screeningMode', option.value)}
                  className={`h-6 rounded-md px-2.5 text-[11px] font-medium transition-colors ${
                    isSelected
                      ? option.value === 'INTRADAY'
                        ? 'bg-cyan-500/15 text-cyan-200 shadow-[0_0_10px_rgba(34,211,238,0.12)]'
                        : 'bg-purple-500/15 text-purple-200 shadow-[0_0_10px_rgba(168,85,247,0.12)]'
                      : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>

          <div className="flex h-8 items-center rounded-lg border border-white/10 bg-slate-950/50 p-0.5">
            {UNIVERSE_OPTIONS.map(option => {
              const isSelected = universe === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => updateUniverse(option.value)}
                  className={`h-6 rounded-md px-2.5 text-[11px] font-medium transition-colors ${
                    isSelected
                      ? 'bg-cyan-500/15 text-cyan-200 shadow-[0_0_10px_rgba(34,211,238,0.12)]'
                      : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
                  }`}
                >
                  {option.label}
                </button>
              );
            })}
          </div>

          <label
            className={`flex h-8 items-center gap-2 rounded-lg border px-2.5 text-[11px] font-medium transition-colors ${
              stockUniverseEnabled
                ? 'cursor-pointer border-white/10 bg-slate-900/50 text-slate-300 hover:bg-slate-800 hover:text-white'
                : 'cursor-not-allowed border-white/5 bg-slate-950/40 text-slate-600'
            }`}
          >
            <Checkbox
              checked={excludeST}
              disabled={!stockUniverseEnabled}
              onCheckedChange={checked =>
                updateCriteria('excludeST', Boolean(checked))
              }
              aria-label="排除 ST 股票"
              className="h-3.5 w-3.5 data-[state=checked]:bg-red-500 data-[state=checked]:border-red-500 disabled:opacity-40"
            />
            <span>排除 ST</span>
          </label>

          {/* 1. 行业板块 Popover */}
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                disabled={!stockOnlyFiltersEnabled}
                className="h-8 border-white/10 bg-slate-900/50 hover:bg-slate-800 hover:text-white text-slate-300 disabled:cursor-not-allowed disabled:opacity-40"
              >
                行业分类{' '}
                <span className="ml-2 text-[10px] bg-slate-800 px-1.5 rounded-full">
                  {screeningCriteria.includeIndustries?.length || 0}
                </span>
              </Button>
            </PopoverTrigger>
            <PopoverContent
              className="w-80 p-0 bg-[#0F1729] border-white/10 shadow-2xl"
              align="start"
            >
              <div className="p-3 border-b border-white/10 space-y-3">
                <h4 className="font-medium text-sm text-slate-200">
                  选择包含的行业
                </h4>
                <div className="relative">
                  <Search className="absolute left-2 top-2 h-4 w-4 text-slate-500" />
                  <Input
                    placeholder="搜索行业名称..."
                    value={industrySearch}
                    onChange={e => setIndustrySearch(e.target.value)}
                    className="h-8 pl-8 bg-slate-900/50 border-white/10 text-slate-200"
                  />
                </div>
              </div>
              <ScrollArea className="h-64">
                <div className="p-3 grid grid-cols-2 gap-2">
                  {availableIndustries
                    .filter(ind =>
                      ind.toLowerCase().includes(industrySearch.toLowerCase())
                    )
                    .map(ind => {
                      const isSelected =
                        screeningCriteria.includeIndustries?.includes(ind);
                      return (
                        <div
                          key={ind}
                          className={`flex items-center space-x-2 p-2 rounded-md hover:bg-white/5 cursor-pointer transition-colors ${isSelected ? 'bg-blue-500/10 border border-blue-500/20' : 'border border-transparent'}`}
                          onClick={e => {
                            e.preventDefault();
                            if (!stockOnlyFiltersEnabled) return;
                            toggleIndustry(ind);
                          }}
                        >
                          <Checkbox
                            checked={isSelected}
                            id={`ind-${ind}`}
                            aria-readonly
                            className="data-[state=checked]:bg-blue-500 data-[state=checked]:border-blue-500 pointer-events-none"
                          />
                          <label
                            className="text-xs font-medium leading-none text-slate-300 cursor-pointer flex-1 pointer-events-none"
                            htmlFor={`ind-${ind}`}
                          >
                            {ind}
                          </label>
                        </div>
                      );
                    })}
                  {availableIndustries.filter(ind =>
                    ind.toLowerCase().includes(industrySearch.toLowerCase())
                  ).length === 0 && (
                    <div className="col-span-2 text-center text-xs text-slate-500 py-4">
                      未找到相关行业
                    </div>
                  )}
                </div>
              </ScrollArea>
            </PopoverContent>
          </Popover>

          {/* 2. 基本面参数 Popover */}
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                disabled={!stockOnlyFiltersEnabled}
                className="h-8 border-white/10 bg-slate-900/50 hover:bg-slate-800 hover:text-white text-slate-300 disabled:cursor-not-allowed disabled:opacity-40"
              >
                基本面
              </Button>
            </PopoverTrigger>
            <PopoverContent
              className="w-80 bg-[#0F1729] border-white/10 shadow-2xl"
              align="start"
            >
              <div className="space-y-4">
                <h4 className="font-medium text-sm text-slate-200 border-b border-white/10 pb-2">
                  基本面要求
                </h4>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-1.5 flex flex-col justify-end">
                    <Label className="text-[10px] text-slate-400">
                      最小 ROE (%)
                    </Label>
                    <div className="relative">
                      <Input
                        type="number"
                        value={screeningCriteria.minROE || ''}
                        disabled={!stockOnlyFiltersEnabled}
                        onChange={e =>
                          updateCriteria('minROE', Number(e.target.value))
                        }
                        className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs pr-6"
                        placeholder="例如: 5"
                      />
                      <span className="absolute right-2 top-2 text-xs text-slate-500">
                        %
                      </span>
                    </div>
                  </div>
                  <div className="space-y-1.5 flex flex-col justify-end">
                    <Label className="text-[10px] text-slate-400 leading-tight">
                      最小净利单季同比
                      <br />
                      (YoY %)
                    </Label>
                    <div className="relative">
                      <Input
                        type="number"
                        value={screeningCriteria.minNetProfitGrowth || ''}
                        disabled={!stockOnlyFiltersEnabled}
                        onChange={e =>
                          updateCriteria(
                            'minNetProfitGrowth',
                            Number(e.target.value)
                          )
                        }
                        className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs pr-6"
                        placeholder="例如: 10"
                      />
                      <span className="absolute right-2 top-2 text-xs text-slate-500">
                        %
                      </span>
                    </div>
                  </div>
                  <div className="space-y-1.5 flex flex-col justify-end">
                    <Label className="text-[10px] text-slate-400">
                      最小营收单季同比 (%)
                    </Label>
                    <div className="relative">
                      <Input
                        type="number"
                        value={screeningCriteria.minYoYGrowth || ''}
                        disabled={!stockOnlyFiltersEnabled}
                        onChange={e =>
                          updateCriteria('minYoYGrowth', Number(e.target.value))
                        }
                        className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs pr-6"
                        placeholder="例如: 0"
                      />
                      <span className="absolute right-2 top-2 text-xs text-slate-500">
                        %
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </PopoverContent>
          </Popover>

          {/* 3. 技术参数 Popover */}
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8 border-white/10 bg-slate-900/50 hover:bg-slate-800 hover:text-white text-slate-300"
              >
                技术参数
              </Button>
            </PopoverTrigger>
            <PopoverContent
              className="w-80 bg-[#0F1729] border-white/10 shadow-2xl p-0"
              align="start"
            >
              <div className="p-3 border-b border-white/10">
                <h4 className="font-medium text-sm text-slate-200">
                  量化指标参数设置
                </h4>
              </div>
              <ScrollArea className="h-72 p-3">
                <div className="space-y-4 pr-3">
                  <div className="space-y-3">
                    <h5 className="text-[11px] font-bold text-slate-500 uppercase tracking-widest">
                      基础形态参数
                    </h5>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小跌幅 (Price Drop %)
                        </Label>
                        <Input
                          type="number"
                          value={screeningCriteria.priceDropMin || ''}
                          disabled={isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'priceDropMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 20"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小量比 (Volume Ratio)
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={screeningCriteria.volumeRatioMin || ''}
                          disabled={isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'volumeRatioMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 1.5"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <h5 className="text-[11px] font-bold text-slate-500 uppercase tracking-widest">
                      日级量能
                    </h5>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最大量比
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={screeningCriteria.volumeRatioMax || ''}
                          disabled={isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'volumeRatioMax',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 3"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小5日量比
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={screeningCriteria.volumeRatio5Min || ''}
                          disabled={isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'volumeRatio5Min',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 1.3"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小额比
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={screeningCriteria.amountRatioMin || ''}
                          disabled={isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'amountRatioMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 1.5"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小换手 (%)
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={screeningCriteria.turnoverRateMin || ''}
                          disabled={isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'turnoverRateMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 3"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <h5 className="text-[11px] font-bold text-slate-500 uppercase tracking-widest">
                      盘中量能
                    </h5>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小量速
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={screeningCriteria.intradayVolumePaceMin || ''}
                          disabled={!isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'intradayVolumePaceMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 2"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小额速
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={screeningCriteria.intradayAmountPaceMin || ''}
                          disabled={!isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'intradayAmountPaceMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 2"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小5m放量
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={
                            screeningCriteria.intradayLast5mVolumeRatioMin || ''
                          }
                          disabled={!isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'intradayLast5mVolumeRatioMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 2"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          最小盘中换手
                        </Label>
                        <Input
                          type="number"
                          step="0.1"
                          value={
                            screeningCriteria.intradayTurnoverRateMin || ''
                          }
                          disabled={!isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'intradayTurnoverRateMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 2"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5 col-span-2">
                        <Label className="text-[10px] text-slate-400">
                          最小买盘失衡
                        </Label>
                        <Input
                          type="number"
                          step="0.05"
                          value={
                            screeningCriteria.intradayDepthImbalanceMin || ''
                          }
                          disabled={!isIntradayMode}
                          onChange={e =>
                            updateCriteria(
                              'intradayDepthImbalanceMin',
                              Number(e.target.value)
                            )
                          }
                          placeholder="例如: 0.2"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <h5 className="text-[11px] font-bold text-slate-500 uppercase tracking-widest">
                      移动平均线 (MA)
                    </h5>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          短周期 (Short)
                        </Label>
                        <Input
                          type="number"
                          value={screeningCriteria.maShort || ''}
                          onChange={e =>
                            updateCriteria('maShort', Number(e.target.value))
                          }
                          placeholder="配置项默认: 5"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          长周期 (Long)
                        </Label>
                        <Input
                          type="number"
                          value={screeningCriteria.maLong || ''}
                          onChange={e =>
                            updateCriteria('maLong', Number(e.target.value))
                          }
                          placeholder="配置项默认: 10"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <h5 className="text-[11px] font-bold text-slate-500 uppercase tracking-widest">
                      RSI 相对强弱指标
                    </h5>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5 col-span-2">
                        <Label className="text-[10px] text-slate-400">
                          周期 (Period)
                        </Label>
                        <Input
                          type="number"
                          value={screeningCriteria.rsiPeriod || ''}
                          onChange={e =>
                            updateCriteria('rsiPeriod', Number(e.target.value))
                          }
                          placeholder="配置项默认: 12"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          超卖阈值 (Oversold)
                        </Label>
                        <Input
                          type="number"
                          value={screeningCriteria.rsiOversoldThreshold || ''}
                          onChange={e =>
                            updateCriteria(
                              'rsiOversoldThreshold',
                              Number(e.target.value)
                            )
                          }
                          placeholder="默认: 30"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          强势阈值 (Strong)
                        </Label>
                        <Input
                          type="number"
                          value={screeningCriteria.rsiStrongThreshold || ''}
                          onChange={e =>
                            updateCriteria(
                              'rsiStrongThreshold',
                              Number(e.target.value)
                            )
                          }
                          placeholder="默认: 70"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                    </div>
                  </div>

                  <div className="space-y-3">
                    <h5 className="text-[11px] font-bold text-slate-500 uppercase tracking-widest">
                      布林带 (Bollinger)
                    </h5>
                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          下轨接近乘数
                        </Label>
                        <Input
                          type="number"
                          step="0.01"
                          value={
                            screeningCriteria.bollingerLowerProximity || ''
                          }
                          onChange={e =>
                            updateCriteria(
                              'bollingerLowerProximity',
                              Number(e.target.value)
                            )
                          }
                          placeholder="配置项默认: 1.0"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                      <div className="space-y-1.5">
                        <Label className="text-[10px] text-slate-400">
                          上轨接近乘数
                        </Label>
                        <Input
                          type="number"
                          step="0.01"
                          value={
                            screeningCriteria.bollingerUpperProximity || ''
                          }
                          onChange={e =>
                            updateCriteria(
                              'bollingerUpperProximity',
                              Number(e.target.value)
                            )
                          }
                          placeholder="配置项默认: 0.95"
                          className="h-8 bg-slate-900/50 border-white/10 text-slate-200 text-xs"
                        />
                      </div>
                    </div>
                  </div>
                </div>
              </ScrollArea>
            </PopoverContent>
          </Popover>

          {/* 4. 量化信号 Popover */}
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8 border-violet-500/30 bg-violet-500/10 hover:bg-violet-500/20 text-violet-300"
              >
                量化信号{' '}
                <span className="ml-2 w-1.5 h-1.5 rounded-full bg-violet-400 animate-pulse"></span>
              </Button>
            </PopoverTrigger>
            <PopoverContent
              className="w-[32rem] bg-[#0F1729] border-violet-500/20 shadow-2xl"
              align="start"
            >
              <div className="space-y-4">
                <h4 className="font-medium text-sm text-violet-300 border-b border-white/10 pb-2">
                  技术形态与量化信号组合
                </h4>
                <div className="grid grid-cols-4 gap-2">
                  {STRATEGIES.map(strategy => {
                    const isSelected =
                      !!screeningCriteria[
                        strategy.id as keyof ScreeningCriteria
                      ];
                    return (
                      <div
                        key={strategy.id}
                        className={`flex p-2 rounded-lg cursor-pointer transition-all border ${
                          isSelected
                            ? 'bg-violet-500/20 border-violet-500/50 shadow-[0_0_10px_rgba(139,92,246,0.1)]'
                            : 'bg-slate-900/50 border-white/5 hover:border-white/20 hover:bg-slate-800'
                        }`}
                        onClick={() =>
                          updateCriteria(
                            strategy.id as keyof ScreeningCriteria,
                            !isSelected
                          )
                        }
                      >
                        <div className="flex flex-1 items-center justify-between min-h-[16px]">
                          <span
                            className={`text-[10px] font-medium leading-none flex-1 pr-1 ${isSelected ? 'text-violet-300' : 'text-slate-400'}`}
                          >
                            {strategy.label}
                          </span>
                          {isSelected ? (
                            <Check
                              size={12}
                              className="text-violet-400 shrink-0"
                            />
                          ) : (
                            <div className="w-3 h-3 shrink-0" />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            </PopoverContent>
          </Popover>
        </div>

        {/* Right Side: Run Button & Market Status */}
        <div className="flex items-center gap-3">
          <div
            className="hidden lg:flex flex-col items-end border-r border-white/5 pr-4"
            aria-live="polite"
          >
            <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider">
              数据状态
            </span>
            <div className="flex items-center gap-2">
              <div
                className={`w-1.5 h-1.5 rounded-full ${snapshotDotClass}`}
              ></div>
              <span
                className={`text-[11px] font-mono font-bold ${snapshotStateClass}`}
              >
                {snapshotStateLabel}
              </span>
            </div>
            {!isIntradayMode && snapshotRunState && (
              <span className="mt-0.5 font-mono text-[9px] text-slate-500">
                Prefect · {snapshotRunState}
              </span>
            )}
          </div>

          <div className="hidden 2xl:grid grid-cols-2 gap-x-4 gap-y-1 border-r border-white/5 pr-4 text-right">
            <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider">
              信号日期
            </span>
            <span className="text-[10px] font-mono text-slate-300">
              {meta.snapshotDate || '--'}
            </span>
            <span className="text-[9px] font-bold text-slate-500 uppercase tracking-wider">
              计算状态
            </span>
            <span
              className={`text-[10px] font-mono font-bold ${meta.hasStaleData ? 'text-amber-400' : 'text-emerald-400'}`}
            >
              {meta.hasStaleData
                ? '最近快照'
                : meta.isComplete
                  ? '已完成'
                  : '待计算'}
            </span>
          </div>

          {!isIntradayMode && (
            <div className="hidden xl:flex items-center gap-1.5">
              {!meta.isComplete && (
                <Button
                  type="button"
                  variant={latestRunFailed ? 'destructive' : 'outline'}
                  size="sm"
                  onClick={onBackfillSnapshot}
                  disabled={snapshotBackfillLoading}
                  className="h-8 gap-1.5 border-amber-500/30 bg-amber-500/10 px-3 text-[10px] font-black text-amber-200 transition-colors hover:bg-amber-500/20 focus-visible:ring-2 focus-visible:ring-amber-400/70 disabled:cursor-not-allowed"
                >
                  {snapshotBackfillLoading ? (
                    <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                  ) : latestRunFailed ? (
                    <AlertTriangle className="h-3.5 w-3.5" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                  {snapshotBackfillLoading
                    ? '补算中'
                    : meta.snapshotDate
                      ? '立即补算'
                      : '生成最新快照'}
                </Button>
              )}
              {latestRunFailed && onOpenSnapshotRun && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={onOpenSnapshotRun}
                  className="h-8 gap-1 px-2 text-[10px] font-bold text-red-300 focus-visible:ring-2 focus-visible:ring-red-400/70"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  查看日志
                </Button>
              )}
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={onOpenAdvancedData}
                className="h-8 gap-1 px-2 text-[10px] font-bold text-slate-400 focus-visible:ring-2 focus-visible:ring-blue-400/70"
              >
                <Settings2 className="h-3.5 w-3.5" />
                高级补数
              </Button>
            </div>
          )}

          {!isIntradayMode && (
            <label className="hidden lg:flex items-center gap-2 text-[11px] text-slate-400 select-none">
              <Checkbox
                checked={Boolean(screeningCriteria.requireFresh)}
                onCheckedChange={checked =>
                  updateCriteria('requireFresh', Boolean(checked))
                }
                className="data-[state=checked]:bg-emerald-500 data-[state=checked]:border-emerald-500"
              />
              只看今日信号
            </label>
          )}

          <Button
            onClick={onRunScreening}
            disabled={screeningLoading}
            size="sm"
            className="h-9 px-6 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 text-white shadow-[0_0_20px_rgba(147,51,234,0.3)] transition-all font-medium rounded-full"
          >
            {screeningLoading ? (
              <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Search className="mr-2 h-4 w-4" />
            )}
            {isIntradayMode ? '开始盘中扫描' : '开始智能选股'}
          </Button>
        </div>
      </div>

      {/* Active Tags Area */}
      <div className="flex flex-wrap items-center gap-2 min-h-[24px]">
        {activeTags.length > 0 ? (
          <>
            <span className="text-[11px] font-mono text-slate-500 mr-1 uppercase tracking-wider">
              Active:
            </span>
            {activeTags}
            <Button
              variant="ghost"
              size="sm"
              className="h-5 px-2 text-[10px] text-slate-500 hover:text-white hover:bg-white/5 ml-2"
              onClick={onReset}
            >
              清除全部
            </Button>
          </>
        ) : (
          <span className="text-[11px] font-mono text-slate-600 italic">
            尚未添加任何高级过滤条件
          </span>
        )}
      </div>
    </div>
  );
}
