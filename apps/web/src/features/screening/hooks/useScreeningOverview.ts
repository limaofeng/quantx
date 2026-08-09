import { useMemo } from 'react';

/**
 * 筛选概览数据
 */
interface ScreeningOverviewData {
  totalScreenings: number;
  activeScreenings: number;
  totalStocks: number;
  averageReturn: number;
  bestPerformer: {
    stockCode: string;
    returnPercentage: number;
    stock: { code: string; name: string };
  };
  worstPerformer: {
    stockCode: string;
    returnPercentage: number;
    stock: { code: string; name: string };
  };
}

interface UseScreeningOverviewResult {
  summary: ScreeningOverviewData;
  recentScreenings: RecentScreening[];
  isLoading: boolean;
}

interface RecentScreening {
  id: string;
  name: string;
  description: string;
  isActive: boolean;
  createdAt: Date;
  results: Array<{
    id: string;
    stockCode: string;
    stock: { code: string; name: string };
    score: number;
    returnPercentage: number;
  }>;
}

/**
 * 筛选概览 Hook
 * 提供概览页面所需的汇总数据
 */
export function useScreeningOverview(): UseScreeningOverviewResult {
  // TODO: 将来接入 GraphQL 查询
  const isLoading = false;

  // Mock 数据
  const summary: ScreeningOverviewData = useMemo(
    () => ({
      totalScreenings: 5,
      activeScreenings: 3,
      totalStocks: 42,
      averageReturn: 8.5,
      bestPerformer: {
        stockCode: '600519',
        returnPercentage: 28.4,
        stock: { code: '600519', name: '贵州茅台' },
      },
      worstPerformer: {
        stockCode: '002594',
        returnPercentage: -5.2,
        stock: { code: '002594', name: '比亚迪' },
      },
    }),
    []
  );

  const recentScreenings = useMemo(
    () => [
      {
        id: '1',
        name: '价值投资组合',
        description: '寻找低估值、高ROE的优质股票',
        isActive: true,
        createdAt: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000),
        results: [
          {
            id: '1',
            stockCode: '000001',
            stock: { code: '000001', name: '平安银行' },
            score: 85.2,
            returnPercentage: 12.3,
          },
          {
            id: '2',
            stockCode: '600519',
            stock: { code: '600519', name: '贵州茅台' },
            score: 92.1,
            returnPercentage: 28.4,
          },
        ],
      },
      {
        id: '2',
        name: '成长股筛选',
        description: '高增长潜力的科技和新能源股票',
        isActive: true,
        createdAt: new Date(Date.now() - 15 * 24 * 60 * 60 * 1000),
        results: [
          {
            id: '3',
            stockCode: '300750',
            stock: { code: '300750', name: '宁德时代' },
            score: 78.9,
            returnPercentage: 15.6,
          },
          {
            id: '4',
            stockCode: '002594',
            stock: { code: '002594', name: '比亚迪' },
            score: 73.4,
            returnPercentage: -5.2,
          },
        ],
      },
    ],
    []
  );

  return {
    summary,
    recentScreenings,
    isLoading,
  };
}
