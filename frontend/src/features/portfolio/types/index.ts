import type {
  DailyAssetSnapshotsForPortfolioSummaryQuery,
  HoldingsQuery,
  Portfolio_LiquidationSummaryQuery,
  PortfolioSummaryQuery,
  Trading_TodayOrdersQuery,
  Trading_TodayTradesQuery,
} from '@/generated/gql/graphql';

// 增强的 Position 类型，包含钩子注入的实时数据
export type Position = NonNullable<HoldingsQuery['positions']>[0] & {
  lastPrice?: number | null;
  marketValue?: number | null;
  profitLoss?: number | null;
  profitRate?: number | null;
  todayProfitLoss?: number | null;
  todayProfitRate?: number | null;
  change?: number | null;
  changePercent?: number | null;
  quoteTime?: string | null;
};

// 直接使用 GraphQL 生成的 PortfolioSummary 类型
export type PortfolioSummaryData = NonNullable<
  PortfolioSummaryQuery['portfolioSummary']
>;

export type DailyAssetSnapshotData =
  DailyAssetSnapshotsForPortfolioSummaryQuery['dailyAssetSnapshots'][0];

export type LiquidationSummaryData = NonNullable<
  Portfolio_LiquidationSummaryQuery['liquidationSummary']
>;

export type LiquidationTodayOrder =
  Trading_TodayOrdersQuery['todayOrders'][0];

export type LiquidationTodayTrade =
  Trading_TodayTradesQuery['todayTrades'][0];

export interface LiquidatedStock {
  id: string;
  symbol: string;
  name: string;
  quantity: number;
  sellPrice?: number | null;
  sellDate: string;
  realizedPnL?: number | null;
  realizedPnLPercent?: number | null;
  originalCost?: number | null;
  orderId?: number | string | null;
  source: 'ORDER' | 'TRADE';
  status?: string | null;
}

export interface UseHoldingsResult {
  holdings: Position[];
  portfolioSummary?: PortfolioSummaryData;
  dailyAssetSnapshots: DailyAssetSnapshotData[];
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
  liquidateHolding: (holdingId: string) => Promise<void>;
}

export interface UseLiquidationResult {
  liquidatedStocks: LiquidatedStock[];
  currentHoldings: Position[];
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
  liquidateMultiple: (stockCodes: string[]) => Promise<void>;
  redeemCash: (amount: number) => Promise<void>;
}
