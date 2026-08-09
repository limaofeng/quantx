import {
  ArrowLeft,
  ArrowDownZA,
  ArrowUpAZ,
  Banknote,
  TrendingUp,
  FileText,
  Clock,
  PieChart,
  Search,
  Filter,
  Columns3,
  Copy,
  Eye,
  RefreshCw,
  Loader2,
  ListFilter,
  LayoutList,
  Briefcase,
  Star,
  X,
  Pin,
} from 'lucide-react';
import React, { useEffect, useMemo, useState } from 'react';
import type { Client } from 'urql';
import { useClient, useQuery } from 'urql';
import { useLocation } from 'wouter';

import {
  StudioDataTable,
  StudioMenu,
  useStudioMenu,
} from '@/components/studio-workbench';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet';
import { gql } from '@/generated/gql';
import { useWatchlist } from '@/hooks/useWatchlist';
import { cn } from '@/utils/cn';

import { DataStudioPageFrame } from '../components/DataStudioPageFrame';
import { DeploymentSyncControl } from '../components/DeploymentSyncControl';

const FINANCIAL_DATA_PAGE_QUERY = gql(`
  query FinancialDataPage($search: String, $limit: Int!, $offset: Int!) {
    financialOverview {
      reportCount
      instrumentCount
      latestReportDate
      latestAnnounceDate
    }
    financialReports(search: $search, limit: $limit, offset: $offset) {
      total
      items {
        stockCode
        stockName
        reportDate
        announceDate
        revenue
        netProfitExclMinIntInc
        epsBasic
      }
    }
  }
`);

const FINANCIAL_HOLDINGS_QUERY = gql(`
  query FinancialDataHoldings {
    positions {
      stockCode
      volume
    }
  }
`);

type QuickFilter = 'all' | 'holdings' | 'watchlist';
type ReportPeriodFilter = 'all' | 'latest' | 'annual' | 'q3' | 'half' | 'q1';
type DisclosureFilter = 'all' | 'disclosed' | 'undisclosed';
type ProfitFilter = 'all' | 'profit' | 'loss';
type FinancialSortDirection = 'asc' | 'desc';

type FinancialReportItem = {
  stockCode: string;
  stockName?: string | null;
  reportDate?: string | null;
  announceDate?: string | null;
  revenue?: number | null;
  netProfitExclMinIntInc?: number | null;
  epsBasic?: number | null;
};

type FinancialSummaryItem = {
  stockCode?: string | null;
  latestReportDate?: string | null;
  latestAnnounceDate?: string | null;
  revenue?: number | null;
  netProfitExclMinIntInc?: number | null;
  epsBasic?: number | null;
  totalAssets?: number | null;
  totalLiabilities?: number | null;
  totalEquity?: number | null;
  operatingCashFlow?: number | null;
  cashBalance?: number | null;
  totalCapital?: number | null;
  circulatingCapital?: number | null;
  incomeCount?: number | null;
  balanceCount?: number | null;
  cashFlowCount?: number | null;
  capitalCount?: number | null;
};

type FinancialColumnId =
  | 'stock'
  | 'reportDate'
  | 'announceDate'
  | 'status'
  | 'revenue'
  | 'netProfit'
  | 'epsBasic'
  | 'netProfitMargin'
  | 'totalAssets'
  | 'totalLiabilities'
  | 'totalEquity'
  | 'assetLiabilityRatio'
  | 'operatingCashFlow'
  | 'cashBalance'
  | 'totalCapital'
  | 'circulatingCapital'
  | 'statementCounts';

type FinancialColumn = {
  id: FinancialColumnId;
  label: string;
  group: string;
  width: number;
  align?: 'left' | 'right' | 'center';
  defaultVisible?: boolean;
  alwaysVisible?: boolean;
  defaultPinned?: boolean;
};

type FinancialTableColumn = FinancialColumn & {
  alwaysFrozen?: boolean;
  sortField: FinancialColumnId;
};

type FinancialTableMenuPayload =
  | { column: FinancialColumn; kind: 'column' }
  | { item: FinancialReportItem; kind: 'row' };

type FinancialSortState = {
  columnId: FinancialColumnId;
  direction: FinancialSortDirection;
};

function copyText(value: string | number | undefined | null) {
  if (value === undefined || value === null || value === '') return;
  void navigator.clipboard?.writeText(String(value));
}

const FINANCIAL_TABLE_COLUMNS_STORAGE_KEY = 'quantx_financial_table_columns';

const FINANCIAL_COLUMNS: FinancialColumn[] = [
  {
    id: 'stock',
    label: '股票',
    group: '基础',
    width: 190,
    align: 'left',
    defaultVisible: true,
    alwaysVisible: true,
    defaultPinned: true,
  },
  {
    id: 'reportDate',
    label: '报告期',
    group: '基础',
    width: 132,
    align: 'left',
    defaultVisible: true,
  },
  {
    id: 'announceDate',
    label: '披露日期',
    group: '基础',
    width: 132,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'status',
    label: '状态',
    group: '基础',
    width: 100,
    align: 'center',
    defaultVisible: true,
  },
  {
    id: 'revenue',
    label: '营业收入',
    group: '利润表',
    width: 136,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'netProfit',
    label: '归母净利润',
    group: '利润表',
    width: 136,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'epsBasic',
    label: '基本 EPS',
    group: '利润表',
    width: 108,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'netProfitMargin',
    label: '净利率',
    group: '利润表',
    width: 100,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'totalAssets',
    label: '总资产',
    group: '资产负债表',
    width: 136,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'totalLiabilities',
    label: '总负债',
    group: '资产负债表',
    width: 136,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'totalEquity',
    label: '所有者权益',
    group: '资产负债表',
    width: 136,
    align: 'right',
  },
  {
    id: 'assetLiabilityRatio',
    label: '资产负债率',
    group: '资产负债表',
    width: 116,
    align: 'right',
  },
  {
    id: 'operatingCashFlow',
    label: '经营现金流',
    group: '现金流量表',
    width: 136,
    align: 'right',
    defaultVisible: true,
  },
  {
    id: 'cashBalance',
    label: '现金余额',
    group: '现金流量表',
    width: 136,
    align: 'right',
  },
  {
    id: 'totalCapital',
    label: '总股本',
    group: '股本结构',
    width: 120,
    align: 'right',
  },
  {
    id: 'circulatingCapital',
    label: '流通股本',
    group: '股本结构',
    width: 120,
    align: 'right',
  },
  {
    id: 'statementCounts',
    label: '四表记录',
    group: '数据状态',
    width: 120,
    align: 'center',
  },
];

const FINANCIAL_COLUMN_BY_ID = new Map(
  FINANCIAL_COLUMNS.map(column => [column.id, column])
);
const DEFAULT_VISIBLE_FINANCIAL_COLUMN_IDS = FINANCIAL_COLUMNS.filter(
  column => column.defaultVisible || column.alwaysVisible
).map(column => column.id);
const DEFAULT_PINNED_FINANCIAL_COLUMN_IDS = FINANCIAL_COLUMNS.filter(
  column => column.defaultPinned
).map(column => column.id);
const SUMMARY_COLUMN_IDS = new Set<FinancialColumnId>([
  'totalAssets',
  'totalLiabilities',
  'totalEquity',
  'assetLiabilityRatio',
  'operatingCashFlow',
  'cashBalance',
  'totalCapital',
  'circulatingCapital',
  'statementCounts',
]);

export function FinancialDataPage() {
  const client = useClient();
  const [, setLocation] = useLocation();
  const [showFilters, setShowFilters] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [quickFilter, setQuickFilter] = useState<QuickFilter>('all');
  const [reportPeriodFilter, setReportPeriodFilter] =
    useState<ReportPeriodFilter>('all');
  const [disclosureFilter, setDisclosureFilter] =
    useState<DisclosureFilter>('all');
  const [profitFilter, setProfitFilter] = useState<ProfitFilter>('all');
  const [revenueMinYi, setRevenueMinYi] = useState('');
  const [revenueMaxYi, setRevenueMaxYi] = useState('');
  const [netProfitMinYi, setNetProfitMinYi] = useState('');
  const [netProfitMaxYi, setNetProfitMaxYi] = useState('');
  const [filteredReports, setFilteredReports] = useState<FinancialReportItem[]>(
    []
  );
  const [filteredFetching, setFilteredFetching] = useState(false);
  const [summaryByCode, setSummaryByCode] = useState<
    Record<string, FinancialSummaryItem>
  >({});
  const [summaryFetching, setSummaryFetching] = useState(false);
  const [visibleColumnIds, setVisibleColumnIds] = useState<FinancialColumnId[]>(
    () => readFinancialColumnSettings().visible
  );
  const [pinnedColumnIds, setPinnedColumnIds] = useState<FinancialColumnId[]>(
    () => readFinancialColumnSettings().pinned
  );
  const [sortState, setSortState] = useState<FinancialSortState | null>(null);
  const {
    closeMenu: closeTableMenu,
    menu: tableMenu,
    openAtPointer: openTableMenuAtPointer,
  } = useStudioMenu<FinancialTableMenuPayload>();

  const [{ data: holdingsData, fetching: holdingsFetching }] = useQuery({
    query: FINANCIAL_HOLDINGS_QUERY,
  });
  const { codes: watchlistCodes, fetching: watchlistFetching } = useWatchlist();

  const normalizedSearch = searchTerm.trim() || undefined;
  const holdingCodes = useMemo(
    () =>
      uniqueStockCodes(
        (holdingsData?.positions ?? [])
          .filter(position => Number(position.volume ?? 0) > 0)
          .map(position => position.stockCode)
      ),
    [holdingsData?.positions]
  );
  const scopedStockCodes =
    quickFilter === 'holdings'
      ? holdingCodes
      : quickFilter === 'watchlist'
        ? watchlistCodes
        : undefined;
  const scopedStockCodesKey = scopedStockCodes?.join('|') ?? '';

  const [{ data, fetching }, reloadFinancialData] = useQuery({
    query: FINANCIAL_DATA_PAGE_QUERY,
    variables: {
      search: normalizedSearch,
      limit: 100,
      offset: 0,
    },
  });

  const overview = data?.financialOverview;
  const reports = useMemo(() => {
    const baseReports =
      quickFilter === 'all'
        ? (data?.financialReports.items ?? [])
        : filteredReports;
    return applyFinancialFilters(baseReports, {
      reportPeriod: reportPeriodFilter,
      disclosure: disclosureFilter,
      profit: profitFilter,
      revenueMinYi,
      revenueMaxYi,
      netProfitMinYi,
      netProfitMaxYi,
      latestReportDate: overview?.latestReportDate,
    });
  }, [
    data?.financialReports.items,
    disclosureFilter,
    filteredReports,
    netProfitMaxYi,
    netProfitMinYi,
    overview?.latestReportDate,
    profitFilter,
    quickFilter,
    reportPeriodFilter,
    revenueMaxYi,
    revenueMinYi,
  ]);
  const displayReports = useMemo(() => {
    if (!sortState) return reports;

    const direction = sortState.direction === 'asc' ? 1 : -1;
    return [...reports].sort((left, right) => {
      const leftSummary = summaryByCode[left.stockCode];
      const rightSummary = summaryByCode[right.stockCode];
      const leftValue = getFinancialSortValue(
        sortState.columnId,
        left,
        leftSummary
      );
      const rightValue = getFinancialSortValue(
        sortState.columnId,
        right,
        rightSummary
      );

      if (typeof leftValue === 'number' && typeof rightValue === 'number') {
        return (leftValue - rightValue) * direction;
      }

      return (
        String(leftValue).localeCompare(String(rightValue), 'zh-CN') * direction
      );
    });
  }, [reports, sortState, summaryByCode]);
  const activeFetching =
    quickFilter === 'all'
      ? fetching
      : filteredFetching ||
        (quickFilter === 'holdings' && holdingsFetching) ||
        (quickFilter === 'watchlist' && watchlistFetching);
  const isFilteringReady = quickFilter === 'all' || !activeFetching;
  const activeFilterCount = [
    quickFilter !== 'all',
    reportPeriodFilter !== 'all',
    disclosureFilter !== 'all',
    profitFilter !== 'all',
    Boolean(revenueMinYi.trim() || revenueMaxYi.trim()),
    Boolean(netProfitMinYi.trim() || netProfitMaxYi.trim()),
  ].filter(Boolean).length;
  const quickFilters = [
    {
      value: 'all' as const,
      label: '全部',
      count: overview?.instrumentCount,
      icon: ListFilter,
    },
    {
      value: 'holdings' as const,
      label: '持仓股',
      count: holdingCodes.length,
      icon: Briefcase,
    },
    {
      value: 'watchlist' as const,
      label: '自选股',
      count: watchlistCodes.length,
      icon: Star,
    },
  ];
  const visibleColumns = useMemo(
    () =>
      FINANCIAL_COLUMNS.filter(column => visibleColumnIds.includes(column.id)),
    [visibleColumnIds]
  );
  const tableColumns = useMemo<FinancialTableColumn[]>(
    () =>
      visibleColumns.map(column => ({
        ...column,
        alwaysFrozen: column.defaultPinned,
        sortField: column.id,
      })),
    [visibleColumns]
  );
  const summaryColumnsVisible = visibleColumns.some(column =>
    SUMMARY_COLUMN_IDS.has(column.id)
  );
  const selectedColumnCount = visibleColumns.length;
  const reportCodesKey = reports.map(item => item.stockCode).join('|');

  const openStockDetail = (stockCode: string) => {
    setLocation(`/settings/data/${encodeURIComponent(stockCode)}`);
  };

  const resetFilters = () => {
    setQuickFilter('all');
    setReportPeriodFilter('all');
    setDisclosureFilter('all');
    setProfitFilter('all');
    setRevenueMinYi('');
    setRevenueMaxYi('');
    setNetProfitMinYi('');
    setNetProfitMaxYi('');
  };

  const resetColumnSettings = () => {
    setVisibleColumnIds(DEFAULT_VISIBLE_FINANCIAL_COLUMN_IDS);
    setPinnedColumnIds(DEFAULT_PINNED_FINANCIAL_COLUMN_IDS);
  };

  const toggleColumnVisibility = (
    column: FinancialColumn,
    checked: boolean | 'indeterminate'
  ) => {
    if (column.alwaysVisible) return;
    const shouldShow = checked === true;

    setVisibleColumnIds(current =>
      normalizeFinancialColumnIds(
        shouldShow
          ? [...current, column.id]
          : current.filter(id => id !== column.id),
        'visible'
      )
    );

    if (!shouldShow) {
      setPinnedColumnIds(current =>
        normalizeFinancialColumnIds(
          current.filter(id => id !== column.id),
          'pinned'
        )
      );
    }
  };

  const toggleColumnPinned = (column: FinancialColumn) => {
    if (!visibleColumnIds.includes(column.id) || column.defaultPinned) return;

    setPinnedColumnIds(current =>
      normalizeFinancialColumnIds(
        current.includes(column.id)
          ? current.filter(id => id !== column.id)
          : [...current, column.id],
        'pinned',
        visibleColumnIds
      )
    );
  };

  const updatePinnedColumnIds = (ids: string[]) => {
    setPinnedColumnIds(
      normalizeFinancialColumnIds(
        ids.filter((id): id is FinancialColumnId =>
          FINANCIAL_COLUMN_BY_ID.has(id as FinancialColumnId)
        ),
        'pinned',
        visibleColumnIds
      )
    );
  };

  const toggleFinancialColumnSort = (column: FinancialTableColumn) => {
    setSortState(current => {
      if (!current || current.columnId !== column.id) {
        return { columnId: column.id, direction: 'desc' };
      }
      if (current.direction === 'desc') {
        return { columnId: column.id, direction: 'asc' };
      }
      return null;
    });
  };

  const renderFinancialSortIndicator = (column: FinancialTableColumn) => {
    if (sortState?.columnId !== column.id) {
      return <span className="h-3 w-3 text-slate-700" />;
    }

    return sortState.direction === 'asc' ? (
      <ArrowUpAZ className="h-3.5 w-3.5 text-red-300" />
    ) : (
      <ArrowDownZA className="h-3.5 w-3.5 text-red-300" />
    );
  };

  useEffect(() => {
    if (quickFilter === 'all') {
      setFilteredReports([]);
      setFilteredFetching(false);
      return;
    }

    if (quickFilter === 'holdings' && holdingsFetching) {
      setFilteredFetching(true);
      return;
    }

    const codes = scopedStockCodes ?? [];
    if (codes.length === 0) {
      setFilteredReports([]);
      setFilteredFetching(false);
      return;
    }

    let cancelled = false;
    setFilteredFetching(true);

    fetchFinancialReportsForCodes(client, codes, normalizedSearch)
      .then(items => {
        if (!cancelled) setFilteredReports(items);
      })
      .catch(() => {
        if (!cancelled) setFilteredReports([]);
      })
      .finally(() => {
        if (!cancelled) setFilteredFetching(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    client,
    holdingsFetching,
    normalizedSearch,
    quickFilter,
    scopedStockCodesKey,
    scopedStockCodes,
  ]);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    window.localStorage.setItem(
      FINANCIAL_TABLE_COLUMNS_STORAGE_KEY,
      JSON.stringify({
        visible: visibleColumnIds,
        pinned: pinnedColumnIds,
      })
    );
  }, [pinnedColumnIds, visibleColumnIds]);

  useEffect(() => {
    const stockCodes = uniqueStockCodes(reports.map(item => item.stockCode));

    if (!summaryColumnsVisible || stockCodes.length === 0) {
      setSummaryByCode({});
      setSummaryFetching(false);
      return;
    }

    let cancelled = false;
    setSummaryFetching(true);

    fetchFinancialSummariesForCodes(client, stockCodes)
      .then(summaries => {
        if (!cancelled) setSummaryByCode(summaries);
      })
      .catch(() => {
        if (!cancelled) setSummaryByCode({});
      })
      .finally(() => {
        if (!cancelled) setSummaryFetching(false);
      });

    return () => {
      cancelled = true;
    };
  }, [client, reportCodesKey, reports, summaryColumnsVisible]);

  return (
    <DataStudioPageFrame
      activeMode="FINANCIAL"
      description="财报、指标、财务快照"
      title="财务数据"
    >
      <div className="flex flex-col gap-4 pb-10 animate-fade-in">
        {/* Compact Header Section */}
        <div className="flex items-center justify-between gap-4 py-1">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 rounded-lg bg-white/50 dark:bg-white/5 border border-slate-200/60 dark:border-white/5 shadow-sm hover:scale-105 active:scale-95 transition-all backdrop-blur-sm"
              onClick={() => setLocation('/settings/data')}
            >
              <ArrowLeft className="w-4 h-4 text-slate-600 dark:text-slate-400" />
            </Button>
            <div>
              <h1 className="text-lg font-black text-slate-900 dark:text-white tracking-tight leading-none">
                财务数据
              </h1>
              <p className="text-[10px] font-bold text-slate-400 dark:text-slate-500 uppercase tracking-widest mt-0.5 opacity-80">
                FINANCIAL REPORTS & STATEMENTS
              </p>
            </div>
          </div>

          <DeploymentSyncControl
            deploymentName="financial-sync"
            defaultFlowName="财务数据同步"
            successMessage="财务数据同步任务已提交"
          />
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <Card className="border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 shadow-sm p-5 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-red-50 text-red-700 dark:bg-red-950 dark:text-red-500">
              <FileText className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                已入库报表
              </p>
              <p className="text-2xl font-black text-slate-900 dark:text-white mt-1">
                {formatNumber(overview?.reportCount)}
              </p>
            </div>
          </Card>

          <Card className="border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 shadow-sm p-5 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-red-50 text-red-600 dark:bg-red-950 dark:text-red-400">
              <PieChart className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                覆盖标的
              </p>
              <p className="text-2xl font-black text-slate-900 dark:text-white mt-1">
                {formatNumber(overview?.instrumentCount)}
              </p>
            </div>
          </Card>

          <Card className="border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-950 shadow-sm p-5 flex items-center gap-4">
            <div className="p-3 rounded-xl bg-orange-50 text-orange-600 dark:bg-orange-950 dark:text-orange-400">
              <TrendingUp className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs font-medium text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                最新报告期
              </p>
              <p className="text-2xl font-black text-slate-900 dark:text-white mt-1">
                {overview?.latestReportDate || '--'}
              </p>
            </div>
          </Card>
        </div>

        {/* Main Content: Data Table */}
        <div className="flex min-h-[500px] flex-col overflow-hidden rounded-md border border-white/10 bg-[#050915] shadow-sm">
          <div className="flex flex-col gap-2 border-b border-white/5 bg-slate-900/40 px-4 py-2 md:flex-row md:items-center md:justify-between">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <LayoutList className="h-4 w-4 shrink-0 text-slate-400" />
              <span className="truncate text-xs font-bold text-slate-200">
                上市公司财报明细
              </span>
              <Badge
                variant="outline"
                className="font-mono text-[10px] font-normal text-slate-400"
              >
                总数: {displayReports.length}
              </Badge>
              <Badge
                variant="outline"
                className="font-mono text-[10px] font-normal text-slate-400"
              >
                字段: {selectedColumnCount}
              </Badge>
              {activeFilterCount > 0 && (
                <Badge
                  variant="outline"
                  className="border-red-500/30 bg-red-500/10 font-mono text-[10px] font-normal text-red-300"
                >
                  筛选: {activeFilterCount}
                </Badge>
              )}
            </div>

            <div className="w-full md:w-auto">
              <div className="flex w-full items-center gap-2 md:w-auto">
                <div className="relative flex-1 md:w-64">
                  <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
                  <Input
                    placeholder="搜索股票代码或名称..."
                    className="h-7 rounded-[4px] border-white/10 bg-[#0b1120]/80 pl-8 text-xs text-slate-200 placeholder:text-slate-600 focus-visible:ring-red-500/30"
                    value={searchTerm}
                    onChange={e => setSearchTerm(e.target.value)}
                  />
                </div>
                <Sheet open={showFilters} onOpenChange={setShowFilters}>
                  <SheetTrigger asChild>
                    <Button
                      variant="outline"
                      size="icon"
                      className={cn(
                        'relative h-7 w-7 shrink-0 rounded-[4px] border-white/10 bg-transparent text-slate-400 hover:bg-white/[0.06] hover:text-white',
                        activeFilterCount > 0 &&
                          'border-red-500/40 bg-red-500/10 text-red-300'
                      )}
                      title="筛选财务列表"
                    >
                      <Filter className="h-3.5 w-3.5" />
                      {activeFilterCount > 0 && (
                        <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 font-mono text-[10px] leading-none text-white">
                          {activeFilterCount}
                        </span>
                      )}
                    </Button>
                  </SheetTrigger>
                  <SheetContent
                    side="right"
                    className="w-[92vw] overflow-y-auto border-slate-200 bg-white p-0 dark:border-slate-800 dark:bg-slate-950 sm:max-w-[420px]"
                  >
                    <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
                      <div>
                        <div className="text-sm font-black text-slate-900 dark:text-slate-100">
                          筛选条件
                        </div>
                        <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400">
                          Financial filters
                        </div>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="mr-8 h-7 px-2 text-xs"
                        onClick={resetFilters}
                        disabled={activeFilterCount === 0}
                      >
                        <X className="mr-1 h-3.5 w-3.5" />
                        重置
                      </Button>
                    </div>

                    <div className="space-y-5 p-5">
                      <div className="space-y-2">
                        <FilterLabel>快捷范围</FilterLabel>
                        <div className="grid grid-cols-3 gap-2">
                          {quickFilters.map(filter => {
                            const Icon = filter.icon;
                            const active = quickFilter === filter.value;
                            return (
                              <button
                                key={filter.value}
                                type="button"
                                className={cn(
                                  'rounded-lg border px-2.5 py-2 text-left transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500',
                                  active
                                    ? 'border-red-300 bg-red-50 text-red-700 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300'
                                    : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50 dark:border-slate-800 dark:bg-slate-950 dark:text-slate-300 dark:hover:bg-slate-900'
                                )}
                                onClick={() => setQuickFilter(filter.value)}
                              >
                                <div className="flex items-center gap-1.5 text-xs font-bold">
                                  <Icon className="h-3.5 w-3.5" />
                                  {filter.label}
                                </div>
                                <div className="mt-1 font-mono text-[11px] text-slate-400">
                                  {formatNumber(filter.count)}
                                </div>
                              </button>
                            );
                          })}
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-3">
                        <div className="space-y-2">
                          <FilterLabel>报告期</FilterLabel>
                          <Select
                            value={reportPeriodFilter}
                            onValueChange={value =>
                              setReportPeriodFilter(value as ReportPeriodFilter)
                            }
                          >
                            <SelectTrigger className="h-9 text-xs">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">全部</SelectItem>
                              <SelectItem value="latest">最新期</SelectItem>
                              <SelectItem value="annual">年报</SelectItem>
                              <SelectItem value="q3">三季报</SelectItem>
                              <SelectItem value="half">中报</SelectItem>
                              <SelectItem value="q1">一季报</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>

                        <div className="space-y-2">
                          <FilterLabel>披露状态</FilterLabel>
                          <Select
                            value={disclosureFilter}
                            onValueChange={value =>
                              setDisclosureFilter(value as DisclosureFilter)
                            }
                          >
                            <SelectTrigger className="h-9 text-xs">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="all">全部</SelectItem>
                              <SelectItem value="disclosed">已披露</SelectItem>
                              <SelectItem value="undisclosed">
                                未披露
                              </SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>

                      <div className="space-y-2">
                        <FilterLabel>盈利状态</FilterLabel>
                        <div className="grid grid-cols-3 gap-2">
                          {[
                            ['all', '全部'],
                            ['profit', '盈利'],
                            ['loss', '亏损'],
                          ].map(([value, label]) => (
                            <button
                              key={value}
                              type="button"
                              className={cn(
                                'h-8 rounded-lg border text-xs font-bold transition-colors cursor-pointer',
                                profitFilter === value
                                  ? 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-300'
                                  : 'border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-800 dark:text-slate-400 dark:hover:bg-slate-900'
                              )}
                              onClick={() =>
                                setProfitFilter(value as ProfitFilter)
                              }
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <RangeFilter
                        label="营业收入"
                        minValue={revenueMinYi}
                        maxValue={revenueMaxYi}
                        onMinChange={setRevenueMinYi}
                        onMaxChange={setRevenueMaxYi}
                      />

                      <RangeFilter
                        label="归母净利润"
                        minValue={netProfitMinYi}
                        maxValue={netProfitMaxYi}
                        onMinChange={setNetProfitMinYi}
                        onMaxChange={setNetProfitMaxYi}
                      />
                    </div>
                  </SheetContent>
                </Sheet>
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      size="icon"
                      className="relative h-7 w-7 shrink-0 rounded-[4px] border-white/10 bg-transparent text-slate-400 hover:bg-white/[0.06] hover:text-white"
                      title="配置显示字段"
                    >
                      <Columns3 className="h-3.5 w-3.5" />
                      <span className="absolute -right-1 -top-1 flex h-4 min-w-4 items-center justify-center rounded-full bg-slate-900 px-1 font-mono text-[10px] leading-none text-white dark:bg-slate-100 dark:text-slate-900">
                        {selectedColumnCount}
                      </span>
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent
                    align="end"
                    side="top"
                    sideOffset={10}
                    className="max-h-[300px] w-80 overflow-hidden border-slate-200 bg-white p-0 shadow-xl dark:border-slate-800 dark:bg-slate-950"
                  >
                    <div className="border-b border-slate-100 px-4 py-3 dark:border-slate-800">
                      <div className="text-sm font-black text-slate-900 dark:text-slate-100">
                        显示字段
                      </div>
                      <div className="text-[10px] font-bold uppercase tracking-widest text-slate-400">
                        Columns & pinned fields
                      </div>
                    </div>

                    <div className="max-h-[188px] overflow-y-auto p-3">
                      {[
                        '基础',
                        '利润表',
                        '资产负债表',
                        '现金流量表',
                        '股本结构',
                        '数据状态',
                      ].map(group => {
                        const columns = FINANCIAL_COLUMNS.filter(
                          column => column.group === group
                        );

                        return (
                          <div key={group} className="mb-3 last:mb-0">
                            <FilterLabel>{group}</FilterLabel>
                            <div className="mt-2 space-y-1">
                              {columns.map(column => {
                                const visible = visibleColumnIds.includes(
                                  column.id
                                );
                                const pinned = pinnedColumnIds.includes(
                                  column.id
                                );

                                return (
                                  <div
                                    key={column.id}
                                    className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-slate-50 dark:hover:bg-slate-900"
                                  >
                                    <Checkbox
                                      checked={visible}
                                      disabled={column.alwaysVisible}
                                      onCheckedChange={checked =>
                                        toggleColumnVisibility(column, checked)
                                      }
                                    />
                                    <button
                                      type="button"
                                      className="min-w-0 flex-1 text-left text-xs font-bold text-slate-700 dark:text-slate-200"
                                      onClick={() =>
                                        toggleColumnVisibility(column, !visible)
                                      }
                                      disabled={column.alwaysVisible}
                                    >
                                      {column.label}
                                    </button>
                                    <button
                                      type="button"
                                      aria-pressed={pinned}
                                      title={
                                        pinned ? '取消固定列' : '固定到左侧'
                                      }
                                      className={cn(
                                        'flex h-7 w-7 items-center justify-center rounded-md border transition-colors',
                                        pinned
                                          ? 'border-red-300 bg-red-50 text-red-600 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-300'
                                          : 'border-slate-200 text-slate-400 hover:text-slate-700 dark:border-slate-800 dark:hover:text-slate-200',
                                        (!visible || column.defaultPinned) &&
                                          'cursor-not-allowed opacity-50'
                                      )}
                                      onClick={() => toggleColumnPinned(column)}
                                      disabled={
                                        !visible || column.defaultPinned
                                      }
                                    >
                                      <Pin className="h-3.5 w-3.5" />
                                    </button>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 dark:border-slate-800">
                      <span className="text-[11px] text-slate-500">
                        已显示 {selectedColumnCount} 列
                      </span>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-xs"
                        onClick={resetColumnSettings}
                      >
                        重置默认
                      </Button>
                    </div>
                  </PopoverContent>
                </Popover>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-7 w-7 shrink-0 rounded-[4px] border-white/10 bg-transparent text-slate-400 hover:bg-white/[0.06] hover:text-white"
                  disabled={activeFetching || !isFilteringReady}
                  onClick={() => {
                    if (quickFilter === 'all') {
                      reloadFinancialData({ requestPolicy: 'network-only' });
                    } else {
                      setFilteredReports([]);
                      setFilteredFetching(true);
                      void fetchFinancialReportsForCodes(
                        client,
                        scopedStockCodes ?? [],
                        normalizedSearch
                      )
                        .then(setFilteredReports)
                        .finally(() => setFilteredFetching(false));
                    }
                  }}
                  title="刷新财务列表"
                >
                  {activeFetching || !isFilteringReady ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            </div>
          </div>
          <StudioDataTable<FinancialReportItem, FinancialTableColumn>
            ariaLabel="上市公司财报明细"
            className="!min-h-0 flex-1 rounded-none border-0 bg-transparent shadow-none"
            columns={tableColumns}
            columnMenuTestIdPrefix="financial-column-menu"
            defaultFrozenColumnIds={DEFAULT_PINNED_FINANCIAL_COLUMN_IDS}
            frozenColumnIds={pinnedColumnIds}
            getCellClassName={({ column }) =>
              cn('h-[40px]', column.id === 'stock' && 'font-sans')
            }
            getRowKey={item => item.stockCode}
            getRowTitle={item =>
              `查看 ${item.stockName || item.stockCode} 数据详情`
            }
            isColumnSorted={column => sortState?.columnId === column.id}
            loading={activeFetching}
            testId="financial-reports-grid"
            loadingOverlay={
              activeFetching && (
                <div className="absolute inset-0 z-20 flex items-center justify-center bg-slate-950/60 backdrop-blur-[2px]">
                  <div className="flex flex-col items-center text-slate-400">
                    <Loader2 className="mb-4 h-8 w-8 animate-spin opacity-50" />
                    <p className="text-xs font-bold uppercase tracking-widest">
                      正在加载真实财务数据
                    </p>
                  </div>
                </div>
              )
            }
            onColumnContextMenu={(event, column) =>
              openTableMenuAtPointer(event, {
                kind: 'column',
                column,
              })
            }
            onColumnMenuOpen={(event, column) =>
              openTableMenuAtPointer(event, {
                kind: 'column',
                column,
              })
            }
            onColumnSortToggle={toggleFinancialColumnSort}
            onFrozenColumnIdsChange={updatePinnedColumnIds}
            onRowClick={item => openStockDetail(item.stockCode)}
            onRowContextMenu={(event, item) =>
              openTableMenuAtPointer(event, { kind: 'row', item })
            }
            renderCell={({ column, row }) =>
              renderFinancialCell(
                column.id,
                row,
                summaryByCode[row.stockCode],
                summaryFetching
              )
            }
            renderSortIndicator={renderFinancialSortIndicator}
            rows={displayReports}
            sortTestIdPrefix="financial-sort"
            emptyState={
              <tr>
                <td
                  colSpan={Math.max(visibleColumns.length, 1)}
                  className="h-[400px] border-b border-white/5 text-center"
                >
                  <div className="flex flex-col items-center justify-center space-y-3 text-slate-500">
                    <div className="flex h-12 w-12 items-center justify-center rounded-full bg-slate-800/50">
                      <Banknote className="h-6 w-6 text-slate-400" />
                    </div>
                    <p className="text-xs font-bold uppercase tracking-widest text-slate-400">
                      {getEmptyMessage(quickFilter)}
                    </p>
                  </div>
                </td>
              </tr>
            }
          />
        </div>
      </div>

      <StudioMenu
        ariaLabel="财务数据表菜单"
        menu={tableMenu}
        onClose={closeTableMenu}
        width={220}
        items={[
          {
            id: 'open-row',
            label: '打开股票数据详情',
            icon: <Eye size={14} />,
            disabled: tableMenu?.payload?.kind !== 'row',
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'row') {
                openStockDetail(tableMenu.payload.item.stockCode);
              }
            },
          },
          {
            id: 'copy-stock-code',
            label: '复制股票代码',
            icon: <Copy size={14} />,
            disabled: tableMenu?.payload?.kind !== 'row',
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'row') {
                copyText(tableMenu.payload.item.stockCode);
              }
            },
          },
          {
            id: 'copy-stock-name',
            label: '复制股票名称',
            icon: <Copy size={14} />,
            disabled: tableMenu?.payload?.kind !== 'row',
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'row') {
                copyText(tableMenu.payload.item.stockName);
              }
            },
          },
          { id: 'sep-column', type: 'separator' },
          {
            id: 'sort-asc',
            label: '升序排序',
            icon: <ListFilter size={14} />,
            disabled: tableMenu?.payload?.kind !== 'column',
            checked:
              tableMenu?.payload?.kind === 'column' &&
              sortState?.columnId === tableMenu.payload.column.id &&
              sortState.direction === 'asc',
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'column') {
                setSortState({
                  columnId: tableMenu.payload.column.id,
                  direction: 'asc',
                });
              }
            },
          },
          {
            id: 'sort-desc',
            label: '降序排序',
            icon: <ListFilter size={14} />,
            disabled: tableMenu?.payload?.kind !== 'column',
            checked:
              tableMenu?.payload?.kind === 'column' &&
              sortState?.columnId === tableMenu.payload.column.id &&
              sortState.direction === 'desc',
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'column') {
                setSortState({
                  columnId: tableMenu.payload.column.id,
                  direction: 'desc',
                });
              }
            },
          },
          {
            id: 'clear-sort',
            label: '清除排序',
            icon: <X size={14} />,
            disabled: !sortState,
            onSelect: () => setSortState(null),
          },
          {
            id: 'copy-column-name',
            label: '复制列名',
            icon: <Copy size={14} />,
            disabled: tableMenu?.payload?.kind !== 'column',
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'column') {
                copyText(tableMenu.payload.column.label);
              }
            },
          },
          {
            id: 'toggle-pin-column',
            label:
              tableMenu?.payload?.kind === 'column' &&
              pinnedColumnIds.includes(tableMenu.payload.column.id)
                ? '取消固定列'
                : '固定列',
            icon: <Pin size={14} />,
            disabled:
              tableMenu?.payload?.kind !== 'column' ||
              tableMenu.payload.column.defaultPinned ||
              !visibleColumnIds.includes(tableMenu.payload.column.id),
            checked:
              tableMenu?.payload?.kind === 'column' &&
              pinnedColumnIds.includes(tableMenu.payload.column.id),
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'column') {
                toggleColumnPinned(tableMenu.payload.column);
              }
            },
          },
          {
            id: 'hide-column',
            label: '隐藏列',
            icon: <X size={14} />,
            disabled:
              tableMenu?.payload?.kind !== 'column' ||
              tableMenu.payload.column.alwaysVisible,
            onSelect: () => {
              if (tableMenu?.payload?.kind === 'column') {
                toggleColumnVisibility(tableMenu.payload.column, false);
              }
            },
          },
          {
            id: 'reset-columns',
            label: '恢复默认列',
            icon: <Columns3 size={14} />,
            onSelect: resetColumnSettings,
          },
        ]}
      />
    </DataStudioPageFrame>
  );
}

function FilterLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">
      {children}
    </div>
  );
}

function RangeFilter({
  label,
  minValue,
  maxValue,
  onMinChange,
  onMaxChange,
}: {
  label: string;
  minValue: string;
  maxValue: string;
  onMinChange: (value: string) => void;
  onMaxChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <FilterLabel>{label}</FilterLabel>
      <div className="grid grid-cols-2 gap-2">
        <Input
          inputMode="decimal"
          placeholder="最小值(亿)"
          className="h-9 text-xs"
          value={minValue}
          onChange={e => onMinChange(e.target.value)}
        />
        <Input
          inputMode="decimal"
          placeholder="最大值(亿)"
          className="h-9 text-xs"
          value={maxValue}
          onChange={e => onMaxChange(e.target.value)}
        />
      </div>
    </div>
  );
}

function readFinancialColumnSettings() {
  if (typeof window === 'undefined') {
    return {
      visible: DEFAULT_VISIBLE_FINANCIAL_COLUMN_IDS,
      pinned: DEFAULT_PINNED_FINANCIAL_COLUMN_IDS,
    };
  }

  try {
    const raw = window.localStorage.getItem(
      FINANCIAL_TABLE_COLUMNS_STORAGE_KEY
    );
    if (!raw) {
      return {
        visible: DEFAULT_VISIBLE_FINANCIAL_COLUMN_IDS,
        pinned: DEFAULT_PINNED_FINANCIAL_COLUMN_IDS,
      };
    }

    const parsed = JSON.parse(raw) as {
      visible?: unknown;
      pinned?: unknown;
    };
    const parsedVisible = parseFinancialColumnIds(parsed.visible);
    const visible = normalizeFinancialColumnIds(
      parsedVisible.length > 0
        ? parsedVisible
        : DEFAULT_VISIBLE_FINANCIAL_COLUMN_IDS,
      'visible'
    );
    const parsedPinned = parseFinancialColumnIds(parsed.pinned);
    const pinned = normalizeFinancialColumnIds(
      parsedPinned.length > 0
        ? parsedPinned
        : DEFAULT_PINNED_FINANCIAL_COLUMN_IDS,
      'pinned',
      visible
    );

    return { visible, pinned };
  } catch {
    return {
      visible: DEFAULT_VISIBLE_FINANCIAL_COLUMN_IDS,
      pinned: DEFAULT_PINNED_FINANCIAL_COLUMN_IDS,
    };
  }
}

function parseFinancialColumnIds(value: unknown): FinancialColumnId[] {
  if (!Array.isArray(value)) return [];

  return value.filter(
    (item): item is FinancialColumnId =>
      typeof item === 'string' &&
      FINANCIAL_COLUMNS.some(column => column.id === item)
  );
}

function normalizeFinancialColumnIds(
  ids: FinancialColumnId[],
  mode: 'visible' | 'pinned',
  visibleIds = DEFAULT_VISIBLE_FINANCIAL_COLUMN_IDS
) {
  const input = new Set(ids);

  if (mode === 'visible') {
    return FINANCIAL_COLUMNS.filter(
      column => column.alwaysVisible || input.has(column.id)
    ).map(column => column.id);
  }

  const visible = new Set(visibleIds);
  return FINANCIAL_COLUMNS.filter(
    column =>
      visible.has(column.id) && (column.defaultPinned || input.has(column.id))
  ).map(column => column.id);
}

function uniqueStockCodes(values: Array<string | null | undefined>) {
  return Array.from(
    new Set(
      values
        .map(value => value?.trim().toUpperCase())
        .filter((value): value is string => Boolean(value))
    )
  );
}

function applyFinancialFilters(
  items: FinancialReportItem[],
  filters: {
    reportPeriod: ReportPeriodFilter;
    disclosure: DisclosureFilter;
    profit: ProfitFilter;
    revenueMinYi: string;
    revenueMaxYi: string;
    netProfitMinYi: string;
    netProfitMaxYi: string;
    latestReportDate?: string | null;
  }
) {
  const revenueMin = parseYi(filters.revenueMinYi);
  const revenueMax = parseYi(filters.revenueMaxYi);
  const netProfitMin = parseYi(filters.netProfitMinYi);
  const netProfitMax = parseYi(filters.netProfitMaxYi);

  return items.filter(item => {
    if (
      !matchesReportPeriod(
        item.reportDate,
        filters.reportPeriod,
        filters.latestReportDate
      )
    ) {
      return false;
    }

    if (filters.disclosure === 'disclosed' && !item.announceDate) return false;
    if (filters.disclosure === 'undisclosed' && item.announceDate) return false;

    const netProfit = item.netProfitExclMinIntInc;
    if (filters.profit === 'profit' && !(Number(netProfit) > 0)) return false;
    if (filters.profit === 'loss' && !(Number(netProfit) < 0)) return false;

    if (!matchesRange(item.revenue, revenueMin, revenueMax)) return false;
    if (!matchesRange(netProfit, netProfitMin, netProfitMax)) return false;

    return true;
  });
}

function parseYi(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed * 100000000 : null;
}

function matchesRange(
  value: number | null | undefined,
  min: number | null,
  max: number | null
) {
  if (min === null && max === null) return true;
  if (value == null || Number.isNaN(Number(value))) return false;
  const numeric = Number(value);
  if (min !== null && numeric < min) return false;
  if (max !== null && numeric > max) return false;
  return true;
}

function matchesReportPeriod(
  value: string | null | undefined,
  filter: ReportPeriodFilter,
  latestReportDate?: string | null
) {
  if (filter === 'all') return true;
  if (!value) return false;
  if (filter === 'latest') return value === latestReportDate;

  const [, month, day] = value.split('-');
  if (!month || !day) return false;
  if (filter === 'q1') return month === '03';
  if (filter === 'half') return month === '06';
  if (filter === 'q3') return month === '09';
  if (filter === 'annual') return month === '12';
  return true;
}

async function fetchFinancialReportsForCodes(
  client: Client,
  stockCodes: string[],
  search?: string
) {
  const codes = stockCodes.slice(0, 100);
  if (codes.length === 0) return [];

  const variables = Object.fromEntries(
    codes.map((code, index) => [`code${index}`, code])
  );
  const variableDefinitions = codes
    .map((_, index) => `$code${index}: String!`)
    .join(', ');
  const aliases = codes
    .map(
      (_, index) => `
        r${index}: financialReports(search: $code${index}, limit: 1, offset: 0) {
          items {
            stockCode
            stockName
            reportDate
            announceDate
            revenue
            netProfitExclMinIntInc
            epsBasic
          }
        }
      `
    )
    .join('\n');

  const result = await client
    .query<
      Record<string, { items?: Array<FinancialReportItem | null> } | null>,
      Record<string, string>
    >(
      `query FinancialReportsForCodes(${variableDefinitions}) { ${aliases} }`,
      variables
    )
    .toPromise();

  if (result.error || !result.data) return [];

  const term = search?.trim().toUpperCase();
  return Object.values(result.data)
    .flatMap(value => value?.items ?? [])
    .filter(
      (
        item: FinancialReportItem | null | undefined
      ): item is FinancialReportItem => Boolean(item?.stockCode)
    )
    .filter(item => {
      if (!term) return true;
      return (
        item.stockCode.toUpperCase().includes(term) ||
        (item.stockName ?? '').toUpperCase().includes(term)
      );
    });
}

async function fetchFinancialSummariesForCodes(
  client: Client,
  stockCodes: string[]
) {
  const codes = stockCodes.slice(0, 100);
  if (codes.length === 0) return {};

  const variables = Object.fromEntries(
    codes.map((code, index) => [`code${index}`, code])
  );
  const variableDefinitions = codes
    .map((_, index) => `$code${index}: String!`)
    .join(', ');
  const aliases = codes
    .map(
      (_, index) => `
        s${index}: financialSummary(stockCode: $code${index}) {
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
      `
    )
    .join('\n');

  const result = await client
    .query<Record<string, FinancialSummaryItem | null>, Record<string, string>>(
      `query FinancialSummariesForCodes(${variableDefinitions}) { ${aliases} }`,
      variables
    )
    .toPromise();

  if (result.error || !result.data) return {};

  return Object.values(result.data).reduce<
    Record<string, FinancialSummaryItem>
  >((acc, value) => {
    if (value?.stockCode) {
      acc[value.stockCode] = value;
    }
    return acc;
  }, {});
}

function renderFinancialCell(
  columnId: FinancialColumnId,
  item: FinancialReportItem,
  summary: FinancialSummaryItem | undefined,
  summaryFetching: boolean
) {
  const revenue = item.revenue ?? summary?.revenue;
  const netProfit =
    item.netProfitExclMinIntInc ?? summary?.netProfitExclMinIntInc;

  switch (columnId) {
    case 'stock':
      return (
        <div className="flex flex-col">
          <span className="text-sm font-semibold leading-4 text-slate-200 group-hover:text-red-300">
            {item.stockName || item.stockCode}
          </span>
          <span className="font-mono text-[10px] leading-4 text-slate-500">
            {item.stockCode}
          </span>
        </div>
      );

    case 'reportDate':
      return (
        <Badge
          variant="secondary"
          className="h-5 border-0 bg-slate-800/70 px-2 font-mono text-[10px] font-medium text-slate-400"
        >
          {formatReportPeriod(item.reportDate)}
        </Badge>
      );

    case 'announceDate':
      return (
        <div className="flex items-center justify-end gap-2 font-mono text-[11px] text-slate-500">
          <Clock className="h-3.5 w-3.5 opacity-40" />
          {item.announceDate || summary?.latestAnnounceDate || '--'}
        </div>
      );

    case 'status':
      return (
        <Badge className="h-5 border border-emerald-500/20 bg-emerald-500/10 px-2 font-mono text-[10px] text-emerald-400">
          {item.announceDate || summary?.latestAnnounceDate
            ? '已披露'
            : '已入库'}
        </Badge>
      );

    case 'revenue':
      return <MoneyValue value={revenue} />;

    case 'netProfit':
      return <MoneyValue value={netProfit} trend />;

    case 'epsBasic':
      return (
        <span className="font-mono text-[11px] font-bold text-slate-300">
          {formatDecimal(item.epsBasic ?? summary?.epsBasic, 3)}
        </span>
      );

    case 'netProfitMargin':
      return (
        <span className="font-mono text-[11px] font-bold text-slate-300">
          {formatRatio(netProfit, revenue)}
        </span>
      );

    case 'totalAssets':
      return (
        <SummaryMoneyValue
          value={summary?.totalAssets}
          loading={summaryFetching}
        />
      );

    case 'totalLiabilities':
      return (
        <SummaryMoneyValue
          value={summary?.totalLiabilities}
          loading={summaryFetching}
        />
      );

    case 'totalEquity':
      return (
        <SummaryMoneyValue
          value={summary?.totalEquity}
          loading={summaryFetching}
        />
      );

    case 'assetLiabilityRatio':
      return (
        <span className="font-mono text-[11px] font-bold text-slate-300">
          {summaryFetching && !summary
            ? '--'
            : formatRatio(summary?.totalLiabilities, summary?.totalAssets)}
        </span>
      );

    case 'operatingCashFlow':
      return (
        <SummaryMoneyValue
          value={summary?.operatingCashFlow}
          loading={summaryFetching}
          trend
        />
      );

    case 'cashBalance':
      return (
        <SummaryMoneyValue
          value={summary?.cashBalance}
          loading={summaryFetching}
        />
      );

    case 'totalCapital':
      return (
        <SummaryNumberValue
          value={summary?.totalCapital}
          loading={summaryFetching}
        />
      );

    case 'circulatingCapital':
      return (
        <SummaryNumberValue
          value={summary?.circulatingCapital}
          loading={summaryFetching}
        />
      );

    case 'statementCounts':
      return (
        <span className="font-mono text-[11px] font-bold text-slate-500">
          {summaryFetching && !summary
            ? '--'
            : [
                summary?.incomeCount ?? 0,
                summary?.balanceCount ?? 0,
                summary?.cashFlowCount ?? 0,
                summary?.capitalCount ?? 0,
              ].join('/')}
        </span>
      );

    default:
      return '--';
  }
}

function getFinancialSortValue(
  columnId: FinancialColumnId,
  item: FinancialReportItem,
  summary?: FinancialSummaryItem
) {
  const revenue = item.revenue ?? summary?.revenue;
  const netProfit =
    item.netProfitExclMinIntInc ?? summary?.netProfitExclMinIntInc;

  switch (columnId) {
    case 'stock':
      return item.stockName || item.stockCode;
    case 'reportDate':
      return item.reportDate || summary?.latestReportDate || '';
    case 'announceDate':
      return item.announceDate || summary?.latestAnnounceDate || '';
    case 'status':
      return item.announceDate || summary?.latestAnnounceDate ? 1 : 0;
    case 'revenue':
      return Number(revenue ?? Number.NEGATIVE_INFINITY);
    case 'netProfit':
      return Number(netProfit ?? Number.NEGATIVE_INFINITY);
    case 'epsBasic':
      return Number(
        item.epsBasic ?? summary?.epsBasic ?? Number.NEGATIVE_INFINITY
      );
    case 'netProfitMargin':
      return revenue
        ? Number(netProfit ?? 0) / Number(revenue)
        : Number.NEGATIVE_INFINITY;
    case 'totalAssets':
      return Number(summary?.totalAssets ?? Number.NEGATIVE_INFINITY);
    case 'totalLiabilities':
      return Number(summary?.totalLiabilities ?? Number.NEGATIVE_INFINITY);
    case 'totalEquity':
      return Number(summary?.totalEquity ?? Number.NEGATIVE_INFINITY);
    case 'assetLiabilityRatio':
      return summary?.totalAssets
        ? Number(summary.totalLiabilities ?? 0) / Number(summary.totalAssets)
        : Number.NEGATIVE_INFINITY;
    case 'operatingCashFlow':
      return Number(summary?.operatingCashFlow ?? Number.NEGATIVE_INFINITY);
    case 'cashBalance':
      return Number(summary?.cashBalance ?? Number.NEGATIVE_INFINITY);
    case 'totalCapital':
      return Number(summary?.totalCapital ?? Number.NEGATIVE_INFINITY);
    case 'circulatingCapital':
      return Number(summary?.circulatingCapital ?? Number.NEGATIVE_INFINITY);
    case 'statementCounts':
      return (
        Number(summary?.incomeCount ?? 0) +
        Number(summary?.balanceCount ?? 0) +
        Number(summary?.cashFlowCount ?? 0) +
        Number(summary?.capitalCount ?? 0)
      );
    default:
      return '';
  }
}

function MoneyValue({
  value,
  trend,
}: {
  value?: number | null;
  trend?: boolean;
}) {
  return (
    <span
      className={cn(
        'font-mono text-[11px] font-bold',
        trend
          ? Number(value ?? 0) < 0
            ? 'text-rose-600 dark:text-rose-400'
            : 'text-emerald-600 dark:text-emerald-400'
          : 'text-slate-300'
      )}
    >
      {formatCompactMoney(value)}
    </span>
  );
}

function SummaryMoneyValue({
  value,
  loading,
  trend,
}: {
  value?: number | null;
  loading: boolean;
  trend?: boolean;
}) {
  if (loading && value == null) {
    return <span className="font-mono text-sm text-slate-300">--</span>;
  }

  return <MoneyValue value={value} trend={trend} />;
}

function SummaryNumberValue({
  value,
  loading,
}: {
  value?: number | null;
  loading: boolean;
}) {
  if (loading && value == null) {
    return <span className="font-mono text-sm text-slate-300">--</span>;
  }

  return (
    <span className="font-mono text-[11px] font-bold text-slate-300">
      {formatCompactNumber(value)}
    </span>
  );
}

function getEmptyMessage(filter: QuickFilter) {
  if (filter === 'holdings') return '未找到持仓股财务数据';
  if (filter === 'watchlist') return '未找到自选股财务数据';
  return '未找到相关数据';
}

function formatReportPeriod(value?: string | null) {
  if (!value) return '--';
  const [year, month, day] = value.split('-');
  if (!year || !month || !day) return value;
  if (month === '03' && day === '31') return `${year}年一季报`;
  if (month === '06' && day === '30') return `${year}年中报`;
  if (month === '09' && day === '30') return `${year}年三季报`;
  if (month === '12' && day === '31') return `${year}年年报`;
  return value;
}

function formatNumber(value?: number | null) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  return Number(value).toLocaleString('zh-CN');
}

function formatDecimal(value?: number | null, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  return Number(value).toLocaleString('zh-CN', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

function formatRatio(numerator?: number | null, denominator?: number | null) {
  if (
    numerator == null ||
    denominator == null ||
    Number(denominator) === 0 ||
    Number.isNaN(Number(numerator)) ||
    Number.isNaN(Number(denominator))
  ) {
    return '--';
  }

  return `${((Number(numerator) / Number(denominator)) * 100).toFixed(2)}%`;
}

function formatCompactMoney(value?: number | null) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  const numeric = Number(value);
  const abs = Math.abs(numeric);
  if (abs >= 100000000) return `${(numeric / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${(numeric / 10000).toFixed(2)}万`;
  return numeric.toLocaleString('zh-CN', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  });
}

function formatCompactNumber(value?: number | null) {
  if (value == null || Number.isNaN(Number(value))) return '--';
  const numeric = Number(value);
  const abs = Math.abs(numeric);
  if (abs >= 100000000) return `${(numeric / 100000000).toFixed(2)}亿`;
  if (abs >= 10000) return `${(numeric / 10000).toFixed(2)}万`;
  return numeric.toLocaleString('zh-CN', {
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  });
}
