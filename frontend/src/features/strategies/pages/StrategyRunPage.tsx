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
import {
  StrategyStudioShell,
  type StrategyStudioMode,
} from '../components/StrategyStudioShell';
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

  const renderStudioMessage = (message: string, tone: 'rose' | 'slate') => (
    <StrategyStudioShell
      activeMode="CONFIG"
      className="h-full min-h-0"
      content={
        <div className="flex h-full items-center justify-center bg-[#08101d] p-6">
          <div
            className={cn(
              'rounded-lg border px-5 py-4 text-center text-[11px] font-black uppercase tracking-widest',
              tone === 'rose'
                ? 'border-rose-500/20 bg-rose-500/10 text-rose-300'
                : 'border-white/10 bg-white/[0.03] text-slate-400'
            )}
          >
            {message}
          </div>
        </div>
      }
      onModeChange={mode => {
        if (mode === 'RUNS' || mode === 'CATALOG') setLocation('/strategies');
      }}
      showSidebar={false}
      statusBarLeft={<span>新建策略实例</span>}
      statusBarRight={<span>{strategyIdParam || selectedId || '-'}</span>}
      tabBar={
        <div className="flex h-10 shrink-0 items-center border-b border-white/5 bg-[#0b1120]/80 px-3 text-[11px] font-black uppercase tracking-[0.2em] text-slate-300">
          QuantX Strategy Studio
        </div>
      }
    />
  );

  // 只在首次加载且无数据时显示全屏加载，切换策略时不显示以避免闪烁
  if (strategyLoading && !strategy) {
    return renderStudioMessage('正在加载策略引擎...', 'slate');
  }

  if (error || (selectedId && !strategyLoading && !strategy)) {
    return renderStudioMessage(
      error ? `错误: ${error.message}` : '策略不存在或已被移除',
      'rose'
    );
  }

  const ConfigPanel = strategy ? getStrategyConfigPanel(strategy.name) : null;
  const activeStudioMode: StrategyStudioMode =
    runMode === StrategyRunMode.Backtest ? 'BACKTEST' : 'CONFIG';
  const handleStudioModeChange = (mode: StrategyStudioMode) => {
    if (mode === 'RUNS' || mode === 'CATALOG') {
      setLocation('/strategies');
      return;
    }

    if (mode === 'BACKTEST') {
      setRunMode(StrategyRunMode.Backtest);
      return;
    }

    if (mode === 'MONITOR' || mode === 'TRACE') {
      setLocation('/strategies');
      return;
    }

    setRunMode(StrategyRunMode.Paper);
  };

  const createSidebar = (
    <aside className="flex h-full min-h-0 flex-col">
      <div className="border-b border-white/5 px-4 py-3">
        <button
          type="button"
          onClick={() => setLocation('/strategies')}
          className="mb-3 inline-flex h-7 items-center gap-2 rounded-md border border-white/10 px-2 text-[10px] font-bold uppercase tracking-wider text-slate-400 transition-colors hover:border-red-500/40 hover:text-red-300"
        >
          <ArrowLeft className="h-3 w-3" />
          策略看板
        </button>
        <div className="text-[10px] font-black uppercase tracking-[0.24em] text-red-400">
          Create Strategy
        </div>
        <h2 className="mt-1 truncate text-sm font-bold text-slate-100">
          {strategy?.name || '选择策略模板'}
        </h2>
        <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
          绑定单一 A 股标的，配置参数后保存为运行实例。
        </p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2 custom-scrollbar">
        <div className="mb-2 px-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
          Templates
        </div>
        <div className="space-y-1">
          {availableStrategies.map(template => {
            const isActive = selectedId === template.id;

            return (
              <button
                key={template.id}
                type="button"
                onClick={() => handleStrategyChange(template.id)}
                className={cn(
                  'flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70',
                  isActive
                    ? 'border-red-500/30 bg-red-500/10 text-red-100'
                    : 'border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.04] hover:text-slate-200'
                )}
              >
                <LayoutTemplate className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-bold">
                    {template.name}
                  </span>
                  <span className="block truncate text-[10px] text-slate-600">
                    {template.category || 'strategy'}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="border-t border-white/5 p-3">
        <div className="grid grid-cols-3 gap-1 rounded-md border border-white/10 bg-white/[0.03] p-1">
          {[
            { mode: StrategyRunMode.Backtest, label: '回测' },
            { mode: StrategyRunMode.Paper, label: '模拟' },
            { mode: StrategyRunMode.Live, label: '实盘' },
          ].map(item => (
            <button
              key={item.mode}
              type="button"
              onClick={() => setRunMode(item.mode)}
              className={cn(
                'h-7 rounded text-[10px] font-black uppercase tracking-wider transition-colors',
                runMode === item.mode
                  ? 'bg-red-500 text-white'
                  : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>
    </aside>
  );

  const createContent = (
    <div className="h-full overflow-y-auto bg-[#08101d] p-3 custom-scrollbar">
      <div className="mx-auto max-w-6xl space-y-6 pb-12">
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
                    className="group h-8 px-3 -ml-3 rounded-full text-[11px] font-bold text-slate-500 hover:text-red-400 hover:bg-red-500/10 uppercase tracking-wider gap-2 transition-all duration-300"
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
                    className="w-[300px] p-0 bg-[#0B1121] border-red-900/30 rounded-2xl overflow-hidden shadow-2xl shadow-black/50"
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
                              className="text-xs font-bold py-3 px-3 rounded-lg aria-selected:bg-red-600/10 aria-selected:text-red-400 cursor-pointer"
                            >
                              <Check
                                className={cn(
                                  'mr-2 h-3 w-3',
                                  selectedId === s.id
                                    ? 'opacity-100 text-red-500'
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
                        <div className="w-1.5 h-1.5 rounded-full bg-red-500 shadow-[0_0_8px_rgba(59,130,246,0.5)]" />
                        <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-red-400">
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
                        <Shield size={14} className="text-red-400" />
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
                          className="h-[40px] bg-transparent border-0 border-b border-white/10 rounded-none px-0 text-lg font-bold text-white placeholder:text-slate-600 focus-visible:ring-0 focus-visible:border-red-500 transition-all py-2"
                          placeholder="为本次运行命名..."
                        />
                        <div className="absolute bottom-0 left-0 w-0 h-px bg-red-500 group-hover:w-full transition-all duration-300" />
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
                            color: 'red',
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
                          buttonClassName="h-[40px] bg-slate-900/50 border-white/10 text-white font-mono text-xs focus-visible:ring-1 focus-visible:ring-red-500 rounded-xl px-3"
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
                    <span className="text-red-500 not-italic">策略实例</span>
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
    </div>
  );

  return (
    <StrategyStudioShell
      activeMode={activeStudioMode}
      className="h-full min-h-0"
      content={createContent}
      onModeChange={handleStudioModeChange}
      sidebar={createSidebar}
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
            新建策略实例
          </span>
          <span className="text-slate-700">|</span>
          <span>{strategy?.name || '等待模板'}</span>
        </>
      }
      statusBarRight={
        <>
          <span>{runMode}</span>
          <span className="text-slate-700">|</span>
          <span>{stockCodes || '未绑定标的'}</span>
        </>
      }
      tabBar={
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120]/80 px-3">
          <div className="flex min-w-0 items-center gap-3">
            <LayoutTemplate className="h-4 w-4 shrink-0 text-red-400" />
            <div className="min-w-0">
              <div className="truncate text-[11px] font-black uppercase tracking-[0.2em] text-slate-200">
                {strategyName || '新建策略实例'}
              </div>
              <div className="truncate text-[10px] font-medium text-slate-600">
                {strategy?.name || '请选择策略模板'}
              </div>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => void handleSave()}
              className="h-7 rounded-md border border-white/10 px-2.5 text-[10px] font-bold uppercase tracking-wider text-slate-400 transition-colors hover:border-red-500/40 hover:text-red-300"
            >
              保存
            </button>
            <button
              type="button"
              onClick={() => void handleSaveAndRun()}
              className="h-7 rounded-md bg-red-500 px-2.5 text-[10px] font-black uppercase tracking-wider text-white transition-colors hover:bg-red-400"
            >
              保存并运行
            </button>
          </div>
        </div>
      }
    />
  );
}
