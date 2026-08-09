import { format } from 'date-fns';
import {
  ChevronLeft,
  ChevronRight,
  Calendar,
  Trash2,
  Loader2,
  Timer,
  Info,
  RefreshCw,
  RotateCcw,
} from 'lucide-react';
import React, { useState, useMemo } from 'react';
import { useMutation, useQuery } from 'urql';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { gql } from '@/generated/gql';
import { useToast } from '@/hooks/use-toast';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { cn } from '@/utils/cn';

import { TaskHistory } from './TaskHistory';

const GET_HOLIDAYS = gql(`
  query GetHolidays($market: String!, $year: Int!) {
    holidays(market: $market, year: $year) {
      items {
        id
        market
        year
        holidayDate
        description
      }
      total
    }
  }
`);

const ADD_HOLIDAY = gql(`
  mutation AddHoliday($market: String!, $holidayDate: Date!, $description: String) {
    addHoliday(market: $market, holidayDate: $holidayDate, description: $description) {
      success
      message
      holiday { id holidayDate description }
    }
  }
`);

const DELETE_HOLIDAY = gql(`
  mutation DeleteHoliday($id: Int!) {
    deleteHoliday(id: $id) {
      success
      message
    }
  }
`);

interface TradingCalendarProps {
  market?: string;
  hideSyncWidget?: boolean;
}

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];
const MONTHS = [
  '一月',
  '二月',
  '三月',
  '四月',
  '五月',
  '六月',
  '七月',
  '八月',
  '九月',
  '十月',
  '十一月',
  '十二月',
];

export function TradingCalendar({
  market = 'SH',
  hideSyncWidget = false,
}: TradingCalendarProps) {
  const { toast } = useToast();
  const [currentDate, setCurrentDate] = useState(new Date());
  const [selectedDate, setSelectedDate] = useState<Date | null>(null);
  const [newDescription, setNewDescription] = useState('');
  const [showHistory, setShowHistory] = useState(false);
  const [showMonthPicker, setShowMonthPicker] = useState(false);
  const [showYearPicker, setShowYearPicker] = useState(false);
  const [highlightedId, setHighlightedId] = useState<number | null>(null);

  const {
    deployment,
    isSyncing,
    triggerSync: handleSync,
  } = useDeploymentSync('holiday-sync', {
    successMessage: '交易日历同步任务已提交',
  });

  const currentYear = currentDate.getFullYear();
  const currentMonth = currentDate.getMonth();

  // Generate year range
  const years = useMemo(() => {
    const y = currentYear;
    const range = [];
    for (let i = y - 5; i <= y + 2; i++) {
      range.push(i);
    }
    return range;
  }, [currentYear]);

  const [{ data }, refetch] = useQuery({
    query: GET_HOLIDAYS,
    variables: { market, year: currentYear },
    requestPolicy: 'network-only',
  });

  const [addResult, addHoliday] = useMutation(ADD_HOLIDAY);
  const [deleteResult, deleteHoliday] = useMutation(DELETE_HOLIDAY);

  const rawHolidays = data?.holidays?.items;
  const holidays = useMemo(
    () => (Array.isArray(rawHolidays) ? rawHolidays : []),
    [rawHolidays]
  );

  // Sort holidays by date for the list
  const sortedHolidays = useMemo(() => {
    return [...holidays].sort((a, b) =>
      a.holidayDate.localeCompare(b.holidayDate)
    );
  }, [holidays]);

  const holidayMap = useMemo(() => {
    const map = new Map<string, { id: number; description: string | null }>();
    holidays.forEach(h => {
      map.set(h.holidayDate, {
        id: h.id,
        description: h.description ?? null,
      });
    });
    return map;
  }, [holidays]);

  const calendarDays = useMemo(() => {
    const firstDay = new Date(currentYear, currentMonth, 1);
    const lastDay = new Date(currentYear, currentMonth + 1, 0);
    const startDay = (firstDay.getDay() + 6) % 7;
    const daysInMonth = lastDay.getDate();
    const days: (Date | null)[] = [];
    for (let i = 0; i < startDay; i++) days.push(null);
    for (let d = 1; d <= daysInMonth; d++)
      days.push(new Date(currentYear, currentMonth, d));
    return days;
  }, [currentYear, currentMonth]);

  const formatDateKey = (date: Date) => format(date, 'yyyy-MM-dd');
  const isWeekend = (date: Date) => {
    const day = date.getDay();
    return day === 0 || day === 6;
  };
  const isHoliday = (date: Date) => holidayMap.has(formatDateKey(date));
  const isToday = (date: Date) =>
    formatDateKey(date) === formatDateKey(new Date());

  const handleDayClick = (date: Date) => {
    const dateKey = formatDateKey(date);
    const holidayInfo = holidayMap.get(dateKey);

    if (holidayInfo) {
      const element = document.getElementById(`holiday-item-${holidayInfo.id}`);
      if (element) {
        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setHighlightedId(holidayInfo.id);
        setTimeout(() => setHighlightedId(null), 2000);
      } else {
        toast({
          title: '提示',
          description: '该节假日未在列表中显示',
          variant: 'destructive',
        });
      }
    } else if (!isWeekend(date)) {
      setSelectedDate(date);
      setNewDescription('');
    }
  };

  const handleAddHoliday = async () => {
    if (!selectedDate) return;
    const result = await addHoliday({
      market,
      holidayDate: formatDateKey(selectedDate),
      description: newDescription || undefined,
    });
    if (result.data?.addHoliday?.success) {
      toast({
        title: '节假日已添加',
        description: result.data.addHoliday.message,
        variant: 'success',
      });
      setSelectedDate(null);
      setNewDescription('');
      refetch({ requestPolicy: 'network-only' });
    }
  };

  const handleDeleteFromList = async (id: number) => {
    const result = await deleteHoliday({ id });
    if (result.data?.deleteHoliday?.success) {
      toast({
        title: '删除成功',
        description: result.data.deleteHoliday.message,
      });
      refetch({ requestPolicy: 'network-only' });
    }
  };

  const calendarStats = useMemo(() => {
    const todayStr = format(new Date(), 'yyyy-MM-dd');
    const futureHolidays = holidays.filter(h => h.holidayDate >= todayStr);
    return [
      {
        label: '全年节假日',
        value: holidays.length,
        detail: `${currentYear}年`,
        icon: Calendar,
        color: 'text-indigo-600 dark:text-indigo-400',
        bg: 'bg-indigo-500/10',
        gradient:
          'from-indigo-50/50 via-white to-white dark:from-indigo-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
      },
      {
        label: '剩余休市',
        value: futureHolidays.length,
        detail: '本年度',
        icon: Timer,
        color: 'text-emerald-600 dark:text-emerald-400',
        bg: 'bg-emerald-500/10',
        gradient:
          'from-emerald-50/50 via-white to-white dark:from-emerald-950/20 dark:via-slate-900/40 dark:to-slate-900/40',
      },
    ];
  }, [holidays, currentYear]);

  return (
    <div className="flex flex-col lg:flex-row gap-4 h-full min-h-0 animate-fade-in relative">
      {/* Left Column: Calendar Main Area */}
      <div className="flex-1 flex flex-col gap-4 min-h-0 min-w-0">
        {/* Stats Row */}
        <div className="grid grid-cols-2 gap-4 shrink-0 h-24">
          {calendarStats.map((stat, i) => (
            <Card
              key={i}
              className={cn(
                'relative overflow-hidden border-slate-200/60 dark:border-white/5 shadow-sm bg-gradient-to-br',
                stat.gradient
              )}
            >
              <div className="absolute top-0 right-0 p-2 opacity-5 pointer-events-none">
                <stat.icon className={cn('w-16 h-16 rotate-12', stat.color)} />
              </div>
              <CardContent className="p-4 h-full flex flex-col justify-center">
                <div className="flex items-center gap-2 mb-1">
                  <div className={cn('p-1 rounded-md', stat.bg)}>
                    <stat.icon className={cn('w-3 h-3', stat.color)} />
                  </div>
                  <span className="text-[10px] font-black text-slate-400 uppercase tracking-widest">
                    {stat.label}
                  </span>
                </div>
                <div className="flex items-baseline gap-2 relative z-10">
                  <span
                    className={cn(
                      'text-3xl font-black tracking-tighter tabular-nums',
                      stat.color
                    )}
                  >
                    {stat.value}
                  </span>
                  <span className="text-[10px] font-bold text-slate-400/80">
                    {stat.detail}
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* Calendar Grid */}
        <Card className="flex-1 border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-2xl rounded-[24px] overflow-hidden flex flex-col shadow-sm">
          <CardHeader className="py-2.5 px-6 border-b border-slate-100/50 dark:border-white/5 shadow-sm z-50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1.5 p-1.5 rounded-lg bg-indigo-50 dark:bg-indigo-900/20 text-indigo-600 dark:text-indigo-400">
                  <Calendar className="w-4 h-4" />
                  <span className="text-xs font-black tracking-tight">
                    月视图
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-3 text-[10px] font-black uppercase tracking-wider text-slate-500 hover:text-indigo-600 hover:bg-indigo-50/50 dark:hover:bg-white/5 rounded-lg transition-all ml-2"
                  onClick={() => setCurrentDate(new Date())}
                >
                  <RotateCcw className="w-3 h-3 mr-1.5" />
                  回到今天
                </Button>
              </div>

              <div className="flex items-center gap-1 bg-slate-100/50 dark:bg-white/5 p-0.5 rounded-lg border border-slate-200/50 dark:border-white/5">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 rounded-md hover:bg-white"
                  onClick={() =>
                    setCurrentDate(new Date(currentYear, currentMonth - 1, 1))
                  }
                >
                  <ChevronLeft className="w-4 h-4" />
                </Button>
                <div className="flex items-center gap-1 mx-2">
                  <button
                    onClick={() => {
                      setShowYearPicker(!showYearPicker);
                      setShowMonthPicker(false);
                    }}
                    className="px-2 py-0.5 rounded hover:bg-white/50 transition-colors text-sm font-black text-slate-700 dark:text-slate-300 font-mono tabular-nums"
                  >
                    {currentYear}
                  </button>
                  <span className="text-slate-300">/</span>
                  <button
                    onClick={() => {
                      setShowMonthPicker(!showMonthPicker);
                      setShowYearPicker(false);
                    }}
                    className="px-2 py-0.5 rounded hover:bg-white/50 transition-colors text-sm font-black text-slate-700 dark:text-slate-300 font-mono tracking-wide uppercase"
                  >
                    {MONTHS[currentMonth]}
                  </button>
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 rounded-md hover:bg-white"
                  onClick={() =>
                    setCurrentDate(new Date(currentYear, currentMonth + 1, 1))
                  }
                >
                  <ChevronRight className="w-4 h-4" />
                </Button>
              </div>
            </div>
          </CardHeader>

          <CardContent className="p-0 overflow-hidden relative group/calendar flex-1 flex flex-col">
            <div className="px-6 py-2 border-b border-slate-100/30 dark:border-white/5 bg-slate-50/30 dark:bg-white/[0.01]">
              <div className="grid grid-cols-7 gap-2">
                {WEEKDAYS.map((day, i) => (
                  <div
                    key={day}
                    className={cn(
                      'text-center text-[10px] font-black uppercase tracking-widest',
                      i >= 5 ? 'text-rose-500' : 'text-slate-400'
                    )}
                  >
                    {day}
                  </div>
                ))}
              </div>
            </div>

            <div className="flex-1 p-4 relative">
              {/* Pickers Overlay */}
              {(showMonthPicker || showYearPicker) && (
                <div className="absolute inset-0 z-50 bg-white/95 dark:bg-slate-950/95 backdrop-blur-md p-6 flex flex-col animate-in fade-in zoom-in-95">
                  <div className="flex justify-between items-center mb-4">
                    <span className="text-xs font-black uppercase text-slate-400">
                      选择{showYearPicker ? '年份' : '月份'}
                    </span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-[10px]"
                      onClick={() => {
                        setShowMonthPicker(false);
                        setShowYearPicker(false);
                      }}
                    >
                      关闭
                    </Button>
                  </div>
                  <div className="grid grid-cols-4 gap-3 content-start">
                    {showYearPicker
                      ? years.map(y => (
                          <Button
                            key={y}
                            variant={currentYear === y ? 'default' : 'outline'}
                            className={cn(
                              'h-10 text-xs font-mono',
                              currentYear === y && 'bg-indigo-600'
                            )}
                            onClick={() => {
                              setCurrentDate(new Date(y, currentMonth, 1));
                              setShowYearPicker(false);
                            }}
                          >
                            {y}
                          </Button>
                        ))
                      : MONTHS.map((m, i) => (
                          <Button
                            key={m}
                            variant={currentMonth === i ? 'default' : 'outline'}
                            className={cn(
                              'h-10 text-xs',
                              currentMonth === i && 'bg-indigo-600'
                            )}
                            onClick={() => {
                              setCurrentDate(new Date(currentYear, i, 1));
                              setShowMonthPicker(false);
                            }}
                          >
                            {m}
                          </Button>
                        ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-7 gap-2 h-full grid-rows-6">
                {calendarDays.map((date, i) => {
                  if (!date)
                    return (
                      <div
                        key={`e-${i}`}
                        className="bg-slate-50/30 dark:bg-white/[0.01] rounded-xl border border-transparent border-dashed border-slate-200/50"
                      />
                    );

                  const weekend = isWeekend(date);
                  const holiday = isHoliday(date);
                  const today = isToday(date);
                  const info = holidayMap.get(formatDateKey(date));
                  const selected =
                    selectedDate &&
                    formatDateKey(selectedDate) === formatDateKey(date);

                  let containerClass =
                    'bg-white dark:bg-white/[0.03] border-slate-100 dark:border-white/5 hover:border-indigo-500/30 hover:shadow-lg hover:scale-[1.02] z-10'; // Default Open
                  let textClass = 'text-slate-700 dark:text-slate-200';

                  if (holiday) {
                    containerClass =
                      'bg-rose-50 border-rose-200 dark:bg-rose-900/20 dark:border-rose-800/50 hover:bg-rose-100 dark:hover:bg-rose-900/30 z-20';
                    textClass = 'text-rose-600 dark:text-rose-400';
                  } else if (weekend) {
                    containerClass =
                      'bg-slate-100/50 dark:bg-white/[0.01] border-transparent opacity-70 hover:opacity-100';
                    textClass = 'text-slate-400';
                  }

                  if (selected) {
                    containerClass =
                      'bg-indigo-600 border-indigo-500 text-white z-30 shadow-xl scale-110 ring-2 ring-indigo-200 dark:ring-indigo-900';
                    textClass = 'text-white';
                  }

                  if (today) {
                    containerClass +=
                      ' ring-2 ring-indigo-500 ring-offset-2 ring-offset-white dark:ring-offset-slate-950';
                  }

                  return (
                    <button
                      key={formatDateKey(date)}
                      onClick={() => handleDayClick(date)}
                      disabled={weekend && !holiday}
                      className={cn(
                        'relative rounded-xl border flex flex-col items-center justify-center transition-all group p-1 overflow-hidden',
                        containerClass
                      )}
                    >
                      <span
                        className={cn(
                          'text-sm font-black tracking-tighter mb-0.5 transition-colors',
                          textClass
                        )}
                      >
                        {date.getDate()}
                      </span>
                      {info?.description && (
                        <span
                          className={cn(
                            'text-[8px] font-bold truncate max-w-[90%] px-1.5 py-0.5 rounded-full mt-1 transition-colors',
                            selected
                              ? 'bg-white/20 text-white'
                              : 'bg-white/60 dark:bg-black/20 text-rose-600 dark:text-rose-400'
                          )}
                        >
                          {info.description}
                        </span>
                      )}

                      {/* Status Indicator (Dot) */}
                      <div className="absolute top-1.5 right-1.5">
                        {holiday ? (
                          <div
                            className={cn(
                              'w-1.5 h-1.5 rounded-full bg-rose-500',
                              selected && 'bg-white'
                            )}
                          />
                        ) : weekend ? null : (
                          <div className="w-1.5 h-1.5 rounded-full bg-emerald-400/50 group-hover:bg-emerald-500 transition-colors" />
                        )}
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Right Column: Sidebar & Actions */}
      <div className="w-full lg:w-72 shrink-0 flex flex-col gap-4">
        {/* Sync Widget */}
        {!hideSyncWidget && (
          <Card className="border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-xl rounded-[20px] shadow-sm">
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] uppercase font-black text-slate-400">
                  同步状态
                </span>
                <div className="flex items-center gap-1">
                  <div
                    className={cn(
                      'w-1.5 h-1.5 rounded-full',
                      isSyncing
                        ? 'bg-indigo-500 animate-pulse'
                        : 'bg-emerald-500'
                    )}
                  />
                  <span className="text-[10px] font-bold text-slate-500">
                    {isSyncing ? '运行中' : '空闲'}
                  </span>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div className="bg-white/50 dark:bg-white/5 rounded-lg p-2 border border-slate-100 dark:border-white/5">
                  <div className="text-[9px] text-slate-400 mb-0.5">
                    上次运行
                  </div>
                  <div className="text-[10px] font-mono font-bold text-slate-700 dark:text-slate-300">
                    {deployment?.lastRunTime
                      ? format(new Date(deployment.lastRunTime), 'MM-dd HH:mm')
                      : '--'}
                  </div>
                </div>
                <div className="bg-white/50 dark:bg-white/5 rounded-lg p-2 border border-slate-100 dark:border-white/5">
                  <div className="text-[9px] text-slate-400 mb-0.5">
                    下次运行
                  </div>
                  <div className="text-[10px] font-mono font-bold text-slate-700 dark:text-slate-300">
                    {deployment?.nextRunTime
                      ? format(new Date(deployment.nextRunTime), 'MM-dd HH:mm')
                      : '--'}
                  </div>
                </div>
              </div>
              <Button
                className="w-full h-8 text-xs font-bold bg-indigo-600 hover:bg-indigo-700"
                size="sm"
                disabled={isSyncing}
                onClick={() => {
                  void handleSync();
                }}
              >
                {isSyncing ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <RefreshCw className="w-3 h-3 mr-2" />
                )}
                {isSyncing ? '同步中...' : '立即同步'}
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Holiday List */}
        <Card className="flex-1 border-slate-200/60 dark:border-white/5 bg-white/40 dark:bg-white/[0.02] backdrop-blur-xl rounded-[24px] overflow-hidden flex flex-col shadow-sm min-h-[300px]">
          <div className="px-4 py-3 border-b border-slate-100/50 dark:border-white/5 flex justify-between items-center">
            <span className="text-xs font-black text-slate-500 dark:text-slate-400 flex items-center gap-2">
              <Info size={12} />
              节假日列表
            </span>
            <span className="text-[9px] bg-slate-100 dark:bg-white/10 px-1.5 py-0.5 rounded font-mono">
              {holidays.length}
            </span>
          </div>
          <ScrollArea className="flex-1">
            <div className="p-3 space-y-2">
              {holidays.length === 0 ? (
                <div className="text-center py-10 opacity-40">
                  <Calendar size={24} className="mx-auto mb-2" />
                  <p className="text-[10px]">暂无节假日数据</p>
                </div>
              ) : (
                sortedHolidays.map(h => (
                  <div
                    key={h.id}
                    id={`holiday-item-${h.id}`}
                    className={cn(
                      'p-3 rounded-xl border border-transparent transition-all group relative scroll-mt-20 duration-300',
                      highlightedId === h.id
                        ? 'bg-rose-50 dark:bg-rose-900/20 border-rose-200 shadow-md ring-1 ring-rose-500/20 scale-[1.02] z-10'
                        : 'bg-white/50 dark:bg-white/5 hover:bg-white hover:shadow-sm'
                    )}
                  >
                    <div className="flex justify-between items-start">
                      <div className="flex flex-col">
                        <span
                          className={cn(
                            'text-xs font-bold transition-colors',
                            highlightedId === h.id
                              ? 'text-rose-700 dark:text-rose-300'
                              : 'text-slate-800 dark:text-slate-200'
                          )}
                        >
                          {h.description || '节假日'}
                        </span>
                        <span className="text-[10px] font-mono text-slate-400 mt-0.5">
                          {h.holidayDate}
                        </span>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 text-rose-400 hover:text-rose-600 hover:bg-rose-50"
                        onClick={() => handleDeleteFromList(h.id)}
                      >
                        {deleteResult.fetching ? (
                          <Loader2 size={10} className="animate-spin" />
                        ) : (
                          <Trash2 size={12} />
                        )}
                      </Button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </ScrollArea>
        </Card>
      </div>

      <TaskHistory
        open={showHistory}
        onOpenChange={setShowHistory}
        deploymentId={deployment?.id}
        deploymentName={deployment?.flowName || '交易日历同步'}
        workPoolName={deployment?.workPoolName}
      />

      {/* Floating Add Dialog */}
      {selectedDate && (
        <div className="absolute bottom-6 left-6 right-80 z-50 p-4 bg-white/90 dark:bg-slate-900/90 backdrop-blur-xl rounded-[24px] border border-indigo-500/20 shadow-2xl animate-in slide-in-from-bottom-4">
          <div className="flex gap-2">
            <Input
              autoFocus
              placeholder={`添加节假日: ${formatDateKey(selectedDate)}...`}
              value={newDescription}
              onChange={e => setNewDescription(e.target.value)}
              className="flex-1 h-9 bg-white dark:bg-black/20 text-xs"
            />
            <Button
              size="sm"
              className="h-9 bg-indigo-600 hover:bg-indigo-700 text-xs"
              onClick={handleAddHoliday}
              disabled={addResult.fetching}
            >
              添加
            </Button>
            <Button
              size="sm"
              variant="ghost"
              className="h-9 text-xs"
              onClick={() => setSelectedDate(null)}
            >
              取消
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
