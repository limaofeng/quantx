// 类型定义统一导出

// 基础类型
export * from './common';

// 业务模块类型
export * from './stock';
export * from './trading';
export * from './strategy';
export * from './dashboard';

// 重新导出常用类型（向后兼容）
export type { Stock, MarketIndex } from './stock';

export type { Order } from './trading';

export type {
  PortfolioAllocation,
  PerformanceMetrics,
  TopPerformer,
} from './dashboard';
