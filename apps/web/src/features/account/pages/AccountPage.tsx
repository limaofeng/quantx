import {
  ArrowDownToLine,
  ArrowUpRight,
  BarChart3,
  BriefcaseBusiness,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Download,
  HandCoins,
  Landmark,
  RefreshCw,
  Repeat2,
  ShoppingCart,
  WalletCards,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useLocation, useSearch } from 'wouter';

import { Button } from '@/components/ui/button';
import { ConfirmDialog } from '@/components/ui/confirm-dialog';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  StudioPageFrame,
  StudioPageStack,
} from '@/components/ui/studio-layout';
import { useCurrentAccount } from '@/features/dashboard/hooks';
import {
  useCancelOrder,
  useHistoryOrders,
  useHistoryTrades,
  useTodayOrders,
  useTodayTrades,
} from '@/features/trading/hooks/useTrading';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';

import {
  useAccountOverview,
  useClosedPositionCycles,
} from '../hooks/useAccountCenter';
import {
  daysAgoKey,
  calculateIntradayReference,
  downloadCsv,
  formatDateTime,
  formatMoney,
  formatPercent,
  pnlClass,
  shanghaiDateKey,
} from '../utils';

type AccountView = 'overview' | 'orders' | 'trades' | 'pnl' | 'closed';
type RecordScope = 'today' | 'history';

const VIEWS: Array<{ id: AccountView; label: string }> = [
  { id: 'overview', label: '总览' },
  { id: 'orders', label: '委托' },
  { id: 'trades', label: '成交' },
  { id: 'pnl', label: '盈亏' },
  { id: 'closed', label: '已清仓' },
];
const PAGE_SIZE = 20;
const CANCELABLE_STATUSES = new Set([
  'UNREPORTED',
  'WAIT_REPORTING',
  'REPORTED',
  'REPORTED_CANCEL',
  'PART_SUCC',
]);

function getView(search: string): AccountView {
  const view = new URLSearchParams(search).get('view');
  return VIEWS.some(item => item.id === view)
    ? (view as AccountView)
    : 'overview';
}

function KpiCard({
  label,
  value,
  detail,
  tone,
  icon: Icon,
}: {
  label: string;
  value: string;
  detail: string;
  tone?: string;
  icon: typeof WalletCards;
}) {
  return (
    <section className="min-w-0 rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section">
      <div className="mb-4 flex items-start justify-between gap-3">
        <span className="text-ui-label font-medium text-slate-400">
          {label}
        </span>
        <span className="rounded-md border border-white/[0.08] bg-white/[0.035] p-2 text-slate-300">
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div
        className={cn('truncate font-mono text-ui-display font-semibold', tone)}
      >
        {value}
      </div>
      <p className="mt-2 truncate text-ui-caption text-slate-500">{detail}</p>
    </section>
  );
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="flex min-h-48 flex-col items-center justify-center rounded-lg border border-dashed border-white/[0.08] bg-white/[0.015] px-ui-panel text-center">
      <Clock3 className="mb-3 h-6 w-6 text-slate-600" />
      <p className="text-ui-body font-medium text-slate-300">{title}</p>
      <p className="mt-1 max-w-md text-ui-label leading-5 text-slate-500">
        {detail}
      </p>
    </div>
  );
}

function ScopeSwitch({
  value,
  onChange,
}: {
  value: RecordScope;
  onChange: (value: RecordScope) => void;
}) {
  return (
    <div className="flex rounded-md border border-white/[0.08] bg-[#080d18]/80 p-0.5">
      {(['today', 'history'] as const).map(scope => (
        <button
          key={scope}
          type="button"
          onClick={() => onChange(scope)}
          className={cn(
            'min-h-8 cursor-pointer rounded-md px-3 text-ui-label transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
            value === scope
              ? 'bg-blue-500/15 text-blue-300'
              : 'text-slate-500 hover:text-slate-300'
          )}
        >
          {scope === 'today' ? '当日' : '历史'}
        </button>
      ))}
    </div>
  );
}

function Pagination({
  page,
  total,
  onPageChange,
}: {
  page: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return (
    <div className="flex items-center justify-between border-t border-white/[0.06] px-3 py-2 text-ui-label text-slate-500">
      <span>共 {total} 条</span>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          className="h-control-compact w-8"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <span className="font-mono text-slate-300">
          {page} / {pages}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-control-compact w-8"
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

export function AccountPage() {
  const [, setLocation] = useLocation();
  const search = useSearch();
  const view = getView(search);
  const { toast } = useToast();
  const today = shanghaiDateKey();
  const {
    data: accountData,
    loading: accountLoading,
    error: accountError,
  } = useCurrentAccount();
  const account = accountData?.currentAccount;
  const accountId = account?.id;
  const [pnlDays, setPnlDays] = useState(90);
  const overview = useAccountOverview(accountId, daysAgoKey(pnlDays), today);
  const todayOrders = useTodayOrders(accountId);
  const todayTrades = useTodayTrades(accountId);
  const [orderScope, setOrderScope] = useState<RecordScope>('today');
  const [tradeScope, setTradeScope] = useState<RecordScope>('today');
  const [startDate, setStartDate] = useState(() => daysAgoKey(30));
  const [endDate, setEndDate] = useState(today);
  const historyOrders = useHistoryOrders(
    orderScope === 'history' ? accountId || '' : '',
    startDate,
    endDate
  );
  const historyTrades = useHistoryTrades(
    tradeScope === 'history' ? accountId || '' : '',
    startDate,
    endDate
  );
  const [stockFilter, setStockFilter] = useState('');
  const [directionFilter, setDirectionFilter] = useState('ALL');
  const [statusFilter, setStatusFilter] = useState('ALL');
  const [page, setPage] = useState(1);
  const [closedPage, setClosedPage] = useState(1);
  const closed = useClosedPositionCycles(
    accountId,
    startDate,
    endDate,
    PAGE_SIZE,
    (closedPage - 1) * PAGE_SIZE,
    view !== 'closed'
  );
  const { cancelOrder, fetching: cancelling } = useCancelOrder();
  const [cancelTarget, setCancelTarget] = useState<{
    id: string;
    label: string;
  } | null>(null);

  useEffect(() => {
    setPage(1);
  }, [stockFilter, directionFilter, statusFilter, orderScope, tradeScope]);

  const intraday = useMemo(() => {
    const result = calculateIntradayReference(
      overview.positions,
      overview.snapshots.map(snapshot => ({
        ...snapshot,
        tradeDate: String(snapshot.tradeDate),
      })),
      today
    );
    const detail =
      result.source === 'REALTIME_QUOTE'
        ? `实时行情覆盖 ${result.covered}/${result.total} · ${formatDateTime(result.quoteTime)}`
        : result.source === 'SAME_DAY_SNAPSHOT'
          ? `同交易日资产快照 · ${formatDateTime(result.snapshotAt)}`
          : result.total > 0
            ? `实时行情覆盖 0/${result.total}，且无同日资产快照`
            : '当前无持仓；未生成当日资产快照';
    return { ...result, detail };
  }, [overview.positions, overview.snapshots, today]);

  const pnlStats = useMemo(() => {
    const values = overview.snapshots.filter(
      snapshot => typeof snapshot.dailyPnlCny === 'number'
    );
    const amounts = values.map(snapshot => snapshot.dailyPnlCny as number);
    const winning = amounts.filter(value => value > 0).length;
    const losing = amounts.filter(value => value < 0).length;
    const total = amounts.reduce((sum, value) => sum + value, 0);
    const quality = [...new Set(values.map(item => item.dataQuality))];
    return {
      values,
      total,
      winning,
      losing,
      ratio: amounts.length ? (winning / amounts.length) * 100 : null,
      average: amounts.length ? total / amounts.length : null,
      best: amounts.length ? Math.max(...amounts) : null,
      worst: amounts.length ? Math.min(...amounts) : null,
      maxAbs: Math.max(1, ...amounts.map(value => Math.abs(value))),
      quality: quality.length ? quality.join(' / ') : '无有效快照',
    };
  }, [overview.snapshots]);

  const orderSource =
    orderScope === 'today' ? todayOrders.orders : historyOrders.orders;
  const filteredOrders = useMemo(
    () =>
      orderSource.filter(order => {
        const keyword = stockFilter.trim().toUpperCase();
        const stockMatch =
          !keyword ||
          order.stockCode.toUpperCase().includes(keyword) ||
          order.stockName.includes(stockFilter.trim());
        const directionMatch =
          directionFilter === 'ALL' || String(order.type) === directionFilter;
        const statusMatch =
          statusFilter === 'ALL' || String(order.status) === statusFilter;
        return stockMatch && directionMatch && statusMatch;
      }),
    [directionFilter, orderSource, statusFilter, stockFilter]
  );
  const visibleOrders = filteredOrders.slice(
    (page - 1) * PAGE_SIZE,
    page * PAGE_SIZE
  );

  const tradeSource =
    tradeScope === 'today' ? todayTrades.trades : historyTrades.trades;
  const filteredTrades = useMemo(
    () =>
      tradeSource.filter(trade => {
        const keyword = stockFilter.trim().toUpperCase();
        const stockMatch =
          !keyword ||
          trade.stockCode.toUpperCase().includes(keyword) ||
          trade.stockName.includes(stockFilter.trim());
        const direction = Number(trade.orderType) === 23 ? 'BUY' : 'SELL';
        return (
          stockMatch &&
          (directionFilter === 'ALL' || direction === directionFilter)
        );
      }),
    [directionFilter, stockFilter, tradeSource]
  );
  const visibleTrades = filteredTrades.slice(
    (page - 1) * PAGE_SIZE,
    page * PAGE_SIZE
  );

  const refreshAll = () => {
    overview.refresh();
    todayOrders.refresh();
    todayTrades.refresh();
    historyOrders.refresh();
    historyTrades.refresh();
    closed.refresh();
  };

  const exportCurrentView = () => {
    if (view === 'orders') {
      downloadCsv(
        `quantx-orders-${today}.csv`,
        [
          '委托时间',
          '证券代码',
          '证券名称',
          '方向',
          '状态',
          '价格',
          '数量',
          '成交数量',
        ],
        filteredOrders.map(order => [
          order.time,
          order.stockCode,
          order.stockName,
          String(order.type),
          String(order.status),
          order.price,
          order.volume,
          order.tradedVolume,
        ])
      );
      return;
    }
    if (view === 'trades') {
      downloadCsv(
        `quantx-trades-${today}.csv`,
        [
          '成交时间',
          '成交编号',
          '证券代码',
          '证券名称',
          '方向',
          '价格',
          '数量',
          '金额',
        ],
        filteredTrades.map(trade => [
          trade.tradedTime,
          trade.tradedId,
          trade.stockCode,
          trade.stockName,
          Number(trade.orderType) === 23 ? '买入' : '卖出',
          trade.tradedPrice,
          trade.tradedVolume,
          trade.tradedAmount,
        ])
      );
      return;
    }
    if (view === 'closed') {
      downloadCsv(
        `quantx-closed-${today}.csv`,
        [
          '证券代码',
          '证券名称',
          '开仓时间',
          '清仓时间',
          '买入数量',
          '卖出数量',
          '毛实现盈亏',
          '数据质量',
        ],
        (closed.page?.items ?? []).map(item => [
          item.stockCode,
          item.instrumentName,
          item.openedAt,
          item.closedAt,
          item.buyVolume,
          item.sellVolume,
          item.grossRealizedPnl,
          item.pnlQuality,
        ])
      );
      return;
    }
    if (view === 'pnl') {
      downloadCsv(
        `quantx-pnl-${pnlDays}d-${today}.csv`,
        ['交易日', '当日盈亏', '当日收益率', '总资产', '数据质量', '来源'],
        pnlStats.values.map(item => [
          String(item.tradeDate),
          item.dailyPnlCny,
          item.dailyReturnPct,
          item.totalAssetCny,
          item.dataQuality,
          item.source,
        ])
      );
      return;
    }
    downloadCsv(
      `quantx-positions-${today}.csv`,
      [
        '证券代码',
        '证券名称',
        '持仓',
        '可用',
        '成本价',
        '现价',
        '市值',
        '持仓盈亏',
      ],
      overview.positions.map(position => [
        position.stockCode,
        position.instrumentName,
        position.volume,
        position.canUseVolume,
        position.avgPrice,
        position.quote?.lastPrice ?? position.lastPrice,
        position.marketValue,
        position.profitLoss,
      ])
    );
  };

  const handleCancel = async () => {
    if (!cancelTarget || !accountId) return;
    const result = await cancelOrder(cancelTarget.id, accountId);
    const payload = result.data?.cancelOrder;
    if (!payload?.success) {
      toast({
        title: '撤单失败',
        description:
          payload?.message || result.error?.message || '未收到券商确认',
        variant: 'destructive',
      });
      return;
    }
    toast({ title: '撤单已提交', description: payload.message });
    setCancelTarget(null);
    todayOrders.refresh();
  };

  const loading = accountLoading || overview.loading;
  const error = accountError || overview.error;
  if (!loading && !account) {
    return (
      <StudioPageFrame className="text-slate-200">
        <EmptyState
          title="当前未连接资金账户"
          detail={
            error?.message ||
            '账户概览不会使用默认账号或模拟资产。请先连接 miniQMT 并完成一次账户同步。'
          }
        />
      </StudioPageFrame>
    );
  }

  const marketRatio = account?.totalAsset
    ? (account.marketValue / account.totalAsset) * 100
    : null;
  const recentActiveOrders = todayOrders.orders
    .filter(order => CANCELABLE_STATUSES.has(String(order.status)))
    .slice(0, 5);
  const recentTrades = todayTrades.trades.slice(0, 5);

  return (
    <StudioPageFrame className="text-slate-100">
      <StudioPageStack className="space-y-ui-section pb-ui-section">
        <header className="flex min-h-control-large flex-col justify-center gap-3 rounded-panel border border-white/[0.06] bg-[#0b1120]/95 px-ui-panel py-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <Landmark className="h-5 w-5 text-blue-400" />
              <h1 className="truncate text-ui-label font-black uppercase tracking-[0.18em] text-slate-100">
                账户概览
              </h1>
              <span className="rounded-full border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-ui-caption text-emerald-300">
                miniQMT · 单账户
              </span>
            </div>
            <p className="mt-1 truncate text-ui-caption font-medium text-slate-500">
              {account
                ? `资金、持仓、委托与盈亏 · ${account.accountName} · ${account.id}`
                : '正在读取 miniQMT 账户'}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-control-compact border-white/[0.08] bg-white/[0.025] text-ui-label hover:border-white/20 hover:bg-white/[0.05]"
              onClick={refreshAll}
              disabled={loading}
            >
              <RefreshCw
                className={cn('mr-2 h-4 w-4', loading && 'animate-spin')}
              />
              刷新
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-control-compact border-white/[0.08] bg-white/[0.025] text-ui-label hover:border-white/20 hover:bg-white/[0.05]"
              onClick={exportCurrentView}
            >
              <Download className="mr-2 h-4 w-4" />
              导出 CSV
            </Button>
          </div>
        </header>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <KpiCard
            label="总资产"
            value={formatMoney(account?.totalAsset)}
            detail={`现金 ${formatMoney(account?.cash)} · 冻结 ${formatMoney(account?.frozenCash)}`}
            icon={WalletCards}
          />
          <KpiCard
            label="总盈亏"
            value={formatMoney(account?.totalProfitLoss, true)}
            detail={`账户口径 ${formatPercent(account?.profitLossPercent, true)}`}
            tone={pnlClass(account?.totalProfitLoss)}
            icon={BarChart3}
          />
          <KpiCard
            label="总市值"
            value={formatMoney(account?.marketValue)}
            detail={`仓位 ${formatPercent(marketRatio)} · ${overview.positions.length} 个持仓`}
            icon={BriefcaseBusiness}
          />
          <KpiCard
            label="当日参考盈亏"
            value={formatMoney(intraday.value, true)}
            detail={`${formatPercent(intraday.percent, true)} · ${intraday.detail}`}
            tone={pnlClass(intraday.value)}
            icon={ArrowUpRight}
          />
        </div>

        <div className="flex items-center justify-between gap-3 text-ui-caption text-slate-500">
          <span>
            数据来源：
            {intraday.source === 'SAME_DAY_SNAPSHOT'
              ? 'miniQMT 同日资产快照'
              : 'miniQMT 当前账户 / 实时行情'}
          </span>
          <span>账户更新时间：{formatDateTime(account?.updateTime)}</span>
        </div>

        <nav className="sticky top-0 z-20 overflow-x-auto rounded-lg border border-white/[0.06] bg-[#0b1120]/95 px-2 backdrop-blur">
          <div className="flex min-w-max">
            {VIEWS.map(item => (
              <button
                key={item.id}
                type="button"
                onClick={() => setLocation(`/account?view=${item.id}`)}
                className={cn(
                  'min-h-12 cursor-pointer border-b-2 px-ui-section text-ui-body transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400/70',
                  view === item.id
                    ? 'border-blue-400 text-blue-300'
                    : 'border-transparent text-slate-500 hover:text-slate-200'
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
        </nav>

        {error && (
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/10 px-ui-section py-3 text-ui-label text-amber-200">
            部分账户数据加载失败：{error.message}。刷新后将重新请求真实数据。
          </div>
        )}

        {view === 'overview' && (
          <div className="grid gap-ui-section xl:grid-cols-[1.1fr_1fr]">
            <section className="rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section">
              <h2 className="text-ui-body font-medium">资产构成</h2>
              <div className="mt-5 h-2 overflow-hidden rounded-full bg-white/5">
                <div
                  className="h-full bg-blue-400"
                  style={{
                    width: `${Math.min(100, Math.max(0, marketRatio || 0))}%`,
                  }}
                />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3 text-ui-label sm:grid-cols-4">
                {[
                  ['持仓市值', formatMoney(account?.marketValue)],
                  ['可用现金', formatMoney(account?.cash)],
                  ['冻结资金', formatMoney(account?.frozenCash)],
                  ['现金占比', formatPercent(overview.summary?.cashRatio)],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-lg bg-white/[0.03] p-3">
                    <p className="text-slate-500">{label}</p>
                    <p className="mt-2 font-mono text-slate-200">{value}</p>
                  </div>
                ))}
              </div>
            </section>
            <section className="rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section">
              <h2 className="text-ui-body font-medium">快捷操作</h2>
              <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {[
                  ['买入', ShoppingCart, '/holdings?mode=ORDER&side=BUY'],
                  ['卖出', ArrowDownToLine, '/holdings?mode=ORDER&side=SELL'],
                  ['卖出管理', HandCoins, '/liquidation'],
                  ['做T助手', Repeat2, '/t-trade'],
                ].map(([label, Icon, href]) => (
                  <button
                    key={String(label)}
                    type="button"
                    onClick={() => setLocation(String(href))}
                    className="flex min-h-20 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-white/[0.08] bg-white/[0.025] text-ui-label text-slate-300 transition-colors duration-200 hover:border-blue-500/40 hover:bg-blue-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                  >
                    <Icon className="h-5 w-5 text-blue-300" />
                    {String(label)}
                  </button>
                ))}
              </div>
              <p className="mt-3 text-ui-caption text-slate-600">
                账户概览只提供入口，不在本页直接下单或清仓。
              </p>
            </section>
            <section className="rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-ui-body font-medium">待处理委托</h2>
                <button
                  className="cursor-pointer rounded-sm text-ui-label text-blue-300 transition-colors hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                  onClick={() => setLocation('/account?view=orders')}
                >
                  查看全部
                </button>
              </div>
              {recentActiveOrders.length ? (
                <div className="space-y-2">
                  {recentActiveOrders.map(order => (
                    <div
                      key={order.id}
                      className="flex items-center justify-between rounded-lg bg-white/[0.025] p-3 text-ui-label"
                    >
                      <div>
                        <p>{order.stockName || order.stockCode}</p>
                        <p className="mt-1 font-mono text-slate-500">
                          {order.stockCode} · {String(order.status)}
                        </p>
                      </div>
                      <div className="text-right">
                        <p
                          className={
                            String(order.type) === 'BUY'
                              ? 'text-market-up'
                              : 'text-market-down'
                          }
                        >
                          {String(order.type) === 'BUY' ? '买入' : '卖出'}{' '}
                          {order.volume}
                        </p>
                        <p className="mt-1 font-mono text-slate-500">
                          {formatMoney(order.price)}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="暂无待处理委托"
                  detail="当前账户没有可撤或待成交委托。"
                />
              )}
            </section>
            <section className="rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-ui-body font-medium">最近成交</h2>
                <button
                  className="cursor-pointer rounded-sm text-ui-label text-blue-300 transition-colors hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                  onClick={() => setLocation('/account?view=trades')}
                >
                  查看全部
                </button>
              </div>
              {recentTrades.length ? (
                <div className="space-y-2">
                  {recentTrades.map(trade => (
                    <div
                      key={trade.tradedId}
                      className="flex items-center justify-between rounded-lg bg-white/[0.025] p-3 text-ui-label"
                    >
                      <div>
                        <p>{trade.stockName || trade.stockCode}</p>
                        <p className="mt-1 font-mono text-slate-500">
                          {formatDateTime(trade.tradedTime)}
                        </p>
                      </div>
                      <div className="text-right">
                        <p
                          className={
                            Number(trade.orderType) === 23
                              ? 'text-market-up'
                              : 'text-market-down'
                          }
                        >
                          {Number(trade.orderType) === 23 ? '买入' : '卖出'}{' '}
                          {trade.tradedVolume}
                        </p>
                        <p className="mt-1 font-mono text-slate-500">
                          {formatMoney(trade.tradedAmount)}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState
                  title="暂无当日成交"
                  detail="miniQMT 尚未返回当前账户的当日成交。"
                />
              )}
            </section>
            <section className="rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section xl:col-span-2">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="text-ui-body font-medium">主要持仓</h2>
                <button
                  className="cursor-pointer rounded-sm text-ui-label text-blue-300 transition-colors hover:text-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70"
                  onClick={() => setLocation('/holdings')}
                >
                  打开持仓工作台
                </button>
              </div>
              {overview.positions.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[720px] text-left text-ui-label">
                    <thead className="text-slate-500">
                      <tr>
                        <th className="py-2">证券</th>
                        <th>持仓 / 可用</th>
                        <th className="text-right">成本 / 现价</th>
                        <th className="text-right">市值</th>
                        <th className="text-right">持仓盈亏</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {overview.positions.slice(0, 10).map(position => (
                        <tr key={position.id}>
                          <td className="py-3">
                            <p>
                              {position.instrumentName || position.stockCode}
                            </p>
                            <p className="mt-1 font-mono text-slate-500">
                              {position.stockCode}
                            </p>
                          </td>
                          <td className="font-mono">
                            {position.volume} / {position.canUseVolume}
                          </td>
                          <td className="text-right font-mono">
                            {position.avgPrice?.toFixed(3) ?? '--'} /{' '}
                            {(
                              position.quote?.lastPrice ?? position.lastPrice
                            )?.toFixed(3) ?? '--'}
                          </td>
                          <td className="text-right font-mono">
                            {formatMoney(position.marketValue)}
                          </td>
                          <td
                            className={cn(
                              'text-right font-mono',
                              pnlClass(position.profitLoss, 'holding')
                            )}
                          >
                            {formatMoney(position.profitLoss, true)}
                            <p className="mt-1 text-ui-caption">
                              {formatPercent(position.profitRate, true)}
                            </p>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <EmptyState
                  title="当前无持仓"
                  detail="该账户的真实持仓快照为空。"
                />
              )}
            </section>
          </div>
        )}

        {(view === 'orders' || view === 'trades') && (
          <section className="overflow-hidden rounded-lg border border-white/[0.06] bg-[#0b1120]/80">
            <div className="flex flex-col gap-3 border-b border-white/[0.06] p-3 lg:flex-row lg:items-center lg:justify-between">
              <ScopeSwitch
                value={view === 'orders' ? orderScope : tradeScope}
                onChange={value =>
                  view === 'orders'
                    ? setOrderScope(value)
                    : setTradeScope(value)
                }
              />
              <div className="flex flex-wrap gap-2">
                <Input
                  value={stockFilter}
                  onChange={event => setStockFilter(event.target.value)}
                  placeholder="代码 / 名称"
                  className="h-9 w-36 border-white/[0.08] bg-[#080d18]/80 text-ui-label"
                />
                <Select
                  value={directionFilter}
                  onValueChange={setDirectionFilter}
                >
                  <SelectTrigger className="h-control-default w-28 border-white/[0.08] bg-[#080d18]/80 text-ui-label">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="ALL">全部方向</SelectItem>
                    <SelectItem value="BUY">买入</SelectItem>
                    <SelectItem value="SELL">卖出</SelectItem>
                  </SelectContent>
                </Select>
                {view === 'orders' && (
                  <Select value={statusFilter} onValueChange={setStatusFilter}>
                    <SelectTrigger className="h-control-default w-32 border-white/[0.08] bg-[#080d18]/80 text-ui-label">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="ALL">全部状态</SelectItem>
                      <SelectItem value="REPORTED">已报</SelectItem>
                      <SelectItem value="PART_SUCC">部分成交</SelectItem>
                      <SelectItem value="SUCCEEDED">已成</SelectItem>
                      <SelectItem value="CANCELED">已撤</SelectItem>
                      <SelectItem value="JUNK">废单</SelectItem>
                    </SelectContent>
                  </Select>
                )}
                {((view === 'orders' && orderScope === 'history') ||
                  (view === 'trades' && tradeScope === 'history')) && (
                  <>
                    <Input
                      type="date"
                      value={startDate}
                      min={daysAgoKey(365)}
                      max={endDate}
                      onChange={event => setStartDate(event.target.value)}
                      className="h-9 w-36 border-white/[0.08] bg-[#080d18]/80 text-ui-label"
                    />
                    <Input
                      type="date"
                      value={endDate}
                      min={startDate}
                      max={today}
                      onChange={event => setEndDate(event.target.value)}
                      className="h-9 w-36 border-white/[0.08] bg-[#080d18]/80 text-ui-label"
                    />
                  </>
                )}
              </div>
            </div>
            {view === 'orders' ? (
              visibleOrders.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[900px] text-left text-ui-label">
                    <thead className="bg-white/[0.025] text-slate-500">
                      <tr>
                        <th className="px-3 py-3">时间</th>
                        <th>证券</th>
                        <th>方向</th>
                        <th>状态</th>
                        <th className="text-right">委托价</th>
                        <th className="text-right">委托 / 成交</th>
                        <th className="px-3 text-right">操作</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/5">
                      {visibleOrders.map(order => (
                        <tr key={order.id}>
                          <td className="px-3 py-3 font-mono text-slate-400">
                            {formatDateTime(order.time)}
                          </td>
                          <td>
                            <p>{order.stockName || order.stockCode}</p>
                            <p className="mt-1 font-mono text-slate-500">
                              {order.stockCode}
                            </p>
                          </td>
                          <td
                            className={
                              String(order.type) === 'BUY'
                                ? 'text-market-up'
                                : 'text-market-down'
                            }
                          >
                            {String(order.type) === 'BUY' ? '买入' : '卖出'}
                          </td>
                          <td>{String(order.status)}</td>
                          <td className="text-right font-mono">
                            {formatMoney(order.price)}
                          </td>
                          <td className="text-right font-mono">
                            {order.volume} / {order.tradedVolume}
                          </td>
                          <td className="px-3 text-right">
                            {orderScope === 'today' &&
                            CANCELABLE_STATUSES.has(String(order.status)) ? (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-control-compact text-amber-300"
                                disabled={cancelling}
                                onClick={() =>
                                  setCancelTarget({
                                    id: order.id,
                                    label: order.stockName || order.stockCode,
                                  })
                                }
                              >
                                撤单
                              </Button>
                            ) : (
                              <span className="text-slate-600">--</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="p-ui-section">
                  <EmptyState
                    title="暂无委托记录"
                    detail="筛选范围内没有当前账户的真实委托。"
                  />
                </div>
              )
            ) : visibleTrades.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[860px] text-left text-ui-label">
                  <thead className="bg-white/[0.025] text-slate-500">
                    <tr>
                      <th className="px-3 py-3">时间</th>
                      <th>证券</th>
                      <th>方向</th>
                      <th className="text-right">成交价</th>
                      <th className="text-right">数量</th>
                      <th className="px-3 text-right">成交金额</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {visibleTrades.map(trade => (
                      <tr key={trade.tradedId}>
                        <td className="px-3 py-3 font-mono text-slate-400">
                          {formatDateTime(trade.tradedTime)}
                        </td>
                        <td>
                          <p>{trade.stockName || trade.stockCode}</p>
                          <p className="mt-1 font-mono text-slate-500">
                            {trade.stockCode}
                          </p>
                        </td>
                        <td
                          className={
                            Number(trade.orderType) === 23
                              ? 'text-market-up'
                              : 'text-market-down'
                          }
                        >
                          {Number(trade.orderType) === 23 ? '买入' : '卖出'}
                        </td>
                        <td className="text-right font-mono">
                          {trade.tradedPrice.toFixed(3)}
                        </td>
                        <td className="text-right font-mono">
                          {trade.tradedVolume}
                        </td>
                        <td className="px-3 text-right font-mono">
                          {formatMoney(trade.tradedAmount)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-ui-section">
                <EmptyState
                  title="暂无成交记录"
                  detail="筛选范围内没有当前账户的真实成交。"
                />
              </div>
            )}
            <Pagination
              page={page}
              total={
                view === 'orders'
                  ? filteredOrders.length
                  : filteredTrades.length
              }
              onPageChange={setPage}
            />
          </section>
        )}

        {view === 'pnl' && (
          <div className="space-y-ui-section">
            <div className="flex justify-end">
              <Select
                value={String(pnlDays)}
                onValueChange={value => setPnlDays(Number(value))}
              >
                <SelectTrigger className="h-control-default w-32 border-white/[0.08] bg-[#080d18]/80 text-ui-label">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[30, 90, 180, 365].map(days => (
                    <SelectItem key={days} value={String(days)}>
                      近 {days} 日
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-6">
              {[
                [
                  '累计日盈亏',
                  formatMoney(pnlStats.total, true),
                  pnlClass(pnlStats.total),
                ],
                [
                  '盈利 / 亏损天数',
                  `${pnlStats.winning} / ${pnlStats.losing}`,
                  'text-slate-200',
                ],
                ['盈利日占比', formatPercent(pnlStats.ratio), 'text-slate-200'],
                [
                  '平均日盈亏',
                  formatMoney(pnlStats.average, true),
                  pnlClass(pnlStats.average),
                ],
                [
                  '最佳交易日',
                  formatMoney(pnlStats.best, true),
                  pnlClass(pnlStats.best),
                ],
                [
                  '最差交易日',
                  formatMoney(pnlStats.worst, true),
                  pnlClass(pnlStats.worst),
                ],
              ].map(([label, value, tone]) => (
                <div
                  key={label}
                  className="rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section"
                >
                  <p className="text-ui-label text-slate-500">{label}</p>
                  <p
                    className={cn(
                      'mt-3 font-mono text-ui-title font-medium',
                      tone
                    )}
                  >
                    {value}
                  </p>
                </div>
              ))}
            </div>
            <section className="rounded-lg border border-white/[0.06] bg-[#0b1120]/80 p-ui-section">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-ui-body font-medium">日终盈亏序列</h2>
                <span className="text-ui-caption text-slate-500">
                  数据质量：{pnlStats.quality}
                </span>
              </div>
              {pnlStats.values.length ? (
                <div className="space-y-2">
                  {pnlStats.values.slice(-90).map(item => {
                    const value = item.dailyPnlCny as number;
                    return (
                      <div
                        key={item.id}
                        className="grid grid-cols-[84px_1fr_110px] items-center gap-3 text-ui-label"
                      >
                        <span className="font-mono text-slate-500">
                          {String(item.tradeDate)}
                        </span>
                        <div className="relative h-6 rounded bg-white/[0.025]">
                          <div
                            className={cn(
                              'absolute top-1/2 h-2 -translate-y-1/2 rounded',
                              value >= 0
                                ? 'left-1/2 bg-market-up/70'
                                : 'right-1/2 bg-market-down/70'
                            )}
                            style={{
                              width: `${Math.max(1, (Math.abs(value) / pnlStats.maxAbs) * 50)}%`,
                            }}
                          />
                        </div>
                        <span
                          className={cn(
                            'text-right font-mono',
                            pnlClass(value)
                          )}
                        >
                          {formatMoney(value, true)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <EmptyState
                  title="暂无可统计的日终盈亏"
                  detail="dailyAssetSnapshots 没有非空 dailyPnlCny；不会据此生成模拟收益率。"
                />
              )}
            </section>
          </div>
        )}

        {view === 'closed' && (
          <section className="overflow-hidden rounded-lg border border-white/[0.06] bg-[#0b1120]/80">
            <div className="flex flex-wrap justify-end gap-2 border-b border-white/[0.06] p-3">
              <Input
                type="date"
                value={startDate}
                max={endDate}
                onChange={event => {
                  setStartDate(event.target.value);
                  setClosedPage(1);
                }}
                className="h-9 w-36 border-white/[0.08] bg-[#080d18]/80 text-ui-label"
              />
              <Input
                type="date"
                value={endDate}
                min={startDate}
                max={today}
                onChange={event => {
                  setEndDate(event.target.value);
                  setClosedPage(1);
                }}
                className="h-9 w-36 border-white/[0.08] bg-[#080d18]/80 text-ui-label"
              />
            </div>
            {closed.loading ? (
              <div className="p-ui-empty text-center text-ui-label text-slate-500">
                正在读取清仓周期...
              </div>
            ) : closed.error ? (
              <div className="p-ui-section">
                <EmptyState
                  title="清仓周期加载失败"
                  detail={closed.error.message}
                />
              </div>
            ) : closed.page?.items.length ? (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[980px] text-left text-ui-label">
                  <thead className="bg-white/[0.025] text-slate-500">
                    <tr>
                      <th className="px-3 py-3">证券</th>
                      <th>持有周期</th>
                      <th className="text-right">买入 / 卖出数量</th>
                      <th className="text-right">买入 / 卖出均价</th>
                      <th className="text-right">真实成交金额</th>
                      <th className="text-right">毛实现盈亏</th>
                      <th className="px-3">质量</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5">
                    {closed.page.items.map(item => (
                      <tr key={item.id}>
                        <td className="px-3 py-3">
                          <p>{item.instrumentName || item.stockCode}</p>
                          <p className="mt-1 font-mono text-slate-500">
                            {item.stockCode}
                          </p>
                        </td>
                        <td className="font-mono text-slate-400">
                          {formatDateTime(item.openedAt)} →{' '}
                          {formatDateTime(item.closedAt)}
                        </td>
                        <td className="text-right font-mono">
                          {item.buyVolume} / {item.sellVolume}
                        </td>
                        <td className="text-right font-mono">
                          {item.averageBuyPrice?.toFixed(3) ?? '--'} /{' '}
                          {item.averageSellPrice?.toFixed(3) ?? '--'}
                        </td>
                        <td className="text-right font-mono">
                          {formatMoney(item.grossBuyAmount)} /{' '}
                          {formatMoney(item.grossSellAmount)}
                        </td>
                        <td
                          className={cn(
                            'text-right font-mono',
                            pnlClass(item.grossRealizedPnl)
                          )}
                        >
                          {item.pnlQuality === 'COMPLETE_GROSS' ? (
                            <>
                              {formatMoney(item.grossRealizedPnl, true)}
                              <p className="mt-1 text-ui-caption">
                                {formatPercent(
                                  item.grossRealizedPnlPercent,
                                  true
                                )}
                              </p>
                            </>
                          ) : (
                            '数据不足'
                          )}
                        </td>
                        <td className="px-3">
                          <span
                            className={cn(
                              'rounded-full px-2 py-1 text-ui-caption',
                              item.pnlQuality === 'COMPLETE_GROSS'
                                ? 'bg-emerald-500/10 text-emerald-300'
                                : 'bg-amber-500/10 text-amber-300'
                            )}
                          >
                            {item.pnlQuality === 'COMPLETE_GROSS'
                              ? '完整毛盈亏'
                              : '历史不完整'}
                          </span>
                          {item.qualityFlags.length > 0 && (
                            <p className="mt-2 max-w-48 text-ui-caption text-slate-600">
                              {item.qualityFlags.join('、')}
                            </p>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="p-ui-section">
                <EmptyState
                  title="暂无已清仓记录"
                  detail="记录只在真实持仓由正数变为零时产生；查询不会隐式回填或修改历史。"
                />
              </div>
            )}
            <Pagination
              page={closedPage}
              total={closed.page?.totalCount ?? 0}
              onPageChange={setClosedPage}
            />
          </section>
        )}
      </StudioPageStack>

      <ConfirmDialog
        open={Boolean(cancelTarget)}
        onOpenChange={open => !open && setCancelTarget(null)}
        title="确认撤销委托"
        description={`将向 miniQMT 提交 ${cancelTarget?.label || ''} 的撤单请求。券商回报后列表会自动刷新。`}
        confirmText="确认撤单"
        loading={cancelling}
        onConfirm={handleCancel}
      />
    </StudioPageFrame>
  );
}
