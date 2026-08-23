import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  HistogramSeries,
  type CandlestickData,
  type HistogramData,
  type UTCTimestamp,
} from 'lightweight-charts';
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  CalendarDays,
  CandlestickChart,
  CheckCircle2,
  Clock3,
  Database,
  Download,
  FileText,
  Layers3,
  Loader2,
  RefreshCw,
  Search,
  Table2,
  WalletCards,
} from 'lucide-react';
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useQuery } from 'urql';
import { Link, useLocation, useRoute } from 'wouter';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { gql } from '@/generated/gql';
import {
  DividendType,
  GetTradingCalendarDocument,
  KLinePeriod,
} from '@/generated/gql/graphql';
import { useDeploymentSync } from '@/hooks/useDeploymentSync';
import { FINANCIAL_CHART_COLORS } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { TaskHistory } from '../components/TaskHistory';

const STOCK_DATA_DETAIL_QUERY = gql(`
  query StockDataDetailInstrument($stockCode: String!) {
    instrument(stockCode: $stockCode) {
      id
      name
      market
      type
      isTrading
      updatedAt
      preClose
      upStopPrice
      downStopPrice
      priceTick
      totalVolume
      floatVolume
      quote {
        lastPrice
        open
        high
        low
        preClose
        change
        changePercent
        volume
        amount
        turnoverRate
        time
      }
    }
  }
`);

const STOCK_DATA_KLINES_QUERY = gql(`
  query StockDataDetailKLines(
    $stockCode: String!
    $period: KLinePeriod!
    $startTime: DateTime
    $endTime: DateTime
    $limit: Int
    $dividendType: DividendType!
  ) {
    klines(
      stockCode: $stockCode
      period: $period
      startTime: $startTime
      endTime: $endTime
      limit: $limit
      dividendType: $dividendType
      order: "asc"
    ) {
      stockCode
      period
      time
      open
      high
      low
      close
      volume
      amount
    }
  }
`);

const STOCK_DATA_FINANCIAL_QUERY = gql(`
  query StockDataDetailFinancial($stockCode: String!, $limit: Int!) {
    financialSummary(stockCode: $stockCode) {
      stockCode
      latestReportDate
      latestAnnounceDate
      revenue
      netProfitExclMinIntInc
      epsBasic
      totalAssets
      totalLiabilities
      totalEquity
      operatingCashFlow
      cashBalance
      totalCapital
      circulatingCapital
      incomeCount
      balanceCount
      cashFlowCount
      capitalCount
    }
    financialStatements(stockCode: $stockCode, limit: $limit) {
      income {
        stockCode
        reportDate
        announceDate
        revenue
        revenueInc
        totalOperatingCost
        operProfit
        totalProfit
        netProfit
        netProfitExclMinIntInc
        epsBasic
      }
      balance {
        stockCode
        reportDate
        announceDate
        totalAssets
        totalCurrentAssets
        totalNonCurrentAssets
        cashEquivalents
        inventories
        totalLiabilities
        totalCurrentLiability
        nonCurrentLiabilities
        totalEquity
        shareholderEquity
      }
      cashFlow {
        stockCode
        reportDate
        announceDate
        netCashFlowsOperAct
        netCashFlowsInvAct
        netCashFlowsFncAct
        netIncrCashCashEqu
        cashCashEquEndPeriod
      }
      capital {
        stockCode
        reportDate
        announceDate
        totalCapital
        circulatingCapital
        restrictCirculatingCapital
        freeFloatCapital
      }
    }
  }
`);

type DeploymentHistoryTarget = {
  id?: string;
  name: string;
  workPoolName?: string | null;
};

const KLINE_PERIOD_OPTIONS = [
  { label: '日K', value: KLinePeriod.Day_1 },
  { label: '1分钟', value: KLinePeriod.Min_1 },
  { label: '5分钟', value: KLinePeriod.Min_5 },
  { label: '15分钟', value: KLinePeriod.Min_15 },
  { label: '30分钟', value: KLinePeriod.Min_30 },
  { label: '60分钟', value: KLinePeriod.Min_60 },
] as const;

const DIVIDEND_OPTIONS = [
  { label: '不复权', value: DividendType.None },
  { label: '前复权', value: DividendType.Front },
  { label: '后复权', value: DividendType.Back },
] as const;

const KLINE_QUERY_LIMIT = 800;
const DAY_MS = 24 * 60 * 60 * 1000;

type CoverageTone = 'good' | 'warning' | 'danger' | 'muted';

type KLineCoveragePoint = {
  date: string;
  status: 'present' | 'missing';
};

type MissingRange = {
  startDate: string;
  endDate: string;
  count: number;
};

type KLineCoverage = {
  coveragePoints: KLineCoveragePoint[];
  coverageRatio: number;
  expectedDays: number;
  firstDataTime?: string;
  lastDataTime?: string;
  missingDays: number;
  missingRanges: MissingRange[];
  presentDays: number;
  queryCapped: boolean;
  records: number;
  tone: CoverageTone;
};

type DataAssetStatus = {
  icon: React.ElementType;
  label: string;
  meta: string;
  status: CoverageTone;
  statusLabel: string;
  value: string;
};

type DataAssetAction = {
  disabled?: boolean;
  label: string;
  onClick: () => void;
};

export function StockDataDetailPage() {
  const [, params] = useRoute('/settings/data/:stockCode');
  const [, setLocation] = useLocation();
  const stockCode = decodeURIComponent(params?.stockCode || '').toUpperCase();

  const today = useMemo(() => formatDateInput(new Date()), []);
  const defaultStart = useMemo(() => {
    const date = new Date();
    date.setFullYear(date.getFullYear() - 3);
    return formatDateInput(date);
  }, []);

  const [queryCode, setQueryCode] = useState(stockCode);
  const [period, setPeriod] = useState<KLinePeriod>(KLinePeriod.Day_1);
  const [dividendType, setDividendType] = useState<DividendType>(
    DividendType.None
  );
  const [startDate, setStartDate] = useState(defaultStart);
  const [endDate, setEndDate] = useState(today);
  const [historyTarget, setHistoryTarget] =
    useState<DeploymentHistoryTarget | null>(null);
  const [submittedRuns, setSubmittedRuns] = useState<Record<string, string>>(
    {}
  );

  useEffect(() => {
    setQueryCode(stockCode);
  }, [stockCode]);

  const instrumentSync = useDeploymentSync('instrument-sync', {
    successMessage: '基础信息同步任务已提交',
  });
  const marketSync = useDeploymentSync('daily-market-data-sync', {
    successMessage: 'K线同步任务已提交',
  });
  const financialSync = useDeploymentSync('financial-sync', {
    successMessage: '财务同步任务已提交',
  });

  const [{ data: instrumentData }, reloadInstrument] = useQuery({
    query: STOCK_DATA_DETAIL_QUERY,
    variables: { stockCode },
    pause: !stockCode,
  });

  const [{ data: klineData, fetching: klineLoading }, reloadKlines] = useQuery({
    query: STOCK_DATA_KLINES_QUERY,
    variables: {
      stockCode,
      period,
      dividendType,
      startTime: toDateTimeStart(startDate),
      endTime: toDateTimeEnd(endDate),
      limit: KLINE_QUERY_LIMIT,
    },
    pause: !stockCode,
  });

  const [{ data: financialData, fetching: financialLoading }, reloadFinancial] =
    useQuery({
      query: STOCK_DATA_FINANCIAL_QUERY,
      variables: { stockCode, limit: 20 },
      pause: !stockCode,
    });

  const instrument = instrumentData?.instrument;
  const calendarMarket = useMemo(
    () => resolveCalendarMarket(instrument?.market, stockCode),
    [instrument?.market, stockCode]
  );
  const [{ data: calendarData, fetching: calendarLoading }, reloadCalendar] =
    useQuery({
      query: GetTradingCalendarDocument,
      variables: {
        endDate,
        market: calendarMarket,
        startDate,
      },
      pause: !stockCode || !startDate || !endDate,
      requestPolicy: 'cache-and-network',
    });

  const klines = useMemo(() => klineData?.klines ?? [], [klineData?.klines]);
  const statements = financialData?.financialStatements;
  const summary = financialData?.financialSummary;
  const isAnySyncing =
    instrumentSync.isSyncing || marketSync.isSyncing || financialSync.isSyncing;
  const klineCoverage = useMemo(
    () =>
      buildKLineCoverage({
        endDate,
        expectedTradingDays: calendarData?.tradingCalendar ?? [],
        klines,
        limit: KLINE_QUERY_LIMIT,
        startDate,
      }),
    [calendarData?.tradingCalendar, endDate, klines, startDate]
  );
  const assetStatuses = useMemo<DataAssetStatus[]>(
    () => [
      {
        icon: Database,
        label: '基础资料',
        meta: instrument?.updatedAt
          ? `更新 ${formatDateTime(instrument.updatedAt)}`
          : '等待基础资料',
        status: instrument
          ? 'good'
          : instrumentSync.isSyncing
            ? 'warning'
            : 'muted',
        statusLabel: instrument ? '已存在' : '缺失',
        value: instrument?.name || stockCode,
      },
      {
        icon: BarChart3,
        label: '行情快照',
        meta: instrument?.quote?.time
          ? `时间 ${formatDateTime(instrument.quote.time)}`
          : '等待行情快照',
        status: instrument?.quote?.time ? 'good' : 'muted',
        statusLabel: instrument?.quote?.time ? '已存在' : '缺失',
        value: formatMoney(instrument?.quote?.lastPrice),
      },
      {
        icon: CandlestickChart,
        label: 'K线缓存',
        meta: `${periodLabel(period)} · ${startDate} 至 ${endDate}`,
        status:
          marketSync.isSyncing && klineCoverage.tone !== 'good'
            ? 'warning'
            : klineCoverage.tone,
        statusLabel: coverageStatusLabel(klineCoverage),
        value: `${formatRatio(klineCoverage.coverageRatio)} 覆盖`,
      },
      {
        icon: FileText,
        label: '财务四表',
        meta: summary?.latestReportDate
          ? `最新报告 ${summary.latestReportDate}`
          : '等待财务数据',
        status:
          financialSync.isSyncing && !summary
            ? 'warning'
            : summary
              ? 'good'
              : 'muted',
        statusLabel: summary ? '已存在' : '缺失',
        value: `${summary?.incomeCount ?? 0}/${summary?.balanceCount ?? 0}/${summary?.cashFlowCount ?? 0}/${summary?.capitalCount ?? 0}`,
      },
    ],
    [
      endDate,
      financialSync.isSyncing,
      instrument,
      instrumentSync.isSyncing,
      klineCoverage,
      marketSync.isSyncing,
      period,
      startDate,
      stockCode,
      summary,
    ]
  );

  const syncMarketParams = useMemo(
    () => ({
      stock_list: [stockCode],
      start_time: toCompactDate(startDate),
      end_time: toCompactDate(endDate),
      periods: toSyncPeriods(period),
    }),
    [endDate, period, startDate, stockCode]
  );

  const setRun = useCallback((key: string, runId?: string) => {
    if (!runId) return;
    setSubmittedRuns(prev => ({ ...prev, [key]: runId }));
  }, []);

  const handleSyncInstrument = async () => {
    setRun(
      'instrument',
      await instrumentSync.triggerSync({ stock_code: stockCode })
    );
  };

  const handleSyncKLines = async () => {
    setRun('market', await marketSync.triggerSync(syncMarketParams));
  };

  const handleSyncKLineRange = async (range: MissingRange) => {
    setStartDate(range.startDate);
    setEndDate(range.endDate);
    setRun(
      'market',
      await marketSync.triggerSync({
        stock_list: [stockCode],
        start_time: toCompactDate(range.startDate),
        end_time: toCompactDate(range.endDate),
        periods: toSyncPeriods(period),
      })
    );
  };

  const handleSyncFinancial = async () => {
    setRun(
      'financial',
      await financialSync.triggerSync({ stock_codes: [stockCode] })
    );
  };

  const handleSyncAll = async () => {
    await handleSyncInstrument();
    await handleSyncKLines();
    await handleSyncFinancial();
  };

  const handleNavigateCode = () => {
    const nextCode = queryCode.trim().toUpperCase();
    if (!nextCode || nextCode === stockCode) return;
    setLocation(`/settings/data/${nextCode}`);
  };

  if (!stockCode) {
    return (
      <DataStudioPageFrame
        activeMode="STOCKS"
        description="基础资料、K线、财务与同步任务"
        title="标的数据详情"
      >
        <EmptyState
          title="缺少标的代码"
          description="请从数据管理门户选择一个标的。"
        />
      </DataStudioPageFrame>
    );
  }

  return (
    <DataStudioPageFrame
      activeMode="STOCKS"
      description="基础资料、K线、财务与同步任务"
      title={`标的数据详情 ${stockCode}`}
    >
      <div className="container mx-auto max-w-[1600px] space-y-6 pb-10">
        <div className="flex flex-col gap-4 border-b border-slate-200 pb-4 dark:border-slate-800 lg:flex-row lg:items-end lg:justify-between">
          <div className="flex items-start gap-3">
            <Link href="/settings/data">
              <Button
                variant="ghost"
                size="icon"
                className="h-10 w-10 rounded-xl border border-slate-200 bg-white shadow-sm dark:border-white/5 dark:bg-white/5"
              >
                <ArrowLeft className="h-5 w-5" />
              </Button>
            </Link>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h1 className="text-2xl font-black tracking-tight text-slate-900 dark:text-white">
                  {instrument?.name || stockCode}
                </h1>
                <Badge className="rounded-md bg-slate-900 font-mono text-white dark:bg-white dark:text-slate-950">
                  {stockCode}
                </Badge>
                <Badge
                  variant="outline"
                  className={cn(
                    'rounded-md',
                    instrument?.isTrading
                      ? 'border-emerald-500/30 text-emerald-600'
                      : 'border-slate-300 text-slate-500'
                  )}
                >
                  {instrument?.isTrading ? '交易中' : '非交易'}
                </Badge>
              </div>
              <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
                数据管理门户内的单票数据层页面：行情缓存、K线明细、财务四表与同步任务。
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <div className="relative w-full sm:w-72">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-slate-400" />
              <Input
                value={queryCode}
                onChange={event => setQueryCode(event.target.value)}
                onKeyDown={event =>
                  event.key === 'Enter' && handleNavigateCode()
                }
                className="h-9 pl-9 text-sm"
                placeholder="切换标的代码"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNavigateCode}
              className="h-9 gap-1.5"
            >
              <Search className="h-3.5 w-3.5" />
              查询
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
          <MetricTile
            icon={Database}
            label="标的类型"
            value={`${instrument?.market || '--'} · ${instrument?.type || '--'}`}
            subValue={`最小价位 ${formatNumber(instrument?.priceTick, 4)}`}
          />
          <MetricTile
            icon={BarChart3}
            label="最新行情"
            value={formatMoney(instrument?.quote?.lastPrice)}
            subValue={formatPercent(instrument?.quote?.changePercent)}
            tone={
              (instrument?.quote?.changePercent ?? 0) >= 0
                ? 'marketUp'
                : 'marketDown'
            }
          />
          <MetricTile
            icon={CandlestickChart}
            label="K线覆盖"
            value={formatRatio(klineCoverage.coverageRatio)}
            subValue={`${klineCoverage.presentDays}/${klineCoverage.expectedDays} 交易日`}
            tone={coverageMetricTone(klineCoverage.tone)}
          />
          <MetricTile
            icon={AlertTriangle}
            label="缺失交易日"
            value={`${klineCoverage.missingDays}`}
            subValue={
              klineCoverage.queryCapped
                ? `查询已达 ${KLINE_QUERY_LIMIT} 条上限`
                : `${klineCoverage.missingRanges.length} 段缺口`
            }
            tone={klineCoverage.missingDays > 0 ? 'amber' : 'green'}
          />
          <MetricTile
            icon={FileText}
            label="最新财报"
            value={summary?.latestReportDate || '--'}
            subValue={
              summary?.latestAnnounceDate
                ? `公告 ${summary.latestAnnounceDate}`
                : '等待财务数据'
            }
          />
        </div>

        <DataAssetMatrix
          assets={assetStatuses}
          actions={[
            {
              disabled: instrumentSync.isSyncing,
              label: instrumentSync.isSyncing ? '基础同步中' : '同步基础',
              onClick: handleSyncInstrument,
            },
            {
              disabled: marketSync.isSyncing,
              label: marketSync.isSyncing ? 'K线同步中' : '同步K线',
              onClick: handleSyncKLines,
            },
            {
              disabled: financialSync.isSyncing,
              label: financialSync.isSyncing ? '财务同步中' : '同步财务',
              onClick: handleSyncFinancial,
            },
          ]}
        />

        <Card className="overflow-hidden border-slate-200/70 shadow-sm dark:border-slate-800/70">
          <CardHeader className="border-b border-slate-100 bg-slate-50/60 px-5 py-4 dark:border-slate-800 dark:bg-white/[0.02]">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <CardTitle className="flex items-center gap-2 text-base">
                  <RefreshCw className="h-4 w-4 text-indigo-500" />
                  手动同步
                </CardTitle>
                <CardDescription className="text-xs">
                  通过 Prefect 提交真实数据同步任务，运行历史可追踪。
                </CardDescription>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  onClick={handleSyncAll}
                  disabled={isAnySyncing}
                  className="h-8 gap-1.5 bg-indigo-600 text-xs hover:bg-indigo-700"
                >
                  <RefreshCw
                    className={cn(
                      'h-3.5 w-3.5',
                      isAnySyncing && 'animate-spin'
                    )}
                  />
                  同步全部
                </Button>
                <SyncButton
                  label="基础信息"
                  syncing={instrumentSync.isSyncing}
                  runId={submittedRuns.instrument}
                  onSync={handleSyncInstrument}
                  onHistory={() =>
                    setHistoryTarget({
                      id: instrumentSync.deployment?.id,
                      name:
                        instrumentSync.deployment?.flowName ||
                        '单只标的数据同步',
                      workPoolName: instrumentSync.deployment?.workPoolName,
                    })
                  }
                />
                <SyncButton
                  label="K线"
                  syncing={marketSync.isSyncing}
                  runId={submittedRuns.market}
                  onSync={handleSyncKLines}
                  onHistory={() =>
                    setHistoryTarget({
                      id: marketSync.deployment?.id,
                      name: marketSync.deployment?.flowName || '市场数据同步',
                      workPoolName: marketSync.deployment?.workPoolName,
                    })
                  }
                />
                <SyncButton
                  label="财务"
                  syncing={financialSync.isSyncing}
                  runId={submittedRuns.financial}
                  onSync={handleSyncFinancial}
                  onHistory={() =>
                    setHistoryTarget({
                      id: financialSync.deployment?.id,
                      name:
                        financialSync.deployment?.flowName || '财务数据同步',
                      workPoolName: financialSync.deployment?.workPoolName,
                    })
                  }
                />
              </div>
            </div>
          </CardHeader>
          <CardContent className="grid gap-3 p-5 md:grid-cols-3">
            <SyncStatus
              title="基础信息"
              deploymentName={
                instrumentSync.deployment?.name || 'instrument-sync'
              }
              status={instrumentSync.deployment?.status}
              isStale={instrumentSync.deployment?.isStale}
              staleReason={instrumentSync.deployment?.staleReason}
              lastRunTime={instrumentSync.deployment?.lastRunTime}
              runId={submittedRuns.instrument}
            />
            <SyncStatus
              title="K线缓存"
              deploymentName={
                marketSync.deployment?.name || 'daily-market-data-sync'
              }
              status={marketSync.deployment?.status}
              isStale={marketSync.deployment?.isStale}
              staleReason={marketSync.deployment?.staleReason}
              lastRunTime={marketSync.deployment?.lastRunTime}
              runId={submittedRuns.market}
            />
            <SyncStatus
              title="财务四表"
              deploymentName={
                financialSync.deployment?.name || 'financial-sync'
              }
              status={financialSync.deployment?.status}
              isStale={financialSync.deployment?.isStale}
              staleReason={financialSync.deployment?.staleReason}
              lastRunTime={financialSync.deployment?.lastRunTime}
              runId={submittedRuns.financial}
            />
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.45fr)_minmax(420px,0.9fr)]">
          <Card className="overflow-hidden border-slate-200/70 shadow-sm dark:border-slate-800/70">
            <CardHeader className="border-b border-slate-100 bg-slate-50/60 px-5 py-4 dark:border-slate-800 dark:bg-white/[0.02]">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <CandlestickChart className="h-4 w-4 text-indigo-500" />
                    K线数据
                  </CardTitle>
                  <CardDescription className="text-xs">
                    默认日K近3年，可切换分钟周期与复权方式。
                  </CardDescription>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 text-xs"
                  onClick={() => {
                    reloadKlines({ requestPolicy: 'network-only' });
                    reloadInstrument({ requestPolicy: 'network-only' });
                    reloadCalendar({ requestPolicy: 'network-only' });
                  }}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  刷新行情
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-2 pt-2 sm:grid-cols-2 lg:grid-cols-5">
                <Select
                  value={period}
                  onValueChange={value => setPeriod(value as KLinePeriod)}
                >
                  <SelectTrigger className="h-9 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {KLINE_PERIOD_OPTIONS.map(item => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Select
                  value={dividendType}
                  onValueChange={value =>
                    setDividendType(value as DividendType)
                  }
                >
                  <SelectTrigger className="h-9 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DIVIDEND_OPTIONS.map(item => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input
                  type="date"
                  value={startDate}
                  onChange={event => setStartDate(event.target.value)}
                  className="h-9 text-xs"
                />
                <Input
                  type="date"
                  value={endDate}
                  onChange={event => setEndDate(event.target.value)}
                  className="h-9 text-xs"
                />
                <Button
                  variant="secondary"
                  size="sm"
                  className="h-9 gap-1.5 text-xs"
                  onClick={handleSyncKLines}
                  disabled={marketSync.isSyncing}
                >
                  {marketSync.isSyncing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                  同步区间
                </Button>
              </div>
            </CardHeader>
            <CardContent className="p-0">
              <KLineCoveragePanel
                calendarLoading={calendarLoading}
                coverage={klineCoverage}
                dividendType={dividendType}
                period={period}
                onSyncAll={handleSyncKLines}
                onSyncRange={handleSyncKLineRange}
                syncing={marketSync.isSyncing}
              />
              <KLinePreviewChart data={klines} loading={klineLoading} />
              <KLineTable data={klines.slice(-80).reverse()} />
            </CardContent>
          </Card>

          <Card className="overflow-hidden border-slate-200/70 shadow-sm dark:border-slate-800/70">
            <CardHeader className="border-b border-slate-100 bg-slate-50/60 px-5 py-4 dark:border-slate-800 dark:bg-white/[0.02]">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FileText className="h-4 w-4 text-red-600" />
                    财务数据
                  </CardTitle>
                  <CardDescription className="text-xs">
                    摘要来自最新报告期，明细按报告期倒序展示。
                  </CardDescription>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-8 gap-1.5 text-xs"
                  onClick={() =>
                    reloadFinancial({ requestPolicy: 'network-only' })
                  }
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  刷新
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4 p-5">
              {financialLoading ? (
                <div className="flex h-40 items-center justify-center text-sm text-slate-400">
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  加载财务数据
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-3">
                    <FinancialMetric
                      label="营业总收入"
                      value={summary?.revenue}
                    />
                    <FinancialMetric
                      label="归母净利润"
                      value={summary?.netProfitExclMinIntInc}
                    />
                    <FinancialMetric
                      label="总资产"
                      value={summary?.totalAssets}
                    />
                    <FinancialMetric
                      label="经营现金流"
                      value={summary?.operatingCashFlow}
                    />
                  </div>
                  <Tabs defaultValue="income" className="w-full">
                    <TabsList className="grid w-full grid-cols-4">
                      <TabsTrigger value="income">利润</TabsTrigger>
                      <TabsTrigger value="balance">资产</TabsTrigger>
                      <TabsTrigger value="cash">现金流</TabsTrigger>
                      <TabsTrigger value="capital">股本</TabsTrigger>
                    </TabsList>
                    <TabsContent value="income" className="mt-3">
                      <FinancialRows
                        rows={statements?.income ?? []}
                        columns={[
                          ['reportDate', '报告期'],
                          ['revenue', '营业总收入'],
                          ['netProfitExclMinIntInc', '归母净利'],
                          ['epsBasic', 'EPS'],
                        ]}
                      />
                    </TabsContent>
                    <TabsContent value="balance" className="mt-3">
                      <FinancialRows
                        rows={statements?.balance ?? []}
                        columns={[
                          ['reportDate', '报告期'],
                          ['totalAssets', '总资产'],
                          ['totalLiabilities', '总负债'],
                          ['totalEquity', '权益'],
                        ]}
                      />
                    </TabsContent>
                    <TabsContent value="cash" className="mt-3">
                      <FinancialRows
                        rows={statements?.cashFlow ?? []}
                        columns={[
                          ['reportDate', '报告期'],
                          ['netCashFlowsOperAct', '经营现金流'],
                          ['netCashFlowsInvAct', '投资现金流'],
                          ['cashCashEquEndPeriod', '期末现金'],
                        ]}
                      />
                    </TabsContent>
                    <TabsContent value="capital" className="mt-3">
                      <FinancialRows
                        rows={statements?.capital ?? []}
                        columns={[
                          ['reportDate', '报告期'],
                          ['totalCapital', '总股本'],
                          ['circulatingCapital', '流通A股'],
                          ['freeFloatCapital', '自由流通'],
                        ]}
                      />
                    </TabsContent>
                  </Tabs>
                </>
              )}
            </CardContent>
          </Card>
        </div>

        <Card className="overflow-hidden border-slate-200/70 shadow-sm dark:border-slate-800/70">
          <CardHeader className="border-b border-slate-100 bg-slate-50/60 px-5 py-4 dark:border-slate-800 dark:bg-white/[0.02]">
            <CardTitle className="flex items-center gap-2 text-base">
              <WalletCards className="h-4 w-4 text-emerald-500" />
              基础资料与数据覆盖
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-4">
            <InfoCell label="名称" value={instrument?.name || '--'} />
            <InfoCell label="市场" value={instrument?.market || '--'} />
            <InfoCell label="昨收" value={formatMoney(instrument?.preClose)} />
            <InfoCell
              label="涨跌停"
              value={`${formatMoney(instrument?.upStopPrice)} / ${formatMoney(instrument?.downStopPrice)}`}
            />
            <InfoCell
              label="总股本"
              value={formatNumber(instrument?.totalVolume)}
            />
            <InfoCell
              label="流通股本"
              value={formatNumber(instrument?.floatVolume)}
            />
            <InfoCell
              label="行情时间"
              value={formatDateTime(instrument?.quote?.time)}
            />
            <InfoCell
              label="资料更新"
              value={formatDateTime(instrument?.updatedAt)}
            />
          </CardContent>
        </Card>
      </div>

      <TaskHistory
        open={!!historyTarget}
        onOpenChange={open => {
          if (!open) setHistoryTarget(null);
        }}
        deploymentId={historyTarget?.id}
        deploymentName={historyTarget?.name || '数据同步'}
        workPoolName={historyTarget?.workPoolName || undefined}
      />
    </DataStudioPageFrame>
  );
}

function DataAssetMatrix({
  actions,
  assets,
}: {
  actions: DataAssetAction[];
  assets: DataAssetStatus[];
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-slate-200/70 bg-white shadow-sm dark:border-slate-800/70 dark:bg-slate-950">
      <div className="flex flex-col gap-3 border-b border-slate-100 bg-slate-50/60 px-5 py-4 dark:border-slate-800 dark:bg-white/[0.02] lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="flex items-center gap-2 text-base font-bold text-slate-900 dark:text-white">
            <Layers3 className="h-4 w-4 text-indigo-500" />
            数据资产状态
          </h2>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            单票基础资料、行情快照、K线缓存与财务四表的当前可用情况。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {actions.map(action => (
            <Button
              key={action.label}
              type="button"
              size="sm"
              variant="outline"
              className="h-8 gap-1.5 text-xs"
              disabled={action.disabled}
              onClick={action.onClick}
            >
              {action.disabled ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              {action.label}
            </Button>
          ))}
        </div>
      </div>
      <div className="grid gap-3 p-5 md:grid-cols-2 xl:grid-cols-4">
        {assets.map(asset => {
          const Icon = asset.icon;
          return (
            <div
              key={asset.label}
              className="rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-white/[0.02]"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-400">
                    <Icon className="h-3.5 w-3.5" />
                    {asset.label}
                  </div>
                  <div className="mt-2 truncate text-sm font-black text-slate-900 dark:text-white">
                    {asset.value}
                  </div>
                </div>
                <StatusBadge label={asset.statusLabel} tone={asset.status} />
              </div>
              <div className="mt-2 truncate text-xs text-slate-500 dark:text-slate-400">
                {asset.meta}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function KLineCoveragePanel({
  calendarLoading,
  coverage,
  dividendType,
  onSyncAll,
  onSyncRange,
  period,
  syncing,
}: {
  calendarLoading: boolean;
  coverage: KLineCoverage;
  dividendType: DividendType;
  onSyncAll: () => void;
  onSyncRange: (range: MissingRange) => void;
  period: KLinePeriod;
  syncing: boolean;
}) {
  const visibleRanges = coverage.missingRanges.slice(0, 6);
  return (
    <div className="border-b border-slate-100 p-5 dark:border-slate-800">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="flex items-center gap-2 text-sm font-black text-slate-900 dark:text-white">
              <CalendarDays className="h-4 w-4 text-indigo-500" />
              K线覆盖诊断
            </h3>
            <StatusBadge
              label={coverageStatusLabel(coverage)}
              tone={coverage.tone}
            />
            {coverage.queryCapped && (
              <Badge
                variant="outline"
                className="rounded-md border-amber-500/30 text-amber-600"
              >
                查询触顶
              </Badge>
            )}
          </div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {periodLabel(period)} · {dividendLabel(dividendType)} ·
            交易日存在性检查
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          className="h-8 gap-1.5 bg-indigo-600 text-xs hover:bg-indigo-700"
          disabled={syncing}
          onClick={onSyncAll}
        >
          {syncing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Download className="h-3.5 w-3.5" />
          )}
          拉取当前区间
        </Button>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <CoverageStat
          label="覆盖交易日"
          value={`${coverage.presentDays}/${coverage.expectedDays}`}
          subValue={formatRatio(coverage.coverageRatio)}
        />
        <CoverageStat
          label="缺失交易日"
          value={`${coverage.missingDays}`}
          subValue={`${coverage.missingRanges.length} 段缺口`}
          tone={coverage.missingDays > 0 ? 'warning' : 'good'}
        />
        <CoverageStat
          label="首条样本"
          value={formatDateShort(coverage.firstDataTime)}
          subValue={formatTimeShort(coverage.firstDataTime)}
        />
        <CoverageStat
          label="末条样本"
          value={formatDateShort(coverage.lastDataTime)}
          subValue={formatTimeShort(coverage.lastDataTime)}
        />
      </div>

      <div className="mt-4 overflow-x-auto rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-white/[0.02] custom-scrollbar">
        <div
          className="grid min-w-full gap-1"
          style={{
            gridTemplateColumns: `repeat(${Math.max(coverage.coveragePoints.length, 1)}, minmax(5px, 1fr))`,
          }}
          aria-label="K线交易日覆盖时间带"
        >
          {coverage.coveragePoints.length === 0 ? (
            <span className="h-4 rounded-sm bg-slate-200 dark:bg-slate-800" />
          ) : (
            coverage.coveragePoints.map(point => (
              <span
                key={point.date}
                title={`${point.date} ${point.status === 'present' ? '已存在' : '缺失'}`}
                className={cn(
                  'h-4 min-w-[5px] rounded-sm',
                  point.status === 'present'
                    ? 'bg-emerald-500/80'
                    : 'bg-amber-400/80'
                )}
              />
            ))
          )}
        </div>
        <div className="mt-2 flex items-center justify-between gap-3 text-[10px] font-bold text-slate-500">
          <span>左侧为开始日期，右侧为结束日期</span>
          <span>
            {calendarLoading ? '交易日历加载中' : `${coverage.records} 条样本`}
          </span>
        </div>
      </div>

      {coverage.queryCapped && (
        <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 text-xs font-medium leading-relaxed text-amber-700 dark:text-amber-300">
          <AlertTriangle className="mr-1.5 inline h-3.5 w-3.5" />
          当前查询返回数量达到 {KLINE_QUERY_LIMIT}
          条上限，覆盖率可能只代表已加载窗口。
        </div>
      )}

      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between gap-3">
          <div className="text-xs font-black uppercase tracking-widest text-slate-400">
            Missing Ranges
          </div>
          {coverage.missingRanges.length > visibleRanges.length && (
            <div className="text-[10px] font-bold text-slate-500">
              仅显示前 {visibleRanges.length} 段
            </div>
          )}
        </div>
        {visibleRanges.length === 0 ? (
          <div className="flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 px-3 py-2 text-xs font-bold text-emerald-700 dark:text-emerald-300">
            <CheckCircle2 className="h-3.5 w-3.5" />
            当前区间未发现交易日级缺口
          </div>
        ) : (
          <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {visibleRanges.map(range => (
              <div
                key={`${range.startDate}-${range.endDate}`}
                className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900"
              >
                <div className="min-w-0">
                  <div className="truncate font-mono text-xs font-black text-slate-900 dark:text-white">
                    {formatRangeLabel(range)}
                  </div>
                  <div className="text-[10px] font-bold text-slate-500">
                    {range.count} 个交易日
                  </div>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 shrink-0 gap-1.5 px-2 text-xs"
                  disabled={syncing}
                  onClick={() => onSyncRange(range)}
                >
                  {syncing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="h-3.5 w-3.5" />
                  )}
                  拉取
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function CoverageStat({
  label,
  subValue,
  tone = 'muted',
  value,
}: {
  label: string;
  subValue?: string;
  tone?: CoverageTone;
  value: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-white/[0.02]">
      <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 truncate font-mono text-lg font-black text-slate-900 dark:text-white',
          tone === 'good' && 'text-emerald-600 dark:text-emerald-400',
          tone === 'warning' && 'text-amber-600 dark:text-amber-400',
          tone === 'danger' && 'text-red-600 dark:text-red-400'
        )}
      >
        {value}
      </div>
      {subValue && (
        <div className="truncate text-xs text-slate-500">{subValue}</div>
      )}
    </div>
  );
}

function StatusBadge({ label, tone }: { label: string; tone: CoverageTone }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        'rounded-md',
        tone === 'good' && 'border-emerald-500/30 text-emerald-600',
        tone === 'warning' && 'border-amber-500/30 text-amber-600',
        tone === 'danger' && 'border-red-500/30 text-red-600',
        tone === 'muted' && 'border-slate-300 text-slate-500'
      )}
    >
      {label}
    </Badge>
  );
}

function MetricTile({
  icon: Icon,
  label,
  value,
  subValue,
  tone,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  subValue?: string;
  tone?: 'amber' | 'blue' | 'red' | 'green' | 'marketUp' | 'marketDown';
}) {
  return (
    <Card className="border-slate-200/70 p-4 shadow-sm dark:border-slate-800/70">
      <div className="flex items-center gap-3">
        <div className="rounded-xl bg-slate-100 p-2 text-slate-600 dark:bg-white/5 dark:text-slate-300">
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
            {label}
          </div>
          <div
            className={cn(
              'truncate text-lg font-black text-slate-900 dark:text-white',
              tone === 'marketUp' && 'text-market-up',
              tone === 'marketDown' && 'text-market-down',
              tone === 'red' && 'text-red-600 dark:text-red-400',
              tone === 'green' && 'text-emerald-600 dark:text-emerald-400',
              tone === 'amber' && 'text-amber-600 dark:text-amber-400',
              tone === 'blue' && 'text-blue-600 dark:text-blue-400'
            )}
          >
            {value}
          </div>
          {subValue && (
            <div className="truncate text-xs text-slate-500 dark:text-slate-400">
              {subValue}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

function SyncButton({
  label,
  syncing,
  runId,
  onSync,
  onHistory,
}: {
  label: string;
  syncing: boolean;
  runId?: string;
  onSync: () => void;
  onHistory: () => void;
}) {
  return (
    <div className="flex items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 dark:border-slate-800 dark:bg-slate-900">
      <Button
        variant="ghost"
        size="sm"
        className="h-7 gap-1.5 px-2 text-xs"
        disabled={syncing}
        onClick={onSync}
      >
        {syncing ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <RefreshCw className="h-3.5 w-3.5" />
        )}
        {label}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-xs text-slate-500"
        onClick={onHistory}
      >
        历史
      </Button>
      {runId && (
        <Badge
          variant="secondary"
          className="h-6 rounded-md font-mono text-[10px]"
        >
          {runId.slice(0, 8)}
        </Badge>
      )}
    </div>
  );
}

function SyncStatus({
  title,
  deploymentName,
  status,
  isStale,
  staleReason,
  lastRunTime,
  runId,
}: {
  title: string;
  deploymentName: string;
  status?: string | null;
  isStale?: boolean | null;
  staleReason?: string | null;
  lastRunTime?: string | null;
  runId?: string;
}) {
  const running = [
    'Running',
    'Pending',
    'Cancelling',
    'Scheduled',
    'Late',
  ].includes(status || '');
  const failed =
    ['Failed', 'Crashed'].includes(status || '') || Boolean(isStale);
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-white/[0.02]">
      <div className="flex items-center justify-between gap-2">
        <div className="font-bold text-sm text-slate-800 dark:text-slate-100">
          {title}
        </div>
        <Badge
          variant="outline"
          className={cn(
            'gap-1 rounded-md',
            running
              ? 'border-red-500/30 text-red-600'
              : failed
                ? 'border-red-500/30 text-red-600'
                : 'border-emerald-500/30 text-emerald-600'
          )}
        >
          {running ? (
            <Activity className="h-3 w-3 animate-spin" />
          ) : failed ? (
            <Clock3 className="h-3 w-3" />
          ) : (
            <CheckCircle2 className="h-3 w-3" />
          )}
          {running && isStale ? 'Running · 卡住' : status || 'Ready'}
        </Badge>
      </div>
      <div className="mt-2 text-[11px] text-slate-500">
        <div className="truncate font-mono">{deploymentName}</div>
        {isStale && staleReason ? <div>{staleReason}</div> : null}
        <div>上次同步：{formatDateTime(lastRunTime)}</div>
        <div>最近提交：{runId ? runId.slice(0, 12) : '--'}</div>
      </div>
    </div>
  );
}

function KLinePreviewChart({
  data,
  loading,
}: {
  data: Array<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
  loading: boolean;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 360,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#64748b',
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.12)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.12)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(148, 163, 184, 0.22)',
      },
      timeScale: {
        borderColor: 'rgba(148, 163, 184, 0.22)',
        timeVisible: true,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
      },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: FINANCIAL_CHART_COLORS.up,
      downColor: FINANCIAL_CHART_COLORS.down,
      borderVisible: false,
      wickUpColor: FINANCIAL_CHART_COLORS.up,
      wickDownColor: FINANCIAL_CHART_COLORS.down,
    });
    const volumeSeries = chart.addSeries(HistogramSeries, {
      color: '#94a3b8',
      priceFormat: { type: 'volume' },
      priceScaleId: '',
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 },
    });

    const candleData: CandlestickData<UTCTimestamp>[] = data.map(item => ({
      time: toChartTime(item.time),
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }));
    const volumeData: HistogramData<UTCTimestamp>[] = data.map(item => ({
      time: toChartTime(item.time),
      value: item.volume,
      color:
        item.close >= item.open
          ? `${FINANCIAL_CHART_COLORS.up}33`
          : `${FINANCIAL_CHART_COLORS.down}33`,
    }));

    candleSeries.setData(candleData);
    volumeSeries.setData(volumeData);
    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver(entries => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [data]);

  return (
    <div className="relative border-b border-slate-100 dark:border-slate-800">
      <div ref={containerRef} className="h-[360px] w-full" />
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-white/60 text-sm text-slate-500 backdrop-blur-sm dark:bg-slate-950/60">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          加载 K线
        </div>
      )}
      {!loading && data.length === 0 && (
        <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-400">
          <CandlestickChart className="mb-2 h-8 w-8 opacity-30" />
          <div className="text-sm font-bold">暂无 K线数据</div>
        </div>
      )}
    </div>
  );
}

function KLineTable({
  data,
}: {
  data: Array<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    amount: number;
  }>;
}) {
  if (data.length === 0) {
    return (
      <EmptyState title="没有明细记录" description="请先同步或调整查询区间。" />
    );
  }
  return (
    <Table wrapperClassName="max-h-[340px]">
      <TableHeader className="sticky top-0 z-10 bg-white dark:bg-slate-950">
        <TableRow>
          <TableHead className="h-9 text-xs">时间</TableHead>
          <TableHead className="h-9 text-right text-xs">开</TableHead>
          <TableHead className="h-9 text-right text-xs">高</TableHead>
          <TableHead className="h-9 text-right text-xs">低</TableHead>
          <TableHead className="h-9 text-right text-xs">收</TableHead>
          <TableHead className="h-9 text-right text-xs">成交量</TableHead>
          <TableHead className="h-9 text-right text-xs">成交额</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map(item => (
          <TableRow key={`${item.time}-${item.close}`}>
            <TableCell className="py-2 font-mono text-xs">
              {formatDateTime(item.time)}
            </TableCell>
            <TableCell className="py-2 text-right font-mono text-xs">
              {formatNumber(item.open, 2)}
            </TableCell>
            <TableCell className="py-2 text-right font-mono text-xs text-market-up">
              {formatNumber(item.high, 2)}
            </TableCell>
            <TableCell className="py-2 text-right font-mono text-xs text-market-down">
              {formatNumber(item.low, 2)}
            </TableCell>
            <TableCell className="py-2 text-right font-mono text-xs">
              {formatNumber(item.close, 2)}
            </TableCell>
            <TableCell className="py-2 text-right font-mono text-xs">
              {formatNumber(item.volume)}
            </TableCell>
            <TableCell className="py-2 text-right font-mono text-xs">
              {formatCompactMoney(item.amount)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function FinancialMetric({
  label,
  value,
}: {
  label: string;
  value?: number | null;
}) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-white/[0.02]">
      <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
        {label}
      </div>
      <div className="mt-1 truncate font-mono text-lg font-black text-slate-900 dark:text-white">
        {formatCompactMoney(value)}
      </div>
    </div>
  );
}

function FinancialRows({
  rows,
  columns,
}: {
  rows: Array<Record<string, unknown>>;
  columns: Array<[string, string]>;
}) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="暂无财务记录"
        description="可点击财务同步后再刷新。"
        compact
      />
    );
  }
  return (
    <Table wrapperClassName="max-h-[360px] rounded-lg border border-slate-200 dark:border-slate-800">
      <TableHeader className="sticky top-0 z-10 bg-white dark:bg-slate-950">
        <TableRow>
          {columns.map(([, label]) => (
            <TableHead key={label} className="h-9 text-xs">
              {label}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row, index) => (
          <TableRow key={`${String(row.reportDate)}-${index}`}>
            {columns.map(([key]) => (
              <TableCell key={key} className="py-2 font-mono text-xs">
                {key.toLowerCase().includes('date')
                  ? String(row[key] ?? '--')
                  : formatCompactMoney(row[key] as number | null | undefined)}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function InfoCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-3 dark:border-slate-800 dark:bg-white/[0.02]">
      <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
        {label}
      </div>
      <div className="mt-1 truncate text-sm font-bold text-slate-800 dark:text-slate-100">
        {value}
      </div>
    </div>
  );
}

function EmptyState({
  title,
  description,
  compact,
}: {
  title: string;
  description: string;
  compact?: boolean;
}) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center text-center text-slate-400',
        compact ? 'py-10' : 'min-h-[220px] p-8'
      )}
    >
      <Table2 className="mb-2 h-8 w-8 opacity-20" />
      <div className="text-sm font-bold text-slate-500 dark:text-slate-300">
        {title}
      </div>
      <div className="mt-1 max-w-sm text-xs">{description}</div>
    </div>
  );
}

function periodLabel(value: string) {
  return (
    KLINE_PERIOD_OPTIONS.find(item => item.value === value)?.label || value
  );
}

function dividendLabel(value: string) {
  return DIVIDEND_OPTIONS.find(item => item.value === value)?.label || value;
}

function toSyncPeriods(value: KLinePeriod) {
  return value === KLinePeriod.Day_1 ? ['1d'] : ['1m'];
}

function resolveCalendarMarket(
  market: string | null | undefined,
  stockCode: string
) {
  const normalizedMarket = (market || '').toUpperCase();
  const normalizedCode = stockCode.toUpperCase();
  if (normalizedMarket.includes('SZ') || normalizedCode.endsWith('.SZ')) {
    return 'SZ';
  }
  return 'SH';
}

function buildKLineCoverage({
  endDate,
  expectedTradingDays,
  klines,
  limit,
  startDate,
}: {
  endDate: string;
  expectedTradingDays: string[];
  klines: Array<{ time: string }>;
  limit: number;
  startDate: string;
}): KLineCoverage {
  const presentDateSet = new Set(
    klines
      .map(item => toDateKey(item.time))
      .filter((value): value is string => Boolean(value))
  );
  const expectedDays = buildExpectedTradingDays({
    endDate,
    tradingDays: expectedTradingDays,
    startDate,
  });
  const coveragePoints: KLineCoveragePoint[] = expectedDays.map(date => ({
    date,
    status: presentDateSet.has(date) ? 'present' : 'missing',
  }));
  const presentDays = coveragePoints.filter(
    point => point.status === 'present'
  ).length;
  const missingRanges = groupMissingRanges(coveragePoints);
  const sortedTimes = klines
    .map(item => item.time)
    .filter(Boolean)
    .sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
  const expectedCount = expectedDays.length;
  const coverageRatio =
    expectedCount > 0 ? presentDays / expectedCount : klines.length > 0 ? 1 : 0;
  const missingDays = Math.max(expectedCount - presentDays, 0);

  return {
    coveragePoints,
    coverageRatio,
    expectedDays: expectedCount,
    firstDataTime: sortedTimes[0],
    lastDataTime: sortedTimes[sortedTimes.length - 1],
    missingDays,
    missingRanges,
    presentDays,
    queryCapped: klines.length >= limit,
    records: klines.length,
    tone: getCoverageTone(coverageRatio, expectedCount, klines.length),
  };
}

function buildExpectedTradingDays({
  endDate,
  startDate,
  tradingDays,
}: {
  endDate: string;
  startDate: string;
  tradingDays: string[];
}) {
  const normalizedStart = toDateKey(startDate);
  const normalizedEnd = toDateKey(endDate);
  const normalizedDays = Array.from(
    new Set(
      tradingDays
        .map(day => toDateKey(day))
        .filter((value): value is string => Boolean(value))
    )
  )
    .filter(day => isDateKeyInRange(day, normalizedStart, normalizedEnd))
    .sort();

  if (normalizedDays.length > 0) return normalizedDays;
  if (!normalizedStart || !normalizedEnd) return [];
  return getWeekdayDateKeys(normalizedStart, normalizedEnd);
}

function groupMissingRanges(points: KLineCoveragePoint[]) {
  const ranges: MissingRange[] = [];
  let activeRange: MissingRange | null = null;

  points.forEach(point => {
    if (point.status === 'missing') {
      if (!activeRange) {
        activeRange = {
          count: 1,
          endDate: point.date,
          startDate: point.date,
        };
        return;
      }
      activeRange.endDate = point.date;
      activeRange.count += 1;
      return;
    }

    if (activeRange) {
      ranges.push(activeRange);
      activeRange = null;
    }
  });

  if (activeRange) ranges.push(activeRange);
  return ranges;
}

function getCoverageTone(
  ratio: number,
  expectedDays: number,
  records: number
): CoverageTone {
  if (expectedDays === 0) return records > 0 ? 'warning' : 'muted';
  if (ratio >= 0.98) return 'good';
  if (ratio >= 0.85) return 'warning';
  return records > 0 ? 'danger' : 'muted';
}

function coverageStatusLabel(coverage: KLineCoverage) {
  if (coverage.expectedDays === 0 && coverage.records === 0) return '无样本';
  if (coverage.tone === 'good') return '覆盖正常';
  if (coverage.tone === 'warning') return '存在缺口';
  if (coverage.tone === 'danger') return '缺口较多';
  return '待同步';
}

function coverageMetricTone(tone: CoverageTone) {
  if (tone === 'good') return 'green';
  if (tone === 'warning') return 'amber';
  if (tone === 'danger') return 'red';
  return 'blue';
}

function formatRatio(value: number) {
  if (!Number.isFinite(value)) return '--';
  return `${Math.round(value * 100)}%`;
}

function formatRangeLabel(range: MissingRange) {
  if (range.startDate === range.endDate) return range.startDate;
  return `${range.startDate} 至 ${range.endDate}`;
}

function formatDateShort(value?: string | null) {
  const dateKey = toDateKey(value);
  return dateKey ? dateKey.slice(5) : '--';
}

function formatTimeShort(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    hour12: false,
    minute: '2-digit',
  });
}

function toDateTimeStart(value: string) {
  return value ? `${value}T00:00:00` : undefined;
}

function toDateTimeEnd(value: string) {
  return value ? `${value}T23:59:59` : undefined;
}

function toCompactDate(value: string) {
  return value.replace(/-/g, '');
}

function toDateKey(value?: string | null) {
  if (!value) return undefined;
  const match = String(value).match(/^(\d{4})-?(\d{2})-?(\d{2})/);
  if (match) return `${match[1]}-${match[2]}-${match[3]}`;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return formatDateInput(date);
}

function isDateKeyInRange(value: string, startDate?: string, endDate?: string) {
  if (startDate && value < startDate) return false;
  if (endDate && value > endDate) return false;
  return true;
}

function getWeekdayDateKeys(startDate: string, endDate: string) {
  const start = parseDateKey(startDate);
  const end = parseDateKey(endDate);
  if (!start || !end || end < start) return [];

  const days: string[] = [];
  for (
    let cursor = start;
    cursor <= end;
    cursor = new Date(cursor.getTime() + DAY_MS)
  ) {
    const day = cursor.getUTCDay();
    if (day !== 0 && day !== 6) days.push(formatUtcDateKey(cursor));
  }
  return days;
}

function parseDateKey(value: string) {
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return undefined;
  return new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]))
  );
}

function formatUtcDateKey(value: Date) {
  const year = value.getUTCFullYear();
  const month = String(value.getUTCMonth() + 1).padStart(2, '0');
  const day = String(value.getUTCDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatDateInput(value: Date) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function toChartTime(value: string): UTCTimestamp {
  return Math.floor(new Date(value).getTime() / 1000) as UTCTimestamp;
}

function formatDateTime(value?: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function formatNumber(value?: number | null, digits = 0) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  return Number(value).toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatMoney(value?: number | null) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatPercent(value?: number | null) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  return `${value >= 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
}

function formatCompactMoney(value?: number | null) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  const abs = Math.abs(Number(value));
  if (abs >= 100000000) return `${(Number(value) / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${(Number(value) / 10000).toFixed(2)}万`;
  return formatNumber(Number(value), 2);
}
