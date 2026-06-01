// 交易相关常量

export const ORDER_TYPES = {
  MARKET: 'market',
  LIMIT: 'limit',
  STOP: 'stop',
  STOP_LIMIT: 'stop_limit',
} as const;

export const ORDER_SIDES = {
  BUY: 'buy',
  SELL: 'sell',
} as const;

export const ORDER_STATUS = {
  PENDING: 'pending',
  FILLED: 'filled',
  CANCELLED: 'cancelled',
  REJECTED: 'rejected',
  PARTIALLY_FILLED: 'partially_filled',
} as const;

export const TRANSACTION_TYPES = {
  BUY: 'buy',
  SELL: 'sell',
  DIVIDEND: 'dividend',
  SPLIT: 'split',
  BONUS: 'bonus',
} as const;

export const STRATEGY_STATUS = {
  RUNNING: 'running',
  PAUSED: 'paused',
  STOPPED: 'stopped',
  ERROR: 'error',
} as const;

export const STRATEGY_TYPES = {
  MOMENTUM: 'momentum',
  MEAN_REVERSION: 'mean_reversion',
  ARBITRAGE: 'arbitrage',
  GRID: 'grid',
  CUSTOM: 'custom',
} as const;

export const MARKET_SESSIONS = {
  PRE_MARKET: 'pre_market',
  MARKET: 'market',
  AFTER_MARKET: 'after_market',
  CLOSED: 'closed',
} as const;

export const EXCHANGES = {
  SH: 'SH', // 上海证券交易所
  SZ: 'SZ', // 深圳证券交易所
  BJ: 'BJ', // 北京证券交易所
} as const;
