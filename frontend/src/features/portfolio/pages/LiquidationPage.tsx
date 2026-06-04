import {
  Activity,
  AlertTriangle,
  BarChart3,
  Briefcase,
  ClipboardList,
  Hand,
  History,
  Loader2,
  Power,
  RefreshCw,
  Save,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Trash2,
  Wallet,
} from 'lucide-react';
import * as React from 'react';
import { useMutation, useQuery } from 'urql';
import { useSearch } from 'wouter';

import {
  StudioWorkbench,
  type StudioMode,
} from '@/components/studio-workbench';
import { useStudioNavigate } from '@/components/studio-workspace';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { TradingHoldingsSidebar } from '@/features/trading/components/TradingHoldingsSidebar';
import type { ConditionalLiquidationOrdersQuery as ConditionalLiquidationOrdersQueryData } from '@/generated/gql/graphql';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/utils/cn';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import { CurrentHoldingsSection } from '../components/CurrentHoldingsSection';
import { LiquidatedStocksSection } from '../components/LiquidatedStocksSection';
import { LiquidationStats } from '../components/LiquidationStats';
import {
  useLiquidationActions,
  type LiquidationActionResult,
} from '../hooks/useLiquidationActions';
import { useLiquidationData } from '../hooks/useLiquidationData';
import {
  CancelConditionalLiquidationOrderMutation,
  ConditionalLiquidationOrdersQuery,
  EvaluateConditionalLiquidationOrdersMutation,
  SetConditionalLiquidationOrderEnabledMutation,
  UpsertConditionalLiquidationOrderMutation,
} from '../hooks/usePortfolio';
import type {
  LiquidationTodayOrder,
  LiquidationTodayTrade,
  Position,
} from '../types';

type LiquidationStudioMode = 'ACCOUNT' | 'HISTORY' | 'HOLDINGS' | 'LIQUIDATION';
type ConditionalLiquidationOrderView = NonNullable<
  ConditionalLiquidationOrdersQueryData['conditionalLiquidationOrders']
>[number];

const liquidationModes: StudioMode[] = [
  { id: 'LIQUIDATION', icon: Hand, label: '清仓' },
  { id: 'HISTORY', icon: History, label: '回报' },
  { id: 'HOLDINGS', icon: Briefcase, label: '持仓' },
  { id: 'ACCOUNT', icon: Wallet, label: '账户' },
];

const compactTabTriggerClass =
  'h-8 rounded-md px-3 text-[11px] font-black text-slate-500 transition-colors data-[state=active]:bg-red-500/15 data-[state=active]:text-red-100';

function normalizeStockCode(value: unknown) {
  return typeof value === 'string' ? value.trim().toUpperCase() : '';
}

function getUrlSymbol(search: string) {
  return normalizeStockCode(new URLSearchParams(search).get('symbol'));
}

function getManualWorkspaceTabId(search: string) {
  return normalizeStockCode(new URLSearchParams(search).get('workspaceTab'));
}

function buildLiquidationSymbolPath(
  stockCode: unknown,
  workspaceTab?: string,
  stockName?: string | null
) {
  const params = new URLSearchParams();
  const normalizedStockCode = normalizeStockCode(stockCode);
  if (normalizedStockCode) params.set('symbol', normalizedStockCode);
  if (workspaceTab) params.set('workspaceTab', workspaceTab);
  if (workspaceTab && stockName) params.set('name', stockName);
  return `/liquidation?${params.toString()}`;
}

function getStockCodePrefix(value: unknown) {
  return normalizeStockCode(value).split('.')[0] || '';
}

function stockCodeMatches(left: unknown, right: unknown) {
  const leftCode = normalizeStockCode(left);
  const rightCode = normalizeStockCode(right);
  if (!leftCode || !rightCode) return false;
  return (
    leftCode === rightCode ||
    getStockCodePrefix(leftCode) === getStockCodePrefix(rightCode)
  );
}

function toFiniteNumber(value: unknown) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function formatShares(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return Math.trunc(amount).toLocaleString('zh-CN');
}

function formatPrice(value: unknown) {
  const amount = toFiniteNumber(value);
  return amount === null || amount <= 0 ? '--' : formatCurrency(amount);
}

function formatCurrencyOrDash(value: unknown) {
  const amount = toFiniteNumber(value);
  return amount === null ? '--' : formatCurrency(amount);
}

function formatSignedCurrency(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return `${amount >= 0 ? '+' : ''}${formatCurrency(amount)}`;
}

function formatPercentOrDash(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null) return '--';
  return formatPercent(amount);
}

function getToneClass(value: unknown) {
  const amount = toFiniteNumber(value);
  if (amount === null || amount === 0) return 'text-slate-200';
  return amount > 0 ? 'text-rose-300' : 'text-emerald-300';
}

function getSellableVolume(holding?: Position | null) {
  if (!holding) return 0;
  return Math.max(0, Math.trunc(toFiniteNumber(holding.canUseVolume) ?? 0));
}

function getEstimatedSellValue(holding?: Position | null) {
  if (!holding) return 0;
  const sellableVolume = getSellableVolume(holding);
  if (sellableVolume <= 0) return 0;

  const volume = toFiniteNumber(holding.volume);
  const marketValue = toFiniteNumber(holding.marketValue);
  if (volume !== null && volume > 0 && marketValue !== null) {
    return (marketValue * sellableVolume) / volume;
  }

  const price =
    toFiniteNumber(holding.lastPrice) ?? toFiniteNumber(holding.avgPrice) ?? 0;
  return sellableVolume * price;
}

function getBatchEstimatedSellValue(holdings: Position[]) {
  return holdings.reduce((sum, holding) => sum + getEstimatedSellValue(holding), 0);
}

function getBatchSellableVolume(holdings: Position[]) {
  return holdings.reduce((sum, holding) => sum + getSellableVolume(holding), 0);
}

function formatDateTime(value: unknown) {
  if (typeof value === 'number') {
    return new Date(value * 1000).toLocaleString('zh-CN');
  }

  if (typeof value !== 'string' || !value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN');
}

function getOrderStatusLabel(value: unknown) {
  const status = String(value ?? '').toUpperCase();
  const labels: Record<string, string> = {
    CANCELED: '已撤',
    JUNK: '废单',
    PARTSUCC_CANCEL: '部成待撤',
    PART_CANCEL: '部撤',
    PART_SUCC: '部成',
    REPORTED: '已报',
    REPORTED_CANCEL: '已报待撤',
    SUCCEEDED: '已成',
    UNREPORTED: '未报',
    UNKNOWN: '未知',
    WAIT_REPORTING: '待报',
  };
  return labels[status] || status || '--';
}

function getOrderTypeLabel(value: unknown) {
  const type = String(value ?? '').toUpperCase();
  if (type.includes('SELL')) return '卖出';
  if (type.includes('BUY')) return '买入';
  return type || '--';
}

function getTradeDirectionLabel(value: unknown) {
  const text = String(value ?? '').toUpperCase();
  if (text.includes('SELL') || text === '24') return '卖出';
  if (text.includes('BUY') || text === '23') return '买入';
  return text || '--';
}

function MetricCard({
  label,
  subValue,
  toneValue,
  value,
}: {
  label: string;
  subValue?: string;
  toneValue?: unknown;
  value: string;
}) {
  return (
    <div className="min-w-0 rounded-md border border-white/5 bg-white/[0.03] px-3 py-2.5">
      <div className="truncate text-[10px] font-bold text-slate-500">
        {label}
      </div>
      <div
        className={cn(
          'mt-1 truncate font-mono text-sm font-black tabular-nums',
          toneValue === undefined ? 'text-slate-100' : getToneClass(toneValue)
        )}
      >
        {value}
      </div>
      {subValue && (
        <div className="mt-1 truncate text-[10px] font-bold text-slate-600">
          {subValue}
        </div>
      )}
    </div>
  );
}

function DataPanel({
  children,
  icon: Icon,
  title,
}: {
  children: React.ReactNode;
  icon: React.ElementType;
  title: string;
}) {
  return (
    <section className="min-w-0 rounded-md border border-white/5 bg-[#0b1120]/70">
      <div className="flex h-10 items-center gap-2 border-b border-white/5 px-3">
        <Icon className="h-3.5 w-3.5 text-red-300" />
        <h3 className="truncate text-xs font-black text-slate-200">{title}</h3>
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

function EmptyDataState({ label }: { label: string }) {
  return (
    <div className="rounded-md border border-dashed border-white/10 bg-white/[0.025] px-4 py-6 text-center text-xs font-bold text-slate-500">
      {label}
    </div>
  );
}

function RelatedOrdersTable({
  orders,
  trades,
}: {
  orders: LiquidationTodayOrder[];
  trades: LiquidationTodayTrade[];
}) {
  return (
    <div className="grid gap-3 xl:grid-cols-2">
      <DataPanel icon={ClipboardList} title="当日相关委托">
        {orders.length === 0 ? (
          <EmptyDataState label="暂无该标的当日委托" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-xs">
              <thead className="text-[10px] font-black uppercase tracking-wider text-slate-600">
                <tr className="border-b border-white/5">
                  <th className="px-2 py-2">方向</th>
                  <th className="px-2 py-2 text-right">委托/成交</th>
                  <th className="px-2 py-2 text-right">价格</th>
                  <th className="px-2 py-2 text-right">状态</th>
                  <th className="px-2 py-2 text-right">时间</th>
                </tr>
              </thead>
              <tbody>
                {orders.map(order => (
                  <tr key={order.id} className="border-b border-white/5">
                    <td className="px-2 py-2 font-bold text-slate-200">
                      {getOrderTypeLabel(order.type)}
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-slate-300">
                      {formatShares(order.volume)} /{' '}
                      {formatShares(order.tradedVolume)}
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-slate-300">
                      {formatPrice(order.tradedPrice || order.price)}
                    </td>
                    <td className="px-2 py-2 text-right font-bold text-amber-200">
                      {getOrderStatusLabel(order.status)}
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-slate-500">
                      {formatDateTime(order.time)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DataPanel>

      <DataPanel icon={Activity} title="当日相关成交">
        {trades.length === 0 ? (
          <EmptyDataState label="暂无该标的当日成交" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-left text-xs">
              <thead className="text-[10px] font-black uppercase tracking-wider text-slate-600">
                <tr className="border-b border-white/5">
                  <th className="px-2 py-2">方向</th>
                  <th className="px-2 py-2 text-right">成交数量</th>
                  <th className="px-2 py-2 text-right">成交价</th>
                  <th className="px-2 py-2 text-right">金额</th>
                  <th className="px-2 py-2 text-right">时间</th>
                </tr>
              </thead>
              <tbody>
                {trades.map(trade => (
                  <tr key={trade.tradedId} className="border-b border-white/5">
                    <td className="px-2 py-2 font-bold text-slate-200">
                      {getTradeDirectionLabel(trade.orderType)}
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-slate-300">
                      {formatShares(trade.tradedVolume)}
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-slate-300">
                      {formatPrice(trade.tradedPrice)}
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-slate-300">
                      {formatCurrencyOrDash(trade.tradedAmount)}
                    </td>
                    <td className="px-2 py-2 text-right font-mono text-slate-500">
                      {formatDateTime(trade.tradedTime)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </DataPanel>
    </div>
  );
}

type ConditionalLiquidationFormPayload = {
  accountId?: string;
  enabled: boolean;
  id?: string;
  instrumentName?: string | null;
  remark?: string | null;
  sellMode: string;
  sellRatioPct?: number | null;
  sellVolume?: number | null;
  stockCode: string;
  targetPrice?: number | null;
  targetProfitPct?: number | null;
};

function numericInput(value: unknown) {
  const amount = toFiniteNumber(value);
  return amount === null ? '' : String(amount);
}

function parseOptionalNumber(value: string) {
  const text = value.trim();
  if (!text) return null;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? parsed : null;
}

function getConditionalOrderStatus(order?: ConditionalLiquidationOrderView | null) {
  if (!order) return { label: '未配置', tone: 'text-slate-400' };
  if (order.status === 'SUBMITTED') return { label: '已提交', tone: 'text-emerald-300' };
  if (order.status === 'FAILED') return { label: '提交失败', tone: 'text-rose-300' };
  if (order.status === 'CANCELLED') return { label: '已取消', tone: 'text-slate-500' };
  return order.enabled
    ? { label: '监控中', tone: 'text-red-200' }
    : { label: '已停用', tone: 'text-amber-200' };
}

function ConditionalLiquidationPanel({
  accountId,
  actionLoading,
  holding,
  isLoading,
  onCancel,
  onEvaluate,
  onSave,
  onToggleEnabled,
  order,
  selectedStockCode,
}: {
  accountId?: string;
  actionLoading: boolean;
  holding?: Position | null;
  isLoading: boolean;
  onCancel: (orderId: string) => Promise<void>;
  onEvaluate: () => Promise<void>;
  onSave: (payload: ConditionalLiquidationFormPayload) => Promise<void>;
  onToggleEnabled: (orderId: string, enabled: boolean) => Promise<void>;
  order?: ConditionalLiquidationOrderView | null;
  selectedStockCode: string;
}) {
  const { toast } = useToast();
  const [enabled, setEnabled] = React.useState(true);
  const [targetProfitPct, setTargetProfitPct] = React.useState('');
  const [targetPrice, setTargetPrice] = React.useState('');
  const [sellMode, setSellMode] = React.useState('ALL_AVAILABLE');
  const [sellRatioPct, setSellRatioPct] = React.useState('50');
  const [sellVolume, setSellVolume] = React.useState('');

  React.useEffect(() => {
    setEnabled(order?.enabled ?? true);
    setTargetProfitPct(numericInput(order?.targetProfitPct));
    setTargetPrice(numericInput(order?.targetPrice));
    setSellMode(order?.sellMode || 'ALL_AVAILABLE');
    setSellRatioPct(numericInput(order?.sellRatioPct) || '50');
    setSellVolume(numericInput(order?.sellVolume));
  }, [order?.id, order?.updatedAt]);

  const status = getConditionalOrderStatus(order);
  const isTerminal =
    order?.status === 'SUBMITTED' || order?.status === 'CANCELLED';
  const canOperate = Boolean(selectedStockCode) && !actionLoading;
  const canUpdateExisting = Boolean(order?.id && !isTerminal);
  const existingId = canUpdateExisting ? order?.id : undefined;
  const sellableVolume = getSellableVolume(holding);

  const handleSave = async () => {
    const profitPct = parseOptionalNumber(targetProfitPct);
    const price = parseOptionalNumber(targetPrice);
    const ratioPct = parseOptionalNumber(sellRatioPct);
    const fixedVolume = parseOptionalNumber(sellVolume);

    if (profitPct === null && price === null) {
      toast({
        title: '缺少触发条件',
        description: '请至少填写目标收益率或目标价。',
        variant: 'destructive',
      });
      return;
    }
    if (sellMode === 'PERCENT_AVAILABLE' && (!ratioPct || ratioPct <= 0)) {
      toast({
        title: '卖出比例无效',
        description: '按比例卖出时，请填写 0 到 100 之间的比例。',
        variant: 'destructive',
      });
      return;
    }
    if (sellMode === 'FIXED_VOLUME' && (!fixedVolume || fixedVolume <= 0)) {
      toast({
        title: '固定股数无效',
        description: '按股数卖出时，请填写大于 0 的股数。',
        variant: 'destructive',
      });
      return;
    }

    await onSave({
      accountId,
      enabled,
      id: existingId,
      instrumentName: holding?.instrumentName || null,
      sellMode,
      sellRatioPct: sellMode === 'PERCENT_AVAILABLE' ? ratioPct : null,
      sellVolume:
        sellMode === 'FIXED_VOLUME' && fixedVolume !== null
          ? Math.trunc(fixedVolume)
          : null,
      stockCode: selectedStockCode,
      targetPrice: price,
      targetProfitPct: profitPct,
    });
  };

  return (
    <DataPanel icon={ShieldCheck} title="条件清仓">
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  'rounded border border-white/10 bg-white/[0.04] px-2 py-1 text-[10px] font-black',
                  status.tone
                )}
              >
                {status.label}
              </span>
              {order?.submittedOrderId && (
                <span className="rounded border border-emerald-400/20 bg-emerald-500/10 px-2 py-1 font-mono text-[10px] font-bold text-emerald-200">
                  委托 {order.submittedOrderId}
                </span>
              )}
              {isTerminal && (
                <span className="text-[10px] font-bold text-slate-500">
                  一次性条件单
                </span>
              )}
            </div>
            <div className="mt-1 text-[10px] font-bold text-slate-600">
              最近检查 {formatDateTime(order?.lastCheckedAt)}
              {order?.lastError ? ` · ${order.lastError}` : ''}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              title="立即检查条件，满足时提交清仓委托"
              disabled={!order?.id || isTerminal || actionLoading}
              onClick={onEvaluate}
              className="h-8 px-2 text-[10px] font-black"
            >
              <Target className="mr-1.5 h-3.5 w-3.5" />
              检查触发
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              title={order?.enabled ? '停用条件单' : '启用条件单'}
              disabled={!order?.id || isTerminal || actionLoading}
              onClick={() =>
                order?.id && onToggleEnabled(order.id, !order.enabled)
              }
              className="h-8 px-2 text-[10px] font-black"
            >
              <Power className="mr-1.5 h-3.5 w-3.5" />
              {order?.enabled ? '停用' : '启用'}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              title="取消条件单"
              disabled={!order?.id || isTerminal || actionLoading}
              onClick={() => order?.id && onCancel(order.id)}
              className="h-8 px-2 text-[10px] font-black text-rose-200 hover:text-rose-100"
            >
              <Trash2 className="mr-1.5 h-3.5 w-3.5" />
              取消
            </Button>
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <div className="grid gap-1.5">
            <Label
              htmlFor="conditional-target-profit"
              className="text-[10px] font-black text-slate-500"
            >
              目标收益率 (%)
            </Label>
            <Input
              id="conditional-target-profit"
              type="number"
              inputMode="decimal"
              value={targetProfitPct}
              onChange={event => setTargetProfitPct(event.target.value)}
              placeholder="15"
              className="h-9 rounded-md border-white/10 bg-[#08101d]/90 text-xs font-bold text-slate-100"
            />
          </div>

          <div className="grid gap-1.5">
            <Label
              htmlFor="conditional-target-price"
              className="text-[10px] font-black text-slate-500"
            >
              目标价
            </Label>
            <Input
              id="conditional-target-price"
              type="number"
              inputMode="decimal"
              value={targetPrice}
              onChange={event => setTargetPrice(event.target.value)}
              placeholder={holding?.lastPrice ? String(holding.lastPrice) : '0.00'}
              className="h-9 rounded-md border-white/10 bg-[#08101d]/90 text-xs font-bold text-slate-100"
            />
          </div>

          <div className="grid gap-1.5">
            <Label
              htmlFor="conditional-sell-mode"
              className="text-[10px] font-black text-slate-500"
            >
              卖出数量
            </Label>
            <Select value={sellMode} onValueChange={setSellMode}>
              <SelectTrigger
                id="conditional-sell-mode"
                className="h-9 rounded-md border-white/10 bg-[#08101d]/90 text-xs font-bold text-slate-100"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ALL_AVAILABLE">全部可卖</SelectItem>
                <SelectItem value="PERCENT_AVAILABLE">按比例</SelectItem>
                <SelectItem value="FIXED_VOLUME">固定股数</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-1.5">
            <Label
              htmlFor="conditional-sell-value"
              className="text-[10px] font-black text-slate-500"
            >
              {sellMode === 'PERCENT_AVAILABLE'
                ? '卖出比例 (%)'
                : sellMode === 'FIXED_VOLUME'
                  ? '卖出股数'
                  : '可卖库存'}
            </Label>
            <Input
              id="conditional-sell-value"
              type="number"
              inputMode="decimal"
              value={
                sellMode === 'PERCENT_AVAILABLE'
                  ? sellRatioPct
                  : sellMode === 'FIXED_VOLUME'
                    ? sellVolume
                    : String(sellableVolume)
              }
              disabled={sellMode === 'ALL_AVAILABLE'}
              onChange={event => {
                if (sellMode === 'PERCENT_AVAILABLE') {
                  setSellRatioPct(event.target.value);
                } else {
                  setSellVolume(event.target.value);
                }
              }}
              className="h-9 rounded-md border-white/10 bg-[#08101d]/90 text-xs font-bold text-slate-100 disabled:opacity-70"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-white/5 bg-[#08101d]/75 px-3 py-2">
          <button
            type="button"
            onClick={() => setEnabled(value => !value)}
            className={cn(
              'inline-flex h-8 items-center gap-2 rounded-md border px-3 text-[10px] font-black transition-colors',
              enabled
                ? 'border-red-400/30 bg-red-500/10 text-red-100'
                : 'border-white/10 bg-white/[0.03] text-slate-500'
            )}
          >
            <SlidersHorizontal className="h-3.5 w-3.5" />
            {enabled ? '保存后启用监控' : '保存但暂不启用'}
          </button>

          <Button
            type="button"
            size="sm"
            disabled={!canOperate || isLoading}
            onClick={handleSave}
            className="h-8 bg-red-600 px-4 text-[10px] font-black text-white hover:bg-red-500"
          >
            {isLoading ? (
              <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="mr-2 h-3.5 w-3.5" />
            )}
            保存条件单
          </Button>
        </div>
      </div>
    </DataPanel>
  );
}

function SingleStockLiquidationPanel({
  actionLoading,
  accountId,
  conditionalActionLoading,
  conditionalLoading,
  conditionalOrder,
  holding,
  onCancelConditionalOrder,
  onEvaluateConditionalOrders,
  onLiquidate,
  onSaveConditionalOrder,
  onToggleConditionalOrder,
  orders,
  selectedStockCode,
  trades,
}: {
  actionLoading: boolean;
  accountId?: string;
  conditionalActionLoading: boolean;
  conditionalLoading: boolean;
  conditionalOrder?: ConditionalLiquidationOrderView | null;
  holding?: Position | null;
  onCancelConditionalOrder: (orderId: string) => Promise<void>;
  onEvaluateConditionalOrders: () => Promise<void>;
  onLiquidate: (stockCodes: string[]) => Promise<void>;
  onSaveConditionalOrder: (
    payload: ConditionalLiquidationFormPayload
  ) => Promise<void>;
  onToggleConditionalOrder: (orderId: string, enabled: boolean) => Promise<void>;
  orders: LiquidationTodayOrder[];
  selectedStockCode: string;
  trades: LiquidationTodayTrade[];
}) {
  const stockName = holding?.instrumentName || selectedStockCode;
  const sellableVolume = getSellableVolume(holding);
  const estimatedSellValue = getEstimatedSellValue(holding);
  const canSubmit = Boolean(holding) && sellableVolume > 0 && !actionLoading;
  const metrics = [
    {
      label: '可卖数量',
      subValue: `持仓 ${formatShares(holding?.volume)} 股`,
      value: `${formatShares(sellableVolume)} 股`,
    },
    {
      label: '估算委托市值',
      subValue: '按市值比例或最新价估算',
      value: formatCurrency(estimatedSellValue),
    },
    {
      label: '成本 / 现价',
      subValue: `成本 ${formatPrice(holding?.avgPrice)}`,
      value: formatPrice(holding?.lastPrice),
    },
    {
      label: '持仓盈亏',
      subValue: `收益率 ${formatPercentOrDash(holding?.profitRate)}`,
      toneValue: holding?.profitLoss,
      value: formatSignedCurrency(holding?.profitLoss),
    },
  ];
  const riskRows = [
    ['证券代码', selectedStockCode],
    ['持仓数量', `${formatShares(holding?.volume)} 股`],
    ['可卖数量', `${formatShares(holding?.canUseVolume)} 股`],
    ['冻结数量', `${formatShares(holding?.frozenVolume)} 股`],
    ['在途数量', `${formatShares(holding?.onRoadVolume)} 股`],
    ['昨日持仓', `${formatShares(holding?.yesterdayVolume)} 股`],
    ['当前市值', formatCurrencyOrDash(holding?.marketValue)],
    ['平均成本', formatPrice(holding?.avgPrice)],
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-white/5 bg-[#07111f]/95 px-4 py-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <h2 className="truncate text-base font-black text-slate-100">
              {stockName}
            </h2>
            <span className="rounded border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-[10px] font-bold text-slate-500">
              {selectedStockCode}
            </span>
            <span
              className={cn(
                'rounded border px-2 py-1 text-[10px] font-black',
                holding
                  ? 'border-red-500/25 bg-red-500/10 text-red-200'
                  : 'border-amber-400/20 bg-amber-500/10 text-amber-200'
              )}
            >
              {holding ? '当前持仓' : '未找到持仓'}
            </span>
          </div>
          <p className="mt-1 text-[10px] font-bold text-slate-600">
            预检可卖库存后提交 SELL 委托，成交状态只来自券商回报。
          </p>
        </div>

        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={!canSubmit}
              data-testid={`liquidation-single-submit-${selectedStockCode}`}
            >
              {actionLoading ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <AlertTriangle className="mr-2 h-4 w-4" />
              )}
              提交清仓委托
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>确认清仓 {stockName}</AlertDialogTitle>
              <AlertDialogDescription asChild>
                <div>
                  将对 {selectedStockCode} 提交 SELL 委托。
                  <div className="mt-3 rounded-md bg-muted p-3 text-sm">
                    <p>可卖数量: {sellableVolume.toLocaleString()} 股</p>
                    <p>冻结数量: {formatShares(holding?.frozenVolume)} 股</p>
                    <p>在途数量: {formatShares(holding?.onRoadVolume)} 股</p>
                    <p>估算委托市值: {formatCurrency(estimatedSellValue)}</p>
                  </div>
                </div>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction
                onClick={() => onLiquidate([selectedStockCode])}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              >
                提交委托
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>

      {!holding && (
        <div className="rounded-md border border-amber-400/20 bg-amber-500/10 px-4 py-3 text-xs font-bold text-amber-100">
          当前账户未返回该标的持仓。请从左侧持仓列表选择可清仓标的。
        </div>
      )}

      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        {metrics.map(metric => (
          <MetricCard
            key={metric.label}
            label={metric.label}
            subValue={metric.subValue}
            toneValue={metric.toneValue}
            value={metric.value}
          />
        ))}
      </div>

      <DataPanel icon={ShieldAlert} title="清仓预检">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {riskRows.map(([label, value]) => (
            <div
              key={label}
              className="min-w-0 rounded-md border border-white/5 bg-[#08101d]/80 px-3 py-2"
            >
              <div className="truncate text-[10px] font-bold text-slate-500">
                {label}
              </div>
              <div className="mt-1 truncate font-mono text-xs font-black text-slate-200">
                {value}
              </div>
            </div>
          ))}
        </div>
      </DataPanel>

      <ConditionalLiquidationPanel
        accountId={accountId}
        actionLoading={actionLoading || conditionalActionLoading}
        holding={holding}
        isLoading={conditionalLoading || conditionalActionLoading}
        onCancel={onCancelConditionalOrder}
        onEvaluate={onEvaluateConditionalOrders}
        onSave={onSaveConditionalOrder}
        onToggleEnabled={onToggleConditionalOrder}
        order={conditionalOrder}
        selectedStockCode={selectedStockCode}
      />

      <RelatedOrdersTable orders={orders} trades={trades} />
    </div>
  );
}

export function LiquidationPage() {
  const search = useSearch();
  const selectedStockCode = React.useMemo(() => getUrlSymbol(search), [search]);
  const manualWorkspaceTabId = React.useMemo(
    () => getManualWorkspaceTabId(search),
    [search]
  );
  const openStudioTab = useStudioNavigate();
  const { toast } = useToast();
  const [selectedHoldings, setSelectedHoldings] = React.useState<string[]>([]);
  const [activeTab, setActiveTab] = React.useState('current');
  const {
    accountId,
    currentHoldings,
    error: dataError,
    isLoading: dataLoading,
    liquidatedStocks,
    liquidationSummary,
    portfolioSummary,
    refetch,
    todayOrders,
    todayTrades,
  } = useLiquidationData();
  const {
    error: actionError,
    isLoading: actionLoading,
    liquidateAll,
    liquidateMultiple,
  } = useLiquidationActions();
  const [conditionalOrdersResult, refetchConditionalOrders] = useQuery({
    query: ConditionalLiquidationOrdersQuery,
    variables: {
      accountId,
      includeCancelled: false,
      stockCode: undefined,
    },
    requestPolicy: 'cache-and-network',
  });
  const [upsertConditionalResult, upsertConditionalOrder] = useMutation(
    UpsertConditionalLiquidationOrderMutation
  );
  const [toggleConditionalResult, setConditionalOrderEnabled] = useMutation(
    SetConditionalLiquidationOrderEnabledMutation
  );
  const [cancelConditionalResult, cancelConditionalOrder] = useMutation(
    CancelConditionalLiquidationOrderMutation
  );
  const [evaluateConditionalResult, evaluateConditionalOrders] = useMutation(
    EvaluateConditionalLiquidationOrdersMutation
  );
  const conditionalOrders =
    conditionalOrdersResult.data?.conditionalLiquidationOrders ?? [];
  const conditionalActionLoading =
    upsertConditionalResult.fetching ||
    toggleConditionalResult.fetching ||
    cancelConditionalResult.fetching ||
    evaluateConditionalResult.fetching;

  const selectedHolding = React.useMemo(
    () =>
      selectedStockCode
        ? currentHoldings.find(holding =>
            stockCodeMatches(holding.stockCode, selectedStockCode)
          ) || null
        : null,
    [currentHoldings, selectedStockCode]
  );
  const liquidatableHoldings = React.useMemo(
    () => currentHoldings.filter(holding => getSellableVolume(holding) > 0),
    [currentHoldings]
  );
  const relatedOrders = React.useMemo(
    () =>
      selectedStockCode
        ? todayOrders.filter(order =>
            stockCodeMatches(order.stockCode, selectedStockCode)
          )
        : [],
    [selectedStockCode, todayOrders]
  );
  const relatedTrades = React.useMemo(
    () =>
      selectedStockCode
        ? todayTrades.filter(trade =>
            stockCodeMatches(trade.stockCode, selectedStockCode)
          )
        : [],
    [selectedStockCode, todayTrades]
  );
  const selectedConditionalOrder = React.useMemo(
    () =>
      selectedStockCode
        ? conditionalOrders.find(
            order =>
              stockCodeMatches(order.stockCode, selectedStockCode) &&
              order.status !== 'CANCELLED'
          ) || null
        : null,
    [conditionalOrders, selectedStockCode]
  );
  const conditionalOrderStockCodes = React.useMemo(() => {
    const codes = new Set<string>();
    conditionalOrders
      .filter(order => order.status !== 'CANCELLED')
      .forEach(order => {
        const code = normalizeStockCode(order.stockCode);
        const prefix = getStockCodePrefix(code);
        if (code) codes.add(code);
        if (prefix) codes.add(prefix);
      });
    return codes;
  }, [conditionalOrders]);
  const portfolioMarketValue =
    portfolioSummary?.totalMarketValue ??
    currentHoldings.reduce((sum, holding) => sum + (holding.marketValue ?? 0), 0);
  const totalLiquidatedPnL = liquidatedStocks.reduce(
    (sum, stock) => sum + (toFiniteNumber(stock.realizedPnL) ?? 0),
    0
  );
  const accountName =
    portfolioSummary?.accountName || accountId || '当前账户';
  const totalAsset = portfolioSummary?.totalAsset;
  const activeMode: LiquidationStudioMode =
    activeTab === 'liquidated' ? 'HISTORY' : 'LIQUIDATION';
  const batchSellableVolume = getBatchSellableVolume(liquidatableHoldings);
  const batchEstimatedSellValue =
    liquidationSummary?.totalMarketValue ??
    getBatchEstimatedSellValue(liquidatableHoldings);

  const showActionResult = React.useCallback(
    (result: LiquidationActionResult) => {
      toast({
        description:
          result.submittedOrderIds.length > 0
            ? `委托编号: ${result.submittedOrderIds.join(', ')}`
            : result.message,
        title: result.success ? '清仓委托已提交' : '清仓委托部分失败',
        variant: result.success ? 'default' : 'destructive',
      });
    },
    [toast]
  );

  const refreshConditionalOrders = React.useCallback(() => {
    refetchConditionalOrders({ requestPolicy: 'network-only' });
  }, [refetchConditionalOrders]);

  const handleRefresh = React.useCallback(() => {
    refetch();
    refreshConditionalOrders();
  }, [refetch, refreshConditionalOrders]);

  const handleLiquidateStockCodes = React.useCallback(
    async (stockCodes: string[]) => {
      const result = await liquidateMultiple(stockCodes);
      showActionResult(result);
      if (result.success) setSelectedHoldings([]);
      refetch();
    },
    [liquidateMultiple, refetch, showActionResult]
  );

  const handleLiquidateSelected = React.useCallback(async () => {
    if (selectedHoldings.length === 0) return;
    await handleLiquidateStockCodes(selectedHoldings);
  }, [handleLiquidateStockCodes, selectedHoldings]);

  const handleLiquidateAll = React.useCallback(async () => {
    const result = await liquidateAll();
    showActionResult(result);
    if (result.success) setSelectedHoldings([]);
    refetch();
  }, [liquidateAll, refetch, showActionResult]);

  const handleSaveConditionalOrder = React.useCallback(
    async (payload: ConditionalLiquidationFormPayload) => {
      const result = await upsertConditionalOrder({ input: payload });
      if (result.error) {
        toast({
          title: '条件清仓保存失败',
          description: result.error.message,
          variant: 'destructive',
        });
        return;
      }

      toast({
        title: payload.enabled ? '条件清仓已启用' : '条件清仓已保存',
        description: payload.stockCode,
      });
      refreshConditionalOrders();
    },
    [refreshConditionalOrders, toast, upsertConditionalOrder]
  );

  const handleToggleConditionalOrder = React.useCallback(
    async (orderId: string, enabled: boolean) => {
      const result = await setConditionalOrderEnabled({ enabled, orderId });
      if (result.error) {
        toast({
          title: enabled ? '启用失败' : '停用失败',
          description: result.error.message,
          variant: 'destructive',
        });
        return;
      }

      toast({
        title: enabled ? '条件清仓已启用' : '条件清仓已停用',
      });
      refreshConditionalOrders();
    },
    [refreshConditionalOrders, setConditionalOrderEnabled, toast]
  );

  const handleCancelConditionalOrder = React.useCallback(
    async (orderId: string) => {
      const result = await cancelConditionalOrder({ orderId });
      if (result.error) {
        toast({
          title: '取消失败',
          description: result.error.message,
          variant: 'destructive',
        });
        return;
      }

      toast({ title: '条件清仓已取消' });
      refreshConditionalOrders();
    },
    [cancelConditionalOrder, refreshConditionalOrders, toast]
  );

  const handleEvaluateConditionalOrders = React.useCallback(async () => {
    const result = await evaluateConditionalOrders({
      accountId,
      stockCode: selectedStockCode || undefined,
    });
    if (result.error) {
      toast({
        title: '条件检查失败',
        description: result.error.message,
        variant: 'destructive',
      });
      return;
    }

    const evaluations = result.data?.evaluateConditionalLiquidationOrders ?? [];
    const submitted = evaluations.filter(item => item.submitted);
    const triggered = evaluations.filter(item => item.triggered);
    const failed = evaluations.find(item => item.error);
    toast({
      title:
        submitted.length > 0
          ? '条件清仓委托已提交'
          : triggered.length > 0
            ? '条件已触发但未提交'
            : '条件尚未触发',
      description:
        submitted[0]?.orderId ||
        failed?.error ||
        evaluations[0]?.message ||
        selectedStockCode,
      variant: failed && submitted.length === 0 ? 'destructive' : 'default',
    });
    refreshConditionalOrders();
    refetch();
  }, [
    accountId,
    evaluateConditionalOrders,
    refetch,
    refreshConditionalOrders,
    selectedStockCode,
    toast,
  ]);

  const handleStudioModeChange = React.useCallback(
    (mode: string) => {
      const nextMode = mode as LiquidationStudioMode;
      if (nextMode === 'HOLDINGS') {
        openStudioTab('/holdings');
        return;
      }
      if (nextMode === 'ACCOUNT') {
        openStudioTab('/holdings');
        return;
      }
      if (nextMode === 'HISTORY') {
        setActiveTab('liquidated');
        return;
      }
      setActiveTab('current');
    },
    [openStudioTab]
  );

  const sidebar = (
    <TradingHoldingsSidebar
      accountName={accountName}
      error={dataError}
      holdings={currentHoldings}
      isLoading={dataLoading}
      onAccountOpen={() => openStudioTab('/holdings')}
      onHoldingSelect={holding =>
        openStudioTab(
          buildLiquidationSymbolPath(
            holding.stockCode,
            manualWorkspaceTabId,
            holding.instrumentName
          )
        )
      }
      onHoldingOpenInNewWindow={holding =>
        openStudioTab(
          buildLiquidationSymbolPath(
            holding.stockCode,
            `${normalizeStockCode(holding.stockCode)}-${Date.now()}`,
            holding.instrumentName
          )
        )
      }
      onRefresh={handleRefresh}
      onStockInfoOpen={holding =>
        openStudioTab(`/stock/${normalizeStockCode(holding.stockCode)}`)
      }
      portfolioSummary={portfolioSummary}
      selectedStockCode={selectedStockCode}
      totalAsset={totalAsset}
    />
  );

  const toolbar = (
    <div className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-white/5 bg-[#0b1120]/70 px-4">
      <div className="min-w-0">
        <div className="truncate text-xs font-black uppercase tracking-[0.2em] text-slate-200">
          清仓管理
        </div>
        <div className="truncate text-[10px] font-medium text-slate-600">
          当前持仓、清仓委托、当日真实回报
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={handleRefresh}
          className="flex h-8 items-center justify-center gap-2 rounded-md border border-white/10 px-3 text-[10px] font-black uppercase tracking-wider text-slate-400 transition-colors hover:border-red-500/40 hover:text-red-300"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          刷新数据
        </button>

        <AlertDialog>
          <AlertDialogTrigger asChild>
            <button
              type="button"
              disabled={liquidatableHoldings.length === 0 || actionLoading}
              className="flex h-8 items-center justify-center gap-2 rounded-md border border-rose-400/25 bg-rose-500/10 px-3 text-[10px] font-black uppercase tracking-wider text-rose-200 transition-colors hover:bg-rose-500/15 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {actionLoading ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <AlertTriangle className="h-3.5 w-3.5" />
              )}
              一键清仓
            </button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>确认一键清仓</AlertDialogTitle>
              <AlertDialogDescription asChild>
                <div>
                  将对当前账户所有可卖持仓提交 SELL 委托。
                  <div className="mt-3 rounded-md bg-muted p-3 text-sm">
                    <p>可清仓标的: {liquidatableHoldings.length} 只</p>
                    <p>可卖数量: {batchSellableVolume.toLocaleString()} 股</p>
                    <p>
                      估算委托市值:{' '}
                      {formatCurrency(batchEstimatedSellValue)}
                    </p>
                  </div>
                </div>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>取消</AlertDialogCancel>
              <AlertDialogAction
                onClick={handleLiquidateAll}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              >
                提交全部清仓委托
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>

        <button
          type="button"
          disabled
          title="资金划转请在券商客户端办理"
          className="flex h-8 cursor-not-allowed items-center justify-center gap-2 rounded-md border border-white/10 px-3 text-[10px] font-black uppercase tracking-wider text-slate-600"
        >
          <Wallet className="h-3.5 w-3.5" />
          券商端划转
        </button>
      </div>
    </div>
  );

  const batchContent = (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
      <LiquidationStats
        availableCash={portfolioSummary?.cash ?? 0}
        totalLiquidatedPnL={totalLiquidatedPnL}
        totalMarketValue={portfolioMarketValue}
      />

      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-3">
        <TabsList className="flex h-9 w-full max-w-[360px] gap-1 rounded-md border border-white/10 bg-white/[0.03] p-1">
          <TabsTrigger value="current" className={compactTabTriggerClass}>
            <Briefcase className="mr-2 h-3.5 w-3.5" />
            当前持仓
            <span className="ml-2 rounded bg-white/[0.04] px-1 text-[9px]">
              {currentHoldings.length}
            </span>
          </TabsTrigger>
          <TabsTrigger value="liquidated" className={compactTabTriggerClass}>
            <History className="mr-2 h-3.5 w-3.5" />
            真实回报
            <span className="ml-2 rounded bg-white/[0.04] px-1 text-[9px]">
              {liquidatedStocks.length}
            </span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="current" className="mt-0 focus-visible:outline-none">
          <CurrentHoldingsSection
            conditionalOrderStockCodes={conditionalOrderStockCodes}
            holdings={currentHoldings}
            isSubmitting={actionLoading}
            liquidateMultiple={handleLiquidateStockCodes}
            onConfigureConditionalOrder={holding =>
              openStudioTab(
                buildLiquidationSymbolPath(
                  holding.stockCode,
                  manualWorkspaceTabId,
                  holding.instrumentName
                )
              )
            }
            onLiquidateSelected={handleLiquidateSelected}
            onSelectionChange={setSelectedHoldings}
            selectedHoldings={selectedHoldings}
          />
        </TabsContent>

        <TabsContent
          value="liquidated"
          className="mt-0 focus-visible:outline-none"
        >
          <LiquidatedStocksSection liquidatedStocks={liquidatedStocks} />
        </TabsContent>
      </Tabs>
    </div>
  );

  const stockContent = (
    <div className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar">
      <SingleStockLiquidationPanel
        actionLoading={actionLoading}
        accountId={accountId}
        conditionalActionLoading={conditionalActionLoading}
        conditionalLoading={conditionalOrdersResult.fetching}
        conditionalOrder={selectedConditionalOrder}
        holding={selectedHolding}
        onCancelConditionalOrder={handleCancelConditionalOrder}
        onEvaluateConditionalOrders={handleEvaluateConditionalOrders}
        onLiquidate={handleLiquidateStockCodes}
        onSaveConditionalOrder={handleSaveConditionalOrder}
        onToggleConditionalOrder={handleToggleConditionalOrder}
        orders={relatedOrders}
        selectedStockCode={selectedStockCode}
        trades={relatedTrades}
      />
    </div>
  );

  const content = (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {toolbar}
      {dataError && (
        <div className="border-b border-amber-400/20 bg-amber-500/10 px-4 py-2 text-xs font-bold text-amber-100">
          数据读取异常：{dataError.message}
        </div>
      )}
      {actionError && (
        <div className="border-b border-rose-400/20 bg-rose-500/10 px-4 py-2 text-xs font-bold text-rose-100">
          最近一次清仓提交异常：{actionError.message}
        </div>
      )}
      {dataLoading && currentHoldings.length === 0 ? (
        <div className="flex min-h-0 flex-1 items-center justify-center text-sm font-medium text-slate-500">
          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          加载真实清仓数据中...
        </div>
      ) : selectedStockCode ? (
        stockContent
      ) : (
        batchContent
      )}
    </div>
  );

  return (
    <StudioWorkbench
      activeMode={activeMode}
      className="h-full min-h-0"
      content={content}
      isPage
      modes={liquidationModes}
      onModeChange={handleStudioModeChange}
      sidebar={sidebar}
      sidebarSizing={{
        defaultWidth: 312,
        maxWidth: 430,
        minWidth: 260,
        storageScope: 'liquidation-studio',
      }}
      showSidebar
      statusBarLeft={
        <>
          <span className="inline-flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
            清仓工作台
          </span>
          <span className="text-slate-700">|</span>
          <span>{accountName}</span>
          <span className="text-slate-700">|</span>
          <span>已选 {selectedHoldings.length}</span>
        </>
      }
      statusBarRight={
        <>
          <span className="inline-flex items-center gap-2">
            <BarChart3 className="h-3 w-3 text-red-400" />
            {selectedStockCode || '批量清仓'}
          </span>
          <span className="text-slate-700">|</span>
          <span>可清仓 {liquidatableHoldings.length} 只</span>
          <span className="text-slate-700">|</span>
          <span>真实回报 {liquidatedStocks.length} 笔</span>
        </>
      }
      theme={{
        icon: Hand,
        name: 'red',
        title: '清仓管理',
      }}
    />
  );
}
