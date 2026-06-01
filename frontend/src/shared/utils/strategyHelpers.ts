import {
  Bot,
  BarChart,
  TrendingUp,
  TrendingDown,
  Target,
  Zap,
} from 'lucide-react';

/**
 * 策略相关的工具函数
 * 用于处理策略分类、风险等级等枚举值的转换和展示
 */

/**
 * 映射策略分类到中文名称
 */
export const getCategoryName = (category?: string | null): string => {
  if (!category) return '其他';

  switch (category) {
    case 'trend':
    case 'TREND_FOLLOWING':
      return '趋势跟随';
    case 'mean_reversion':
    case 'MEAN_REVERSION':
      return '均值回归';
    case 'momentum':
    case 'MOMENTUM':
      return '动量策略';
    case 'volatility':
    case 'VOLATILITY':
      return '波动率策略';
    case 'ARBITRAGE':
      return '套利策略';
    case 'MARKET_MAKING':
      return '做市策略';
    default:
      return '其他';
  }
};

/**
 * 映射风险等级到中文名称
 */
export const getRiskLevelName = (riskLevel?: string | null): string => {
  if (!riskLevel) return '未知';

  switch (riskLevel) {
    case 'low':
    case 'LOW':
      return '低风险';
    case 'medium':
    case 'MEDIUM':
      return '中等风险';
    case 'high':
    case 'HIGH':
      return '高风险';
    case 'VERY_HIGH':
      return '极高风险';
    default:
      return '未知';
  }
};

/**
 * 映射风险等级到对应的颜色样式类
 */
export const getRiskLevelColor = (riskLevel?: string | null): string => {
  if (!riskLevel) return 'text-muted-foreground';

  switch (riskLevel) {
    case 'low':
    case 'LOW':
      return 'text-success';
    case 'medium':
    case 'MEDIUM':
      return 'text-warning';
    case 'high':
    case 'HIGH':
      return 'text-destructive';
    case 'VERY_HIGH':
      return 'text-destructive font-bold';
    default:
      return 'text-muted-foreground';
  }
};

/**
 * 映射策略状态到中文名称
 */
export const getStrategyStatusName = (status?: string | null): string => {
  if (!status) return '未知';

  switch (status) {
    case 'ACTIVE':
    case 'Active':
      return '运作中';
    case 'PAUSED':
    case 'Paused':
      return '已暂停';
    case 'STOPPED':
    case 'Stopped':
      return '已停止';
    case 'UPGRADING':
    case 'Upgrading':
      return '升级中';
    case 'DEPRECATED':
    case 'Deprecated':
      return '已废弃';
    default:
      return status;
  }
};

/**
 * 映射运行模式到中文名称
 */
export const getModeName = (mode?: string | null): string => {
  if (!mode) return '本地';

  switch (mode) {
    case 'LIVE':
    case 'Live':
      return '实盘';
    case 'PAPER':
    case 'Paper':
      return '模拟';
    case 'BACKTEST':
    case 'Backtest':
      return '回测';
    default:
      return mode;
  }
};

/**
 * 映射策略分类到对应的图标组件
 */
export const getCategoryIcon = (category?: string | null) => {
  if (!category) return Bot;

  switch (category) {
    case 'TREND_FOLLOWING':
    case 'trend':
      return TrendingUp;
    case 'MEAN_REVERSION':
    case 'mean_reversion':
      return TrendingDown;
    case 'MOMENTUM':
    case 'momentum':
      return Zap;
    case 'VOLATILITY':
    case 'volatility':
      return BarChart;
    case 'ARBITRAGE':
      return Target;
    case 'MARKET_MAKING':
      return Bot;
    default:
      return Bot;
  }
};
