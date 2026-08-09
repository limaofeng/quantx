import { useQuery } from 'urql';

import { gql } from '@/generated/gql';

/**
 * 获取证券详情
 */
export const GetInstrumentQuery = gql(`
  query GetInstrument($stockCode: String!) {
    instrument(stockCode: $stockCode) {
      id

      name
      market
      type
      preClose
      upStopPrice
      downStopPrice
      priceTick
      isTrading
      productName
      totalVolume
      floatVolume
      quote {
        lastPrice
        open
        high
        low
        preClose
        change
        changePercent
        volume
        amount
        turnoverRate
        time
      }
    }
  }
`);

/**
 * K 线数据类型
 */
export interface KLineData {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
}

/**
 * 生成模拟 K 线数据
 */
function generateMockKLine(days: number = 100): KLineData[] {
  const data: KLineData[] = [];
  let price = 100;
  const now = new Date();

  for (let i = days; i > 0; i--) {
    const date = new Date(now);
    date.setDate(date.getDate() - i);

    const volatility = 0.02;
    const change = price * (Math.random() * volatility * 2 - volatility);
    const open = price;
    const close = price + change;
    const high = Math.max(open, close) + price * Math.random() * 0.01;
    const low = Math.min(open, close) - price * Math.random() * 0.01;
    const volume = Math.floor(Math.random() * 1000000) + 100000;

    data.push({
      date: date.toISOString(),
      open: Number(open.toFixed(2)),
      high: Number(high.toFixed(2)),
      low: Number(low.toFixed(2)),
      close: Number(close.toFixed(2)),
      volume,
      amount: volume * ((open + close) / 2),
    });

    price = close;
  }

  return data;
}

/**
 * 证券详情 Hook
 */
export function useStock(stockCode: string) {
  const [result, refetch] = useQuery({
    query: GetInstrumentQuery,
    variables: { stockCode },
    pause: !stockCode,
  });

  return {
    stock: result.data?.instrument,
    loading: result.fetching,
    error: result.error,
    refetch,
  };
}

/**
 * K 线数据 Hook (使用模拟数据)
 */
export function useStockKLine(code: string, _period: string = '1d') {
  // 生成稳定的模拟数据
  const mockData = generateMockKLine(100);

  return {
    kline: mockData,
    loading: false,
    error: undefined,
  };
}

/**
 * 复合证券详情 Hook
 */
export function useStockDetail(stockCode: string) {
  const {
    stock,
    loading: stockLoading,
    error: stockError,
    refetch,
  } = useStock(stockCode);
  const { kline, loading: klineLoading } = useStockKLine(stockCode);
  const isLoading = stockLoading || klineLoading;

  return {
    stock,
    kline,
    loading: isLoading,
    isLoading,
    error: stockError ?? null,
    refetch,
  };
}
