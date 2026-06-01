/**
 * 数据转换工具
 * 处理前后端数据格式差异
 */

// 后端 Position 类型定义（驼峰命名）
export interface BackendPosition {
  id: string;
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

// 前端期望的 Holding 结构
export interface FrontendHolding {
  id: string;
  quantity: number;
  averageCost: number;
  stock: {
    id: string;
    code: string;
    name: string;
    currentPrice: number;
  };
}

/**
 * 将后端 Position 转换为前端 Holding 格式
 */
export function transformPositionToHolding(
  position: BackendPosition
): FrontendHolding {
  return {
    id: position.id,
    quantity: position.volume,
    averageCost: position.avgPrice,
    stock: {
      id: position.stockCode,
      code: position.stockCode,
      name: position.stockName,
      currentPrice: position.lastPrice,
    },
  };
}

/**
 * 批量转换 Positions 数组
 */
export function transformPositionsToHoldings(
  positions: BackendPosition[]
): FrontendHolding[] {
  return positions.map(transformPositionToHolding);
}

/**
 * 安全的数字转换，处理可能的字符串类型
 */
export function safeNumber(
  value: number | string,
  defaultValue: number = 0
): number {
  if (typeof value === 'number' && !isNaN(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = parseFloat(value);
    return isNaN(parsed) ? defaultValue : parsed;
  }
  return defaultValue;
}

/**
 * 格式化货币显示
 */
export function formatCurrency(value: number): string {
  return new Intl.NumberFormat('zh-CN', {
    style: 'currency',
    currency: 'CNY',
    minimumFractionDigits: 2,
  }).format(value);
}

/**
 * 格式化百分比显示
 */
export function formatPercent(value: number): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}
