import { Check, LoaderCircle, Plus, Star, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { gql as urqlGql, useQuery, useSubscription } from 'urql';
import { Link } from 'wouter';

import { useAppDialog } from '@/components/ui/app-dialog-context';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import type {
  PortfolioSummaryData,
  Position,
} from '@/features/portfolio/types';
import { useWatchlistWorkspace } from '@/features/watchlist/hooks';
import type { Stock } from '@/shared/types';
import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';

interface TradingInstrumentHeaderProps {
  accountId?: string;
  accountCash?: number | null;
  holdings: Position[];
  onInstrumentNameChange?: (name: string) => void;
  portfolioSummary?: Pick<PortfolioSummaryData, 'cash'>;
  selectedStock: Stock | null;
  stockCode?: string;
}

interface MarketSnapshotTick {
  amount?: number | null;
  high?: number | null;
  lastPrice?: number | null;
  low?: number | null;
  open?: number | null;
  preClose?: number | null;
  stockCode?: string | null;
  time?: string | null;
  volume?: number | null;
}

interface MarketSnapshotTickData {
  marketTicks?: MarketSnapshotTick | null;
}

interface HeaderInstrumentInfo {
  id: string;
  name?: string | null;
  market?: string | null;
  type?: string | null;
  totalVolume?: number | null;
  floatVolume?: number | null;
  quote?: {
    amount?: number | null;
    change?: number | null;
    changePercent?: number | null;
    high?: number | null;
    lastPrice?: number | null;
    low?: number | null;
    open?: number | null;
    preClose?: number | null;
    time?: string | null;
    turnoverRate?: number | null;
    volume?: number | null;
  } | null;
}

interface HeaderFinancialSummary {
  circulatingCapital?: number | null;
  epsBasic?: number | null;
  totalCapital?: number | null;
}

interface HeaderInfoData {
  financialSummary?: HeaderFinancialSummary | null;
  instrument?: HeaderInstrumentInfo | null;
}

type HeaderStock = Stock & {
  floatVolume?: number | null;
  instrumentName?: string | null;
  profitRate?: number | null;
  totalVolume?: number | null;
  volume?: number | null;
};

const HeaderInfoQuery = urqlGql`
  query TradingInstrumentHeader_Info($stockCode: String!) {
    instrument(stockCode: $stockCode) {
      id
      name
      market
      type
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
    financialSummary(stockCode: $stockCode) {
      totalCapital
      circulatingCapital
      epsBasic
    }
  }
`;

const MarketSnapshotTickSubscription = urqlGql`
  subscription TradingInstrumentHeader_MarketSnapshotTick($stockList: [String!]!) {
    marketTicks(stockList: $stockList) {
      stockCode
      time
      lastPrice
      open
      high
      low
      preClose
      volume
      amount
    }
  }
`;

const normalizeStockCode = (value: unknown) =>
  typeof value === 'string' ? value.trim().toUpperCase() : '';

const toFiniteNumber = (value: unknown): number | null => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const firstNumber = (...values: unknown[]) => {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null) return parsed;
  }
  return null;
};

const firstPositiveNumber = (...values: unknown[]) => {
  for (const value of values) {
    const parsed = toFiniteNumber(value);
    if (parsed !== null && parsed > 0) return parsed;
  }
  return null;
};

const getQuoteExtra = (stock: Stock | null, key: string) => {
  if (!stock?.quote || typeof stock.quote !== 'object') return null;
  return (stock.quote as Record<string, unknown>)[key];
};

const formatPrice = (value: unknown) => {
  const price = firstPositiveNumber(value);
  if (price === null) return '--';
  return price.toFixed(price >= 10 ? 2 : 3);
};

const formatSignedPrice = (value: unknown) => {
  const parsed = firstNumber(value);
  if (parsed === null) return '--';
  const prefix = parsed > 0 ? '+' : '';
  return `${prefix}${parsed.toFixed(Math.abs(parsed) >= 10 ? 2 : 3)}`;
};

const formatSignedPercent = (value: unknown) => {
  const parsed = firstNumber(value);
  if (parsed === null) return '--';
  const prefix = parsed > 0 ? '+' : '';
  return `${prefix}${parsed.toFixed(2)}%`;
};

const formatPlainPercent = (value: unknown) => {
  const parsed = firstNumber(value);
  if (parsed === null) return '--';
  return `${parsed.toFixed(2)}%`;
};

const formatLargeMetric = (value: unknown) => {
  const parsed = firstPositiveNumber(value);
  if (parsed === null) return '--';
  if (parsed >= 1e8) return `${(parsed / 1e8).toFixed(2)}亿`;
  if (parsed >= 1e4) return `${(parsed / 1e4).toFixed(1)}万`;
  return Math.round(parsed).toLocaleString();
};

const formatRatio = (value: unknown) => {
  const parsed = firstPositiveNumber(value);
  if (parsed === null) return '--';
  return parsed.toFixed(parsed >= 100 ? 0 : 2);
};

const formatTime = (value: unknown) => {
  if (typeof value !== 'string' || !value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(11, 19) || '--';
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: 'Asia/Shanghai',
  });
};

function HeaderMetric({
  label,
  title,
  tone,
  value,
}: {
  label: string;
  title?: string;
  tone?: string;
  value: string;
}) {
  return (
    <div className="flex min-w-[68px] items-baseline gap-2" title={title}>
      <span className="shrink-0 text-ui-label font-black text-slate-500">
        {label}
      </span>
      <span
        className={cn(
          'min-w-0 truncate font-mono text-ui-heading font-black text-slate-200 tabular-nums',
          tone
        )}
      >
        {value}
      </span>
    </div>
  );
}

export function TradingInstrumentHeader({
  accountId,
  accountCash,
  holdings,
  onInstrumentNameChange,
  portfolioSummary,
  selectedStock,
  stockCode,
}: TradingInstrumentHeaderProps) {
  const normalizedStockCode = normalizeStockCode(
    stockCode || selectedStock?.stockCode || selectedStock?.id
  );
  const [headerInfoResult] = useQuery<HeaderInfoData>({
    query: HeaderInfoQuery,
    variables: {
      stockCode: normalizedStockCode,
    },
    pause: !normalizedStockCode,
  });
  const holding = holdings.find(
    item => normalizeStockCode(item?.stockCode) === normalizedStockCode
  );
  const instrument = headerInfoResult.data?.instrument || null;
  const financialSummary = headerInfoResult.data?.financialSummary || null;
  const stock = (selectedStock ||
    holding ||
    instrument ||
    null) as HeaderStock | null;
  const [snapshotTickResult] = useSubscription<MarketSnapshotTickData>({
    query: MarketSnapshotTickSubscription,
    variables: {
      stockList: normalizedStockCode ? [normalizedStockCode] : [],
    },
    pause: !normalizedStockCode,
  });
  const tick =
    snapshotTickResult.data?.marketTicks?.stockCode === normalizedStockCode
      ? snapshotTickResult.data.marketTicks
      : null;

  const lastPrice = firstPositiveNumber(
    tick?.lastPrice,
    instrument?.quote?.lastPrice,
    stock?.quote?.lastPrice,
    stock?.currentPrice,
    holding?.lastPrice
  );
  const preClose = firstPositiveNumber(
    tick?.preClose,
    instrument?.quote?.preClose,
    stock?.quote?.preClose
  );
  const tickChange =
    lastPrice !== null && preClose !== null ? lastPrice - preClose : null;
  const change =
    tickChange ?? firstNumber(instrument?.quote?.change, stock?.quote?.change);
  const changePercent =
    tickChange !== null && preClose !== null && preClose > 0
      ? (tickChange / preClose) * 100
      : firstNumber(
          instrument?.quote?.changePercent,
          stock?.quote?.changePercent,
          stock?.profitRate
        );
  const tone =
    changePercent === null
      ? 'text-slate-300'
      : financialToneClass(changePercent);
  const cash = firstNumber(accountCash, portfolioSummary?.cash);
  const availableToBuy =
    cash !== null && lastPrice !== null && lastPrice > 0
      ? Math.floor(cash / lastPrice)
      : null;
  const availableToSell = firstNumber(holding?.volume);
  const stockName =
    instrument?.name ||
    stock?.name ||
    stock?.instrumentName ||
    holding?.instrumentName ||
    normalizedStockCode ||
    '未选择标的';

  const watchlist = useWatchlistWorkspace(accountId);
  const { confirm: confirmDialog } = useAppDialog();
  const watchlistItem = useMemo(
    () =>
      watchlist.items.find(
        item => normalizeStockCode(item.stockCode) === normalizedStockCode
      ) || null,
    [normalizedStockCode, watchlist.items]
  );
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectedGroupIds, setSelectedGroupIds] = useState<string[]>([]);
  const [newGroupName, setNewGroupName] = useState('');
  const [pickerMessage, setPickerMessage] = useState<string | null>(null);
  const [pickerSaving, setPickerSaving] = useState(false);

  useEffect(() => {
    if (!pickerOpen) return;
    setSelectedGroupIds(watchlistItem?.groups.map(group => group.id) || []);
    setNewGroupName('');
    setPickerMessage(null);
  }, [pickerOpen, watchlistItem]);

  const toggleGroup = (groupId: string) => {
    setSelectedGroupIds(current =>
      current.includes(groupId)
        ? current.filter(id => id !== groupId)
        : [...current, groupId]
    );
  };

  const saveWatchlistSelection = async () => {
    if (!normalizedStockCode) return;
    setPickerSaving(true);
    setPickerMessage(null);
    try {
      await watchlist.saveItem({
        accountId,
        groupIds: selectedGroupIds,
        instrumentName: stockName,
        stockCode: normalizedStockCode,
      });
      setPickerMessage('已保存自选分组');
    } catch (error) {
      setPickerMessage(
        error instanceof Error ? error.message : '保存失败，请重试'
      );
    } finally {
      setPickerSaving(false);
    }
  };

  const createAndAddGroup = async () => {
    const name = newGroupName.trim();
    if (!name || !normalizedStockCode) return;
    setPickerSaving(true);
    setPickerMessage(null);
    try {
      await watchlist.createGroup({
        accountId,
        initialStockCodes: [normalizedStockCode],
        name,
      });
      setNewGroupName('');
      setPickerMessage(`已创建“${name}”并加入`);
    } catch (error) {
      setPickerMessage(
        error instanceof Error ? error.message : '创建分组失败，请重试'
      );
    } finally {
      setPickerSaving(false);
    }
  };

  const removeFromWatchlist = async () => {
    if (!normalizedStockCode) return;
    const groupCount = watchlistItem?.groups.length || 0;
    const confirmed = await confirmDialog({
      confirmText: '移出总自选',
      description: `移出总自选后将同时清除${groupCount ? ` ${groupCount} 个分组` : ''}归属。`,
      title: '确认移出总自选',
      variant: 'destructive',
    });
    if (!confirmed) {
      return;
    }
    setPickerSaving(true);
    setPickerMessage(null);
    try {
      await watchlist.removeItem(normalizedStockCode);
      setPickerMessage('已移出总自选');
      setPickerOpen(false);
    } catch (error) {
      setPickerMessage(
        error instanceof Error ? error.message : '移出失败，请重试'
      );
    } finally {
      setPickerSaving(false);
    }
  };

  useEffect(() => {
    onInstrumentNameChange?.(stockName);
  }, [onInstrumentNameChange, stockName]);

  const marketType =
    instrument?.type ||
    stock?.type ||
    (normalizedStockCode.endsWith('.SH') || normalizedStockCode.endsWith('.SZ')
      ? 'A股'
      : '--');
  const limitDown = preClose !== null ? formatPrice(preClose * 0.9) : '--';
  const limitUp = preClose !== null ? formatPrice(preClose * 1.1) : '--';
  const totalShares = firstPositiveNumber(
    financialSummary?.totalCapital,
    instrument?.totalVolume,
    stock?.totalVolume
  );
  const floatShares = firstPositiveNumber(
    financialSummary?.circulatingCapital,
    instrument?.floatVolume,
    stock?.floatVolume
  );
  const marketCap =
    lastPrice !== null && totalShares !== null ? lastPrice * totalShares : null;
  const floatMarketCap =
    lastPrice !== null && floatShares !== null ? lastPrice * floatShares : null;
  const epsBasic = firstPositiveNumber(financialSummary?.epsBasic);
  const peRatio =
    lastPrice !== null && epsBasic !== null ? lastPrice / epsBasic : null;
  const sessionVolume = firstPositiveNumber(
    tick?.volume,
    instrument?.quote?.volume,
    stock?.quote?.volume,
    stock?.volume
  );
  const sessionAmount = firstPositiveNumber(
    tick?.amount,
    instrument?.quote?.amount,
    stock?.quote?.amount
  );
  const quotedTurnoverRate = firstNumber(
    instrument?.quote?.turnoverRate,
    getQuoteExtra(stock as Stock | null, 'turnoverRate')
  );
  const turnoverRate =
    quotedTurnoverRate ??
    (sessionVolume !== null && floatShares !== null
      ? (sessionVolume / floatShares) * 100
      : null);

  return (
    <div className="studio-workspace-surface shrink-0 border-b border-white/5 px-3 py-2">
      <div className="flex min-h-[86px] min-w-0 items-center gap-ui-section">
        <div className="min-w-[190px] shrink-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-ui-body font-black text-slate-100">
              {stockName}
            </span>
            {normalizedStockCode && (
              <Popover open={pickerOpen} onOpenChange={setPickerOpen}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    aria-label={
                      watchlistItem
                        ? `已自选 · ${watchlistItem.groups.length}组`
                        : '未自选'
                    }
                    className={cn(
                      'inline-flex h-7 shrink-0 items-center gap-1 rounded border px-2 text-ui-caption font-bold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/70',
                      watchlistItem
                        ? 'border-amber-400/35 bg-amber-400/10 text-amber-200 hover:bg-amber-400/15'
                        : 'border-white/10 text-slate-500 hover:border-amber-400/35 hover:text-amber-200'
                    )}
                    title={
                      watchlistItem
                        ? `已自选 · ${watchlistItem.groups.length}组`
                        : '未自选'
                    }
                  >
                    <Star
                      className="h-3.5 w-3.5"
                      fill={watchlistItem ? 'currentColor' : 'none'}
                    />
                    <span className="hidden xl:inline">
                      {watchlistItem
                        ? `已自选 · ${watchlistItem.groups.length}组`
                        : '未自选'}
                    </span>
                  </button>
                </PopoverTrigger>
                <PopoverContent
                  align="start"
                  className="w-[300px] border-[#263b53] bg-[#0b1627] p-3 text-slate-200 shadow-none"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-ui-label font-black text-slate-100">
                        自选分组
                      </div>
                      <div className="mt-1 text-ui-caption text-slate-500">
                        {watchlistItem
                          ? `已自选 · ${watchlistItem.groups.length}组`
                          : '未自选 · 保存后加入总自选'}
                      </div>
                    </div>
                    <Link
                      href={`/watchlist?collection=all&symbol=${encodeURIComponent(normalizedStockCode)}`}
                      className="text-ui-caption font-bold text-blue-300 hover:text-blue-100"
                      onClick={() => setPickerOpen(false)}
                    >
                      查看自选
                    </Link>
                  </div>

                  <div className="mt-3 max-h-44 space-y-1 overflow-y-auto pr-1 custom-scrollbar">
                    {watchlist.groups.length === 0 ? (
                      <div className="border border-dashed border-white/10 px-3 py-ui-section text-center text-ui-caption text-slate-500">
                        暂无自定义分组
                      </div>
                    ) : (
                      watchlist.groups.map(group => {
                        const checked = selectedGroupIds.includes(group.id);
                        return (
                          <label
                            key={group.id}
                            className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-ui-caption text-slate-300 hover:bg-white/5"
                          >
                            <Checkbox
                              checked={checked}
                              onCheckedChange={() => toggleGroup(group.id)}
                              aria-label={`加入分组 ${group.name}`}
                            />
                            <span className="min-w-0 flex-1 truncate">
                              {group.name}
                            </span>
                            <span className="font-mono text-ui-caption text-slate-600">
                              {group.itemCount}
                            </span>
                          </label>
                        );
                      })
                    )}
                  </div>

                  <div className="mt-3 flex items-center gap-1.5">
                    <Input
                      value={newGroupName}
                      onChange={event => setNewGroupName(event.target.value)}
                      onKeyDown={event => {
                        if (event.key === 'Enter') void createAndAddGroup();
                      }}
                      placeholder="新建分组并加入"
                      maxLength={80}
                      className="h-7 border-white/10 bg-white/[0.03] text-ui-caption"
                      aria-label="新建分组名称"
                    />
                    <button
                      type="button"
                      onClick={() => void createAndAddGroup()}
                      disabled={!newGroupName.trim() || pickerSaving}
                      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-blue-500/30 text-blue-300 hover:bg-blue-500/10 disabled:cursor-not-allowed disabled:opacity-40"
                      aria-label="创建分组并加入"
                    >
                      <Plus className="h-3.5 w-3.5" />
                    </button>
                  </div>

                  {pickerMessage && (
                    <div
                      className="mt-2 text-ui-caption text-amber-200"
                      role="status"
                      aria-live="polite"
                    >
                      {pickerMessage}
                    </div>
                  )}

                  <div className="mt-3 flex items-center justify-between gap-2 border-t border-white/5 pt-3">
                    {watchlistItem ? (
                      <button
                        type="button"
                        onClick={() => void removeFromWatchlist()}
                        disabled={pickerSaving}
                        className="inline-flex h-7 items-center gap-1 rounded border border-rose-400/25 px-2 text-ui-caption font-bold text-rose-300 hover:bg-rose-400/10 disabled:opacity-40"
                      >
                        <Trash2 className="h-3 w-3" />
                        移出总自选
                      </button>
                    ) : (
                      <span className="text-ui-caption text-slate-600">
                        保存空分组也会加入总自选
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={() => void saveWatchlistSelection()}
                      disabled={pickerSaving}
                      className="inline-flex h-7 items-center gap-1 rounded bg-blue-600 px-2.5 text-ui-caption font-black text-white hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {pickerSaving ? (
                        <LoaderCircle className="h-3 w-3 animate-spin" />
                      ) : (
                        <Check className="h-3 w-3" />
                      )}
                      保存
                    </button>
                  </div>
                </PopoverContent>
              </Popover>
            )}
            <span className="rounded border border-red-500/25 bg-red-500/10 px-1.5 py-0.5 text-ui-micro font-black text-red-300">
              {marketType}
            </span>
          </div>
          <div className="mt-1 truncate font-mono text-ui-caption font-bold text-slate-500">
            {normalizedStockCode || '--'} · {formatTime(tick?.time)}
          </div>
        </div>

        <div className="w-[160px] shrink-0">
          <div
            className={cn(
              'font-mono text-ui-display-xl font-black leading-none',
              tone
            )}
          >
            {formatPrice(lastPrice)}
          </div>
          <div className={cn('mt-1 font-mono text-ui-body font-black', tone)}>
            {formatSignedPrice(change)} {formatSignedPercent(changePercent)}
          </div>
        </div>

        <div className="grid min-w-0 flex-1 grid-cols-[repeat(auto-fit,minmax(74px,1fr))] gap-x-4 gap-y-1.5">
          <HeaderMetric
            label="高"
            tone="text-market-up"
            value={formatPrice(
              firstPositiveNumber(
                tick?.high,
                instrument?.quote?.high,
                stock?.quote?.high
              )
            )}
          />
          <HeaderMetric
            label="低"
            tone="text-market-down"
            value={formatPrice(
              firstPositiveNumber(
                tick?.low,
                instrument?.quote?.low,
                stock?.quote?.low
              )
            )}
          />
          <HeaderMetric
            label="开"
            value={formatPrice(
              firstPositiveNumber(
                tick?.open,
                instrument?.quote?.open,
                stock?.quote?.open
              )
            )}
          />
          <HeaderMetric label="量" value={formatLargeMetric(sessionVolume)} />
          <HeaderMetric label="额" value={formatLargeMetric(sessionAmount)} />
          <HeaderMetric label="昨" value={formatPrice(preClose)} />
          <HeaderMetric label="市值" value={formatLargeMetric(marketCap)} />
          <HeaderMetric
            label="流通"
            value={formatLargeMetric(floatMarketCap)}
          />
          <HeaderMetric
            label="市盈"
            title="基于最新财报 EPS 估算"
            value={formatRatio(peRatio)}
          />
          <HeaderMetric label="换" value={formatPlainPercent(turnoverRate)} />
        </div>

        <div className="hidden min-w-[180px] shrink-0 grid-cols-2 gap-x-3 gap-y-1.5 border-l border-white/5 pl-4 2xl:grid">
          <HeaderMetric
            label="跌停"
            tone="text-market-down"
            value={limitDown}
          />
          <HeaderMetric label="涨停" tone="text-market-up" value={limitUp} />
          <HeaderMetric
            label="可买"
            value={
              availableToBuy === null ? '--' : availableToBuy.toLocaleString()
            }
          />
          <HeaderMetric
            label="可卖"
            value={
              availableToSell === null ? '--' : availableToSell.toLocaleString()
            }
          />
        </div>
      </div>
    </div>
  );
}
