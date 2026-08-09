// 交易相关类型定义
import { type BaseEntity } from './common';
import { type Stock } from './stock';

export interface Holding extends BaseEntity {
  stockCode: string;
  stockName: string;
  volume: number;
  canUseVolume: number;
  openPrice: number;
  marketValue: number;
  frozenVolume: number;
  onRoadVolume: number;
  yesterdayVolume: number;
  avgPrice: number;
  lastPrice: number;
  profitRate: number;
  profitLoss: number;
}

export interface Transaction extends BaseEntity {
  type: 'buy' | 'sell';
  stockCode: string;
  stockName: string;
  quantity: number;
  price: number;
  totalAmount: number;
  status: 'pending' | 'filled' | 'cancelled' | 'rejected';
  orderTime: string;
  fillTime?: string;
  commission?: number;
}

export interface Order extends BaseEntity {
  stockCode: string;
  stockName: string;
  type: 'buy' | 'sell';
  orderType: 'market' | 'limit' | 'stop';
  quantity: number;
  price?: number;
  stopPrice?: number;
  status: 'pending' | 'filled' | 'cancelled' | 'rejected';
  filledQuantity: number;
  avgFillPrice?: number;
  orderTime: string;
  validUntil?: string;
}

export interface LiquidatedStock extends BaseEntity {
  stockCode: string;
  stockName: string;
  quantity: number;
  liquidationPrice: number;
  liquidationValue: number;
  liquidationDate: string;
  reason: string;
}

// 扩展类型，包含关联的股票信息
export interface EnrichedHolding extends Holding {
  stock?: Stock;
}

export interface EnrichedTransaction extends Transaction {
  stock?: Stock;
}

export interface EnrichedLiquidatedStock extends LiquidatedStock {
  stock?: Stock;
}
