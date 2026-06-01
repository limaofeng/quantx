import {
  ArrowLeft,
  Shield,
  BarChart3,
  Zap,
  TestTube,
  LineChart,
  Rocket,
  LayoutTemplate,
  Check,
  AlertCircle,
} from 'lucide-react';
import { useState, useEffect, useMemo, useRef } from 'react';
import type { DateRange } from 'react-day-picker';
import { useQuery, useMutation } from 'urql';
import { useLocation, useParams } from 'wouter';

import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { logger } from '@/core/errors/logger';
import {
  StrategyDocument,
  StrategyRunMode,
  StrategiesDocument,
  type Strategy,
} from '@/generated/gql/graphql';
import {
  getCategoryName,
  getRiskLevelName,
} from '@/shared/utils/strategyHelpers';
import { cn } from '@/utils/cn';

import { ProfessionalBackground } from '../components/ProfessionalBackground';
import { getStrategyConfigPanel } from '../components/strategyRegistry';
import { parseSingleInstrumentCode } from '../domain';
import { CreateStrategyInstanceMutation } from '../hooks/strategyInstanceOperations';
import { type StrategyConfigValue } from '../hooks/types';

const STRATEGY_RUN_DRAFT_KEY = 'quantx.strategyRunDraft';

interface StrategyRunDraft {
  strategyId?: number;
  displayName?: string;
  instrumentCode?: string;
  parameters?: Record<string, StrategyConfigValue>;
  mode?: StrategyRunMode;
  startTime?: string;
  endTime?: string;
}

function readStrategyRunDraft(strategyId: number): StrategyRunDraft | null {
  try {
    const raw = sessionStorage.getItem(STRATEGY_RUN_DRAFT_KEY);
    if (!raw) return null;
    const draft = JSON.parse(raw) as StrategyRunDraft;
    if (draft.strategyId && draft.strategyId !== strategyId) return null;
    sessionStorage.removeItem(STRATEGY_RUN_DRAFT_KEY);
    return draft;
  } catch {
    return null;
  }
}

export default function StrategyRunPage() {
  const [location, setLocation] = useLocation();
  const params = useParams();
  const searchParams = new URLSearchParams(window.location.search);
  const strategyIdParam =
    searchParams.get('strategyId') || params.strategyId || null;

  const [selectedId, setSelectedId] = useState<number | null>(
    strategyIdParam ? parseInt(strategyIdParam, 10) : null
  );

  const [strategyName, setStrategyName] = useState('');
  const [stockCodes, setStockCodes] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [openSelector, setOpenSelector] = useState(false);
  const [strategyConfig, setStrategyConfig] = useState<
    Record<string, StrategyConfigValue>
  >({});
  const [runMode, setRunMode] = useState<StrategyRunMode>(
    StrategyRunMode.Paper
  );
  const appliedDraftRef = useRef<StrategyRunDraft | null>(null);

  const [dateRange, setDateRange] = useState<DateRange | undefined>();

  // 切换到回测模式时，如果未设置日期范围，则默认往前 2 个月
  useEffect(() => {
    if (runMode === StrategyRunMode.Backtest && !dateRange) {
      const now = new Date();
      const twoMonthsAgo = new Date(now);
      twoMonthsAgo.setMonth(twoMonthsAgo.getMonth() - 2);
      setDateRange({ from: twoMonthsAgo, to: now });
    }
  }, [runMode]);

  // 获取所有可用策略列表
  const [{ data: listData }] = useQuery({
    query: StrategiesDocument,
  });

  const availableStrategies = useMemo(
    () => listData?.strategies || [],
    [listData]
  );

  // 获取选中策略模版详情
  const [{ data: strategyData, fetching: strategyLoading, error }] = useQuery({
    query: StrategyDocument,
    variables: { strategyId: selectedId as number },
    pause: !selectedId,
  });

  const [, createStrategyInstance] = useMutation(
    CreateStrategyInstanceMutation
  );
  const strategy = strategyData?.strategy;

  // 当路由 ID 改变时同步内部状态
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('strategyId') || strategyIdParam;
    if (id) {
      setSelectedId(parseInt(id, 10));
    }
  }, [location, strategyIdParam]);

  // 当进入页面且没有选择策略时，自动选择列表中第一个
  useEffect(() => {
    if (availableStrategies.length > 0 && !selectedId) {
      // 如果有 query param 但非法，或者没有
      const params = new URLSearchParams(window.location.search);
      const idParam = params.get('strategyId');

      if (!idParam) {
        const firstId = availableStrategies[0].id;
        setSelectedId(firstId);
        // setLocation(`/strategies/${firstId}/run`, { replace: true }); // 可选：是否强制同步 URL
      }
    }
  }, [availableStrategies, selectedId]);

  // 初始化或切换策略时重置表单
  useEffect(() => {
    if (strategy) {
      const defaultParams =
        typeof strategy.defaultParameters === 'string'
          ? JSON.parse(strategy.defaultParameters)
          : strategy.defaultParameters;
      const draft =
        appliedDraftRef.current?.strategyId === strategy.id
          ? appliedDraftRef.current
          : readStrategyRunDraft(strategy.id);
      if (draft) {
        appliedDraftRef.current = draft;
      }
      const mergedParams = {
        ...(defaultParams || {}),
        ...(draft?.parameters || {}),
      };

      setStrategyName(
        draft?.displayName ||
          `${strategy.name} - ${new Date().toLocaleDateString()}`
      );

      const defaultInstrument =
        draft?.instrumentCode ||
        mergedParams?.instrument_code ||
        mergedParams?.instrumentCode ||
        mergedParams?.symbol ||
        (Array.isArray(mergedParams?.stockCodes)
          ? mergedParams.stockCodes[0]
          : '');
      setStockCodes(defaultInstrument ? String(defaultInstrument) : '');
      setStrategyConfig(mergedParams || {});
      if (draft?.mode) {
        setRunMode(draft.mode);
      }
      if (draft?.startTime && draft?.endTime) {
        setDateRange({
          from: new Date(draft.startTime),
          to: new Date(draft.endTime),
        });
      } else if (draft?.mode === StrategyRunMode.Backtest) {
        setDateRange(undefined);
        setFormError('原回测缺少时间区间，请补齐回测区间后再启动。');
      } else {
        setFormError(null);
      }
    } else if (!selectedId) {
      // 未选择策略时清空表单
      setStrategyName('');
      setStockCodes('');
      setStrategyConfig({});
    }
  }, [strategy, selectedId]);

  const handleStrategyChange = (value: string | number) => {
    const newId = typeof value === 'string' ? parseInt(value, 10) : value;
    setSelectedId(newId);
    // URL 保持不变，只更新内部状态
  };

  // 通用保存函数
  const saveStrategy = async (autoStart: boolean) => {
    if (!strategyName.trim() || !stockCodes.trim() || !strategy) {
      setFormError('请填写实例名称并绑定一个 A 股标的。');
      return;
    }

    try {
      const parsedInstrument = parseSingleInstrumentCode(stockCodes);
      if (!parsedInstrument.instrumentCode) {
        setFormError('请绑定一个 A 股标的。');
        return;
      }
      if (parsedInstrument.hasMultiple) {
        setFormError('新的 A 股策略实例只能绑定一个标的；换股请复制为新实例。');
        return;
      }

      const parameters = {
        ...strategyConfig,
        instrument_code: parsedInstrument.instrumentCode,
        stockCodes: [parsedInstrument.instrumentCode],
      };
      const mode = runMode;

      const input: any = {
        strategyKey: strategy.name,
        displayName: strategyName,
        instrumentCode: parsedInstrument.instrumentCode,
        parameters,
        mode,
      };

      if (mode === StrategyRunMode.Backtest) {
        if (!dateRange || !dateRange.from || !dateRange.to) {
          throw new Error('回测模式必须指定完整的开始和结束时间');
        }
        input.startTime = dateRange.from.toISOString();
        input.endTime = dateRange.to.toISOString();
      }

      const result = await createStrategyInstance({
        input,
        autoStart,
      });

      if (result.error) {
        throw new Error(result.error.message);
      }

      logger.info('Strategy run created:', {
        id: (result.data as any)?.createStrategyInstance?.id,
        autoStart,
      });
      setFormError(null);
      setLocation('/strategies');
    } catch (e) {
      logger.error('Failed to save strategy:', { error: e });
    }
  };

  // 仅保存配置（不启动）
  const handleSave = async () => {
    await saveStrategy(false);
  };

  // 保存并运行
  const handleSaveAndRun = async () => {
    await saveStrategy(true);
  };

  // 只在首次加载且无数据时显示全屏加载，切换策略时不显示以避免闪烁
  if (strategyLoading && !strategy) {
    return (
      <div className="p-12 text-center text-muted-foreground font-mono text-[10px] uppercase tracking-widest animate-pulse">
        正在加载策略引擎...
      </div>
    );
  }

  if (error || (selectedId && !strategyLoading && !strategy)) {
    return (
      <div className="p-12 text-center">
        <Card className="p-8 border-rose-500/20 bg-rose-500/5 rounded-[2rem]">
          <p className="text-rose-500 font-black text-[10px] uppercase tracking-widest">
            {error ? `错误: ${error.message}` : '策略不存在或已被移除'}
          </p>
        </Card>
      </div>
    );
  }

  const ConfigPanel = strategy ? getStrategyConfigPanel(strategy.name) : null;

  return (
    <div className="max-w-6xl mx-auto space-y-8 pb-24">
      {/* Premium Back & Header */}
      <div className="flex flex-col gap-6">
        {/* Unified Header Card */}
        <div className="rounded-[2rem] bg-[#0F1729] border border-white/5 p-8 relative overflow-hidden shadow-2xl">
          <ProfessionalBackground />

          <div className="relative z-10 flex flex-col gap-6">
            {/* Top Row: Meta & Switcher */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Button
                  variant="ghost"
                  className="group h-8 px-3 -ml-3 rounded-full text-[11px] font-bold text-slate-500 hover:text-blue-400 hover:bg-blue-500/10 uppercase tracking-wider gap-2 transition-all duration-300"
                  onClick={() => setLocation('/strategies')}
                >
                  <ArrowLeft className="h-3.5 w-3.5 transition-transform duration-300 group-hover:-translate-x-1" />
                  返回控制中心
                </Button>
              </div>

              {/* Creative Strategy Switcher */}
              <Popover open={openSelector} onOpenChange={setOpenSelector}>
                <PopoverTrigger asChild>
                  <Button
                    variant="ghost"
                    className="h-8 px-3 text-[10px] font-bold text-slate-500 hover:text-white hover:bg-white/5 rounded-full uppercase tracking-wider gap-2 border border-transparent hover:border-white/10 transition-all shadow-sm"
                  >
                    <span>{strategy ? '切换模版' : '选择策略'}</span>
                    <LayoutTemplate size={14} />
                  </Button>
                </PopoverTrigger>
                <PopoverContent
                  align="end"
                  className="w-[300px] p-0 bg-[#0B1121] border-blue-900/30 rounded-2xl overflow-hidden shadow-2xl shadow-black/50"
                >
                  <Command className="bg-transparent text-slate-200">
                    <CommandInput
                      placeholder="搜索策略..."
                      className="h-12 border-b border-white/5 text-xs font-bold"
                    />
                    <CommandEmpty className="py-6 text-center text-xs text-slate-500 font-bold uppercase tracking-wider">
                      暂无相关策略
                    </CommandEmpty>
                    <CommandList className="p-1">
                      <CommandGroup>
                        {availableStrategies?.map(s => (
                          <CommandItem
                            key={s.id}
                            value={s.name}
                            onSelect={() => {
                              handleStrategyChange(s.id);
                              setOpenSelector(false);
                            }}
                            className="text-xs font-bold py-3 px-3 rounded-lg aria-selected:bg-blue-600/10 aria-selected:text-blue-400 cursor-pointer"
                          >
                            <Check
                              className={cn(
                                'mr-2 h-3 w-3',
                                selectedId === s.id
                                  ? 'opacity-100 text-blue-500'
                                  : 'opacity-0'
                              )}
                            />
                            <div className="flex flex-col">
                              <span>{s.name}</span>
                              <span className="text-[10px] text-slate-500 font-normal mt-0.5">
                                {s.category}
                              </span>
                            </div>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    </CommandList>
                  </Command>
                </PopoverContent>
              </Popover>
            </div>

            {strategy ? (
              /* Content Grid */
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 lg:gap-8 items-start">
                {/* Left: Strategy Info (7 cols) */}
                <div className="lg:col-span-7 space-y-6 h-full flex flex-col justify-between">
                  <div className="space-y-3">
                    <div className="flex items-center gap-2">
                      <div className="w-1.5 h-1.5 rounded-full bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]" />
                      <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-blue-400">
                        策略配置
                      </span>
                    </div>
                    <h1 className="text-3xl md:text-4xl font-black text-white uppercase tracking-tight leading-none italic">
                      {strategy.name}{' '}
                      <span className="text-slate-500 not-italic">策略</span>
                    </h1>
                    <p className="text-xs text-slate-400 font-medium leading-relaxed max-w-xl border-l-2 border-slate-800 pl-3">
                      {strategy.description}
                    </p>
                  </div>

                  <div className="flex flex-wrap gap-3">
                    <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800/50 border border-slate-700/50">
                      <Shield size={14} className="text-blue-400" />
                      <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                        {getRiskLevelName(
                          strategy.riskLevel as Parameters<
                            typeof getRiskLevelName
                          >[0]
                        )}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800/50 border border-slate-700/50">
                      <BarChart3 size={14} className="text-indigo-400" />
                      <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                        {getCategoryName(
                          strategy.category as Parameters<
                            typeof getCategoryName
                          >[0]
                        )}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800/50 border border-slate-700/50">
                      <Zap size={14} className="text-emerald-400" />
                      <span className="text-xs font-bold text-slate-300 uppercase tracking-wider">
                        Python / 异步引擎
                      </span>
                    </div>
                  </div>
                </div>

                {/* Vertical Divider (Desktop Only) */}
                <div className="hidden lg:block w-px h-48 bg-gradient-to-b from-transparent via-white/5 to-transparent lg:col-span-1 mx-auto" />

                {/* Right: Integrated Basic Settings (4 cols) */}
                <div className="lg:col-span-4 space-y-4">
                  <div className="space-y-2">
                    <Label className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                      策略实例名称
                    </Label>
                    <div className="relative group">
                      <Input
                        value={strategyName}
                        onChange={e => setStrategyName(e.target.value)}
                        className="h-[40px] bg-transparent border-0 border-b border-white/10 rounded-none px-0 text-lg font-bold text-white placeholder:text-slate-600 focus-visible:ring-0 focus-visible:border-blue-500 transition-all py-2"
                        placeholder="为本次运行命名..."
                      />
                      <div className="absolute bottom-0 left-0 w-0 h-px bg-blue-500 group-hover:w-full transition-all duration-300" />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                      运行模式
                    </Label>
                    <div className="grid grid-cols-3 gap-3">
                      {[
                        {
                          mode: StrategyRunMode.Backtest,
                          label: '回测',
                          icon: LineChart,
                          color: 'blue',
                        },
                        {
                          mode: StrategyRunMode.Paper,
                          label: '模拟',
                          icon: TestTube,
                          color: 'emerald',
                        },
                        {
                          mode: StrategyRunMode.Live,
                          label: '实盘',
                          icon: Rocket,
                          color: 'rose',
                        },
                      ].map(({ mode, label, icon: Icon, color }) => (
                        <div
                          key={mode}
                          onClick={() => setRunMode(mode)}
                          className={cn(
                            'cursor-pointer group relative overflow-hidden rounded-[1.2rem] border p-3.5 flex flex-col items-center justify-center gap-2 transition-all duration-300',
                            runMode === mode
                              ? `bg-${color}-500 border-${color}-400 shadow-xl`
                              : 'bg-[#0F172A]/80 border-white/5 hover:border-white/10 hover:bg-slate-900'
                          )}
                        >
                          {/* Active State Indicator Dot */}
                          {runMode === mode && (
                            <div className="absolute top-2.5 right-2.5 w-1.2 h-1.2 bg-white rounded-full transition-opacity duration-300" />
                          )}

                          {/* Icon */}
                          <Icon
                            size={18}
                            strokeWidth={2.5}
                            className={cn(
                              'transition-all duration-300',
                              runMode === mode
                                ? 'text-white scale-110'
                                : 'text-slate-500 group-hover:text-slate-300'
                            )}
                          />

                          {/* Text */}
                          <div className="relative z-10 text-center">
                            <span
                              className={cn(
                                'block text-[9px] font-black uppercase tracking-[0.15em] transition-colors duration-300',
                                runMode === mode
                                  ? 'text-white'
                                  : 'text-slate-500 group-hover:text-slate-300'
                              )}
                            >
                              {label}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>

                  {runMode === StrategyRunMode.Backtest && (
                    <div className="mt-4 animate-in fade-in slide-in-from-top-2 duration-300 space-y-2">
                      <Label className="text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                        回测区间
                      </Label>
                      <DateRangePicker
                        value={dateRange}
                        onChange={setDateRange}
                        buttonClassName="h-[40px] bg-slate-900/50 border-white/10 text-white font-mono text-xs focus-visible:ring-1 focus-visible:ring-blue-500 rounded-xl px-3"
                      />
                    </div>
                  )}

                  {formError && (
                    <div className="flex items-start gap-2 rounded-xl border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-[10px] font-bold leading-relaxed text-rose-300">
                      <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      {formError}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              /* Empty State */
              <div className="py-20 text-center space-y-6">
                <h1 className="text-4xl md:text-5xl font-black text-white tracking-tighter italic">
                  初始化
                  <span className="text-blue-500 not-italic">策略实例</span>
                </h1>
                <p className="text-sm text-slate-400 font-medium max-w-lg mx-auto leading-relaxed">
                  请点击右上角的“选择策略模版”按钮，从策略库中加载算法交易方案以开始配置。
                </p>
              </div>
            )}
          </div>
        </div>

        {/* 2. Strategy Specific Config Panel (Full Width) */}
        {strategy && (
          <div className="animate-in fade-in slide-in-from-bottom-4 duration-700">
            {ConfigPanel && (
              <ConfigPanel
                strategy={strategy as Strategy}
                strategyName={strategyName}
                setStrategyName={setStrategyName}
                stockCodes={stockCodes}
                setStockCodes={setStockCodes}
                strategyConfig={strategyConfig}
                setStrategyConfig={setStrategyConfig}
                runMode={runMode}
                setRunMode={setRunMode}
                onSubmit={handleSaveAndRun}
                onSave={handleSave}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}
