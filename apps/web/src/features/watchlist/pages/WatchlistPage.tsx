import {
  BriefcaseBusiness,
  ChevronDown,
  ChevronUp,
  Folder,
  GripVertical,
  LoaderCircle,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RefreshCw,
  Search,
  Star,
  X,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation } from 'wouter';

import { useStudioSidebarSizing } from '@/components/studio-workbench/sidebarSizing';
import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import { useHoldings } from '@/features/portfolio/hooks/useHoldings';
import { useLatestMarketQuotes } from '@/features/portfolio/hooks/useRealTimeHoldings';
import type { Position } from '@/features/portfolio/types';
import {
  StockDetailWorkspace,
  type StockWorkspaceView,
} from '@/features/stocks/components';
import { useStockDetail } from '@/features/stocks/hooks/useStockDetail';
import { useStockSearch } from '@/hooks/useStockSearch';
import type { Stock } from '@/shared/types';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

import { MiniSparkline, WatchlistGroupPicker } from '../components';
import { normalizeWatchlistCode, useWatchlistWorkspace } from '../hooks';
import type {
  WatchlistCollection,
  WatchlistGroupSummary,
  WatchlistItemRecord,
} from '../types';
import {
  mergeWatchlistGroupIds,
  sortWatchlistItemsForGroup,
} from '../utils';

type SortField = 'custom' | 'change' | 'price' | 'name';

interface SidebarRow {
  code: string;
  holding?: Position;
  item?: WatchlistItemRecord;
  name: string;
}

function formatPrice(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toFixed(value >= 10 ? 2 : 3)
    : '--';
}

function formatSigned(value?: number | null, suffix = '') {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '--';
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}${suffix}`;
}

function formatQuantity(value?: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('zh-CN')
    : '--';
}

function formatOperationError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function toStockFromRow(row: SidebarRow, quote?: QuoteLike | null): Stock {
  const lastPrice =
    quote?.lastPrice ?? row.holding?.lastPrice ?? row.holding?.avgPrice ?? 0;
  return {
    id: row.code,
    stockCode: row.code,
    name: row.name || row.code,
    quote: {
      amount: quote?.amount ?? undefined,
      change: quote?.change ?? undefined,
      changePercent:
        quote?.changePercent ??
        row.holding?.changePercent ??
        row.holding?.profitRate ??
        0,
      high: quote?.high ?? undefined,
      lastPrice,
      low: quote?.low ?? undefined,
      open: quote?.open ?? undefined,
      preClose: quote?.preClose ?? undefined,
      volume: quote?.volume ?? undefined,
    },
    currentPrice: lastPrice,
  };
}

interface QuoteLike {
  amount?: number | null;
  change?: number | null;
  changePercent?: number | null;
  high?: number | null;
  lastPrice?: number | null;
  low?: number | null;
  open?: number | null;
  preClose?: number | null;
  time?: string | null;
  volume?: number | null;
}

function isStaleQuote(time?: string | null) {
  if (!time) return false;
  const timestamp = new Date(time).getTime();
  return Number.isFinite(timestamp) && Date.now() - timestamp > 90_000;
}

function getCollection(
  value: string | null,
  groups: WatchlistGroupSummary[]
): WatchlistCollection {
  if (value === 'holdings') {
    return { id: 'holdings', kind: 'holdings', label: '持仓' };
  }
  if (value && value !== 'all') {
    const group = groups.find(item => item.id === value);
    if (group) return { id: group.id, kind: 'group', label: group.name };
  }
  return { id: 'all', kind: 'all', label: '自选' };
}

function makeRouteSearch(collectionId: string, symbol?: string | null): string {
  const params = new URLSearchParams();
  params.set('collection', collectionId);
  if (symbol) params.set('symbol', normalizeWatchlistCode(symbol));
  return `?${params.toString()}`;
}

function CollectionTab({
  active,
  collection,
  count,
  onMove,
  onSelect,
}: {
  active: boolean;
  collection: WatchlistCollection;
  count?: number;
  onMove?: (direction: -1 | 1) => void;
  onSelect: () => void;
}) {
  const Icon =
    collection.kind === 'holdings'
      ? BriefcaseBusiness
      : collection.kind === 'group'
        ? Folder
        : Star;
  return (
    <div className="group/tab inline-flex shrink-0 items-center rounded">
      <button
        type="button"
        aria-current={active ? 'page' : undefined}
        onClick={onSelect}
        className={cn(
          'inline-flex h-8 items-center gap-1.5 rounded px-2.5 text-[11px] font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
          active
            ? 'bg-blue-500/15 text-blue-100 ring-1 ring-inset ring-blue-400/25'
            : 'text-slate-500 hover:bg-white/5 hover:text-slate-200'
        )}
      >
        <Icon className="h-3.5 w-3.5" />
        {collection.label}
        {typeof count === 'number' && (
          <span className="font-mono text-[10px] text-slate-600">{count}</span>
        )}
      </button>
      {collection.kind === 'group' && onMove && (
        <span className="hidden items-center gap-0.5 pl-0.5 group-hover/tab:inline-flex">
          <button
            type="button"
            onClick={event => {
              event.stopPropagation();
              onMove(-1);
            }}
            aria-label={`上移分组 ${collection.label}`}
            className="inline-flex h-6 w-4 items-center justify-center text-slate-700 hover:text-slate-300"
          >
            <ChevronUp className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={event => {
              event.stopPropagation();
              onMove(1);
            }}
            aria-label={`下移分组 ${collection.label}`}
            className="inline-flex h-6 w-4 items-center justify-center text-slate-700 hover:text-slate-300"
          >
            <ChevronDown className="h-3 w-3" />
          </button>
        </span>
      )}
    </div>
  );
}

function SidebarRowView({
  active,
  collection,
  onMove,
  onSelect,
  quote,
  row,
  rowIndex,
  rowCount,
}: {
  active: boolean;
  collection: WatchlistCollection;
  onMove: (direction: -1 | 1) => void;
  onSelect: () => void;
  quote?: QuoteLike | null;
  row: SidebarRow;
  rowIndex: number;
  rowCount: number;
}) {
  const changePercent =
    quote?.changePercent ??
    row.holding?.changePercent ??
    row.holding?.profitRate;
  const tone =
    typeof changePercent !== 'number'
      ? 'text-slate-500'
      : financialToneClass(changePercent);
  const stale = isStaleQuote(quote?.time);
  return (
    <div
      className={cn(
        'group relative flex min-h-[62px] shrink-0 items-center gap-2 border-b border-white/[0.045] px-2.5 transition-colors motion-reduce:transition-none',
        active ? 'bg-blue-500/[0.12]' : 'hover:bg-white/[0.035]'
      )}
      data-testid={`watchlist-row-${row.code}`}
    >
      <button
        type="button"
        onClick={onSelect}
        className="absolute inset-0 z-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
        aria-label={`选择 ${row.name} ${row.code}`}
      />
      <GripVertical
        aria-hidden="true"
        className={cn(
          'relative z-10 h-3.5 w-3.5 shrink-0 text-slate-700',
          collection.kind === 'holdings' && 'invisible'
        )}
      />
      <div className="relative z-10 min-w-0 w-[72px] shrink-0 pointer-events-none">
        <div className="truncate text-[12px] font-bold text-slate-200">
          {row.name || row.code}
        </div>
        <div className="mt-0.5 truncate font-mono text-[10px] text-slate-600">
          {row.code}
        </div>
      </div>
      <div className="relative z-10 min-w-0 flex-1 pointer-events-none">
        <MiniSparkline
          changePercent={changePercent}
          high={quote?.high}
          lastPrice={quote?.lastPrice}
          low={quote?.low}
          open={quote?.open}
          preClose={quote?.preClose}
        />
      </div>
      <div className="relative z-10 w-[72px] shrink-0 text-right pointer-events-none">
        <div
          className={cn('font-mono text-[12px] font-black tabular-nums', tone)}
        >
          {formatPrice(quote?.lastPrice ?? row.holding?.lastPrice)}
        </div>
        <div className={cn('mt-0.5 font-mono text-[10px] tabular-nums', tone)}>
          {formatSigned(changePercent, '%')}
        </div>
        {stale && (
          <div className="mt-0.5 text-[9px] text-amber-300">旧快照</div>
        )}
      </div>
      {collection.kind === 'holdings' && (
        <div className="relative z-10 hidden w-[62px] shrink-0 text-right pointer-events-none 2xl:block">
          <div className="font-mono text-[10px] text-slate-400">
            {formatQuantity(row.holding?.volume)}
          </div>
          <div
            className={cn(
              'mt-0.5 font-mono text-[10px]',
              financialToneClass(row.holding?.profitLoss, 'holding')
            )}
          >
            {formatSigned(row.holding?.profitLoss)}
          </div>
        </div>
      )}
      {collection.kind !== 'holdings' && (
        <div className="relative z-20 flex w-6 shrink-0 flex-col items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <button
            type="button"
            onClick={() => onMove(-1)}
            disabled={rowIndex === 0}
            aria-label={`将 ${row.name} 上移`}
            className="flex h-4 w-5 items-center justify-center rounded text-slate-500 hover:bg-white/10 hover:text-slate-200 disabled:invisible"
          >
            <ChevronUp className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={() => onMove(1)}
            disabled={rowIndex === rowCount - 1}
            aria-label={`将 ${row.name} 下移`}
            className="flex h-4 w-5 items-center justify-center rounded text-slate-500 hover:bg-white/10 hover:text-slate-200 disabled:invisible"
          >
            <ChevronDown className="h-3 w-3" />
          </button>
        </div>
      )}
    </div>
  );
}

function SidebarState({
  description,
  onRetry,
  title,
}: {
  description: string;
  onRetry?: () => void;
  title: string;
}) {
  return (
    <div className="flex min-h-[180px] flex-col items-center justify-center px-6 text-center">
      <div className="text-xs font-bold text-slate-300">{title}</div>
      <div className="mt-1 text-[10px] leading-5 text-slate-600">
        {description}
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 inline-flex h-7 items-center gap-1 rounded border border-white/10 px-2.5 text-[10px] font-bold text-slate-400 hover:border-blue-400/30 hover:text-blue-200"
        >
          <RefreshCw className="h-3 w-3" />
          重试
        </button>
      )}
    </div>
  );
}

function SearchResults({
  onSelect,
  results,
}: {
  onSelect: (stock: Stock) => void;
  results: Stock[];
}) {
  if (results.length === 0) {
    return (
      <div className="px-3 py-3 text-[10px] text-slate-600">未找到匹配标的</div>
    );
  }
  return (
    <div className="max-h-52 overflow-y-auto py-1 custom-scrollbar">
      {results.map(stock => (
        <button
          key={stock.stockCode}
          type="button"
          onClick={() => onSelect(stock)}
          className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-blue-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70"
        >
          <Search className="h-3 w-3 text-slate-600" />
          <span className="min-w-0 flex-1 truncate text-[11px] font-bold text-slate-200">
            {stock.name}
          </span>
          <span className="font-mono text-[10px] text-slate-500">
            {stock.stockCode}
          </span>
        </button>
      ))}
    </div>
  );
}

function WatchlistSidebar({
  activeCollection,
  collapsed,
  collections,
  groupCreateName,
  groups,
  isSaving,
  onChangeGroupCreateName,
  onCreateGroup,
  onDeleteGroup,
  onMove,
  onMoveGroup,
  onRefresh,
  onRenameGroup,
  onSearchSelect,
  onSelectCollection,
  onSelectRow,
  onToggleCollapsed,
  quoteError,
  quoteMap,
  rows,
  searchQuery,
  searchResults,
  searchLoading,
  setSearchQuery,
  selectedCode,
  sidebarWidth,
  stale,
  watchlistError,
  watchlistLoading,
}: {
  activeCollection: WatchlistCollection;
  collapsed: boolean;
  collections: Array<{ collection: WatchlistCollection; count: number }>;
  groupCreateName: string;
  groups: WatchlistGroupSummary[];
  isSaving: boolean;
  onChangeGroupCreateName: (value: string) => void;
  onCreateGroup: () => void;
  onDeleteGroup: (group: WatchlistGroupSummary) => void;
  onMove: (row: SidebarRow, direction: -1 | 1) => void;
  onMoveGroup: (group: WatchlistGroupSummary, direction: -1 | 1) => void;
  onRefresh: () => void;
  onRenameGroup: (group: WatchlistGroupSummary, name: string) => void;
  onSearchSelect: (stock: Stock) => void;
  onSelectCollection: (id: string) => void;
  onSelectRow: (row: SidebarRow) => void;
  onToggleCollapsed: () => void;
  quoteError?: Error;
  quoteMap: Map<string, QuoteLike>;
  rows: SidebarRow[];
  searchQuery: string;
  searchResults: Stock[];
  searchLoading: boolean;
  selectedCode?: string;
  setSearchQuery: (value: string) => void;
  sidebarWidth: number;
  stale: boolean;
  watchlistError?: Error;
  watchlistLoading: boolean;
}) {
  if (collapsed) return null;

  return (
    <aside
      aria-label="自选浏览器"
      className="relative flex h-full min-h-0 shrink-0 flex-col border-r border-white/10 bg-[#0a1220]"
      style={{ width: sidebarWidth }}
      data-testid="watchlist-sidebar"
    >
      <div className="flex h-11 shrink-0 items-center gap-2 border-b border-white/5 px-3">
        <div className="min-w-0 flex-1">
          <div className="text-[12px] font-black text-slate-100">
            自选浏览器
          </div>
          <div className="mt-0.5 text-[9px] uppercase tracking-[0.18em] text-slate-600">
            Watchlist
          </div>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          aria-label="刷新自选数据"
          className="inline-flex h-7 w-7 items-center justify-center rounded text-slate-600 hover:bg-white/5 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        >
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={onToggleCollapsed}
          aria-label="折叠自选浏览器"
          className="inline-flex h-7 w-7 items-center justify-center rounded text-slate-600 hover:bg-white/5 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
        >
          <PanelLeftClose className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="shrink-0 border-b border-white/5 px-2 py-2">
        <div className="flex min-w-0 items-center gap-1 overflow-x-auto no-scrollbar">
          {collections.map(({ collection, count }) => (
            <CollectionTab
              key={collection.id}
              active={collection.id === activeCollection.id}
              collection={collection}
              count={count}
              onMove={
                collection.kind === 'group'
                  ? direction =>
                      onMoveGroup(
                        groups.find(group => group.id === collection.id)!,
                        direction
                      )
                  : undefined
              }
              onSelect={() => onSelectCollection(collection.id)}
            />
          ))}
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <Input
            value={searchQuery}
            onChange={event => setSearchQuery(event.target.value)}
            placeholder="搜索股票代码 / 名称"
            aria-label="搜索并添加股票"
            className="h-8 border-white/10 bg-white/[0.03] text-[11px] placeholder:text-slate-700"
          />
          {searchQuery && (
            <button
              type="button"
              onClick={() => setSearchQuery('')}
              aria-label="清除搜索"
              className="-ml-9 z-10 inline-flex h-7 w-7 items-center justify-center text-slate-600 hover:text-slate-200"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        {searchQuery.length >= 2 && (
          <div className="mt-1 overflow-hidden rounded border border-white/10 bg-[#0b1627] shadow-xl shadow-black/30">
            {searchLoading ? (
              <div className="flex items-center gap-2 px-3 py-3 text-[10px] text-slate-500">
                <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> 搜索中…
              </div>
            ) : (
              <SearchResults
                onSelect={onSearchSelect}
                results={searchResults}
              />
            )}
          </div>
        )}
      </div>

      {activeCollection.kind === 'group' &&
        groups.find(group => group.id === activeCollection.id) && (
          <div className="flex shrink-0 items-center gap-1 border-b border-white/5 px-2 py-1.5">
            <GripVertical className="h-3.5 w-3.5 text-slate-700" />
            <span className="min-w-0 flex-1 truncate text-[10px] text-slate-500">
              {activeCollection.label} · {rows.length}只
            </span>
            <WatchlistGroupPicker
              group={groups.find(group => group.id === activeCollection.id)!}
              onDelete={onDeleteGroup}
              onRename={onRenameGroup}
            />
          </div>
        )}

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {quoteError && (
          <div
            className="flex shrink-0 items-center justify-between gap-2 border-b border-amber-400/15 bg-amber-400/[0.06] px-3 py-2 text-[10px] text-amber-200"
            role="alert"
          >
            <span>行情部分不可用，保留最后快照。</span>
            <button
              type="button"
              onClick={onRefresh}
              className="font-bold hover:text-white"
            >
              重试
            </button>
          </div>
        )}
        {stale && !quoteError && (
          <div className="shrink-0 border-b border-amber-400/10 px-3 py-1 text-[9px] text-amber-300">
            行情快照可能已过期
          </div>
        )}
        <div className="flex shrink-0 items-center gap-2 border-b border-white/5 px-3 py-1.5 text-[9px] font-bold uppercase tracking-[0.12em] text-slate-700">
          <span className="w-[88px]">标的</span>
          <span className="flex-1">走势</span>
          <span className="w-[72px] text-right">价格 / 涨跌</span>
          {activeCollection.kind === 'holdings' && (
            <span className="hidden w-[62px] text-right 2xl:block">
              数量 / 盈亏
            </span>
          )}
        </div>
        <div
          className="min-h-0 flex-1 overflow-y-auto custom-scrollbar"
          role="listbox"
          aria-label={`${activeCollection.label}股票列表`}
        >
          {watchlistLoading && rows.length === 0 ? (
            <SidebarState
              title="正在加载自选"
              description="正在同步账户自选与分组…"
            />
          ) : watchlistError && rows.length === 0 ? (
            <SidebarState
              title="自选加载失败"
              description={watchlistError.message}
              onRetry={onRefresh}
            />
          ) : rows.length === 0 ? (
            <SidebarState
              title={
                activeCollection.kind === 'holdings'
                  ? '暂无持仓'
                  : '这里还没有股票'
              }
              description={
                activeCollection.kind === 'holdings'
                  ? '券商持仓同步后会显示在这里。'
                  : '搜索股票代码或名称，加入当前集合。'
              }
            />
          ) : (
            rows.map((row, index) => (
              <SidebarRowView
                key={row.code}
                active={row.code === selectedCode}
                collection={activeCollection}
                onMove={direction => onMove(row, direction)}
                onSelect={() => onSelectRow(row)}
                quote={quoteMap.get(row.code)}
                row={row}
                rowIndex={index}
                rowCount={rows.length}
              />
            ))
          )}
        </div>
      </div>

      <div className="shrink-0 border-t border-white/5 p-2">
        <div className="flex items-center gap-1.5">
          <Input
            value={groupCreateName}
            onChange={event => onChangeGroupCreateName(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') onCreateGroup();
            }}
            placeholder="新建自定义分组"
            maxLength={80}
            aria-label="新建自定义分组"
            className="h-7 border-white/10 bg-white/[0.03] text-[10px]"
          />
          <button
            type="button"
            onClick={onCreateGroup}
            disabled={!groupCreateName.trim() || isSaving}
            aria-label="创建自定义分组"
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-blue-500/30 text-blue-300 hover:bg-blue-500/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </aside>
  );
}

function DetailState({
  description,
  loading,
  onRetry,
  title,
}: {
  description: string;
  loading?: boolean;
  onRetry?: () => void;
  title: string;
}) {
  return (
    <div className="flex h-full min-h-[320px] items-center justify-center bg-[#08101d] p-8 text-center">
      <div>
        {loading ? (
          <LoaderCircle className="mx-auto h-6 w-6 animate-spin text-blue-300" />
        ) : (
          <BriefcaseBusiness className="mx-auto h-6 w-6 text-slate-600" />
        )}
        <div className="mt-3 text-sm font-black text-slate-200">{title}</div>
        <div className="mt-1 text-xs text-slate-600">{description}</div>
        {onRetry && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onRetry}
            className="mt-4"
          >
            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
            重新加载
          </Button>
        )}
      </div>
    </div>
  );
}

function WatchlistDetail({
  account,
  activeView,
  context,
  holding,
  holdings,
  onStockSelect,
  onViewChange,
  portfolioSummary,
  quote,
  row,
}: {
  account?: {
    accountName?: string | null;
    cash: number;
    frozenCash: number;
    id: string;
    marketValue: number;
    profitLossPercent?: number | null;
    totalAsset: number;
    totalProfitLoss?: number | null;
  } | null;
  activeView: StockWorkspaceView;
  context: 'detail' | 'holdings';
  holding?: Position | null;
  holdings: Position[];
  onStockSelect: (stock: Stock | null) => void;
  onViewChange: (view: StockWorkspaceView) => void;
  portfolioSummary?: ReturnType<typeof useHoldings>['portfolioSummary'];
  quote?: QuoteLike | null;
  row: SidebarRow;
}) {
  const { stock, isLoading, error, refetch } = useStockDetail(row.code);
  const selectedStock = useMemo(
    () =>
      stock
        ? {
            id: normalizeWatchlistCode(stock.id || row.code),
            stockCode: normalizeWatchlistCode(stock.id || row.code),
            name: stock.name || row.name || row.code,
            market: stock.market || undefined,
            type: stock.type || undefined,
            quote: {
              amount: stock.quote?.amount ?? quote?.amount ?? undefined,
              change: stock.quote?.change ?? quote?.change ?? undefined,
              changePercent:
                stock.quote?.changePercent ?? quote?.changePercent ?? 0,
              high: stock.quote?.high ?? quote?.high ?? undefined,
              lastPrice: stock.quote?.lastPrice ?? quote?.lastPrice ?? 0,
              low: stock.quote?.low ?? quote?.low ?? undefined,
              open: stock.quote?.open ?? quote?.open ?? undefined,
              preClose: stock.quote?.preClose ?? quote?.preClose ?? undefined,
              volume: stock.quote?.volume ?? quote?.volume ?? undefined,
            },
            currentPrice: stock.quote?.lastPrice ?? quote?.lastPrice ?? 0,
          }
        : toStockFromRow(row, quote),
    [quote, row, stock]
  );

  if (isLoading && !stock && !quote && !holding) {
    return (
      <DetailState
        title="正在加载个股详情"
        description={`${row.code} 行情与基本资料读取中…`}
        loading
      />
    );
  }
  if (error && !selectedStock) {
    return (
      <DetailState
        title="个股详情暂不可用"
        description={error.message}
        onRetry={() => void refetch()}
      />
    );
  }
  return (
    <StockDetailWorkspace
      accountId={account?.id}
      accountName={account?.accountName || undefined}
      accountSummary={account}
      activeOrderCount={0}
      activeView={activeView}
      context={context}
      hasActiveOrders={false}
      holding={holding}
      holdings={holdings}
      onStockSelect={onStockSelect}
      onViewChange={onViewChange}
      portfolioSummary={portfolioSummary}
      selectedStock={selectedStock}
      stockCode={row.code}
    />
  );
}

export default function WatchlistPage() {
  const [location, setLocation] = useLocation();
  const { data: accountData } = useCurrentAccount();
  const account = accountData?.currentAccount;
  const { confirm: confirmDialog } = useAppDialog();
  const accountId = account?.id;
  const holdingsState = useHoldings();
  const { holdings, portfolioSummary } = holdingsState;
  const watchlist = useWatchlistWorkspace(accountId);
  const [collapsed, setCollapsed] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [groupCreateName, setGroupCreateName] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [operationMessage, setOperationMessage] = useState<{
    kind: 'error' | 'success';
    text: string;
  } | null>(null);
  const [sortField, setSortField] = useState<SortField>('custom');
  const [activeView, setActiveView] = useState<StockWorkspaceView>('OVERVIEW');
  const { handleSidebarResizeKeyDown, handleSidebarResizeStart, sidebarWidth } =
    useStudioSidebarSizing({
      sizing: {
        defaultWidth: 360,
        maxWidth: 440,
        minWidth: 320,
        storageScope: 'watchlist-browser',
      },
      storageFallback: 'watchlist-browser',
    });
  const {
    filteredStocks,
    setSearchQuery: setSearchStockQuery,
    stocksLoading,
  } = useStockSearch(
    holdings.map(holding => ({
      instrumentName: holding.instrumentName,
      lastPrice: holding.lastPrice,
      profitRate: holding.profitRate,
      stockCode: holding.stockCode,
    }))
  );

  const params = useMemo(
    () => new URLSearchParams(location.split('?')[1] || ''),
    [location]
  );
  const requestedSymbol = normalizeWatchlistCode(params.get('symbol') || '');
  const activeCollection = useMemo(
    () => getCollection(params.get('collection'), watchlist.groups),
    [params, watchlist.groups]
  );
  const holdingsByCode = useMemo(
    () =>
      new Map(
        holdings.map(holding => [
          normalizeWatchlistCode(holding.stockCode),
          holding,
        ])
      ),
    [holdings]
  );
  const allRows = useMemo<SidebarRow[]>(
    () =>
      watchlist.items
        .slice()
        .sort((left, right) => left.displayOrder - right.displayOrder)
        .map(item => ({
          code: normalizeWatchlistCode(item.stockCode),
          item,
          name: item.instrumentName || item.stockCode,
        })),
    [watchlist.items]
  );
  const holdingsRows = useMemo<SidebarRow[]>(
    () =>
      holdings.map(holding => ({
        code: normalizeWatchlistCode(holding.stockCode),
        holding,
        item: watchlist.items.find(
          item =>
            normalizeWatchlistCode(item.stockCode) ===
            normalizeWatchlistCode(holding.stockCode)
        ),
        name: holding.instrumentName || holding.stockCode,
      })),
    [holdings, watchlist.items]
  );
  const groupRows = useMemo<SidebarRow[]>(() => {
    if (activeCollection.kind !== 'group') return [];
    return sortWatchlistItemsForGroup(watchlist.items, activeCollection.id)
      .map(item => ({
        code: normalizeWatchlistCode(item.stockCode),
        item,
        name: item.instrumentName || item.stockCode,
      }));
  }, [activeCollection, watchlist.items]);
  const baseRows =
    activeCollection.kind === 'holdings'
      ? holdingsRows
      : activeCollection.kind === 'group'
        ? groupRows
        : allRows;
  const quoteCodes = useMemo(() => baseRows.map(row => row.code), [baseRows]);
  const quoteState = useLatestMarketQuotes({ stockCodes: quoteCodes });
  const quoteMap = quoteState.quotes as Map<string, QuoteLike>;
  const rows = useMemo(() => {
    if (sortField === 'custom' || activeCollection.kind !== 'holdings')
      return baseRows;
    const valueFor = (row: SidebarRow) => {
      const quote = quoteMap.get(row.code);
      if (sortField === 'name') return row.name;
      if (sortField === 'price')
        return quote?.lastPrice ?? row.holding?.lastPrice ?? 0;
      return quote?.changePercent ?? row.holding?.profitRate ?? 0;
    };
    return baseRows.slice().sort((left, right) => {
      const leftValue = valueFor(left);
      const rightValue = valueFor(right);
      return typeof leftValue === 'string'
        ? leftValue.localeCompare(String(rightValue), 'zh-CN')
        : Number(rightValue) - Number(leftValue);
    });
  }, [activeCollection.kind, baseRows, quoteMap, sortField]);
  const selectedRow = useMemo(
    () => rows.find(row => row.code === requestedSymbol) || rows[0] || null,
    [requestedSymbol, rows]
  );
  const isQuoteStale = useMemo(
    () => Array.from(quoteMap.values()).some(quote => isStaleQuote(quote.time)),
    [quoteMap]
  );
  const collections = useMemo(
    () => [
      {
        collection: { id: 'all', kind: 'all', label: '自选' } as const,
        count: allRows.length,
      },
      {
        collection: {
          id: 'holdings',
          kind: 'holdings',
          label: '持仓',
        } as const,
        count: holdingsRows.length,
      },
      ...watchlist.groups.map(group => ({
        collection: { id: group.id, kind: 'group' as const, label: group.name },
        count: group.itemCount,
      })),
    ],
    [allRows.length, holdingsRows.length, watchlist.groups]
  );

  useEffect(() => {
    const nextSymbol = selectedRow?.code || '';
    const currentCollection = params.get('collection') || 'all';
    if (
      currentCollection !== activeCollection.id ||
      (nextSymbol && nextSymbol !== requestedSymbol) ||
      (!nextSymbol && requestedSymbol)
    ) {
      setLocation(makeRouteSearch(activeCollection.id, nextSymbol));
    }
  }, [activeCollection.id, params, requestedSymbol, selectedRow, setLocation]);

  useEffect(() => {
    setActiveView(activeCollection.kind === 'holdings' ? 'CHART' : 'OVERVIEW');
    setSortField('custom');
  }, [activeCollection.id, activeCollection.kind]);

  useEffect(() => {
    setSearchStockQuery(searchQuery);
  }, [searchQuery, setSearchStockQuery]);

  const selectCollection = useCallback(
    (collectionId: string) => {
      const nextRows =
        collectionId === 'holdings'
          ? holdingsRows
          : collectionId === 'all'
            ? allRows
            : sortWatchlistItemsForGroup(watchlist.items, collectionId)
                .map(item => ({
                  code: normalizeWatchlistCode(item.stockCode),
                  item,
                  name: item.instrumentName || item.stockCode,
                }));
      setLocation(makeRouteSearch(collectionId, nextRows[0]?.code));
    },
    [allRows, holdingsRows, setLocation, watchlist.items]
  );

  const moveGroup = useCallback(
    async (group: WatchlistGroupSummary, direction: -1 | 1) => {
      const index = watchlist.groups.findIndex(item => item.id === group.id);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= watchlist.groups.length) return;
      const nextGroups = watchlist.groups.slice();
      [nextGroups[index], nextGroups[target]] = [
        nextGroups[target],
        nextGroups[index],
      ];
      setIsSaving(true);
      try {
        await watchlist.reorderGroups({
          groupIds: nextGroups.map(item => item.id),
        });
        setOperationMessage({ kind: 'success', text: '分组排序已保存' });
      } catch (error) {
        setOperationMessage({
          kind: 'error',
          text: formatOperationError(error, '保存分组排序失败'),
        });
      } finally {
        setIsSaving(false);
      }
    },
    [watchlist]
  );

  const selectRow = useCallback(
    (row: SidebarRow) => {
      setLocation(makeRouteSearch(activeCollection.id, row.code));
    },
    [activeCollection.id, setLocation]
  );

  const moveRow = useCallback(
    async (row: SidebarRow, direction: -1 | 1) => {
      if (activeCollection.kind === 'holdings') return;
      const index = rows.findIndex(item => item.code === row.code);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= rows.length) return;
      const nextRows = rows.slice();
      [nextRows[index], nextRows[target]] = [nextRows[target], nextRows[index]];
      setIsSaving(true);
      try {
        if (activeCollection.kind === 'group') {
          await watchlist.reorderGroupItems({
            groupId: activeCollection.id,
            itemIds: nextRows.map(item => item.item?.id || item.code),
          });
        } else {
          await watchlist.reorderItems({
            itemIds: nextRows.map(item => item.item?.id || item.code),
          });
        }
        setOperationMessage({ kind: 'success', text: '列表排序已保存' });
      } catch (error) {
        setOperationMessage({
          kind: 'error',
          text: formatOperationError(error, '保存列表排序失败'),
        });
      } finally {
        setIsSaving(false);
      }
    },
    [activeCollection, rows, watchlist]
  );

  const createGroup = useCallback(async () => {
    const name = groupCreateName.trim();
    if (!name) return;
    setIsSaving(true);
    try {
      await watchlist.createGroup({ name, initialStockCodes: [] });
      setGroupCreateName('');
      setOperationMessage({ kind: 'success', text: `分组“${name}”已创建` });
    } catch (error) {
      setOperationMessage({
        kind: 'error',
        text: formatOperationError(error, '创建分组失败'),
      });
    } finally {
      setIsSaving(false);
    }
  }, [groupCreateName, watchlist]);

  const renameGroup = useCallback(
    async (group: WatchlistGroupSummary, name: string) => {
      setIsSaving(true);
      try {
        await watchlist.renameGroup({ groupId: group.id, name });
        setOperationMessage({ kind: 'success', text: '分组名称已保存' });
      } catch (error) {
        setOperationMessage({
          kind: 'error',
          text: formatOperationError(error, '重命名分组失败'),
        });
      } finally {
        setIsSaving(false);
      }
    },
    [watchlist]
  );

  const deleteGroup = useCallback(
    async (group: WatchlistGroupSummary) => {
      let confirmed = false;
      try {
        confirmed = await confirmDialog({
          confirmText: '删除分组',
          description: '组内股票仍保留在总自选，只有该分组的归属关系会被删除。',
          title: `确认删除“${group.name}”`,
          variant: 'destructive',
        });
      } catch (error) {
        setOperationMessage({
          kind: 'error',
          text: formatOperationError(error, '删除分组确认失败'),
        });
        return;
      }
      if (!confirmed) return;
      setIsSaving(true);
      try {
        await watchlist.deleteGroup({ groupId: group.id });
        if (activeCollection.id === group.id) {
          setLocation(makeRouteSearch('all', allRows[0]?.code));
        }
        setOperationMessage({ kind: 'success', text: `分组“${group.name}”已删除` });
      } catch (error) {
        setOperationMessage({
          kind: 'error',
          text: formatOperationError(error, '删除分组失败'),
        });
      } finally {
        setIsSaving(false);
      }
    },
    [activeCollection.id, allRows, confirmDialog, setLocation, watchlist]
  );

  const selectSearchStock = useCallback(
    async (stock: Stock) => {
      const code = normalizeWatchlistCode(stock.stockCode || stock.id);
      const existingItem = watchlist.items.find(
        item => normalizeWatchlistCode(item.stockCode) === code
      );
      const groupIds = mergeWatchlistGroupIds(
        existingItem,
        activeCollection.kind === 'group' ? activeCollection.id : undefined
      );
      setIsSaving(true);
      try {
        await watchlist.saveItem({
          groupIds,
          instrumentName: stock.name,
          stockCode: code,
        });
        setSearchQuery('');
        setLocation(makeRouteSearch(activeCollection.id, code));
        setOperationMessage({
          kind: 'success',
          text: `${stock.name} 已加入${activeCollection.label}`,
        });
      } catch (error) {
        setOperationMessage({
          kind: 'error',
          text: formatOperationError(error, '加入自选失败'),
        });
      } finally {
        setIsSaving(false);
      }
    },
    [activeCollection, setLocation, watchlist]
  );

  const selectedHolding = selectedRow
    ? holdingsByCode.get(selectedRow.code)
    : null;
  const selectedQuote = selectedRow ? quoteMap.get(selectedRow.code) : null;

  return (
    <div
      className="relative flex h-full min-h-0 min-w-0 flex-col bg-[#08101d]"
      data-testid="watchlist-page"
    >
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-white/5 bg-[#0b1120] px-3">
        <div className="flex min-w-0 items-center gap-2">
          <Star className="h-3.5 w-3.5 text-amber-300" />
          <span className="text-[12px] font-black text-slate-100">自选</span>
          <span className="hidden text-[10px] text-slate-600 sm:inline">
            主从工作区
          </span>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-slate-600">
          {isSaving && (
            <LoaderCircle className="h-3 w-3 animate-spin text-blue-300" />
          )}
          <span className="font-mono">
            {watchlist.fetching ? '同步中…' : `${rows.length} 项`}
          </span>
          <button
            type="button"
            onClick={() => setCollapsed(value => !value)}
            aria-label={collapsed ? '展开自选浏览器' : '折叠自选浏览器'}
            className="inline-flex h-6 w-6 items-center justify-center rounded text-slate-600 hover:bg-white/5 hover:text-slate-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
          >
            {collapsed ? (
              <PanelLeftOpen className="h-3.5 w-3.5" />
            ) : (
              <PanelLeftClose className="h-3.5 w-3.5" />
            )}
          </button>
        </div>
      </div>

      {operationMessage && (
        <div
          className={cn(
            'shrink-0 border-b px-3 py-1.5 text-[10px]',
            operationMessage.kind === 'error'
              ? 'border-rose-400/20 bg-rose-400/[0.06] text-rose-200'
              : 'border-emerald-400/15 bg-emerald-400/[0.05] text-emerald-200'
          )}
          role={operationMessage.kind === 'error' ? 'alert' : 'status'}
        >
          {operationMessage.text}
        </div>
      )}

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <WatchlistSidebar
          activeCollection={activeCollection}
          collapsed={collapsed}
          collections={collections}
          groupCreateName={groupCreateName}
          groups={watchlist.groups}
          isSaving={isSaving}
          onChangeGroupCreateName={setGroupCreateName}
          onCreateGroup={() => void createGroup()}
          onDeleteGroup={group => void deleteGroup(group)}
          onMove={(row, direction) => void moveRow(row, direction)}
          onMoveGroup={(group, direction) => void moveGroup(group, direction)}
          onRefresh={watchlist.refetch}
          onRenameGroup={(group, name) => void renameGroup(group, name)}
          onSearchSelect={stock => void selectSearchStock(stock)}
          onSelectCollection={selectCollection}
          onSelectRow={selectRow}
          onToggleCollapsed={() => setCollapsed(true)}
          quoteError={quoteState.error}
          quoteMap={quoteMap}
          rows={rows}
          searchQuery={searchQuery}
          searchResults={filteredStocks}
          searchLoading={stocksLoading}
          selectedCode={selectedRow?.code}
          setSearchQuery={setSearchQuery}
          sidebarWidth={sidebarWidth}
          stale={isQuoteStale || Boolean(watchlist.isStale)}
          watchlistError={watchlist.error}
          watchlistLoading={watchlist.fetching}
        />
        {!collapsed && (
          <div
            role="separator"
            aria-label="自选浏览器宽度"
            aria-orientation="vertical"
            aria-valuemin={320}
            aria-valuemax={440}
            aria-valuenow={sidebarWidth}
            tabIndex={0}
            onPointerDown={handleSidebarResizeStart}
            onKeyDown={handleSidebarResizeKeyDown}
            className="group relative z-20 -ml-1.5 h-full w-3 shrink-0 cursor-col-resize touch-none outline-none max-[1100px]:hidden"
            data-testid="watchlist-sidebar-resizer"
          >
            <span className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-white/5 transition-colors group-hover:bg-blue-400/70 group-focus-visible:bg-blue-400/70" />
          </div>
        )}
        <main className="relative min-w-0 flex-1 overflow-hidden bg-[#08101d]">
          {collapsed && (
            <button
              type="button"
              onClick={() => setCollapsed(false)}
              className="absolute left-2 top-2 z-20 inline-flex h-8 items-center gap-1.5 rounded border border-blue-400/25 bg-[#0b1627] px-2 text-[10px] font-bold text-blue-200 shadow-lg shadow-black/30 hover:bg-blue-500/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
            >
              <PanelLeftOpen className="h-3.5 w-3.5" />
              展开自选
            </button>
          )}
          {selectedRow ? (
            <WatchlistDetail
              account={account}
              activeView={activeView}
              context={
                activeCollection.kind === 'holdings' ? 'holdings' : 'detail'
              }
              holding={selectedHolding}
              holdings={holdings}
              onStockSelect={stock => {
                const code = normalizeWatchlistCode(
                  stock?.stockCode || stock?.id || ''
                );
                if (code)
                  setLocation(makeRouteSearch(activeCollection.id, code));
              }}
              onViewChange={setActiveView}
              portfolioSummary={portfolioSummary}
              quote={selectedQuote}
              row={selectedRow}
            />
          ) : watchlist.error ? (
            <DetailState
              title="自选加载失败"
              description={watchlist.error.message}
              onRetry={watchlist.refetch}
            />
          ) : (
            <DetailState
              title="选择一只股票"
              description="从左侧集合选择标的，个股详情会在这里原地打开。"
            />
          )}
        </main>
      </div>

      {activeCollection.kind === 'holdings' && (
        <div className="absolute bottom-2 left-2 z-10 flex items-center gap-1 rounded border border-white/10 bg-[#0b1627]/95 p-1 shadow-lg shadow-black/20">
          <span className="px-1.5 text-[9px] font-bold text-slate-600">
            排序
          </span>
          {(
            [
              ['custom', '自定义'],
              ['change', '涨跌'],
              ['price', '价格'],
              ['name', '名称'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setSortField(value)}
              className={cn(
                'rounded px-1.5 py-1 text-[9px] font-bold',
                sortField === value
                  ? 'bg-blue-500/15 text-blue-200'
                  : 'text-slate-600 hover:text-slate-300'
              )}
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
