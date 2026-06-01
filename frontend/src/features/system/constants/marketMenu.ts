import {
  CandlestickChart,
  LayoutGrid,
  Percent,
  PieChart,
  TrendingUp,
} from 'lucide-react';

export const MARKET_MENU_ITEMS = [
  { id: 'overview', label: '市场总览', icon: LayoutGrid },
  { id: 'indices', label: '沪深指数', icon: TrendingUp },
  { id: 'stocks', label: '个股', icon: CandlestickChart },
  { id: 'etf', label: 'ETF', icon: PieChart },
  { id: 'ex-rights', label: '除权数据', icon: Percent },
] as const;

export const MARKET_TAB_LABELS: Record<string, string> = {
  ...MARKET_MENU_ITEMS.reduce(
    (acc, item) => ({
      ...acc,
      [item.id]: item.label,
    }),
    {}
  ),
  // Legacy or extra mappings can be added here if needed, but ideally we stick to the menu items
  calendar: '交易日历',
  kline: 'K线数据',
  'ex-rights': '除权数据管理', // Overrides '除权数据' if we want a longer title in the header vs sidebar, or just consistency
};
