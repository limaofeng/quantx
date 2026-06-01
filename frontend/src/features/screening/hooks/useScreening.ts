import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

/**
 * 获取股票筛选方案列表 (暂时返回空，待后端实现)
 */
export const GetStockScreeningsQuery = gql(`
  query GetStockScreenings {
    currentAccount {
      id
    }
  }
`);

/**
 * 筛选方案列表 Hook
 */
export function useStockScreenings() {
  const [result] = useQuery({
    query: GetStockScreeningsQuery as any,
  });

  return {
    screenings: [],
    loading: result.fetching,
    error: result.error,
  };
}

/**
 * 全部股票指标 Hook
 */
export function useAllStockMetrics() {
  return {
    metrics: [],
    loading: false,
  };
}

/**
 * 筛选方案概况 Hook
 */
export function useScreeningSummary() {
  return {
    summary: null,
    loading: false,
  };
}

/**
 * 可用行业列表 Hook
 */
export function useAvailableIndustries() {
  return {
    industries: [],
    loading: false,
  };
}
