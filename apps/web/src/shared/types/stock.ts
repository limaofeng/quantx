export interface Stock {
  id: string;
  stockCode: string;
  code?: string; // Compatibility
  name: string;
  market?: string;
  type?: string;
  quote?: {
    lastPrice: number;
    changePercent: number;
    change?: number;
    volume?: number;
    amount?: number;
    open?: number;
    high?: number;
    low?: number;
    preClose?: number;
  };
  currentPrice?: number | string; // Compatibility
}

export interface StockPrice {
  stockCode: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  timestamp: string;
}

export interface MarketIndex {
  name: string;
  value: string;
  change: string;
  isPositive: boolean;
  timestamp?: string;
}
