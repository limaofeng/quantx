import { useMemo } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

/**
 * 获取当前账户信息
 */
export const GetCurrentAccountQuery = gql(`
  query GetCurrentAccount {
    currentAccount {
      id
      accountName
      accountType
      totalAsset
      cash
      frozenCash
      marketValue
      totalProfitLoss
      profitLossPercent
      createTime
      updateTime
    }
  }
`);

/**
 * 使用当前账户信息的 Hook
 */
export function useCurrentAccount() {
  const [result] = useQuery({
    query: GetCurrentAccountQuery,
  });

  return useMemo(
    () => ({
      data: result.data,
      loading: result.fetching,
      error: result.error,
    }),
    [result.data, result.fetching, result.error]
  );
}

/**
 * 获取仪表板汇总信息 (暂时注释掉，待后端支持)
 */
// export const DashboardSummaryQuery = gql(`
//   query dashboardSummary {
//     dashboardSummary {
//       totalAsset
//       todayPnL
//       todayPnLPercent
//       totalReturn
//       totalReturnPercent
//       activePositions
//       todayTrades
//       marketStatus
//     }
//   }
// `);
