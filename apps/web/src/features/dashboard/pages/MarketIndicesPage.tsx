import {
  ArrowLeft,
  Check,
  ChevronDown,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useQuery } from 'urql';
import { Link } from 'wouter';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useLatestMarketQuotes } from '@/features/portfolio/hooks/useRealTimeHoldings';
import { financialToneClass } from '@/shared/utils/financialColors';

import { MarketStudioShell } from '../components/MarketStudioShell';
import { useAMarketSession } from '../hooks/useAMarketSession';
import { useMarketIndexPreferences } from '../hooks/useMarketIndexPreferences';
import {
  buildMarketIndexDirectoryWhere,
  getMarketIndexDirectoryQuoteDisplay,
  MARKET_INDEX_PAGE_SIZE,
  marketIndexDirectoryOrder,
  MarketIndexDirectoryQuery,
  mergeMarketIndexDirectoryRows,
  toMarketIndexDirectoryRow,
  updateMarketIndexDirectoryPreference,
  type MarketIndexDirectoryRow,
} from '../marketIndexCatalog';
import {
  formatMarketDate,
  formatMarketPercent,
  formatMarketPrice,
  formatMarketTime,
  isMarketQuoteFreshForSession,
  MAX_MARKET_INDEXES,
  type MarketQuoteSnapshot,
} from '../marketWorkbench';

type MarketFilter = 'ALL' | 'SH' | 'SZ';

function DirectoryQuote({
  quote,
  isFresh,
}: {
  isFresh: boolean;
  quote: MarketQuoteSnapshot | undefined;
}) {
  const display = getMarketIndexDirectoryQuoteDisplay(quote, isFresh);
  return (
    <span className="font-mono text-ui-label font-black tabular-nums text-slate-200">
      {display.currentPrice === null
        ? '--'
        : formatMarketPrice(display.currentPrice)}
    </span>
  );
}

function DirectoryChange({
  quote,
  isFresh,
}: {
  isFresh: boolean;
  quote: MarketQuoteSnapshot | undefined;
}) {
  const display = getMarketIndexDirectoryQuoteDisplay(quote, isFresh);
  return (
    <span
      className={`font-mono text-ui-label font-bold tabular-nums ${
        display.changePercent === null
          ? 'text-slate-600'
          : financialToneClass(display.changePercent)
      }`}
    >
      {display.changePercent === null
        ? '--'
        : formatMarketPercent(display.changePercent)}
    </span>
  );
}

function DirectoryStatus({
  quote,
  isFresh,
}: {
  isFresh: boolean;
  quote: MarketQuoteSnapshot | undefined;
}) {
  const display = getMarketIndexDirectoryQuoteDisplay(quote, isFresh);
  const status =
    display.status === 'missing'
      ? { label: '无快照', tone: 'text-slate-600' }
      : display.status === 'stale'
        ? { label: '已过期', tone: 'text-amber-300' }
        : { label: '实时', tone: 'text-emerald-300' };
  return (
    <div className="flex flex-col items-end gap-1 text-right">
      <span className={`text-ui-caption font-bold ${status.tone}`}>
        {status.label}
      </span>
      <span className="font-mono text-ui-caption text-slate-500">
        {display.time ? formatMarketTime(display.time) : '--'}
      </span>
      <span className="text-ui-micro text-slate-700">
        {display.time && !isFresh
          ? `快照 ${formatMarketDate(display.time)}`
          : display.time
            ? '实时快照'
            : '等待真实快照'}
      </span>
    </div>
  );
}

export default function MarketIndicesPage() {
  const session = useAMarketSession();
  const preferences = useMarketIndexPreferences();
  const [search, setSearch] = useState('');
  const [market, setMarket] = useState<MarketFilter>('ALL');
  const [after, setAfter] = useState<string | null>(null);
  const [rows, setRows] = useState<MarketIndexDirectoryRow[]>([]);
  const [storageMessage, setStorageMessage] = useState<string | null>(null);
  const where = useMemo(
    () => buildMarketIndexDirectoryWhere(search, market),
    [market, search]
  );
  const [result, refresh] = useQuery({
    query: MarketIndexDirectoryQuery,
    variables: {
      after,
      first: MARKET_INDEX_PAGE_SIZE,
      orderBy: marketIndexDirectoryOrder,
      where,
    },
    requestPolicy: 'cache-and-network',
  });

  useEffect(() => {
    setAfter(null);
    setRows([]);
  }, [market, search]);

  useEffect(() => {
    const incoming =
      result.data?.instrumentsConnection.edges.map(edge =>
        toMarketIndexDirectoryRow(edge.node)
      ) ?? [];
    if (after) {
      setRows(current => mergeMarketIndexDirectoryRows(current, incoming));
    } else {
      setRows(incoming);
    }
  }, [after, result.data]);

  const quoteState = useLatestMarketQuotes({
    stockCodes: rows.slice(0, MAX_MARKET_INDEXES).map(row => row.code),
  });
  const totalCount = result.data?.instrumentsConnection.totalCount ?? 0;
  const pageInfo = result.data?.instrumentsConnection.pageInfo;
  const initialLoading = result.fetching && rows.length === 0;
  const loadingNextPage = result.fetching && after !== null;

  const isOnWorkbench = (code: string) =>
    preferences.items.some(item => item.code === code && item.visible);
  const isConfigured = (code: string) =>
    preferences.items.some(item => item.code === code);

  const toggleWorkbench = (row: MarketIndexDirectoryRow) => {
    const update = updateMarketIndexDirectoryPreference(preferences.items, row);
    if (update.action === 'limit') {
      setStorageMessage('工作台最多配置 100 个指数，请先移除一个再加入。');
      return;
    }
    const persisted = preferences.updateItems(update.items);
    setStorageMessage(
      persisted
        ? null
        : '本机存储不可用，当前页面已更新，但刷新后可能恢复上次配置。'
    );
  };

  const workbenchCount = preferences.visibleItems.length;

  return (
    <MarketStudioShell
      content={
        <div className="studio-workspace-surface flex min-h-0 flex-1 flex-col overflow-hidden">
          <header className="studio-workspace-surface shrink-0 border-b border-white/5 px-ui-section py-3">
            <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <Link
                  aria-label="返回行情工作台"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.03] text-slate-400 hover:bg-white/[0.06] hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                  href="/"
                >
                  <ArrowLeft className="h-4 w-4" />
                </Link>
                <div className="min-w-0">
                  <h1 className="truncate text-ui-title font-black text-slate-100">
                    全部指数
                  </h1>
                  <p className="mt-0.5 text-ui-caption text-slate-600">
                    真实指数目录 · {totalCount.toLocaleString('zh-CN')} 个标的 ·
                    工作台已显示 {workbenchCount}
                  </p>
                </div>
              </div>
              <div className="flex min-w-0 flex-1 items-center justify-end gap-2 sm:max-w-xl">
                <div className="relative min-w-0 flex-1">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-600" />
                  <Input
                    aria-label="搜索指数名称或代码"
                    className="h-control-default w-full rounded-md border border-white/10 bg-black/20 pl-9 pr-3 text-ui-label text-slate-200 outline-none placeholder:text-slate-600 focus:border-blue-400/50 focus:ring-2 focus:ring-blue-500/10"
                    onChange={event => setSearch(event.target.value)}
                    placeholder="搜索指数名称或代码"
                    value={search}
                  />
                </div>
                <Button
                  aria-label="刷新指数目录"
                  className="h-control-default w-9 shrink-0 rounded-md border border-white/10 bg-white/[0.03] text-slate-400 hover:bg-white/[0.06] hover:text-slate-100"
                  onClick={() => refresh({ requestPolicy: 'network-only' })}
                  size="icon"
                  type="button"
                  variant="ghost"
                >
                  <RefreshCw
                    className={result.fetching ? 'animate-spin' : ''}
                  />
                </Button>
              </div>
            </div>
          </header>

          <main className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
            <div className="mx-auto w-full max-w-7xl px-ui-section pb-10 pt-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div
                  className="flex items-center gap-2"
                  role="group"
                  aria-label="市场筛选"
                >
                  <SlidersHorizontal className="h-3.5 w-3.5 text-slate-600" />
                  {(
                    [
                      ['ALL', '全部'],
                      ['SH', '沪市'],
                      ['SZ', '深市'],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      aria-pressed={market === value}
                      className={`rounded-md border px-3 py-1.5 text-ui-caption font-bold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70 ${
                        market === value
                          ? 'border-blue-400/30 bg-blue-500/15 text-blue-200'
                          : 'border-white/5 bg-white/5 text-slate-500 hover:bg-white/5 hover:text-slate-200'
                      }`}
                      onClick={() => setMarket(value)}
                      type="button"
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="text-ui-caption text-slate-600">
                  {session.label} · {session.targetTradingDate || '等待交易日'}{' '}
                  ·{result.data ? ` 已载入 ${rows.length}` : ' 正在连接目录'}
                </div>
              </div>

              {storageMessage ? (
                <div
                  aria-live="polite"
                  className="mt-3 rounded-md border border-amber-400/20 bg-amber-400/[0.06] px-3 py-2 text-ui-caption text-amber-200"
                >
                  {storageMessage}
                </div>
              ) : null}

              <section className="mt-4 overflow-hidden rounded-panel border border-white/10 bg-slate-900">
                <div className="flex items-center justify-between gap-3 border-b border-white/5 px-ui-section py-3">
                  <div>
                    <h2 className="text-ui-label font-black text-slate-200">
                      指数目录
                    </h2>
                    <p className="mt-1 text-ui-caption text-slate-600">
                      报价来自最新真实快照；缺失或过期不会显示旧数值。
                    </p>
                  </div>
                  <span className="shrink-0 text-ui-caption font-bold text-slate-600">
                    {search.trim() ? `搜索：${search.trim()}` : '按代码升序'}
                  </span>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full min-w-[860px] border-collapse text-left">
                    <thead className="bg-black/10 text-ui-micro font-bold uppercase tracking-[0.12em] text-slate-600">
                      <tr>
                        <th className="px-ui-section py-2.5">指数</th>
                        <th className="px-ui-section py-2.5">代码</th>
                        <th className="px-ui-section py-2.5">市场</th>
                        <th className="px-ui-section py-2.5 text-right">
                          最新
                        </th>
                        <th className="px-ui-section py-2.5 text-right">
                          涨跌幅
                        </th>
                        <th className="px-ui-section py-2.5 text-right">
                          状态 / 更新时间
                        </th>
                        <th className="px-ui-section py-2.5 text-right">
                          工作台
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {initialLoading ? (
                        <tr>
                          <td
                            className="px-ui-section py-ui-empty text-center text-ui-label text-slate-500"
                            colSpan={7}
                          >
                            <span className="inline-flex items-center gap-2">
                              <LoaderCircle className="h-4 w-4 animate-spin" />
                              正在加载真实指数目录…
                            </span>
                          </td>
                        </tr>
                      ) : result.error ? (
                        <tr>
                          <td
                            className="px-ui-section py-ui-empty text-center text-ui-label text-amber-200"
                            colSpan={7}
                          >
                            目录查询失败，请检查行情服务后重试。
                          </td>
                        </tr>
                      ) : rows.length === 0 ? (
                        <tr>
                          <td
                            className="px-ui-section py-ui-empty text-center text-ui-label text-slate-600"
                            colSpan={7}
                          >
                            {search.trim()
                              ? '没有匹配的真实指数。请修改名称或代码后重试。'
                              : '真实指数目录暂无可展示数据。'}
                          </td>
                        </tr>
                      ) : (
                        rows.map(row => {
                          const liveQuote = quoteState.quotes.get(row.code);
                          const quote: MarketQuoteSnapshot | undefined =
                            liveQuote
                              ? { ...liveQuote, source: 'live' }
                              : undefined;
                          const isFresh = Boolean(
                            quote &&
                            isMarketQuoteFreshForSession(
                              quote.time,
                              session.now,
                              session.phase
                            )
                          );
                          const configured = isConfigured(row.code);
                          const onWorkbench = isOnWorkbench(row.code);
                          const blockedAtLimit =
                            !configured &&
                            preferences.items.length >= MAX_MARKET_INDEXES;
                          return (
                            <tr
                              className="group hover:bg-white/[0.025]"
                              key={row.code}
                            >
                              <td className="px-ui-section py-3">
                                <div className="flex items-center gap-3">
                                  <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-white/5 bg-white/5 text-ui-caption font-black text-slate-400">
                                    {row.shortName.slice(0, 2)}
                                  </div>
                                  <div className="min-w-0">
                                    <div className="truncate text-ui-label font-black text-slate-200">
                                      {row.name}
                                    </div>
                                    <div className="mt-0.5 text-ui-caption text-slate-600">
                                      {row.shortName}
                                    </div>
                                  </div>
                                </div>
                              </td>
                              <td className="px-ui-section py-3 font-mono text-ui-caption font-bold text-slate-400">
                                {row.code}
                              </td>
                              <td className="px-ui-section py-3 text-ui-caption font-bold text-slate-500">
                                {row.group}
                              </td>
                              <td className="px-ui-section py-3 text-right">
                                <DirectoryQuote
                                  isFresh={isFresh}
                                  quote={quote}
                                />
                              </td>
                              <td className="px-ui-section py-3 text-right">
                                <DirectoryChange
                                  isFresh={isFresh}
                                  quote={quote}
                                />
                              </td>
                              <td className="px-ui-section py-3 text-right">
                                <DirectoryStatus
                                  isFresh={isFresh}
                                  quote={quote}
                                />
                              </td>
                              <td className="px-ui-section py-3 text-right">
                                <Button
                                  aria-label={
                                    blockedAtLimit
                                      ? `工作台已达100项，无法加入${row.name}`
                                      : `${onWorkbench ? '移出' : configured ? '显示' : '加入'}工作台${row.name}`
                                  }
                                  className={`h-control-compact rounded-md border px-2.5 text-ui-caption font-bold ${
                                    onWorkbench
                                      ? 'border-blue-400/20 bg-blue-400/10 text-blue-200 hover:bg-rose-400/10 hover:text-rose-200'
                                      : blockedAtLimit
                                        ? 'cursor-not-allowed border-amber-400/20 bg-amber-400/[0.04] text-amber-200/70'
                                        : 'border-white/10 bg-white/[0.03] text-slate-400 hover:bg-blue-500/10 hover:text-blue-200'
                                  }`}
                                  disabled={blockedAtLimit}
                                  onClick={() => toggleWorkbench(row)}
                                  title={
                                    blockedAtLimit
                                      ? '工作台最多配置 100 个指数，请先移除一个。'
                                      : undefined
                                  }
                                  type="button"
                                  variant="ghost"
                                >
                                  {onWorkbench ? (
                                    <>
                                      <Check className="h-3.5 w-3.5" />
                                      已加入
                                    </>
                                  ) : configured ? (
                                    <>
                                      <Plus className="h-3.5 w-3.5" />
                                      显示
                                    </>
                                  ) : (
                                    <>
                                      <Plus className="h-3.5 w-3.5" />
                                      {blockedAtLimit ? '已达100上限' : '加入'}
                                    </>
                                  )}
                                </Button>
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-3 border-t border-white/5 px-ui-section py-3">
                  <span className="text-ui-caption text-slate-600">
                    {pageInfo?.hasNextPage
                      ? '还有更多真实指数，可继续加载。'
                      : rows.length > 0
                        ? '已到达目录末尾。'
                        : ''}
                  </span>
                  {pageInfo?.hasNextPage ? (
                    <Button
                      className="h-control-compact rounded-md border border-white/10 bg-white/[0.03] px-3 text-ui-caption font-bold text-slate-400 hover:bg-white/[0.06] hover:text-slate-100"
                      disabled={loadingNextPage}
                      onClick={() => setAfter(pageInfo.endCursor ?? null)}
                      type="button"
                      variant="ghost"
                    >
                      {loadingNextPage ? (
                        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <ChevronDown className="h-3.5 w-3.5" />
                      )}
                      {loadingNextPage ? '正在加载…' : '加载更多'}
                    </Button>
                  ) : null}
                </div>
              </section>
            </div>
          </main>
        </div>
      }
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
            指数目录
          </span>
          <span className="text-slate-700">|</span>
          <span>真实报价 / 缺失不模拟</span>
        </>
      }
      statusBarRight={
        <>
          <span>{totalCount ? `${totalCount} 个指数` : '目录读取中'}</span>
          <span className="text-slate-700">|</span>
          <Link className="hover:text-slate-200" href="/">
            返回工作台
          </Link>
        </>
      }
    />
  );
}
