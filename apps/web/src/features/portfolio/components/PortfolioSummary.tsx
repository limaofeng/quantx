import { Activity, TrendingUp, Wallet, BarChart3 } from 'lucide-react';
import { useMemo } from 'react';

import { InsightTile } from '@/shared/components/cards/InsightTile';
import { formatCurrency, formatPercent } from '@/shared/utils/format';

import type { DailyAssetSnapshotData, PortfolioSummaryData } from '../types';

interface PortfolioSummaryProps {
  summary: PortfolioSummaryData;
  dailyAssetSnapshots?: DailyAssetSnapshotData[];
}

const TREND_POINT_LIMIT = 30;

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function signedCurrency(value: number) {
  return `${value >= 0 ? '+' : ''}${formatCurrency(value)}`;
}

function signedPercent(value: number) {
  return `${value >= 0 ? '+' : ''}${formatPercent(value)}`;
}

function buildTrend(
  snapshots: DailyAssetSnapshotData[],
  selector: (snapshot: DailyAssetSnapshotData) => number | null | undefined,
  fallbackValue?: number | null
) {
  const values = snapshots.map(selector).filter(isFiniteNumber);
  if (values.length === 0 && isFiniteNumber(fallbackValue)) {
    values.push(fallbackValue);
  }

  if (values.length === 1) {
    return [values[0], values[0]];
  }

  return values;
}

function buildCumulativePnlTrend(
  snapshots: DailyAssetSnapshotData[],
  fallbackValue?: number | null
) {
  let cumulative = 0;
  const values = snapshots
    .map(snapshot => snapshot.dailyPnlCny)
    .filter(isFiniteNumber)
    .map(value => {
      cumulative += value;
      return cumulative;
    });
  if (values.length === 0 && isFiniteNumber(fallbackValue)) {
    values.push(fallbackValue);
  }

  if (values.length === 1) {
    return [values[0], values[0]];
  }

  return values;
}

export function PortfolioSummary({
  summary,
  dailyAssetSnapshots = [],
}: PortfolioSummaryProps) {
  const orderedSnapshots = useMemo(
    () =>
      [...dailyAssetSnapshots]
        .sort((a, b) => a.tradeDate.localeCompare(b.tradeDate))
        .slice(-TREND_POINT_LIMIT),
    [dailyAssetSnapshots]
  );

  const marketValueTrend = useMemo(
    () =>
      buildTrend(
        orderedSnapshots,
        snapshot => snapshot.marketValueCny,
        summary.totalMarketValue
      ),
    [orderedSnapshots, summary.totalMarketValue]
  );
  const cumulativePnlTrend = useMemo(
    () => buildCumulativePnlTrend(orderedSnapshots, summary.totalProfitLoss),
    [orderedSnapshots, summary.totalProfitLoss]
  );

  const hasTodayProfitLoss = isFiniteNumber(summary.todayProfitLoss);
  const todayProfitLoss = summary.todayProfitLoss ?? 0;
  const todayProfitLossPercent = summary.todayProfitLossPercent ?? 0;
  const isTodayProfitable = todayProfitLoss >= 0;
  const isTotalProfitable = summary.totalProfitLoss >= 0;
  const positionRatio =
    summary.totalAsset > 0
      ? (summary.totalMarketValue / summary.totalAsset) * 100
      : 0;
  const dailyPnlTrend = useMemo(
    () =>
      buildTrend(
        orderedSnapshots,
        snapshot => snapshot.dailyPnlCny,
        hasTodayProfitLoss ? todayProfitLoss : null
      ),
    [hasTodayProfitLoss, orderedSnapshots, todayProfitLoss]
  );
  const positionRatioTrend = useMemo(
    () =>
      buildTrend(
        orderedSnapshots,
        snapshot =>
          snapshot.totalAssetCny > 0
            ? (snapshot.marketValueCny / snapshot.totalAssetCny) * 100
            : null,
        positionRatio
      ),
    [orderedSnapshots, positionRatio]
  );

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-ui-section mb-8">
      <InsightTile
        label="持仓市值"
        value={formatCurrency(summary.totalMarketValue)}
        subValue={`总资产: ${formatCurrency(summary.totalAsset)}`}
        icon={<Wallet />}
        theme="sky"
        sparklineData={marketValueTrend}
      />

      <InsightTile
        label="累计收益"
        value={signedCurrency(summary.totalProfitLoss)}
        subValue={`回报率: ${signedPercent(summary.totalProfitLossPercent)}`}
        icon={<TrendingUp />}
        theme={isTotalProfitable ? 'market-up' : 'holding-down'}
        status={isTotalProfitable ? '盈利' : '亏损'}
        sparklineData={cumulativePnlTrend}
      />

      <InsightTile
        label="今日盈亏"
        value={hasTodayProfitLoss ? signedCurrency(todayProfitLoss) : '待更新'}
        subValue={
          hasTodayProfitLoss
            ? `收盘收益率: ${signedPercent(todayProfitLossPercent)}`
            : '等待日终资产快照'
        }
        icon={<Activity />}
        theme={
          hasTodayProfitLoss
            ? isTodayProfitable
              ? 'market-up'
              : 'holding-down'
            : 'amber'
        }
        status={
          hasTodayProfitLoss ? (isTodayProfitable ? '盈利' : '亏损') : undefined
        }
        sparklineData={dailyPnlTrend}
      />

      <InsightTile
        label="持仓分布"
        value={`${summary.positionCount}`}
        subValue={`仓位占比: ${formatPercent(positionRatio)}`}
        icon={<BarChart3 />}
        theme="blue"
        status={`${summary.profitPositionCount}盈利`}
        sparklineData={positionRatioTrend}
      />
    </div>
  );
}
