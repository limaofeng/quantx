import { AlertTriangle, Database, RefreshCw } from 'lucide-react';
import * as React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { Button } from '@/components/ui/button';
import type { StockWorkspaceFinancialsQuery } from '@/generated/gql/graphql';
import { cn } from '@/utils/cn';

type FinancialSummary = NonNullable<
  StockWorkspaceFinancialsQuery['financialSummary']
>;
type FinancialStatements = NonNullable<
  StockWorkspaceFinancialsQuery['financialStatements']
>;

type StatementTab = 'income' | 'balance' | 'cashFlow' | 'capital';
type IncomeBasis = 'accumulated' | 'single';

interface StockFinancialPanelProps {
  error?: Error | null;
  isLoading: boolean;
  onRetry: () => void;
  statements?: FinancialStatements | null;
  summary?: FinancialSummary | null;
}

interface TableRow {
  label: string;
  values: Array<number | null | undefined>;
}

interface PeriodTable {
  periods: Array<{ reportDate: string }>;
  rows: TableRow[];
}

const STATEMENT_TABS: Array<{ id: StatementTab; label: string }> = [
  { id: 'income', label: '利润表' },
  { id: 'balance', label: '资产负债表' },
  { id: 'cashFlow', label: '现金流量表' },
  { id: 'capital', label: '股本结构' },
];

function toFiniteNumber(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatCompactNumber(value: unknown) {
  const number = toFiniteNumber(value);
  if (number === null) return '--';
  const absolute = Math.abs(number);
  if (absolute >= 1e8) return `${(number / 1e8).toFixed(2)}亿`;
  if (absolute >= 1e4) return `${(number / 1e4).toFixed(2)}万`;
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
}

function formatReportPeriod(value?: string | null) {
  if (!value) return '--';
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  const month = date.getMonth() + 1;
  const quarter = Math.max(1, Math.ceil(month / 3));
  return `${date.getFullYear()}Q${quarter}`;
}

function formatAxisValue(value: number) {
  const absolute = Math.abs(value);
  if (absolute >= 1e8) return `${(value / 1e8).toFixed(0)}亿`;
  if (absolute >= 1e4) return `${(value / 1e4).toFixed(0)}万`;
  return String(Math.round(value));
}

function calculateChange(current: unknown, previous: unknown) {
  const currentNumber = toFiniteNumber(current);
  const previousNumber = toFiniteNumber(previous);
  if (
    currentNumber === null ||
    previousNumber === null ||
    previousNumber === 0
  ) {
    return null;
  }
  return ((currentNumber - previousNumber) / Math.abs(previousNumber)) * 100;
}

function findPriorYearPeriod<T extends { reportDate: string }>(
  items: T[],
  current?: T
) {
  if (!current) return undefined;
  const year = Number(current.reportDate.slice(0, 4));
  if (!Number.isFinite(year)) return undefined;
  const priorYearDate = `${year - 1}${current.reportDate.slice(4)}`;
  return items.find(item => item.reportDate === priorYearDate);
}

function formatChange(value: number | null) {
  if (value === null) return '同比 --';
  return `同比 ${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function sortByReportDate<T extends { reportDate: string }>(items: T[]) {
  return [...items].sort((left, right) =>
    left.reportDate.localeCompare(right.reportDate)
  );
}

function sameYear(left: string, right: string) {
  return left.slice(0, 4) === right.slice(0, 4);
}

function singlePeriodValue(
  items: Array<{ reportDate: string; value: number | null }>,
  index: number
) {
  const current = items[index];
  if (!current || current.value === null) return null;
  const previous = items[index - 1];
  if (!previous || !sameYear(current.reportDate, previous.reportDate)) {
    return current.value;
  }
  return previous.value === null ? null : current.value - previous.value;
}

function buildIncomeChartData(
  statements: FinancialStatements | null | undefined,
  basis: IncomeBasis
) {
  const income = sortByReportDate(statements?.income ?? []).slice(-8);
  const revenue = income.map(item => ({
    reportDate: item.reportDate,
    value: toFiniteNumber(item.revenue),
  }));
  const profit = income.map(item => ({
    reportDate: item.reportDate,
    value: toFiniteNumber(item.netProfitExclMinIntInc),
  }));

  return income.map((item, index) => ({
    period: formatReportPeriod(item.reportDate),
    revenue:
      basis === 'single'
        ? singlePeriodValue(revenue, index)
        : toFiniteNumber(item.revenue),
    profit:
      basis === 'single'
        ? singlePeriodValue(profit, index)
        : toFiniteNumber(item.netProfitExclMinIntInc),
  }));
}

function buildTable(
  statements: FinancialStatements | null | undefined,
  tab: StatementTab
): PeriodTable {
  if (tab === 'income') {
    const periods = sortByReportDate(statements?.income ?? [])
      .reverse()
      .slice(0, 6);
    return {
      periods,
      rows: [
        { label: '营业收入', values: periods.map(item => item.revenue) },
        {
          label: '营业总成本',
          values: periods.map(item => item.totalOperatingCost),
        },
        { label: '营业利润', values: periods.map(item => item.operProfit) },
        {
          label: '归母净利润',
          values: periods.map(item => item.netProfitExclMinIntInc),
        },
        { label: '基本每股收益', values: periods.map(item => item.epsBasic) },
      ],
    };
  }

  if (tab === 'balance') {
    const periods = sortByReportDate(statements?.balance ?? [])
      .reverse()
      .slice(0, 6);
    return {
      periods,
      rows: [
        { label: '总资产', values: periods.map(item => item.totalAssets) },
        {
          label: '流动资产',
          values: periods.map(item => item.totalCurrentAssets),
        },
        { label: '总负债', values: periods.map(item => item.totalLiabilities) },
        {
          label: '流动负债',
          values: periods.map(item => item.totalCurrentLiability),
        },
        { label: '所有者权益', values: periods.map(item => item.totalEquity) },
      ],
    };
  }

  if (tab === 'cashFlow') {
    const periods = sortByReportDate(statements?.cashFlow ?? [])
      .reverse()
      .slice(0, 6);
    return {
      periods,
      rows: [
        {
          label: '经营活动现金流',
          values: periods.map(item => item.netCashFlowsOperAct),
        },
        {
          label: '投资活动现金流',
          values: periods.map(item => item.netCashFlowsInvAct),
        },
        {
          label: '筹资活动现金流',
          values: periods.map(item => item.netCashFlowsFncAct),
        },
        {
          label: '现金净增加额',
          values: periods.map(item => item.netIncrCashCashEqu),
        },
        {
          label: '期末现金余额',
          values: periods.map(item => item.cashCashEquEndPeriod),
        },
      ],
    };
  }

  const periods = sortByReportDate(statements?.capital ?? [])
    .reverse()
    .slice(0, 6);
  return {
    periods,
    rows: [
      { label: '总股本', values: periods.map(item => item.totalCapital) },
      {
        label: '流通 A 股',
        values: periods.map(item => item.circulatingCapital),
      },
      {
        label: '限售流通股',
        values: periods.map(item => item.restrictCirculatingCapital),
      },
      {
        label: '自由流通股本',
        values: periods.map(item => item.freeFloatCapital),
      },
    ],
  };
}

function MetricCard({
  change,
  label,
  value,
}: {
  change?: number | null;
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0 border border-white/5 bg-[#0f172a]/70 px-3 py-2.5">
      <div className="truncate text-[10px] font-bold text-slate-500">
        {label}
      </div>
      <div className="mt-1 truncate font-mono text-base font-black tabular-nums text-slate-100">
        {value}
      </div>
      <div
        className={cn(
          'mt-1 text-[10px] font-bold',
          change == null
            ? 'text-slate-600'
            : change >= 0
              ? 'text-market-up'
              : 'text-market-down'
        )}
      >
        {formatChange(change ?? null)}
      </div>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="grid h-full min-h-[560px] gap-2 p-2">
      <div className="grid grid-cols-3 gap-2 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, index) => (
          <div key={index} className="h-20 skeleton-shimmer" />
        ))}
      </div>
      <div className="grid gap-2 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <div className="min-h-64 skeleton-shimmer" />
        <div className="min-h-64 skeleton-shimmer" />
      </div>
      <div className="min-h-52 skeleton-shimmer" />
    </div>
  );
}

export function StockFinancialPanel({
  error,
  isLoading,
  onRetry,
  statements,
  summary,
}: StockFinancialPanelProps) {
  const [activeTab, setActiveTab] = React.useState<StatementTab>('income');
  const [basis, setBasis] = React.useState<IncomeBasis>('accumulated');
  const income = sortByReportDate(statements?.income ?? []);
  const latestIncome = income.at(-1);
  const previousIncome = findPriorYearPeriod(income, latestIncome);
  const incomeChartData = React.useMemo(
    () => buildIncomeChartData(statements, basis),
    [basis, statements]
  );
  const table = React.useMemo(
    () => buildTable(statements, activeTab),
    [activeTab, statements]
  );
  const balanceData = [
    { label: '总资产', value: toFiniteNumber(summary?.totalAssets) },
    { label: '总负债', value: toFiniteNumber(summary?.totalLiabilities) },
    { label: '所有者权益', value: toFiniteNumber(summary?.totalEquity) },
  ];
  const hasFinancialData = Boolean(
    summary &&
    (summary.incomeCount > 0 ||
      summary.balanceCount > 0 ||
      summary.cashFlowCount > 0 ||
      summary.capitalCount > 0)
  );

  if (isLoading && !summary && !statements) return <LoadingState />;

  if (error && !summary && !statements) {
    return (
      <div className="flex h-full min-h-[440px] items-center justify-center p-6">
        <div
          role="alert"
          className="max-w-lg border border-rose-400/20 bg-rose-500/10 p-5 text-center"
        >
          <AlertTriangle className="mx-auto h-5 w-5 text-rose-300" />
          <h2 className="mt-3 text-sm font-black text-rose-100">
            财务数据加载失败
          </h2>
          <p className="mt-2 text-xs text-slate-500">{error.message}</p>
          <Button size="sm" className="mt-4" onClick={onRetry}>
            <RefreshCw className="mr-2 h-3.5 w-3.5" />
            重试财务数据
          </Button>
        </div>
      </div>
    );
  }

  if (!hasFinancialData) {
    return (
      <div className="flex h-full min-h-[440px] items-center justify-center p-6">
        <div className="max-w-md text-center">
          <Database className="mx-auto h-6 w-6 text-slate-600" />
          <h2 className="mt-3 text-sm font-black text-slate-200">
            暂无可用财务四表
          </h2>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            当前标的尚未同步财务数据。其他行情与交易功能仍可正常使用。
          </p>
          <Button
            size="sm"
            variant="outline"
            className="mt-4"
            onClick={onRetry}
          >
            重新读取
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-2 overflow-y-auto bg-[#08101d] p-2 custom-scrollbar">
      <div className="flex flex-wrap items-center justify-between gap-2 border border-white/5 bg-[#0b1120]/80 px-3 py-2">
        <div className="min-w-0">
          <div className="text-[10px] font-black uppercase tracking-[0.18em] text-red-300">
            Financials
          </div>
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-2">
            <h2 className="text-sm font-black text-slate-100">财务分析</h2>
            <span className="font-mono text-[10px] font-bold text-slate-500">
              报告期 {formatReportPeriod(summary?.latestReportDate)}
            </span>
            <span className="text-[10px] font-bold text-slate-600">
              公告 {summary?.latestAnnounceDate || '--'}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {(['single', 'accumulated'] as const).map(item => (
            <button
              key={item}
              type="button"
              onClick={() => setBasis(item)}
              className={cn(
                'h-7 border px-3 text-[10px] font-black transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500/70',
                basis === item
                  ? 'border-red-500/30 bg-red-500/15 text-red-100'
                  : 'border-white/5 text-slate-500 hover:bg-white/[0.04] hover:text-slate-200'
              )}
            >
              {item === 'single' ? '单季' : '累计'}
            </button>
          ))}
          <Button
            size="sm"
            variant="ghost"
            className="ml-1 h-7 px-2 text-[10px] text-slate-500"
            onClick={onRetry}
          >
            <RefreshCw
              className={cn('mr-1.5 h-3 w-3', isLoading && 'animate-spin')}
            />
            刷新
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 2xl:grid-cols-6">
        <MetricCard
          label="营业收入"
          value={formatCompactNumber(summary?.revenue)}
          change={calculateChange(
            latestIncome?.revenue,
            previousIncome?.revenue
          )}
        />
        <MetricCard
          label="归母净利润"
          value={formatCompactNumber(summary?.netProfitExclMinIntInc)}
          change={calculateChange(
            latestIncome?.netProfitExclMinIntInc,
            previousIncome?.netProfitExclMinIntInc
          )}
        />
        <MetricCard
          label="基本 EPS"
          value={formatCompactNumber(summary?.epsBasic)}
          change={calculateChange(
            latestIncome?.epsBasic,
            previousIncome?.epsBasic
          )}
        />
        <MetricCard
          label="总资产"
          value={formatCompactNumber(summary?.totalAssets)}
        />
        <MetricCard
          label="总负债"
          value={formatCompactNumber(summary?.totalLiabilities)}
        />
        <MetricCard
          label="经营现金流"
          value={formatCompactNumber(summary?.operatingCashFlow)}
        />
      </div>

      <div className="grid min-h-[300px] gap-2 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <section className="min-w-0 border border-white/5 bg-[#0b1120]/75 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h3 className="text-xs font-black text-slate-200">
              营业收入与归母净利润
            </h3>
            <span className="text-[10px] font-bold text-slate-600">
              单位：元
            </span>
          </div>
          <div className="h-[255px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart
                data={incomeChartData}
                margin={{ left: 0, right: 8, top: 8, bottom: 0 }}
              >
                <CartesianGrid
                  stroke="rgba(148,163,184,0.08)"
                  vertical={false}
                />
                <XAxis
                  dataKey="period"
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tickFormatter={formatAxisValue}
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                  width={54}
                />
                <Tooltip
                  contentStyle={{
                    background: '#0f172a',
                    border: '1px solid rgba(255,255,255,0.08)',
                    color: '#e2e8f0',
                    fontSize: 11,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 10, color: '#94a3b8' }} />
                <Bar
                  dataKey="revenue"
                  name="营业收入"
                  fill="#ef4444"
                  fillOpacity={0.68}
                  radius={[2, 2, 0, 0]}
                />
                <Line
                  dataKey="profit"
                  name="归母净利润"
                  stroke="#f8fafc"
                  strokeWidth={1.5}
                  dot={{ r: 2, fill: '#f8fafc' }}
                  connectNulls={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>

        <section className="min-w-0 border border-white/5 bg-[#0b1120]/75 p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <h3 className="text-xs font-black text-slate-200">
              资产负债与权益结构
            </h3>
            <span className="font-mono text-[10px] font-bold text-slate-600">
              {formatReportPeriod(summary?.latestReportDate)}
            </span>
          </div>
          <div className="h-[255px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={balanceData}
                layout="vertical"
                margin={{ left: 8, right: 16, top: 8, bottom: 0 }}
              >
                <CartesianGrid
                  stroke="rgba(148,163,184,0.08)"
                  horizontal={false}
                />
                <XAxis
                  type="number"
                  tickFormatter={formatAxisValue}
                  tick={{ fill: '#64748b', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="label"
                  tick={{ fill: '#94a3b8', fontSize: 10 }}
                  axisLine={false}
                  tickLine={false}
                  width={66}
                />
                <Tooltip
                  contentStyle={{
                    background: '#0f172a',
                    border: '1px solid rgba(255,255,255,0.08)',
                    color: '#e2e8f0',
                    fontSize: 11,
                  }}
                />
                <Bar
                  dataKey="value"
                  name="金额"
                  fill="#38bdf8"
                  fillOpacity={0.72}
                  radius={[0, 2, 2, 0]}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>

      <section className="min-h-[250px] border border-white/5 bg-[#0b1120]/75">
        <div className="flex min-h-10 flex-wrap items-center justify-between gap-2 border-b border-white/5 px-3">
          <div className="flex max-w-full overflow-x-auto no-scrollbar">
            {STATEMENT_TABS.map(tab => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'relative h-10 shrink-0 px-3 text-[11px] font-black transition-colors after:absolute after:inset-x-3 after:bottom-0 after:h-0.5',
                  activeTab === tab.id
                    ? 'text-red-100 after:bg-red-400'
                    : 'text-slate-500 after:bg-transparent hover:text-slate-200'
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
          <span className="font-mono text-[10px] font-bold text-slate-600">
            {table.periods.length} 个报告期
          </span>
        </div>
        <div className="max-h-[340px] overflow-auto custom-scrollbar">
          <table className="w-full min-w-[760px] border-collapse text-left">
            <thead className="sticky top-0 z-10 bg-[#0b1120]">
              <tr className="border-b border-white/5">
                <th className="sticky left-0 z-20 min-w-36 bg-[#0b1120] px-3 py-2 text-[10px] font-black text-slate-500">
                  指标
                </th>
                {table.periods.map(period => (
                  <th
                    key={period.reportDate}
                    className="min-w-28 px-3 py-2 text-right font-mono text-[10px] font-black text-slate-500"
                  >
                    {formatReportPeriod(period.reportDate)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map(row => (
                <tr
                  key={row.label}
                  className="border-b border-white/5 transition-colors hover:bg-white/[0.025]"
                >
                  <th className="sticky left-0 bg-[#0b1120] px-3 py-2.5 text-[11px] font-bold text-slate-300">
                    {row.label}
                  </th>
                  {row.values.map((value, index) => (
                    <td
                      key={`${row.label}-${table.periods[index]?.reportDate || index}`}
                      className="px-3 py-2.5 text-right font-mono text-[11px] font-bold tabular-nums text-slate-300"
                    >
                      {formatCompactNumber(value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
