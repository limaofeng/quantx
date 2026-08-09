import { useState, useCallback, useMemo } from 'react';
import { useQuery } from 'urql';

import { gql } from '@/generated/gql';
import { type Stock } from '@/shared/types';

/**
 * 股票搜索 GraphQL 查询
 */
export const SearchInstrumentsQueryGql = gql(`
  query SearchInstrumentsWithIndices($searchQuery: String!, $limit: Int) {
    instruments(
      where: {
        stockCode_contains: $searchQuery
        type_in: [STOCK, ETF, INDEX]
      }
      limit: $limit
    ) {
      id
      stockCode: id
      name
      market
      type
      quote {
        lastPrice
        changePercent
      }
    }
  }
`);

/**
 * 股票搜索和选择逻辑
 */
interface SearchHolding {
  stockCode: string;
  instrumentName?: string | null;
  lastPrice?: number | null;
  profitRate?: number | null;
}

export function useStockSearch(holdings: SearchHolding[] = []) {
  const [selectedStock, setSelectedStock] = useState<Stock | null>(null);
  const [searchQuery, setSearchQuery] = useState('');

  // 使用 URQL 查询搜索股票
  const [result] = useQuery({
    query: SearchInstrumentsQueryGql,
    variables: {
      searchQuery: searchQuery || '',
      limit: 10,
    },
    pause: !searchQuery || searchQuery.length < 2, // 至少输入2个字符才搜索
  });

  const stocksLoading = result.fetching;
  const data = result.data;

  // 将 Instrument 转换为 Stock 类型，并合并持仓
  const filteredStocks = useMemo(() => {
    // 如果没有搜索内容，返回持仓列表
    if (!searchQuery || searchQuery.length < 2) {
      return (holdings || []).map(h => ({
        id: h.stockCode,
        stockCode: h.stockCode,
        name: h.instrumentName || h.stockCode,
        quote: {
          lastPrice: h.lastPrice || 0,
          changePercent: h.profitRate || 0,
        },
      }));
    }

    if (!data?.instruments) return [];
    return data.instruments.map(instrument => ({
      ...instrument,
      name: instrument.name || instrument.id,
      stockCode: instrument.id,
    })) as Stock[];
  }, [data, searchQuery, holdings]);

  // 股票选择处理（选中后自动填充价格）
  const handleStockSelect = useCallback(
    (stock: Stock, setPrice?: (price: string) => void) => {
      setSelectedStock(stock);
      if (setPrice) {
        setPrice(String(stock.quote?.lastPrice || ''));
      }
      setSearchQuery('');
    },
    []
  );

  return useMemo(
    () => ({
      selectedStock,
      setSelectedStock,
      searchQuery,
      setSearchQuery,
      filteredStocks,
      stocksLoading,
      handleStockSelect,
    }),
    [
      selectedStock,
      searchQuery,
      filteredStocks,
      stocksLoading,
      handleStockSelect,
    ]
  );
}
