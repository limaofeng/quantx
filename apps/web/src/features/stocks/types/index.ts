import type { GetInstrumentQuery } from '@/generated/gql/graphql';

export type StockDetail = NonNullable<GetInstrumentQuery['instrument']>;

export interface StockHolding {
  id: string;
  quantity: number;
  averagePrice: number;
  currentValue: number;
  unrealizedPnL: number;
  unrealizedPnLPercent: number;
}

export interface StockTransaction {
  id: string;
  type: 'BUY' | 'SELL';
  quantity: number;
  price: number;
  amount: number;
  timestamp: string;
  commission: number;
}

export interface UseStockDetailResult {
  stock: StockDetail | null;
  holding: StockHolding | null;
  transactions: StockTransaction[];
  isLoading: boolean;
  error: Error | null;
  refetch: () => void;
}

export interface StockChartData {
  timestamp: string;
  price: number;
  volume: number;
}
