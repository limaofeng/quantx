import {
  Activity,
  BarChart3,
  ChevronRight,
  RadioTower,
  ShieldAlert,
  Target,
} from 'lucide-react';
import type * as React from 'react';

import { financialToneClass } from '@/shared/utils/financialColors';
import { cn } from '@/utils/cn';
import { formatCurrency, formatPercent } from '@/utils/transform/data';

import type { LiquidatedStock, Position } from '../types';
import {
  buildConditionalMonitorRows,
  buildHoldingRiskAlerts,
  buildLiquidationDashboardMetrics,
  buildLiquidationDashboardSummary,
  type ConditionalLiquidationOrderLike,
  type ConditionalMonitorRow,
  type DashboardMetricTone,
  type HoldingRiskAlert,
  type HoldingRiskSeverity,
  type LiquidationDashboardMetric,
} from '../utils/liquidationDashboard';

interface LiquidationDashboardProps {
  conditionalOrders: ConditionalLiquidationOrderLike[];
  currentHoldings: Position[];
  liquidatedStocks: LiquidatedStock[];
  onOpenStock: (stockCode: string, instrumentName?: string | null) => void;
  portfolioMarketValue?: number | null;
  tickDropPctByCode?: Record<string, number>;
}

const metricToneClass: Record<DashboardMetricTone, string> = {
  danger: 'border-rose-400/20 bg-rose-500/10 text-rose-100',
  info: 'border-blue-400/20 bg-blue-500/10 text-blue-100',
  neutral: 'border-white/5 bg-white/[0.035] text-slate-100',
  success: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-100',
  warning: 'border-amber-400/20 bg-amber-500/10 text-amber-100',
};

const statusToneClass: Record<ConditionalMonitorRow['status'], string> = {
  cancelled: 'border-white/10 bg-white/[0.03] text-slate-500',
  error: 'border-rose-400/25 bg-rose-500/10 text-rose-100',
  monitoring: 'border-blue-400/20 bg-blue-500/10 text-blue-100',
  paused: 'border-amber-400/20 bg-amber-500/10 text-amber-100',
  submitted: 'border-emerald-400/20 bg-emerald-500/10 text-emerald-100',
  triggered: 'border-red-400/25 bg-red-500/10 text-red-100',
};

const riskToneClass: Record<HoldingRiskSeverity, string> = {
  critical: 'border-rose-400/30 bg-rose-500/10 text-rose-100',
  warning: 'border-amber-400/25 bg-amber-500/10 text-amber-100',
  watch: 'border-blue-400/20 bg-blue-500/10 text-blue-100',
};

function formatMetricValue(metric: LiquidationDashboardMetric) {
  if (metric.id === 'sellableMarketValue' || metric.id === 'totalMarketValue') {
    return formatCurrency(Number(metric.value || 0));
  }
  return String(metric.value);
}

function formatNullableCurrency(value: number | null) {
  return value === null ? '--' : formatCurrency(value);
}

function formatNullablePercent(value: number | null, signed = false) {
  if (value === null) return '--';
  const sign = signed && value > 0 ? '+' : '';
  return `${sign}${formatPercent(value)}`;
}

function formatDateTime(value: string | null) {
  if (!value) return '--';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN');
}

function formatDistance(row: ConditionalMonitorRow) {
  if (row.distancePct === null) return '--';
  if (row.distancePct <= 0) return '已达条件';
  return `差 ${formatPercent(row.distancePct)}`;
}

function DashboardPanel({
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
        <h3 className="truncate text-ui-label font-black text-slate-200">
          {title}
        </h3>
      </div>
      <div className="p-3">{children}</div>
    </section>
  );
}

function EmptyDashboardState({ label }: { label: string }) {
  return (
    <div className="rounded-md border border-dashed border-white/10 bg-white/[0.025] px-ui-section py-7 text-center text-ui-label font-bold text-slate-500">
      {label}
    </div>
  );
}

function MetricTile({ metric }: { metric: LiquidationDashboardMetric }) {
  return (
    <div
      className={cn(
        'min-w-0 rounded-md border px-3 py-3',
        metricToneClass[metric.tone]
      )}
      data-testid={`dashboard-metric-${metric.id}`}
    >
      <div className="truncate text-ui-caption font-black uppercase tracking-wider text-slate-500">
        {metric.label}
      </div>
      <div className="mt-2 truncate font-mono text-ui-heading font-black tabular-nums">
        {formatMetricValue(metric)}
      </div>
      {metric.subValue && (
        <div className="mt-1 truncate text-ui-caption font-bold text-slate-500">
          {metric.subValue}
        </div>
      )}
    </div>
  );
}

function handleRowKeyDown(event: React.KeyboardEvent, onOpen: () => void) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    onOpen();
  }
}

function ConditionalMonitorTable({
  onOpenStock,
  rows,
}: {
  onOpenStock: (stockCode: string, instrumentName?: string | null) => void;
  rows: ConditionalMonitorRow[];
}) {
  if (rows.length === 0) {
    return (
      <EmptyDashboardState label="暂无条件清仓监控，选择左侧持仓可配置条件单。" />
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[820px] text-left text-ui-label">
        <thead className="text-ui-caption font-black uppercase tracking-wider text-slate-600">
          <tr className="border-b border-white/5">
            <th className="px-2 py-2">股票</th>
            <th className="px-2 py-2">条件</th>
            <th className="px-2 py-2 text-right">当前价 / 收益率</th>
            <th className="px-2 py-2 text-right">距触发</th>
            <th className="px-2 py-2 text-right">状态</th>
            <th className="px-2 py-2 text-right">最近检查</th>
            <th className="px-2 py-2 text-right">异常</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const openRow = () =>
              onOpenStock(row.stockCode, row.instrumentName);

            return (
              <tr
                key={row.id}
                className="cursor-pointer border-b border-white/5 transition-colors hover:bg-white/[0.035] focus:bg-white/[0.035] focus:outline-none"
                data-testid={`conditional-monitor-row-${row.stockCode}`}
                onClick={openRow}
                onKeyDown={event => handleRowKeyDown(event, openRow)}
                role="button"
                tabIndex={0}
                title="打开个股清仓监控"
              >
                <td className="px-2 py-2">
                  <div className="font-bold text-slate-100">
                    {row.instrumentName}
                  </div>
                  <div className="font-mono text-ui-caption text-slate-600">
                    {row.stockCode}
                  </div>
                </td>
                <td className="max-w-[240px] px-2 py-2">
                  <div className="truncate font-bold text-slate-300">
                    {row.conditionText}
                  </div>
                </td>
                <td className="px-2 py-2 text-right font-mono text-slate-300">
                  <div>{formatNullableCurrency(row.currentPrice)}</div>
                  <div
                    className={cn(
                      'text-ui-caption',
                      financialToneClass(row.currentProfitPct, 'holding')
                    )}
                  >
                    {formatNullablePercent(row.currentProfitPct, true)}
                  </div>
                </td>
                <td
                  className={cn(
                    'px-2 py-2 text-right font-mono font-black',
                    row.distancePct !== null && row.distancePct <= 0
                      ? 'text-red-200'
                      : 'text-slate-300'
                  )}
                >
                  {formatDistance(row)}
                </td>
                <td className="px-2 py-2 text-right">
                  <span
                    className={cn(
                      'inline-flex rounded border px-2 py-1 text-ui-caption font-black',
                      statusToneClass[row.status]
                    )}
                  >
                    {row.statusLabel}
                  </span>
                </td>
                <td className="px-2 py-2 text-right font-mono text-ui-caption text-slate-500">
                  {formatDateTime(row.lastCheckedAt)}
                </td>
                <td className="max-w-[160px] px-2 py-2 text-right text-ui-caption font-bold text-rose-200">
                  <div className="truncate">{row.lastError || '--'}</div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RiskAlertList({
  alerts,
  onOpenStock,
}: {
  alerts: HoldingRiskAlert[];
  onOpenStock: (stockCode: string, instrumentName?: string | null) => void;
}) {
  if (alerts.length === 0) {
    return <EmptyDashboardState label="暂无极速下跌或明显走弱提示。" />;
  }

  return (
    <div className="grid gap-2">
      {alerts.map(alert => (
        <button
          key={alert.stockCode}
          className={cn(
            'flex min-h-[72px] w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors hover:bg-white/[0.04] focus:outline-none focus:ring-1 focus:ring-red-400/40',
            riskToneClass[alert.severity]
          )}
          data-testid={`holding-risk-alert-${alert.stockCode}`}
          onClick={() => onOpenStock(alert.stockCode, alert.instrumentName)}
          type="button"
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded border border-current/20 px-2 py-1 text-ui-caption font-black">
                {alert.title}
              </span>
              <span className="truncate text-ui-body font-black text-slate-100">
                {alert.instrumentName}
              </span>
              <span className="font-mono text-ui-caption font-bold text-slate-500">
                {alert.stockCode}
              </span>
            </div>
            <div className="mt-1 truncate text-ui-caption font-bold text-slate-500">
              {alert.reason}
            </div>
          </div>
          <div className="grid shrink-0 grid-cols-3 gap-3 text-right font-mono text-ui-caption font-black">
            <div>
              <div className="text-slate-600">涨跌</div>
              <div
                className={financialToneClass(alert.changePercent, 'holding')}
              >
                {formatNullablePercent(alert.changePercent, true)}
              </div>
            </div>
            <div>
              <div className="text-slate-600">滚动</div>
              <div className={financialToneClass(alert.tickDropPct, 'holding')}>
                {formatNullablePercent(alert.tickDropPct, true)}
              </div>
            </div>
            <div className="flex items-center justify-end gap-1">
              <div>
                <div className="text-slate-600">今日盈亏</div>
                <div
                  className={financialToneClass(
                    alert.todayProfitLoss,
                    'holding'
                  )}
                >
                  {formatNullableCurrency(alert.todayProfitLoss)}
                </div>
              </div>
              <ChevronRight className="h-3.5 w-3.5 text-slate-600" />
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}

export function LiquidationDashboard({
  conditionalOrders,
  currentHoldings,
  liquidatedStocks,
  onOpenStock,
  portfolioMarketValue,
  tickDropPctByCode,
}: LiquidationDashboardProps) {
  const conditionalRows = buildConditionalMonitorRows({
    conditionalOrders,
    holdings: currentHoldings,
  });
  const riskAlerts = buildHoldingRiskAlerts({
    holdings: currentHoldings,
    tickDropPctByCode,
  });
  const summary = buildLiquidationDashboardSummary({
    conditionalRows,
    holdings: currentHoldings,
    liquidatedStocks,
    portfolioMarketValue,
  });
  const metrics = buildLiquidationDashboardMetrics(summary);

  return (
    <div
      className="min-h-0 flex-1 overflow-y-auto p-3 custom-scrollbar"
      data-testid="liquidation-dashboard"
    >
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6">
        {metrics.map(metric => (
          <MetricTile key={metric.id} metric={metric} />
        ))}
      </div>

      <div className="mt-3 grid gap-3 2xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.55fr)]">
        <DashboardPanel icon={Target} title="条件清仓实时触发监控">
          <ConditionalMonitorTable
            onOpenStock={onOpenStock}
            rows={conditionalRows}
          />
        </DashboardPanel>

        <DashboardPanel icon={ShieldAlert} title="持仓风险提示">
          <RiskAlertList alerts={riskAlerts} onOpenStock={onOpenStock} />
        </DashboardPanel>
      </div>

      <div className="mt-3 grid gap-3 xl:grid-cols-3">
        <DashboardPanel icon={RadioTower} title="行情状态">
          <div className="grid gap-2 text-ui-label font-bold text-slate-400">
            <div className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.025] px-3 py-2">
              <span>订阅覆盖</span>
              <span className="font-mono text-slate-200">
                {summary.realtimeQuoteCount} / {summary.totalPositions}
              </span>
            </div>
            <div className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.025] px-3 py-2">
              <span>当前状态</span>
              <span
                className={cn(
                  'font-black',
                  summary.quoteStatus === 'live'
                    ? 'text-emerald-300'
                    : 'text-amber-300'
                )}
              >
                {summary.quoteStatus === 'live' ? '实时更新' : '等待行情'}
              </span>
            </div>
          </div>
        </DashboardPanel>

        <DashboardPanel icon={Activity} title="今日触发概览">
          <div className="grid gap-2 text-ui-label font-bold text-slate-400">
            <div className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.025] px-3 py-2">
              <span>今日触发</span>
              <span className="font-mono text-red-200">
                {summary.triggeredToday}
              </span>
            </div>
            <div className="flex items-center justify-between rounded-md border border-white/5 bg-white/[0.025] px-3 py-2">
              <span>异常条件单</span>
              <span className="font-mono text-rose-200">
                {summary.errorOrders}
              </span>
            </div>
          </div>
        </DashboardPanel>

        <DashboardPanel icon={BarChart3} title="Dashboard 边界">
          <div className="rounded-md border border-blue-400/15 bg-blue-500/10 px-3 py-3 text-ui-label font-bold leading-5 text-blue-100">
            本页只做监控与预警。普通清仓、条件配置和委托确认从左侧持仓进入个股页处理。
          </div>
        </DashboardPanel>
      </div>
    </div>
  );
}
