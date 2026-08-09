import type {
  CandlestickData,
  HistogramData,
  Time,
  AreaData,
} from 'lightweight-charts';

export type ChartPeriod = '1m_line' | '5d' | '1d' | '1w' | '1M' | string;

export interface TradingChartProps {
  stockCode?: string;
  className?: string;
}

export interface ChartDataSets {
  candlestick: CandlestickData[];
  area: AreaData[];
  volume: HistogramData[];
}

export interface TradingRange {
  from: Time;
  to: Time;
}
