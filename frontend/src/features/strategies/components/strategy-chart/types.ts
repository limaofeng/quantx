import type {
  CandlestickData,
  HistogramData,
  SeriesMarker,
  Time,
} from 'lightweight-charts';

import type { ExecutionTraceView, StrategyDecision } from '../../domain/types';
import type { StrategyTickData } from '../../hooks/useStrategyTicks';

export type StrategyChartMode = 'live' | 'backtest';

export type StrategyChartPeriod =
  | 'DAY_1'
  | 'WEEK_1'
  | 'MONTH_1'
  | 'MIN_1'
  | 'MIN_5'
  | 'MIN_15'
  | 'MIN_30'
  | 'MIN_60'
  | 'TICK';

export interface StrategyChartRange {
  startTime?: string | null;
  endTime?: string | null;
}

export interface ChartOverlay {
  type: 'priceLine';
  price: number;
  color: string;
  title?: string;
  lineStyle?: 'solid' | 'dashed' | 'dotted';
  lineWidth?: number;
}

export interface StrategyChartDataState {
  priceData: Array<CandlestickData<Time> | { time: Time; value: number }>;
  volumeData: HistogramData<Time>[];
  loading: boolean;
  hasMore: boolean;
  loadMore: () => void;
  hasRange: boolean;
  isTickPeriod: boolean;
}

export interface StrategyChartLayerInput {
  period: StrategyChartPeriod;
  isTickPeriod: boolean;
  decisions?: StrategyDecision[];
  executions?: ExecutionTraceView[];
}

export interface StrategyChartProps {
  stockCode?: string;
  className?: string;
  overlays?: ChartOverlay[];
  latestTick?: StrategyTickData | null;
  liveTicks?: StrategyTickData[];
  mode?: StrategyChartMode;
  backtestRange?: StrategyChartRange | null;
  decisions?: StrategyDecision[];
  executions?: ExecutionTraceView[];
}

export interface StrategyChartMarkerDetail {
  label: string;
  value: string;
  tone?: 'default' | 'buy' | 'sell' | 'success' | 'warning' | 'muted';
}

export type StrategyChartMarker = SeriesMarker<Time> & {
  label: 'B' | 'S' | '?';
  tradeSide: 'BUY' | 'SELL' | 'UNKNOWN';
  eventType: 'signal' | 'filled' | 'rejected' | 'order';
  eventTitle: string;
  eventTime?: string | null;
  priceValue?: number | null;
  quantityValue?: number | null;
  detailRows: StrategyChartMarkerDetail[];
  groupCount?: number;
  childMarkers?: StrategyChartMarker[];
};

export interface StrategyChartHoverData {
  type: 'candle' | 'tick';
  time: string;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  price?: number;
  change?: number;
  changePercent?: number;
  markers?: StrategyChartMarker[];
}
