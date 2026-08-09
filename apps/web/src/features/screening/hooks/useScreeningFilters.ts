import { useState, useCallback, useMemo } from 'react';
import { useQuery } from 'urql';

import {
  GetSectorsDocument,
  type GetSectorsQuery,
  type GetSectorsQueryVariables,
  type GetStockScreeningsQuery as GetStockScreeningsQueryData,
} from '@/generated/gql/graphql';

import { GetStockScreeningsQuery } from './useScreening';

interface ScreeningCriteria {
  includeIndustries?: string[];
  excludeIndustries?: string[];
  peMin?: number;
  peMax?: number;
  pbMin?: number;
  pbMax?: number;
  roeMin?: number;
  roeMax?: number;
  revenueGrowthMin?: number;
  revenueGrowthMax?: number;
  rsiMin?: number;
  rsiMax?: number;
  marketCapMin?: number;
  marketCapMax?: number;
  customFormula?: string;
}

interface UseScreeningFiltersResult {
  // 筛选条件
  criteria: ScreeningCriteria;
  setCriteria: (
    criteria:
      ScreeningCriteria | ((prev: ScreeningCriteria) => ScreeningCriteria)
  ) => void;

  // 可用的行业列表
  availableIndustries: string[];

  // 筛选结果
  screeningResults: GetStockScreeningsQueryData | undefined;
  screeningLoading: boolean;

  // 执行筛选
  runScreening: () => void;
}

function normalizeIndustryName(name: string): string {
  return name.trim().replace(/\s+/g, '').replace(/加权$/, '');
}

/**
 * 筛选器 Hook
 * 管理筛选条件和执行筛选
 */
export function useScreeningFilters(): UseScreeningFiltersResult {
  const [criteria, setCriteria] = useState<ScreeningCriteria>({});

  const [gnSectorsResult] = useQuery<GetSectorsQuery, GetSectorsQueryVariables>(
    {
      query: GetSectorsDocument,
      variables: {
        classification: 'SW1',
        search: null,
        limit: 1000,
        offset: 0,
      },
    }
  );

  const availableIndustries = useMemo(() => {
    const sectorNames =
      gnSectorsResult.data?.sectors?.items
        ?.map(item => item?.name?.trim())
        .filter((name): name is string => Boolean(name)) || [];

    if (sectorNames.length === 0) {
      return ['银行', '房地产', '医药生物', '食品饮料', '电子', '计算机'];
    }

    const deduped = new Map<string, string>();
    for (const rawName of sectorNames) {
      const normalized = normalizeIndustryName(rawName);
      if (!normalized) continue;

      const current = deduped.get(normalized);
      if (!current || current.endsWith('加权')) {
        deduped.set(normalized, normalized);
      }
    }

    return Array.from(deduped.values());
  }, [gnSectorsResult.data?.sectors?.items]);

  // 执行筛选查询 (使用 URQL 占位)
  const [result, executeQuery] = useQuery({
    query: GetStockScreeningsQuery,
    pause: true, // 只在手动触发时执行
  });

  return {
    criteria,
    setCriteria,
    availableIndustries,
    screeningResults: result.data,
    screeningLoading: result.fetching,
    runScreening: useCallback(() => executeQuery(), [executeQuery]),
  };
}
